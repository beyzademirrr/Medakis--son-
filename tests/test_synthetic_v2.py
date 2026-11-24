"""Üretici 2.0 (G1): deterministiklik, tutarlılık, kapasite, takvim, kalibrasyon, Aşama 3 ve 1.0.0 uyumu.

Veritabanı kullanılmaz; bütün üretim bellektedir.
"""

import copy
import json
import math
import os
import subprocess
import sys
import warnings
from collections import Counter
from dataclasses import fields, replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from medakis.domain.capacity import Unavailability, staff_capacity, unit_capacity, window_capacity
from medakis.domain.models import CareTask, Encounter, Staff, StaffShift
from medakis.domain.time import Interval
from medakis.domain.workload import WINDOW, distribute, pending_at, realized_before_created, window_grid
from medakis.repositories.schema import SEED_RESET_TABLES, metadata
from medakis.synthetic.generator import generate as generate_v1
from medakis.synthetic.params import GENERATOR_VERSION as V1_VERSION
from medakis.synthetic.v2 import GENERATOR_VERSION, ParamsV2, generate_v2
from medakis.synthetic.v2 import calendar as cal
from medakis.synthetic.v2 import profiles as P
from medakis.synthetic.v2.checks import capacity_violations, conservation, summarize, validate_v2

ROOT = Path(__file__).resolve().parents[1]
V1_GOLDEN = "83a619cb04371b892ed32803be9d0d16690bd72cecc6b8f560e9d59897191f42"
# G2: revizyon 0004 kolonları ve yataklı doluluk kalibrasyonuyla sabitlendi. Kurallı bir üretici
# değişikliğinde bilerek güncellenir (1.0.0 altın özeti hiçbir zaman değişmez).
V2_GOLDEN = "2a1fdfb0dabcbd9999b3b5f61e3c3f8807f6bb62353c12aea76dfacf2cac733e"


@pytest.fixture(scope="module")
def full():
    """Varsayılan 16 haftalık veri kümesi (bir kez üretilir)."""
    return generate_v2()


@pytest.fixture(scope="module")
def short():
    return generate_v2(ParamsV2(days=14))


@pytest.fixture()
def tiny():
    return generate_v2(ParamsV2(days=5))


def _unit(ds, kind, profile=None):
    return next(uid for uid, (k, p, _) in ds.unit_profiles.items() if k == kind and (profile is None or p == profile))


# ---- 1.0.0 korunur ------------------------------------------------------------------------


def test_v1_golden_hash_unchanged_after_v2_import():
    assert V1_VERSION == "1.0.0" and GENERATOR_VERSION == "2.0.0"
    assert generate_v1().content_sha256() == V1_GOLDEN


def test_v1_and_v2_are_separate_modules():
    v1 = (ROOT / "src/medakis/synthetic/generator.py").read_text(encoding="utf-8")
    assert "synthetic.v2" not in v1 and "synthetic.v2" not in (ROOT / "src/medakis/synthetic/params.py").read_text(encoding="utf-8")


# ---- Deterministiklik ---------------------------------------------------------------------


def test_v2_golden_hash_for_default_params(full):
    assert full.content_sha256() == V2_GOLDEN


def test_same_seed_same_content_extras_and_demand(short):
    again = generate_v2(ParamsV2(days=14))
    assert again.content_sha256() == short.content_sha256()
    assert again.extras_sha256() == short.extras_sha256()
    assert again.demand_sha256() == short.demand_sha256()


def test_same_content_across_processes_with_different_hash_seeds():
    code = ("import sys; sys.path.insert(0, 'src'); from medakis.synthetic.v2 import generate_v2, ParamsV2; "
            "d = generate_v2(ParamsV2(days=7)); print(d.content_sha256(), d.extras_sha256())")  # fmt: skip
    outputs = set()
    for hash_seed in ("0", "999"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        assert out.returncode == 0, out.stderr
        outputs.add(out.stdout.strip())
    assert len(outputs) == 1


def test_different_seed_different_content(short):
    assert generate_v2(ParamsV2(days=14, seed=43)).content_sha256() != short.content_sha256()


def test_fingerprint_tracks_params_and_version():
    base = ParamsV2()
    assert base.fingerprint() == ParamsV2().fingerprint()
    assert base.fingerprint() != replace(base, days=111).fingerprint()
    assert base.fingerprint() != replace(base, ed_staffing_profile="yuksek").fingerprint()


# ---- Şema uyumu ve kurallar ---------------------------------------------------------------


def test_rows_have_exact_schema_columns(full):
    for table in SEED_RESET_TABLES:
        expected = {c.name for c in metadata.tables[table].columns}
        for row in full.tables[table]:
            assert set(row) == expected, table


def test_full_dataset_passes_all_rules(full):
    assert validate_v2(full) == []


def test_only_three_roles_no_physician(full):
    assert {s["role"] for s in full.tables["staff"]} == set(P.ROLES)
    assert {t["required_role"] for t in full.tables["task_types"]} <= set(P.ROLES)
    with pytest.raises(ValueError, match="yalnız nurse, supervisor, support"):
        ParamsV2(staff_pool={"emergency:physician": 3})


def test_three_scenarios_and_two_inpatient_profiles(full):
    kinds = Counter(k for k, _, _ in full.unit_profiles.values())
    assert kinds == {"inpatient": 2, "emergency": 1, "outpatient": 1}
    assert {p for k, p, _ in full.unit_profiles.values() if k == "inpatient"} == {"normal", "yuksek"}
    per_unit = Counter(e["unit_id"] for e in full.tables["encounters"])
    assert all(per_unit[uid] > 0 for uid in full.unit_profiles)


# ---- Zaman tutarlılığı (Z1–Z5) ----------------------------------------------------------


def test_no_task_starts_before_created_and_none_completed_at_creation(full):
    tasks = [_care(r) for r in full.tables["care_tasks"]]
    assert sum(realized_before_created(t) for t in tasks) == 0
    assert all(t.completed_at > t.created_at for t in tasks if t.status == "completed")


def _first_completed(ds):
    return next(r for r in ds.tables["care_tasks"] if r["status"] == "completed")


def test_validator_rejects_completion_at_creation(tiny):
    row = _first_completed(tiny)
    row["completed_at"] = row["created_at"]
    messages = validate_v2(tiny)
    assert any(m.startswith(f"Z4 care_tasks {row['id']}") for m in messages)
    assert any(m.startswith(f"Z3 care_tasks {row['id']}") for m in messages)


def test_validator_rejects_creation_after_due(tiny):
    row = tiny.tables["care_tasks"][0]
    row["created_at"] = row["due_at"] + timedelta(minutes=1)
    assert any(m.startswith(f"Z2 care_tasks {row['id']}") for m in validate_v2(tiny))


def test_validator_rejects_completion_after_discharge(tiny):
    enc = {e["id"]: e for e in tiny.tables["encounters"]}
    row = next(r for r in tiny.tables["care_tasks"] if r["status"] == "completed" and enc[r["encounter_id"]]["discharged_at"])
    enc[row["encounter_id"]]["discharged_at"] = row["completed_at"] - timedelta(minutes=1)
    assert any(m.startswith(f"Z5 care_tasks {row['id']}") for m in validate_v2(tiny))


def test_validator_rejects_wrong_0004_mapping(tiny):
    ed = next(e for e in tiny.tables["encounters"] if e["triage_category"])
    ed["triage_category"] = "green" if ed["triage_category"] != "green" else "red"
    other = next(e for e in tiny.tables["encounters"] if e["id"] not in tiny.ed_visits)
    other["triage_category"] = "red"
    row = next(r for r in tiny.tables["care_tasks"] if r["status"] == "cancelled")
    row["cancel_reason"] = None
    messages = validate_v2(tiny)
    assert any(f"encounters {ed['id']}: triaj kolonu" in m for m in messages)
    assert any(f"encounters {other['id']}: acil dışı" in m for m in messages)
    assert any(m == f"ck_care_tasks_cancel_pair {row['id']}" for m in messages)


def test_0004_columns_mirror_in_memory_records(full):
    for e in full.tables["encounters"]:
        visit = full.ed_visits.get(e["id"])
        assert e["triage_category"] == (visit.triage if visit else None)
    for r in full.tables["care_tasks"]:
        m = full.task_meta[r["id"]]
        expected = (m.cancelled_at, m.cancel_reason) if r["status"] == "cancelled" else (None, None)
        assert (r["cancelled_at"], r["cancel_reason"]) == expected


def test_validator_rejects_cancel_without_time(tiny):
    meta = tiny.task_meta
    tid = next(t for t, m in meta.items() if m.outcome == "cancel_capacity" or m.outcome == "cancel_clinical")
    meta[tid] = replace(meta[tid], cancelled_at=None)
    assert any(f"care_tasks {tid}: iptal zamanı/nedeni eksik" in m for m in validate_v2(tiny))


# ---- Korunum ve kapasite (C1–C6) ---------------------------------------------------------


def test_c1_c2_counts_and_estimated_minutes_are_conserved(full):
    for uid, data in conservation(full).items():
        count, est = data["count"], data["estimated"]
        assert count["talep"] == sum(v for k, v in count.items() if k != "talep"), uid
        assert math.isclose(est["talep"], sum(v for k, v in est.items() if k != "talep"), rel_tol=1e-9), uid


def test_c3_realized_work_within_staff_capacity(full):
    assert capacity_violations(full) == []


def test_c3_detects_overlapping_assignment(tiny):
    done = [r for r in tiny.tables["care_tasks"] if r["status"] == "completed"]
    a = done[0]
    b = next(r for r in done[1:] if tiny.task_meta[r["id"]].staff_id != tiny.task_meta[a["id"]].staff_id
             and r["unit_id"] == a["unit_id"])  # fmt: skip
    tiny.task_meta[b["id"]] = replace(tiny.task_meta[b["id"]], staff_id=tiny.task_meta[a["id"]].staff_id)
    b["completed_at"], b["actual_minutes"] = a["completed_at"], a["actual_minutes"]
    assert any("örtüşüyor" in m for m in capacity_violations(tiny))


def test_c4_completed_never_exceeds_created_at_any_time(short):
    rows = short.tables["care_tasks"]
    moments = sorted({r["completed_at"] for r in rows if r["completed_at"]})[::500]
    for at in moments:
        created = sum(1 for r in rows if r["created_at"] <= at)
        completed = sum(1 for r in rows if r["completed_at"] is not None and r["completed_at"] <= at)
        assert completed <= created


def test_c6_demand_and_realized_minutes_reported_separately(full):
    for info in summarize(full).values():
        assert {"talep_tahmini_dk", "gerceklesen_gercek_dk", "tamamlanan_tahmini_dk"} <= set(info)


def test_s1_actual_to_estimated_ratio_matches_model(full):
    """İstatistiksel kontrol: >%5 sapma uyarı, >%15 sapma hata (talep ≥ gerçekleşen testi kullanılmaz)."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(full.params.timezone)
    expected = actual = 0.0
    for r in full.tables["care_tasks"]:
        if r["status"] != "completed":
            continue
        night = P.NIGHT_HOURS[0] <= r["due_at"].astimezone(tz).hour < P.NIGHT_HOURS[1]
        expected += r["estimated_minutes"] * math.exp(P.ACTUAL_SIGMA**2 / 2) * (P.NIGHT_FACTOR if night else 1.0)
        actual += r["actual_minutes"]
    deviation = abs(actual / expected - 1)
    if deviation > 0.05:
        warnings.warn(f"S1: gerçek/beklenen süre sapması {deviation:.3f}", stacklevel=1)
    assert deviation < 0.15


def test_break_minutes_summary_never_subtracted_twice(short):
    shift = next(s for s in short.tables["staff_shifts"] if s["break_minutes"] > 0)
    person = next(Staff(p["id"], p["synthetic_code"], p["role"], p["active"]) for p in short.tables["staff"] if p["id"] == shift["staff_id"])
    unav = [Unavailability(u["staff_id"], Interval(u["starts_at"], u["ends_at"]), u["reason"])
            for u in short.tables["staff_unavailability"] if u["staff_id"] == person.id]  # fmt: skip
    plan = Interval(shift["starts_at"], shift["ends_at"])
    cap = staff_capacity(person, [StaffShift(shift["id"], person.id, shift["unit_id"], shift["starts_at"], shift["ends_at"],
                                             shift["break_minutes"])], unav, plan)  # fmt: skip
    assert math.isclose(cap.net_minutes, cap.work_minutes - cap.unavailable_minutes)
    assert math.isclose(cap.break_minutes, shift["break_minutes"]) and not cap.warnings


# ---- Talep kadrodan bağımsız; üretici optimizasyon yapmaz ---------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_staffing_profile_changes_execution_not_demand(seed):
    base = generate_v2(ParamsV2(days=14, seed=seed))
    more = generate_v2(ParamsV2(days=14, seed=seed, ed_staffing_profile="yuksek"))
    assert base.demand_sha256() == more.demand_sha256()
    assert base.content_sha256() != more.content_sha256()
    ed = _unit(base, "emergency")

    def unmet(ds):
        c = conservation(ds)[ed]["count"]
        return c.get("cancel_capacity", 0) + c.get("late", 0)

    assert unmet(more) < unmet(base)  # S2: toplam düzeyde, aynı talep akışıyla


def _unit_signature(ds, uid):
    """Bir servisin talep ve yürütme çıktısı, küresel kimliklerden bağımsız biçimde."""
    enc = {e["id"]: e for e in ds.tables["encounters"]}
    return sorted(
        (enc[r["encounter_id"]]["admitted_at"], r["task_type_id"], r["created_at"], r["due_at"], r["estimated_minutes"],
         r["status"], r["completed_at"], r["actual_minutes"])
        for r in ds.tables["care_tasks"] if r["unit_id"] == uid
    )  # fmt: skip


def test_scenarios_are_isolated_from_each_other():
    base = generate_v2(ParamsV2(days=14))
    changed = generate_v2(ParamsV2(days=14, ed_volume_profile="yuksek", ed_staffing_profile="yuksek"))
    for uid, (kind, _, _) in base.unit_profiles.items():
        if kind != "emergency":
            assert _unit_signature(base, uid) == _unit_signature(changed, uid), kind


def test_generator_params_define_no_objective():
    names = {f.name for f in fields(ParamsV2)}
    assert not names & {"objective", "cost", "target_wait", "max_backlog", "optimize"}


# ---- Takvim (2429 sayılı Kanun) -----------------------------------------------------------


def test_calendar_for_design_period():
    days = cal.holidays_between(date(2026, 10, 1), date(2027, 1, 31))
    assert {d: h.kind for d, h in days.items()} == {
        date(2026, 10, 28): "yarim_13", date(2026, 10, 29): "tam",
        date(2027, 1, 1): "tam", date(2027, 1, 2): "tam",  # 1 Ocak 2027 Cuma → Cumartesi (m.2/D)
    }  # fmt: skip
    assert date(2026, 12, 31) not in days


def test_fixed_holidays_of_a_full_year():
    # 2027'de dini bayram kayıtları koruma penceresidir; onları dışarıda bırakan aralıklar sınanır.
    summer = cal.holidays_between(date(2027, 6, 1), date(2027, 9, 30))
    assert {d for d, h in summer.items() if h.kind == "tam"} >= {date(2027, 7, 15), date(2027, 8, 30)}
    spring = cal.holidays_between(date(2027, 4, 1), date(2027, 5, 5))
    assert date(2027, 4, 23) in spring and date(2027, 5, 1) in spring


def test_unverified_religious_holiday_window_blocks_generation():
    with pytest.raises(cal.CalendarError, match="doğrulanmamış"):
        cal.holidays_between(date(2026, 3, 1), date(2026, 3, 31))
    with pytest.raises(cal.CalendarError, match="kaydı olmayan yıl"):
        cal.holidays_between(date(2028, 10, 1), date(2028, 10, 31))


def test_verified_religious_holiday_is_used(tmp_path, monkeypatch):
    data = {"aciklama": "test", "koruma_tamponu_gun": 5, "kayitlar": [
        {"ad": "Ramazan Bayrami", "arefe": "2026-03-19", "gun": 3, "dogrulama": "D1", "kaynak": "test"}]}  # fmt: skip
    path = tmp_path / "bayram.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(cal, "DATA_FILE", path)
    days = cal.holidays_between(date(2026, 3, 15), date(2026, 3, 25))
    assert days[date(2026, 3, 19)].kind == "yarim_13"
    assert [d.day for d in days.values() if d.kind == "tam"] == [date(2026, 3, 20), date(2026, 3, 21), date(2026, 3, 22)]


def test_outpatient_follows_calendar(full):
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(full.params.timezone)
    op = _unit(full, "outpatient")
    local = [e["admitted_at"].astimezone(tz) for e in full.tables["encounters"] if e["unit_id"] == op]
    by_day = Counter(x.date() for x in local)
    assert by_day[date(2026, 10, 29)] == 0 and by_day[date(2027, 1, 1)] == 0
    assert all(d.weekday() < 5 for d in by_day)
    assert by_day[date(2026, 12, 31)] > 0  # 31 Aralık iş günü
    oct28 = [x for x in local if x.date() == date(2026, 10, 28)]
    assert oct28 and max(x.hour for x in oct28) < 13


# ---- Kalibrasyon (kaynaklı parametreler) ve senaryo davranışı -----------------------------


def test_inpatient_occupancy_and_los_calibration(full):
    s = summarize(full)
    for profile, target in full.params.inpatient_profiles:
        info = s[_unit(full, "inpatient", profile)]
        # G2 kalibrasyonu: ölçülen doluluk hedefe oturtulur (G1'de normal profilde +3,6 puan sapma vardı).
        assert abs(info["olculen_doluluk"] - target) <= 0.01, (profile, info["olculen_doluluk"])
        assert abs(info["ortalama_kalis_gun"] - full.params.inpatient_alos_days) <= 0.3, (profile, info["ortalama_kalis_gun"])
    high = s[_unit(full, "inpatient", "yuksek")]
    assert high["reddedilen_yatis"] > s[_unit(full, "inpatient", "normal")]["reddedilen_yatis"]


def test_emergency_triage_los_and_rule_limits(full):
    info = summarize(full)[_unit(full, "emergency")]
    total = sum(info["triaj"].values())
    for level, share in P.TRIAGE_PROFILES[full.params.ed_triage_profile].items():
        assert abs(info["triaj"][level] / total - share) < 0.04, level
    assert info["kalis_max_dk"] <= P.ED_MAX_STAY_MIN
    med = info["kalis_medyan_dk"]
    assert med["green"] < med["yellow"] < med["red"]


def test_emergency_volume_profile_doubles_arrivals():
    base = summarize(generate_v2(ParamsV2(days=14)))
    high_ds = generate_v2(ParamsV2(days=14, ed_volume_profile="yuksek"))
    high = summarize(high_ds)
    ed = _unit(high_ds, "emergency")
    assert 1.7 < high[ed]["basvuru"] / base[ed]["basvuru"] < 2.3


def test_outpatient_appointments_no_show_and_walk_in(full):
    info = summarize(full)[_unit(full, "outpatient")]
    assert abs(info["gelmeyen"] / info["randevu"] - full.params.outpatient_no_show_rate) < 0.03
    attended_booked = info["randevu"] - info["gelmeyen"]
    assert abs(info["randevusuz"] / attended_booked - full.params.outpatient_walk_in_rate) < 0.03
    no_show_ids = {a.encounter_id for a in full.appointments if a.status == "no_show"}
    assert no_show_ids == {None}


def test_scenario_task_types(full):
    code = {t["id"]: t["code"] for t in full.tables["task_types"]}
    by_kind: dict[str, set[str]] = {}
    for r in full.tables["care_tasks"]:
        by_kind.setdefault(full.unit_profiles[r["unit_id"]][0], set()).add(code[r["task_type_id"]])
    assert {"ADMIT", "DISCH", "SAMPLE", "TRANSF", "HANDOVER", "HANDOVER_SUP", "HYGIENE"} <= by_kind["inpatient"]
    assert {"TRIAGE", "VITAL", "MED", "SAMPLE", "ROUND"} <= by_kind["emergency"]
    assert {"ADMIT_P", "VITAL", "MED", "SAMPLE", "WOUND", "EDU"} <= by_kind["outpatient"]
    assert "TRIAGE" not in by_kind["outpatient"] | by_kind["inpatient"]


def test_alternative_shift_pattern_8_16():
    ds = generate_v2(ParamsV2(days=7, inpatient_shift_pattern="8_16"))
    names = {t["name"] for t in ds.tables["shift_templates"]}
    assert {"GUNDUZ8", "GECE16", "GUNDUZ", "GECE"} <= names
    assert validate_v2(ds) == []


# ---- Aşama 3 uyumu ------------------------------------------------------------------------


def _care(r):
    return CareTask(r["id"], r["encounter_id"], r["unit_id"], r["task_type_id"], r["created_at"], r["due_at"],
                    r["estimated_minutes"], r["actual_minutes"], r["status"], r["completed_at"])  # fmt: skip


def test_stage3_window_observation_never_exceeds_unit_capacity(short):
    t = short.tables
    staff = [Staff(s["id"], s["synthetic_code"], s["role"], s["active"]) for s in t["staff"]]
    unav = [Unavailability(u["staff_id"], Interval(u["starts_at"], u["ends_at"]), u["reason"]) for u in t["staff_unavailability"]]
    span = Interval(short.scenario_start + timedelta(days=3), short.scenario_start + timedelta(days=6))
    grid = window_grid(span)
    for uid in short.unit_profiles:
        shifts = [StaffShift(s["id"], s["staff_id"], s["unit_id"], s["starts_at"], s["ends_at"], s["break_minutes"])
                  for s in t["staff_shifts"] if s["unit_id"] == uid]  # fmt: skip
        summary = unit_capacity(staff, shifts, unav, span)
        observed = distribute([_care(r) for r in t["care_tasks"] if r["unit_id"] == uid], grid)
        capacity = window_capacity(summary, grid)
        assert all(o.observed_minutes <= c + 1e-6 for o, c in zip(observed, capacity)), uid
        assert all(w.window.end - w.window.start == WINDOW for w in observed)


def test_stage3_pending_and_patient_count_work_on_v2(short):
    from medakis.domain.workload import patient_count_at

    at = short.scenario_now - timedelta(hours=3)
    tasks = [_care(r) for r in short.tables["care_tasks"]]
    pending = pending_at(tasks, at)
    assert pending.minutes > 0
    encs = [Encounter(e["id"], e["patient_id"], e["unit_id"], e["bed_id"], e["admitted_at"], e["discharged_at"])
            for e in short.tables["encounters"]]  # fmt: skip
    assert patient_count_at(encs, at) > 0


# ---- Parametre bildirimi ------------------------------------------------------------------


def test_every_parameter_and_constant_has_provenance():
    assert {f.name for f in fields(ParamsV2)} == set(P.PARAM_PROVENANCE)
    skip = {"GENERATOR_VERSION", "ROLES", "SB_2025", "ACIL_YON", "KIND_TITLES", "PARAM_PROVENANCE", "CONSTANT_PROVENANCE"}
    constants = {n for n in vars(P) if n.isupper() and n not in skip}
    assert constants <= set(P.CONSTANT_PROVENANCE), constants - set(P.CONSTANT_PROVENANCE)
    kinds = {p.kind for p in [*P.PARAM_PROVENANCE.values(), *P.CONSTANT_PROVENANCE.values()]}
    assert kinds <= {"kaynak", "kural", "varsayim", "tasarim"}
    for p in [*P.PARAM_PROVENANCE.values(), *P.CONSTANT_PROVENANCE.values()]:
        if p.kind in ("kaynak", "kural"):
            assert p.source and p.year
        if p.kind == "kaynak":
            assert p.purpose == "kalibrasyon"


def test_validation_sources_are_not_used_for_calibration():
    text = json.dumps([p.__dict__ for p in [*P.PARAM_PROVENANCE.values(), *P.CONSTANT_PROVENANCE.values()] if p.kind == "kaynak"],
                      ensure_ascii=False)  # fmt: skip
    assert "2024" not in text and "Bozyaka" not in text


def test_parameter_declaration_file_is_current():
    path = ROOT / "docs" / "veri" / "URETICI_2_0_PARAMETRE_BILDIRIMI.md"
    assert path.read_text(encoding="utf-8") == P.declaration_markdown()


def test_dataset_does_not_mutate_between_copies(short):
    clone = copy.deepcopy(short)
    assert clone.content_sha256() == short.content_sha256()
