"""Aşama 4 tahmin modelleri: baseline, Random Forest, sütun sınırları, sızıntı, kayıt/yükleme (veritabanısız).

Küçük veri (28 gün, 2+1+1 hafta) ve küçük orman kullanılır; tam deney scripts/asama4_egit.py ile koşar.
"""

import ast
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from medakis.forecasting import models as M
from medakis.forecasting import registry as R
from medakis.forecasting import training as T
from medakis.forecasting.dataset import CONTEXT_COLUMNS, INPUT_COLUMNS, ROLES, TARGET_COLUMNS, SplitPlan

ROOT = Path(__file__).resolve().parents[1]
SMALL_RF = {"n_estimators": 15, "min_samples_leaf": 5, "max_features": 0.5}
CONFIG = T.ExperimentConfig(days=28, plan=SplitPlan(2, 1, 1), candidates={"kucuk": SMALL_RF}, robustness={})


@pytest.fixture(scope="module")
def tf():
    return T.frame_for(CONFIG)


@pytest.fixture(scope="module")
def parts(tf):
    return T.part(tf, "egitim"), T.part(tf, "dogrulama"), T.part(tf, "test")


@pytest.fixture(scope="module")
def forest(parts):
    return M.ForestModel(SMALL_RF).fit(parts[0])


@pytest.fixture(scope="module")
def baseline(parts):
    return M.SeasonalBaseline().fit(parts[0])


# ---- Hedefler ve sütun sınırları ---------------------------------------------------------------


def test_targets_are_demand_by_horizon_and_three_roles():
    assert set(M.DEMAND_TARGETS) == {"talep_dk_30", "talep_dk_60", *[f"talep_dk_{h}_{r}" for h in (30, 60) for r in ROLES]}
    assert ROLES == ("nurse", "supervisor", "support")
    assert not set(M.DEMAND_TARGETS) & set(M.REALIZED_TARGETS)
    assert set(M.DEMAND_TARGETS) | set(M.REALIZED_TARGETS) <= set(TARGET_COLUMNS)


def test_model_input_is_exactly_input_columns(forest):
    x = M.model_inputs(pd.DataFrame({c: [0] for c in [*INPUT_COLUMNS, "bolum", "servis_id", "an_utc", *TARGET_COLUMNS]}))
    assert list(x.columns) == list(INPUT_COLUMNS)
    seen = list(forest.pipeline.named_steps["donusum"].feature_names_in_)
    assert seen == list(INPUT_COLUMNS)
    forbidden = {"bolum", "servis_id", "an_utc", *TARGET_COLUMNS, *CONTEXT_COLUMNS}
    assert not forbidden & set(seen)


def test_changing_targets_context_or_ids_does_not_change_predictions(forest, baseline, parts):
    _, val, _ = parts
    scrambled = val.copy()
    for column in [*TARGET_COLUMNS, *CONTEXT_COLUMNS]:
        scrambled[column] = np.random.default_rng(0).permutation(scrambled[column].to_numpy())
    scrambled["servis_id"] = 99
    scrambled["bolum"] = "x"
    for model in (forest, baseline):
        pd.testing.assert_frame_equal(model.predict(val), model.predict(scrambled))


def test_inputs_contain_no_future_columns():
    future = ("talep_", "gerceklesen_", "kapasite_dk_", "iptal_dk_")
    assert not [c for c in INPUT_COLUMNS if c.startswith(future)]
    assert {"son_15dk_kapasite_dk", "son_1sa_iptal_dk", "bekleyen_dk", "hasta_sayisi"} <= set(INPUT_COLUMNS)


# ---- Baseline ----------------------------------------------------------------------------------


def test_baseline_learns_only_from_training(baseline, parts):
    train, val, test = parts
    with pytest.raises(ValueError, match="yalnız eğitim"):
        M.SeasonalBaseline().fit(pd.concat([train, val]))
    expected = train.groupby(["servis_turu", "haftanin_gunu", "yerel_saat"])["talep_dk_60"].mean()
    row = test.iloc[0]
    key = (row["servis_turu"], row["haftanin_gunu"], row["yerel_saat"])
    assert baseline.predict(test.iloc[[0]])["talep_dk_60"].iloc[0] == pytest.approx(max(0.0, expected.loc[key]))


def test_baseline_falls_back_for_unseen_combinations(parts):
    train, _, test = parts
    reduced = train[~((train["servis_turu"] == "emergency") & (train["haftanin_gunu"] == 2))]
    model = M.SeasonalBaseline().fit(reduced)
    rows = test[(test["servis_turu"] == "emergency") & (test["haftanin_gunu"] == 2)].head(3)
    assert len(rows)
    fallback = reduced.groupby(["servis_turu", "yerel_saat"])["talep_dk_30"].mean()
    for _, row in rows.iterrows():
        got = model.predict(rows.loc[[row.name]])["talep_dk_30"].iloc[0]
        assert got == pytest.approx(fallback.loc[(row["servis_turu"], row["yerel_saat"])])
    unknown = rows.copy()
    unknown["servis_turu"] = "bilinmeyen"
    assert model.predict(unknown)["talep_dk_30"].iloc[0] == pytest.approx(reduced["talep_dk_30"].mean())


# ---- Random Forest -----------------------------------------------------------------------------


def test_forest_predicts_all_targets_non_negative(forest, parts):
    _, val, test = parts
    for frame in (val, test):
        pred = forest.predict(frame)
        assert list(pred.columns) == list(M.DEMAND_TARGETS) and (pred.to_numpy() >= 0).all()


def test_negative_raw_predictions_are_clipped(forest, parts):
    class Negative:
        def predict(self, x):
            return -np.ones((len(x), len(M.DEMAND_TARGETS)))

    model = M.ForestModel(SMALL_RF)
    model.pipeline = Negative()
    assert (model.predict(parts[1]).to_numpy() == 0).all()


def test_forest_refuses_non_training_rows(parts):
    with pytest.raises(ValueError, match="yalnız eğitim"):
        M.ForestModel(SMALL_RF).fit(parts[1])


def test_forest_training_is_reproducible(parts, forest):
    again = M.ForestModel(SMALL_RF).fit(parts[0])
    pd.testing.assert_frame_equal(forest.predict(parts[2]), again.predict(parts[2]))


def test_three_scenarios_and_roles_are_predicted(forest, parts):
    test = parts[2]
    pred = forest.predict(test)
    for kind in ("inpatient", "emergency", "outpatient"):
        assert (test["servis_turu"] == kind).any(), kind
    assert set(test["servis_profili"]) >= {"normal", "yuksek"}
    for role in ROLES:
        assert f"talep_dk_60_{role}" in pred.columns


# ---- Metrikler ---------------------------------------------------------------------------------


def test_metrics_definitions():
    m = M.metrics(np.array([0.0, 10.0, 20.0]), np.array([2.0, 10.0, 26.0]))
    assert m["mae"] == pytest.approx(8 / 3) and m["rmse"] == pytest.approx(np.sqrt(40 / 3))
    assert m["wape"] == pytest.approx(8 / 30) and m["sifir_talep_n"] == 1 and m["sifir_talep_mae"] == pytest.approx(2.0)
    assert M.metrics(np.zeros(3), np.ones(3))["wape"] is None


# ---- Bölümler, ambargo, test yeniden eğitimi -----------------------------------------------------


def test_training_and_evaluation_label_windows_do_not_overlap(parts):
    train, val, test = parts
    horizon = timedelta(minutes=60)
    assert train["an_utc"].max() + horizon <= val["an_utc"].min()
    assert val["an_utc"].max() + horizon <= test["an_utc"].min()


def test_evaluation_never_fits(monkeypatch, forest, baseline, parts):
    calls = []
    monkeypatch.setattr(M.ForestModel, "fit", lambda self, *a: calls.append("rf"))
    monkeypatch.setattr(M.SeasonalBaseline, "fit", lambda self, *a: calls.append("bl"))
    T.evaluate(forest, parts[2], M.DEMAND_TARGETS)
    T.evaluate(baseline, parts[2], M.DEMAND_TARGETS)
    assert calls == []


def test_experiment_selects_on_validation_and_fits_on_training_only(monkeypatch):
    seen = []
    real_fit = M.ForestModel.fit

    def spy(self, train):
        seen.append(set(train["bolum"]))
        return real_fit(self, train)

    monkeypatch.setattr(M.ForestModel, "fit", spy)
    result = T.run_experiment(CONFIG)
    assert seen and all(s == {"egitim"} for s in seen)
    assert set(result["dogrulama_secimi"]["ortalama_mae"]) == {"baseline", "kucuk"}
    assert result["dogrulama_secimi"]["secilen_rf"] == "kucuk"


def test_dataset_transform_is_deterministic(tf):
    assert T.frame_for(CONFIG).frame_sha256() == tf.frame_sha256()


# ---- Kayıt / yükleme ---------------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path, forest, parts, tf):
    manifest = R.save_model(forest, tmp_path / "rf", tf.metadata)
    assert manifest["model_sha256"] and manifest["veri_kokeni"]["icerik_sha256"] == tf.metadata["icerik_sha256"]
    loaded = R.WorkloadForecaster.load(tmp_path / "rf")
    pd.testing.assert_frame_equal(loaded.predict(parts[2]), forest.predict(parts[2]))
    items = loaded.forecast(parts[2].head(2))
    assert {"servis_id", "an_utc", "talep_dk_60", "model", "model_sha256"} <= set(items[0])


def test_load_refuses_tampered_or_incompatible_models(tmp_path, baseline, tf):
    R.save_model(baseline, tmp_path / "bl", tf.metadata)
    with (tmp_path / "bl" / R.MODEL_FILE).open("ab") as handle:
        handle.write(b"x")
    with pytest.raises(R.ModelCompatibilityError, match="özeti tutmuyor"):
        R.load_model(tmp_path / "bl")
    R.save_model(baseline, tmp_path / "bl2", tf.metadata)
    manifest = json.loads((tmp_path / "bl2" / R.MANIFEST_FILE).read_text(encoding="utf-8"))
    manifest["surumler"]["scikit_learn"] = "0.0.1"
    manifest["sozlesme_surumu"] = "9.9"
    (tmp_path / "bl2" / R.MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(R.ModelCompatibilityError, match="scikit-learn") as info:
        R.load_model(tmp_path / "bl2")
    assert "sözleşme" in str(info.value)


def test_save_refuses_to_overwrite(tmp_path, baseline, tf):
    R.save_model(baseline, tmp_path / "bl", tf.metadata)
    with pytest.raises(FileExistsError):
        R.save_model(baseline, tmp_path / "bl", tf.metadata)


def test_model_files_are_not_tracked_by_git():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "artifacts/*" in text and "*.joblib" in text


# ---- Kapsam: veritabanı, optimizasyon, arayüz bağımlılığı yok ----------------------------------


@pytest.mark.parametrize("name", ["models.py", "registry.py", "training.py", "dataset.py"])
def test_forecasting_has_no_database_ui_or_optimization_dependency(name):
    tree = ast.parse((ROOT / "src" / "medakis" / "forecasting" / name).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    forbidden = ("sqlalchemy", "psycopg", "streamlit", "ortools", "medakis.repositories", "medakis.services",
                 "medakis.optimization", "medakis.ui", "medakis.rag", "langgraph", "ollama")  # fmt: skip
    assert not [m for m in imported if m.startswith(forbidden)]
