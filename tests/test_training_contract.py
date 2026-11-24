"""Aşama 4 eğitim veri sözleşmesi: kaynak, sızıntı, üç senaryo, zaman bölümleri, köken (veritabanısız)."""

import ast
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from medakis.domain.models import Encounter
from medakis.domain.workload import patient_count_at, pending_at
from medakis.forecasting import dataset as D
from medakis.synthetic.v2 import ParamsV2, generate_v2

ROOT = Path(__file__).resolve().parents[1]
V2_GOLDEN = "2a1fdfb0dabcbd9999b3b5f61e3c3f8807f6bb62353c12aea76dfacf2cac733e"


@pytest.fixture(scope="module")
def week():
    return generate_v2(ParamsV2(days=7))


@pytest.fixture(scope="module")
def week_rows(week):
    return D.build_rows(week)


@pytest.fixture(scope="module")
def full():
    ds = generate_v2()
    return ds, D.build_training_frame(ds)


# ---- Sözleşme yapısı ------------------------------------------------------------------------


def test_contract_groups_are_disjoint_and_complete(week_rows):
    names = [f.name for f in D.FIELDS]
    assert len(names) == len(set(names))
    assert {f.group for f in D.FIELDS} == {D.ID, D.SPLIT, D.INPUT, D.TARGET, D.CONTEXT}
    assert set(week_rows.columns) | {"bolum"} == set(names)
    assert not set(D.INPUT_COLUMNS) & (set(D.TARGET_COLUMNS) | set(D.CONTEXT_COLUMNS))
    for f in D.FIELDS:
        assert f.unit and f.known_at and f.source, f.name


def test_inputs_are_documented_as_known_at_forecast_time():
    for f in D.FIELDS:
        if f.group == D.INPUT:
            assert f.known_at in ("t", "sabit", "önceden bilinir"), f.name
        if f.group in (D.TARGET, D.CONTEXT):
            assert "t+" in f.known_at or f.known_at == "gelecek", f.name


def test_targets_separate_demand_realized_capacity_and_cancellations():
    assert {c for c in D.TARGET_COLUMNS if c.startswith("talep_dk_")} and {c for c in D.TARGET_COLUMNS if c.startswith("gerceklesen_dk_")}
    assert all(c.startswith(("kapasite_dk_", "iptal_dk_")) for c in D.CONTEXT_COLUMNS)
    assert not [c for c in D.INPUT_COLUMNS if c.startswith(("talep_", "gerceklesen_", "kapasite_dk_", "iptal_dk_"))]


def test_only_three_roles_no_physician():
    assert D.ROLES == ("nurse", "supervisor", "support")
    assert not [f.name for f in D.FIELDS if "physician" in f.name or "hekim" in f.name]


# ---- Kaynak: bellek içi üretici, veritabanı yok ------------------------------------------------


def test_dataset_module_has_no_database_or_service_dependency():
    tree = ast.parse((ROOT / "src" / "medakis" / "forecasting" / "dataset.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    forbidden = ("sqlalchemy", "psycopg", "medakis.repositories", "medakis.services", "medakis.optimization", "sklearn")
    assert not [m for m in imported if m.startswith(forbidden)]


# ---- Sızıntı: girdiler yalnız t anında bilinen bilgiden ---------------------------------------


@pytest.mark.parametrize("hours", [3, 20, 47, 71, 102, 150])
def test_inputs_equal_when_computed_from_as_of_view(week, hours):
    j = hours * 4
    t = week.scenario_start + timedelta(hours=hours)
    full = D.build_rows(week, origins=[j])
    view = D.build_rows(week, tables=D.known_view(week.tables, t), origins=[j])
    for column in D.INPUT_COLUMNS:
        a, b = full[column].tolist(), view[column].tolist()
        if isinstance(a[0], (float, np.floating)):
            assert np.allclose(a, b, atol=1e-6), column
        else:
            assert a == b, column


def test_as_of_view_really_hides_the_future(week):
    t = week.scenario_start + timedelta(hours=47)
    full = D.build_rows(week, origins=[47 * 4])
    view = D.build_rows(week, tables=D.known_view(week.tables, t), origins=[47 * 4])
    assert not np.allclose(full["talep_dk_60"], view["talep_dk_60"])  # hedef gelecektir: görünümde eksik kalır


def test_pending_and_patients_match_stage3_domain(week, week_rows):
    tasks = [D.care_task(r) for r in week.tables["care_tasks"]]
    encs = [Encounter(e["id"], e["patient_id"], e["unit_id"], e["bed_id"], e["admitted_at"], e["discharged_at"])
            for e in week.tables["encounters"]]  # fmt: skip
    sample = week_rows.iloc[:: 97]
    for _, row in sample.iterrows():
        unit, t = row["servis_id"], row["an_utc"]
        assert row["bekleyen_dk"] == pytest.approx(pending_at([x for x in tasks if x.unit_id == unit], t).minutes)
        assert row["hasta_sayisi"] == patient_count_at([e for e in encs if e.unit_id == unit], t)


def test_demand_target_matches_direct_definition(week, week_rows):
    sample = week_rows.iloc[:: 131]
    for _, row in sample.iterrows():
        unit, t = row["servis_id"], row["an_utc"]
        direct = sum(r["estimated_minutes"] for r in week.tables["care_tasks"]
                     if r["unit_id"] == unit and t <= r["due_at"] < t + timedelta(minutes=60))  # fmt: skip
        assert row["talep_dk_60"] == pytest.approx(direct)


def test_lagged_work_uses_only_tasks_completed_by_t(week, week_rows):
    sample = week_rows.iloc[:: 157]
    for _, row in sample.iterrows():
        unit, t = row["servis_id"], row["an_utc"]
        lo, hi = t - timedelta(minutes=15), t
        known = 0.0
        for r in week.tables["care_tasks"]:
            if r["unit_id"] != unit or r["status"] != "completed" or r["completed_at"] > t:
                continue
            start = r["completed_at"] - timedelta(minutes=r["actual_minutes"])
            overlap = (min(r["completed_at"], hi) - max(start, lo)).total_seconds() / 60
            if overlap > 0:
                known += r["actual_minutes"] * overlap / r["actual_minutes"]
        assert row["gecmis_is_dk_l1"] == pytest.approx(known, abs=1e-6)


# ---- Üç senaryo, bölümler, köken ----------------------------------------------------------------


def test_all_four_units_and_three_scenarios_present(full):
    _, tf = full
    kinds = tf.frame.groupby("servis_id")["servis_turu"].first().to_dict()
    assert sorted(kinds.values()) == ["emergency", "inpatient", "inpatient", "outpatient"]
    assert set(tf.frame["servis_profili"]) >= {"normal", "yuksek"}
    assert (tf.frame.groupby("servis_id")["talep_dk_60"].sum() > 0).all()


def test_chronological_splits_with_embargo(full):
    _, tf = full
    f = tf.frame
    order = {"egitim": 0, "dogrulama": 1, "test": 2}
    used = f[f["bolum"] != "ambargo"]
    assert set(used["bolum"]) == set(order)
    last = used.groupby("bolum")["an_utc"].max()
    first = used.groupby("bolum")["an_utc"].min()
    assert last["egitim"] < first["dogrulama"] and last["dogrulama"] < first["test"]
    # Etiket ufku bölüm içinde kalır: her bölümün son anının t+60'ı o bölümün bitiş sınırını geçmez.
    from datetime import datetime

    for name in ("egitim", "dogrulama", "test"):
        boundary = datetime.fromisoformat(tf.metadata["bolumler"][name].split(" / ")[1])
        assert last[name] + timedelta(minutes=60) <= boundary, name
        assert first[name] >= datetime.fromisoformat(tf.metadata["bolumler"][name].split(" / ")[0]), name
    assert (f["bolum"] == "ambargo").sum() > 0


def test_metadata_traces_source_and_is_deterministic(full):
    ds, tf = full
    m = tf.metadata
    assert m["uretici_surumu"] == "2.0.0" and m["seed"] == 42 and m["icerik_sha256"] == V2_GOLDEN
    assert m["sozlesme_surumu"] == D.CONTRACT_VERSION and m["kaynak"].startswith("üretici 2.0.0, bellek")
    for key in ("parametre_parmak_izi", "talep_sha256", "ek_kayit_sha256", "donem", "bolumler", "cerceve_sha256"):
        assert m[key], key
    again = D.build_training_frame(generate_v2(ParamsV2(days=7)))
    assert again.frame_sha256() == D.build_training_frame(generate_v2(ParamsV2(days=7))).frame_sha256()


def test_v1_like_data_without_cancel_time_is_refused(week):
    tables = {k: list(v) for k, v in week.tables.items()}
    tables["care_tasks"] = [{**r, "cancelled_at": None} if r["status"] == "cancelled" else r for r in tables["care_tasks"]]
    with pytest.raises(ValueError, match="İptal zamanı olmayan"):
        D.build_rows(week, tables=tables, origins=[10])
