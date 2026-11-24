"""Üretici 2.1 (Aşama 5 / U2): planlı kadro katmanı, uzlaşma, bilinme zamanı ve altın özetler.

Veritabanı yok; üretim bellektedir. 1.0.0 ve 2.0.0 üretimi ve altın özetleri değişmez.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from medakis.domain.planning import MISSING, OK, known_view, plan_problems, realized_unit_minutes, unit_capacity_at
from medakis.domain.time import Interval
from medakis.synthetic.generator import generate as generate_v1
from medakis.synthetic.v2 import ParamsV2
from medakis.synthetic.v2 import GENERATOR_VERSION as V2_VERSION
from medakis.synthetic.v2.calendar import day_infos
from medakis.synthetic.v21 import GENERATOR_VERSION, ParamsV21, generate_v21

ROOT = Path(__file__).resolve().parents[1]
V1_GOLDEN = "83a619cb04371b892ed32803be9d0d16690bd72cecc6b8f560e9d59897191f42"
V2_GOLDEN = "2a1fdfb0dabcbd9999b3b5f61e3c3f8807f6bb62353c12aea76dfacf2cac733e"
# U2: varsayılan ParamsV21 (taban ParamsV2 varsayılanı) plan katmanı özeti. Kurallı bir 2.1 değişikliğinde
# bilerek güncellenir; 1.0.0 ve 2.0.0 özetleri hiçbir zaman değişmez.
V21_PLAN_GOLDEN = "09c0315df57944b682a89d815b99406d09d92f48b40cc65a7ac55eda2f85bcc9"
TZ = ZoneInfo("Europe/Istanbul")


@pytest.fixture(scope="module")
def full():
    return generate_v21()


@pytest.fixture(scope="module")
def short():
    return generate_v21(ParamsV21(base=ParamsV2(days=14)))


def _local(dt):
    return dt.astimezone(TZ)


def _template_ids(ds):
    return {t["name"]: t["id"] for t in ds.base.tables["shift_templates"]}


# ---- Sürüm ve altın özetler ----------------------------------------------------------------------


def test_versions_and_golden_hashes(full):
    assert GENERATOR_VERSION == "2.1.0" and V2_VERSION == "2.0.0"
    assert full.base_content_sha256() == V2_GOLDEN  # 2.0.0 tabloları aynen
    assert full.plan_sha256() == V21_PLAN_GOLDEN  # 2.1 plan katmanı ayrı özet


def test_v1_and_v2_generation_unchanged():
    assert generate_v1().content_sha256() == V1_GOLDEN
    v2 = (ROOT / "src/medakis/synthetic/v2").rglob("*.py")
    assert not any("v21" in p.read_text(encoding="utf-8") for p in v2)  # 2.0.0 kodu 2.1'e bağımlı değil


def test_v21_is_deterministic_and_seed_sensitive(short):
    again = generate_v21(ParamsV21(base=ParamsV2(days=14)))
    assert again.plan_sha256() == short.plan_sha256() and again.reconciliation == short.reconciliation
    other = generate_v21(ParamsV21(base=ParamsV2(days=14, seed=43)))
    assert other.plan_sha256() != short.plan_sha256()
    assert ParamsV21().fingerprint() != ParamsV21(cancel_share=0.5).fingerprint()


def test_v21_does_not_import_optimizer_or_db():
    src = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "src/medakis/synthetic/v21").rglob("*.py"))
    src += (ROOT / "src/medakis/domain/planning.py").read_text(encoding="utf-8")
    for banned in ("ortools", "sqlalchemy", "psycopg", "create_engine", "langgraph", "ollama"):
        assert banned not in src.lower()


# ---- Planlı ve gerçekleşen ayrımı ----------------------------------------------------------------


def test_reconciliation_planned_equals_realized_plus_no_show(full):
    r = full.reconciliation
    assert r["planli"] == r["gerceklesen"] + r["gerceklesmeyen"]
    assert r["gerceklesen"] == len(full.base.tables["staff_shifts"])
    assert r["gerceklesmeyen"] == full.base.skipped_shifts > 0
    assert r["onceden_iptal"] + r["ani_devamsizlik"] == r["gerceklesmeyen"]
    assert r["onceden_iptal"] > 0 and r["ani_devamsizlik"] > 0


def test_every_realized_shift_has_identical_planned_shift(full):
    planned = {(s.staff_id, s.starts_at): s for s in full.plan.shifts}
    for real in full.base.tables["staff_shifts"]:
        p = planned[(real["staff_id"], real["starts_at"])]
        assert (p.unit_id, p.ends_at, p.template_id, p.status) == (real["unit_id"], real["ends_at"], real["template_id"], "planned")


def test_no_show_keeps_planned_shift(full):
    realized = {(s["staff_id"], s["starts_at"]) for s in full.base.tables["staff_shifts"]}
    absences = {(u.staff_id, u.starts_at, u.ends_at) for u in full.plan.unavailability if u.reason == "devamsizlik"}
    missing = [s for s in full.plan.shifts if (s.staff_id, s.starts_at) not in realized]
    assert len(missing) == full.base.skipped_shifts
    for s in missing:  # plandan silinmez: ya önceden iptal edilir ya da ani devamsızlıkla işaretlenir
        assert s.status == "cancelled" or (s.staff_id, s.starts_at, s.ends_at) in absences
    for s in full.plan.shifts:
        if s.status == "cancelled":
            assert (s.staff_id, s.starts_at) not in realized


def test_final_view_capacity_matches_realized(short):
    """Bütün bilgiler bilindiğinde planlı görünümün kullanılabilir dakikası gerçekleşenle aynıdır."""
    staff = short.staff()
    shifts, unav = short.realized()
    end = short.base.horizon_end  # sonradan değerlendirme: karar anı açıkça dönem sonu
    start = min(s.starts_at for s in short.plan.shifts)
    for day in range(3, 6):
        window = Interval(start + timedelta(days=day), start + timedelta(days=day + 1))
        for unit in sorted(short.unit_ids):
            r = unit_capacity_at(short.plan, end, staff, unit, short.unit_ids, window, TZ, require_contract=False)
            real = realized_unit_minutes(staff, shifts, unav, unit, window)
            if r.status == MISSING:  # ör. hafta sonu poliklinik: plan yok → gerçekleşen de yok
                assert r.reasons == ("planli_vardiya_yok",) and sum(real.values()) == 0
                continue
            assert r.status == OK, r.reasons
            for role, rc in r.roles.items():
                assert rc.available_minutes == pytest.approx(real[role])
                assert rc.planned_minutes >= rc.available_minutes


def test_plan_is_consistent_and_covers_units_and_roles(full):
    assert plan_problems(full.plan, full.staff_roles, full.unit_ids) == []
    units = {s.unit_id for s in full.plan.shifts}
    roles = {s.role for s in full.plan.shifts}
    assert units == {1, 2, 3, 4} and roles == {"nurse", "supervisor", "support"}
    assert set(full.staff_roles.values()) <= {"nurse", "supervisor", "support"}  # hekim yok
    per_unit = Counter((s.unit_id, s.role) for s in full.plan.shifts)
    assert all(per_unit[(u, "nurse")] > 0 for u in (1, 2, 3, 4))


def test_rules_and_config_are_not_invented(full):
    assert full.plan.staffing_rules == () and full.plan.float_rules == () and full.plan.config is None
    s = full.plan.shifts[0]
    r = unit_capacity_at(full.plan, s.starts_at, full.staff(), s.unit_id, full.unit_ids,
                         Interval(s.starts_at, s.starts_at + timedelta(hours=1)), TZ)  # fmt: skip
    assert r.status == MISSING and "planlama_yapilandirmasi_yok" in r.reasons and r.roles == {}


# ---- Bilinme zamanları ---------------------------------------------------------------------------


def test_knowledge_times_follow_declared_assumptions(full):
    p = full.params
    for s in full.plan.shifts:
        assert s.planned_at == s.starts_at - timedelta(days=p.plan_lead_days)
        if s.status == "cancelled":
            lead = s.starts_at - s.cancelled_at
            assert timedelta(days=p.cancel_notice_days[0]) <= lead <= timedelta(days=p.cancel_notice_days[1])
    shift_start = {(s.staff_id, s.starts_at) for s in full.plan.shifts}
    for u in full.plan.unavailability:
        if u.reason == "devamsizlik":
            assert u.kind == "ani" and (u.staff_id, u.starts_at) in shift_start
            assert -timedelta(minutes=120) <= u.recorded_at - u.starts_at <= timedelta(minutes=30)
        elif u.reason == "mola":
            assert u.kind == "planli" and u.recorded_at <= u.starts_at - timedelta(days=p.plan_lead_days) + timedelta(hours=12)
        else:
            assert u.reason == "izin" and u.recorded_at < u.starts_at
    kinds = Counter((u.reason, u.kind) for u in full.plan.unavailability)
    assert kinds[("izin", "planli")] > 0 and kinds[("izin", "ani")] > 0


def test_known_view_has_no_future_information(full):
    t = full.base.scenario_start + timedelta(days=40, hours=7, minutes=10)
    view = known_view(full.plan, t)
    assert all(s.planned_at <= t for s in view.shifts)
    assert all(u.recorded_at <= t for u in view.unavailability)
    assert all(s.status != "cancelled" or s.cancelled_at <= t for s in view.shifts)
    # Gelecekte bildirilecek ani devamsızlık ve iptal görünmez; vardiya planlı olarak görünür.
    hidden = [u for u in full.plan.unavailability if u.reason == "devamsizlik" and u.recorded_at > t and u.starts_at < t + timedelta(days=3)]
    assert hidden and not any(u in view.unavailability for u in hidden)
    later_cancel = [s for s in full.plan.shifts if s.status == "cancelled" and s.planned_at <= t < s.cancelled_at]
    by_id = {s.id: s for s in view.shifts}
    assert later_cancel and all(by_id[s.id].status == "planned" for s in later_cancel)


def test_sudden_absence_lowers_available_only_after_report(full):
    staff = full.staff()
    u = next(x for x in full.plan.unavailability if x.reason == "devamsizlik" and x.recorded_at > x.starts_at)
    s = next(x for x in full.plan.shifts if x.staff_id == u.staff_id and x.starts_at == u.starts_at)
    window = Interval(u.recorded_at + timedelta(minutes=15), u.recorded_at + timedelta(minutes=75))
    before = unit_capacity_at(full.plan, u.recorded_at - timedelta(seconds=1), staff, s.unit_id, full.unit_ids,
                              window, TZ, require_contract=False).roles[s.role]  # fmt: skip
    after = unit_capacity_at(full.plan, u.recorded_at, staff, s.unit_id, full.unit_ids, window, TZ,
                             require_contract=False).roles[s.role]  # fmt: skip
    assert before.planned_minutes == after.planned_minutes  # plan değişmez
    assert after.available_minutes == before.available_minutes - 60
    assert after.available_headcount_min_atom == before.available_headcount_min_atom - 1


# ---- Takvim: tatil, yarım gün, gece ---------------------------------------------------------------


def test_outpatient_follows_holidays_and_half_days(full):
    ids = _template_ids(full)
    base = full.params.base
    days = {c.day: c for c in day_infos(base.start_day, base.days + 1)}
    outpatient = [s for s in full.plan.shifts if s.unit_id == 4]
    seen_half = seen_full = False
    for s in outpatient:
        info = days[_local(s.starts_at).date()]
        assert info.is_workday  # hafta sonu ve tam gün tatilde poliklinik yok
        if info.is_half_holiday:
            seen_half = True
            assert s.template_id == ids["POLIKLINIK_YARIM"] and s.ends_at - s.starts_at == timedelta(minutes=300)
        else:
            assert s.template_id == ids["POLIKLINIK"]
    full_holidays = [d for d, c in days.items() if c.is_full_holiday and not c.is_weekend
                     and full.base.scenario_start <= datetime.combine(d, datetime.min.time(), TZ).astimezone(UTC) < full.base.horizon_end]  # fmt: skip
    seen_full = bool(full_holidays) and not any(_local(s.starts_at).date() in full_holidays for s in outpatient)
    assert seen_half and seen_full


def test_night_shifts_cross_midnight(full):
    ids = _template_ids(full)
    nights = [s for s in full.plan.shifts if s.template_id == ids["GECE"]]
    assert nights
    for s in nights[:200]:
        a, b = _local(s.starts_at), _local(s.ends_at)
        assert a.hour == 20 and b.hour == 8 and b.date() == a.date() + timedelta(days=1)
