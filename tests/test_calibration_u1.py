"""Aşama 5 / U1: rol bazında seçim, tutarlılık, tampon, dağılım dışı denetim, güvenli çıktı (veritabanısız)."""

import json

import numpy as np
import pandas as pd
import pytest

from medakis.forecasting import calibration as C
from medakis.forecasting import training as T
from medakis.forecasting import u1_run as U
from medakis.forecasting.dataset import CONTRACT_VERSION, INPUT_COLUMNS, ROLES, SplitPlan
from medakis.forecasting.models import DEMAND_TARGETS, ForestModel

SMALL = T.ExperimentConfig(days=28, plan=SplitPlan(2, 1, 1))
CONFIG = C.CalibrationConfig(min_group_size=50)


@pytest.fixture(scope="module")
def tf():
    return T.frame_for(SMALL)


@pytest.fixture(scope="module")
def models(tf):
    forest = ForestModel({"n_estimators": 15, "min_samples_leaf": 5, "max_features": 0.5}).fit(U.part(tf, "egitim"))
    return U.build_models(tf, forest)


@pytest.fixture(scope="module")
def cal(tf, models):
    return U.calibrate(tf, models, CONFIG, {"model_sha256": {}})


def manifests():
    good = {"sozlesme_surumu": CONTRACT_VERSION, "surumler": {"scikit_learn": C.sklearn.__version__}, "girdiler": list(INPUT_COLUMNS)}
    return {"random_forest": good, "baseline_bilgi": good}


# ---- Seçim yalnız doğrulamadan --------------------------------------------------------------


def test_calibration_refuses_test_rows(tf, models):
    train, test = U.part(tf, "egitim"), U.part(tf, "test")
    with pytest.raises(ValueError, match="test kullanılmaz"):
        C.fit_calibration(train, test, U.candidates_for(test, models), CONFIG, {})


def test_test_targets_do_not_change_any_calibration_parameter(tf, models, cal):
    scrambled = tf.frame.copy()
    test = scrambled["bolum"] == "test"
    for target in DEMAND_TARGETS:
        scrambled.loc[test, target] = scrambled.loc[test, target].to_numpy()[::-1] * 3 + 7
    other = type(tf)(scrambled, tf.metadata)
    assert U.calibrate(other, models, CONFIG, {"model_sha256": {}}).sha256() == cal.sha256()


def _toy_val(n=300):
    rng = np.random.default_rng(1)
    rows = {"servis_id": [1] * n, "servis_turu": ["inpatient"] * n, "bolum": ["dogrulama"] * n}
    for t in DEMAND_TARGETS:
        rows[t] = rng.uniform(0, 20, n)
    return pd.DataFrame(rows)


@pytest.mark.parametrize(
    ("rf_error", "bl_error", "expected"),
    [(2.0, 1.0, "baseline_bilgi"), (2.0, 1.85, "random_forest"), (1.0, 0.85, "random_forest"), (2.0, 2.0, "random_forest")],
)  # %50 iyi → seçilir; %7,5 → RF; %15 ama 0,15 dk → RF; eşit → RF
def test_selection_thresholds_for_supervisor(rf_error, bl_error, expected):
    val = _toy_val()
    rf = val[list(DEMAND_TARGETS)].copy() + 0.0
    bl = val[list(DEMAND_TARGETS)].copy() + 0.0
    target = "talep_dk_60_supervisor"
    rf[target] = val[target] + rf_error
    bl[target] = val[target] - bl_error
    table = C.selection_table(val, {"random_forest": rf, "baseline_bilgi": bl}, C.CalibrationConfig())
    row = table[(table["hedef"] == target)].iloc[0]
    assert row["secilen"] == expected
    assert (table[table["hedef"] != target]["secilen"] == "random_forest").all()


def test_supervisor_selection_on_real_validation(cal):
    table = pd.DataFrame(cal.selection)
    sup = table[table["rol"] == "supervisor"]
    assert len(sup) and set(table["secilen"]) <= {"random_forest", "baseline_bilgi"}
    for _, row in table[table["secilen"] == "baseline_bilgi"].iterrows():
        gain = row["mae_random_forest"] - row["mae_baseline_bilgi"]
        assert gain >= CONFIG.min_absolute_gain and gain / row["mae_random_forest"] >= CONFIG.min_relative_gain


# ---- Tutarlılık ve negatif olmama --------------------------------------------------------------


def test_discrepancy_definition():
    pred = pd.DataFrame({"talep_dk_60": [100.0, 50.0], "talep_dk_60_nurse": [60.0, 40.0],
                         "talep_dk_60_supervisor": [5.0, 5.0], "talep_dk_60_support": [20.0, 20.0]})  # fmt: skip
    assert C.discrepancy(pred, 60).tolist() == [15.0, -15.0]


def test_role_predictions_never_reduced_or_negative(tf, models, cal):
    test = U.part(tf, "test")
    cands = U.candidates_for(test, models)
    combined = C.combine(cands, test, cal.choice())
    out = C.safe_forecast(test, cands, cal, manifests(), {})
    assert (out["nokta_tahmin_dk"] >= 0).all() and (out["tampon_dk"] >= 0).all()
    assert (out["tamponlu_talep_dk"] >= out["nokta_tahmin_dk"]).all()
    for (_, row) in out.sample(200, random_state=0).iterrows():
        idx = test.index[(test["servis_id"] == row["servis_id"]) & (test["an_utc"] == row["an_utc"])][0]
        assert row["nokta_tahmin_dk"] == pytest.approx(combined.at[idx, f"talep_dk_{row['ufuk_dk']}_{row['rol']}"])
        roles = sum(combined.at[idx, f"talep_dk_{row['ufuk_dk']}_{r}"] for r in ROLES)
        assert row["rol_toplami_dk"] == pytest.approx(roles)
        assert row["tutarsizlik_dk"] == pytest.approx(combined.at[idx, f"talep_dk_{row['ufuk_dk']}"] - roles)


def test_large_discrepancy_marks_review(tf, models, cal):
    test = U.part(tf, "test").head(3)
    cands = {k: v.copy() for k, v in U.candidates_for(test, models).items()}
    for v in cands.values():
        v["talep_dk_60"] = v["talep_dk_60"] + 10_000  # büyük pozitif fark
    out = C.safe_forecast(test, cands, cal, manifests(), {})
    rows60 = out[out["ufuk_dk"] == 60]
    assert (rows60["durum"] == C.INCELEME).all() and all("toplam_rol_tutarsiz" in r for r in rows60["inceleme_nedenleri"])
    original = C.combine(U.candidates_for(test, models), test, cal.choice())
    assert rows60["nokta_tahmin_dk"].tolist() == pytest.approx(
        [original.at[i, f"talep_dk_60_{r}"] for r in ROLES for i in test.index])  # roller değişmedi


# ---- Tampon ------------------------------------------------------------------------------------


def test_buffers_are_non_negative_and_relative_scales(tf, models, cal):
    assert all(v >= 0 for v in cal.buffers.values())
    val = U.part(tf, "dogrulama")
    pred = C.combine(U.candidates_for(val, models), val, cal.choice())
    ed = val["servis_turu"] == "emergency"
    b_rel = C.buffer_for(cal, val[ed], pred[ed], "talep_dk_60_nurse", method="goreli")
    q = cal.buffers[f"3|talep_dk_60_nurse|gunduz|{CONFIG.alpha}|goreli"]
    day = (val[ed]["yerel_saat"].between(8, 15)).to_numpy()
    expected = q * np.maximum(pred[ed]["talep_dk_60_nurse"].to_numpy(), CONFIG.relative_epsilon)
    assert np.allclose(b_rel[day], np.clip(expected[day], 0, None))


def test_insufficient_groups_fall_back_explicitly(tf, models):
    big = C.CalibrationConfig(min_group_size=10_000)
    cal = U.calibrate(tf, models, big, {})
    assert cal.buffer_fallbacks and all("→" in f for f in cal.buffer_fallbacks)


def test_validation_coverage_close_to_nominal(tf, models, cal):
    val = U.part(tf, "dogrulama")
    pred = C.combine(U.candidates_for(val, models), val, cal.choice())
    rows = U.coverage_table(val, pred, cal)
    abs_rows = [r for r in rows if r["yontem"] == "mutlak" and r["hedef"] == "talep_dk_60_nurse"]
    for r in abs_rows:
        assert r["kapsama"] >= r["q"] - 0.02  # doğrulamada kurulduğu için (sınırlı örnekte) nominale yakın


# ---- Dağılım dışı ve uyumluluk ------------------------------------------------------------------


def test_unseen_category_and_invalid_inputs_detected(tf, models, cal):
    test = U.part(tf, "test").head(2).copy()
    test.loc[test.index[0], "servis_profili"] = "yuksek"
    test.loc[test.index[0], "servis_turu"] = "emergency"
    test.loc[test.index[1], "hasta_sayisi"] = float("nan")
    warnings = C.row_warnings(test, cal)
    assert any(w.startswith("gorulmemis_kategori:emergency/yuksek") for w in warnings[0])
    assert "gecersiz_girdi:hasta_sayisi" in warnings[1]


def test_missing_input_column_is_refused(tf, models, cal):
    test = U.part(tf, "test").head(2).drop(columns=["bekleyen_dk"])
    with pytest.raises(KeyError):
        C.safe_forecast(test, {"random_forest": pd.DataFrame(), "baseline_bilgi": pd.DataFrame()}, cal, manifests(), {})


def test_incompatible_manifest_marks_everything_for_review(tf, models, cal):
    test = U.part(tf, "test").head(2)
    bad = manifests()
    bad["random_forest"] = {**bad["random_forest"], "sozlesme_surumu": "9.9"}
    out = C.safe_forecast(test, U.candidates_for(test, models), cal, bad, {})
    assert (out["durum"] == C.INCELEME).all() and all("uyumsuz_sozlesme:random_forest" in r for r in out["inceleme_nedenleri"])
    # U2 düzeltmesi: uyumsuz modelde geçerli görünen sayı da üretilmez.
    assert out[["nokta_tahmin_dk", "tampon_dk", "tamponlu_talep_dk"]].isna().all().all()
    assert all("gecerli_tahmin_yok" in r for r in out["inceleme_nedenleri"])


@pytest.mark.parametrize("profile", [{"ed_volume_profile": "yuksek"}, {"ed_triage_profile": "yesil_yogun"}])
def test_scenario_warnings_only_where_data_changed(tf, models, cal, profile):
    base = U.part(tf, "test")
    other = U.part(T.frame_for(SMALL, **profile), "test")
    w_base = dict(zip(zip(base["servis_id"], base["an_utc"]), C.row_warnings(base, cal)))
    w_other = dict(zip(zip(other["servis_id"], other["an_utc"]), C.row_warnings(other, cal)))
    for key, warnings in w_other.items():
        if key[0] != 3:  # yalnız acil değişti: diğer servislerde uyarılar aynı
            assert warnings == w_base[key]
    ed_other = [w for k, w in w_other.items() if k[0] == 3]
    ed_base = [w for k, w in w_base.items() if k[0] == 3]
    if profile.get("ed_volume_profile") == "yuksek":
        assert sum("dagilim_disi:hasta_sayisi" in w for w in ed_other) > sum("dagilim_disi:hasta_sayisi" in w for w in ed_base)


# ---- Determinizm ve çıktı ---------------------------------------------------------------------


def test_deterministic_calibration_and_output(tf, models, cal):
    again = U.calibrate(tf, models, CONFIG, {"model_sha256": {}})
    assert again.sha256() == cal.sha256()
    test = U.part(tf, "test").head(20)
    a = C.safe_forecast(test, U.candidates_for(test, models), cal, manifests(), {})
    b = C.safe_forecast(test, U.candidates_for(test, models), again, manifests(), {})
    pd.testing.assert_frame_equal(a, b)


def test_output_fields_and_no_staffing_recommendation(tf, models, cal):
    test = U.part(tf, "test").head(5)
    out = C.safe_forecast(test, U.candidates_for(test, models), cal, manifests(), {"random_forest": "rf@x"})
    required = {"servis_id", "an_utc", "rol", "ufuk_dk", "model", "model_surumu", "nokta_tahmin_dk", "tampon_dk",
                "tamponlu_talep_dk", "tutarsizlik_dk", "uyarilar", "durum", "inceleme_nedenleri"}  # fmt: skip
    assert required <= set(out.columns)
    assert not {c for c in out.columns if any(k in c for k in ("personel_sayisi", "atama", "vardiya", "staff"))}
    assert set(out["rol"]) == set(ROLES) and set(out["durum"]) <= {C.UYGUN, C.INCELEME}
    assert json.loads(cal.to_json())["bicim"] == C.CALIBRATION_FORMAT


# ---- U2: tahmin öncesi girdi doğrulaması (U1 düzeltmesi) ------------------------------------------


class Spy:
    """Modele hangi satırların verildiğini kaydeder."""

    def __init__(self, model):
        self.model, self.seen = model, []

    def predict(self, frame):
        self.seen.append(list(frame.index))
        return self.model.predict(frame)


def _broken(tf):
    test = U.part(tf, "test").head(6).copy()
    idx = test.index
    test["hasta_sayisi"] = test["hasta_sayisi"].astype(float)
    test.loc[idx[0], "hasta_sayisi"] = float("nan")
    test.loc[idx[1], "bekleyen_dk"] = float("inf")
    test.loc[idx[2], "son_15dk_kapasite_dk"] = -5.0
    test.loc[idx[3], "tatil_turu"] = "bayram_ama_bilinmiyor"
    test.loc[idx[4], "servis_turu"] = "yogun_bakim"
    return test  # idx[5] geçerli


def test_invalid_rows_are_never_given_to_model(tf, models, cal):
    test = _broken(tf)
    spies = {k: Spy(m) for k, m in models.items()}
    cands = U.candidates_for(test, spies, manifests(), cal)
    for spy in spies.values():
        assert spy.seen == [[test.index[5]]]  # yalnız geçerli satır modele gitti
    for c in cands.values():
        assert c.loc[test.index[:5]].isna().all().all() and c.loc[test.index[5]].notna().all()


@pytest.mark.parametrize("pos,code", [(0, "gecersiz_girdi:hasta_sayisi"), (1, "gecersiz_girdi:bekleyen_dk"),
                                      (2, "negatif_girdi:son_15dk_kapasite_dk"), (3, "gecersiz_kategori:tatil_turu"),
                                      (4, "gecersiz_kategori:servis_turu")])  # fmt: skip
def test_invalid_inputs_give_safe_refusal_without_numbers(tf, models, cal, pos, code):
    test = _broken(tf)
    out = C.safe_forecast(test, U.candidates_for(test, models, manifests(), cal), cal, manifests(), {})
    rows = out[out["an_utc"] == test.loc[test.index[pos], "an_utc"]]
    rows = rows[rows["servis_id"] == test.loc[test.index[pos], "servis_id"]]
    assert len(rows) == len(ROLES) * len(C.HORIZONS)
    assert (rows["durum"] == C.INCELEME).all()
    assert all(code in r and "gecerli_tahmin_yok" in r for r in rows["inceleme_nedenleri"])
    assert rows[["nokta_tahmin_dk", "tampon_dk", "tamponlu_talep_dk"]].isna().all().all()


def test_externally_supplied_predictions_for_invalid_rows_are_discarded(tf, models, cal):
    """Geçersiz satıra dışarıdan (doğrulamasız) tahmin verilse de güvenli çıktı onu kullanmaz."""
    test = _broken(tf)
    # Doğrulamasız eski yol RF'de sonsuz değerle hata verir, baseline ise sessizce ortalamayla doldurur;
    # her iki durumda da dışarıdan gelen sayılar geçersiz satırda kullanılmamalı.
    raw = {k: pd.DataFrame(5.0, index=test.index, columns=list(DEMAND_TARGETS)) for k in models}
    out = C.safe_forecast(test, raw, cal, manifests(), {})
    bad = out[out["an_utc"].isin(test.loc[test.index[:5], "an_utc"])]
    assert bad["nokta_tahmin_dk"].isna().all() and (bad["durum"] == C.INCELEME).all()


def test_incompatible_model_is_not_called(tf, models, cal):
    test = U.part(tf, "test").head(3)
    bad = manifests()
    bad["baseline_bilgi"] = {**bad["baseline_bilgi"], "surumler": {"scikit_learn": "0.0"}}
    spies = {k: Spy(m) for k, m in models.items()}
    cands = U.candidates_for(test, spies, bad, cal)
    assert all(spy.seen == [] for spy in spies.values())
    assert all(c.isna().all().all() for c in cands.values())


def test_missing_column_refused_before_prediction(tf, models, cal):
    test = U.part(tf, "test").head(2).drop(columns=["hasta_sayisi"])
    spies = {k: Spy(m) for k, m in models.items()}
    cands = U.candidates_for(test, spies, manifests(), cal)
    assert all(spy.seen == [] for spy in spies.values()) and all(c.isna().all().all() for c in cands.values())
    with pytest.raises(KeyError):
        C.safe_forecast(test, cands, cal, manifests(), {})


def test_valid_frame_predictions_unchanged_by_guard(tf, models, cal):
    test = U.part(tf, "test")
    assert all(not p for p in C.row_input_problems(test))
    guarded = U.candidates_for(test, models, manifests(), cal)
    for name, model in models.items():
        pd.testing.assert_frame_equal(guarded[name][list(DEMAND_TARGETS)], model.predict(test)[list(DEMAND_TARGETS)],
                                      check_dtype=False)  # fmt: skip



# ---- İnceleme düzeltmesi 3: servis kimliği pozitif tam sayı ---------------------------------------


@pytest.mark.parametrize("value,ok", [(1, True), (np.int64(1), True), (1.0, True), (np.float64(3.0), True),
                                      (1.5, False), (0, False), (0.0, False), (-1, False), (True, False),
                                      (np.bool_(True), False), ("1", False), (float("nan"), False),
                                      (float("inf"), False), (None, False)])  # fmt: skip
def test_valid_unit_id(value, ok):
    assert C.valid_unit_id(value) is ok


@pytest.mark.parametrize("bad", [1.5, 0, -1, True, "1", float("nan"), float("inf")])
def test_invalid_unit_id_never_reaches_model(tf, models, cal, bad):
    test = U.part(tf, "test").head(2).copy().astype({"servis_id": object})
    test.loc[test.index[0], "servis_id"] = bad
    assert "gecersiz_kimlik:servis_id" in C.row_input_problems(test)[0] and C.row_input_problems(test)[1] == []
    spies = {k: Spy(m) for k, m in models.items()}
    cands = U.candidates_for(test, spies, manifests(), cal)
    assert all(spy.seen == [[test.index[1]]] for spy in spies.values())  # yalnız geçerli satır
    out = C.safe_forecast(test, cands, cal, manifests(), {})
    rows = out[["gecersiz_kimlik:servis_id" in r for r in out["uyarilar"]]]
    assert len(rows) == len(ROLES) * len(C.HORIZONS)  # yalnız hatalı satırın 2 ufuk × 3 rol kaydı
    assert (rows["durum"] == C.INCELEME).all() and all("gecerli_tahmin_yok" in r for r in rows["inceleme_nedenleri"])
    assert rows[["nokta_tahmin_dk", "tampon_dk", "tamponlu_talep_dk"]].isna().all().all()


def test_external_candidates_ignored_for_fractional_unit_id(tf, models, cal):
    test = U.part(tf, "test").head(2).copy().astype({"servis_id": object})
    test.loc[test.index[0], "servis_id"] = 1.5
    raw = {k: pd.DataFrame(5.0, index=test.index, columns=list(DEMAND_TARGETS)) for k in models}
    out = C.safe_forecast(test, raw, cal, manifests(), {})
    bad = out[[("gecersiz_kimlik:servis_id" in r) for r in out["uyarilar"]]]
    good = out[[("gecersiz_kimlik:servis_id" not in r) for r in out["uyarilar"]]]
    assert len(bad) == len(good) == len(ROLES) * len(C.HORIZONS)
    assert bad["nokta_tahmin_dk"].isna().all() and (bad["durum"] == C.INCELEME).all()
    assert (good["nokta_tahmin_dk"] == 5.0).all()


def test_integral_float_unit_id_gives_same_forecast(tf, models, cal):
    test = U.part(tf, "test").head(10)
    as_float = test.astype({"servis_id": float})
    a = C.safe_forecast(test, U.candidates_for(test, models, manifests(), cal), cal, manifests(), {})
    b = C.safe_forecast(as_float, U.candidates_for(as_float, models, manifests(), cal), cal, manifests(), {})
    cols = ["nokta_tahmin_dk", "tampon_dk", "tamponlu_talep_dk", "durum", "model"]
    pd.testing.assert_frame_equal(a[cols], b[cols])
    assert list(a["servis_id"]) == list(b["servis_id"])


def test_missing_unit_id_column_refused_before_prediction(tf, models, cal):
    test = U.part(tf, "test").head(2).drop(columns=["servis_id"])
    spies = {k: Spy(m) for k, m in models.items()}
    cands = U.candidates_for(test, spies, manifests(), cal)
    assert all(spy.seen == [] for spy in spies.values()) and all(c.isna().all().all() for c in cands.values())
    with pytest.raises(KeyError):
        C.safe_forecast(test, cands, cal, manifests(), {})
