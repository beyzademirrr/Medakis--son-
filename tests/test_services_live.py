"""Repository ve servis doğrulamaları gerçek PostgreSQL'de (medakis_app rolüyle): pytest -m postgres

Her test, geri alınan bir dış işlem içinde çalışır; servislerin işlemleri bunun içinde kayıt
noktasıdır. Eşzamanlılık testi kendi kayıtlarını owner rolüyle temizler.
"""

import math
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select, text

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.repositories import care_repo, facility_repo, staff_repo
from medakis.repositories.database import migration_engine, runtime_engine
from medakis.domain.rules import breaks_match_summary
from medakis.domain.time import Interval
from medakis.repositories.schema import assignments, optimization_runs, patients, staff_shifts, staff_unavailability
from medakis.services.care_service import CareService
from medakis.services.staff_service import StaffService

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 4, 17, 0, tzinfo=UTC)  # yerel 20:00


def alembic(*args):
    proc = subprocess.run([sys.executable, "-m", "alembic", "-x", "db=medakis_test", *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.fixture(scope="module", autouse=True)
def migrated():
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    yield
    alembic("downgrade", "base")


@pytest.fixture(scope="module")
def app_engine():
    engine = runtime_engine("medakis_app", "medakis_test")
    yield engine
    engine.dispose()


@pytest.fixture
def world(app_engine):
    """Örnek yapı (geri alınır) ve iç işlem fabrikası."""
    with app_engine.connect() as conn:
        outer = conn.begin()
        f = facility_repo.insert_facility(conn, "Test Tesisi")
        u1 = facility_repo.insert_unit(conn, f, "Yatakli", "inpatient")
        u2 = facility_repo.insert_unit(conn, f, "Acil", "emergency")
        u3 = facility_repo.insert_unit(conn, f, "Kapali", "outpatient", active=False)
        r1, r2 = facility_repo.insert_room(conn, u1, "Y1"), facility_repo.insert_room(conn, u2, "A1")
        ids = {
            "unit1": u1, "unit2": u2, "unit_closed": u3,
            "bed1": facility_repo.insert_bed(conn, r1, "Y1-A"),
            "bed_other_unit": facility_repo.insert_bed(conn, r2, "A1-A"),
            "bed_inactive": facility_repo.insert_bed(conn, r1, "Y1-B", active=False),
            "task_type": care_repo.insert_task_type(conn, "T1", "Test gorevi", 10, "nurse"),
            "nurse": staff_repo.insert_staff(conn, "STAFF9001", "nurse"),
            "nurse_inactive": staff_repo.insert_staff(conn, "STAFF9002", "nurse", active=False),
            "night": staff_repo.insert_template(conn, "GECE-T", time(20, 0), 720),
            "skill": staff_repo.insert_skill(conn, "IV-T", "Test yetkinlik"),
        }  # fmt: skip

        @contextmanager
        def begin():
            with conn.begin_nested():
                yield conn

        ids["conn"], ids["care"], ids["staff"] = conn, CareService(begin=begin), StaffService(begin=begin, timezone="Europe/Istanbul")
        yield ids
        outer.rollback()


def code_of(call):
    with pytest.raises(DomainError) as info:
        call()
    return info.value.code


# ---- Yatış ----------------------------------------------------------------------------------


def test_admit_and_read_back(world):
    enc = world["care"].admit_patient(world["unit1"], "SYN900001", T0, world["bed1"])
    got = care_repo.get_encounter(world["conn"], enc)
    assert (got.unit_id, got.bed_id, got.admitted_at) == (world["unit1"], world["bed1"], T0)


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"bed_id": "bed_other_unit"}, errors.BED_UNIT_MISMATCH),
        ({"bed_id": "bed_inactive"}, errors.BED_INACTIVE),
        ({"unit": "unit_closed"}, errors.UNIT_INACTIVE),
        ({"code": "Ahmet Yilmaz"}, errors.INVALID_CODE),
        ({"naive": True}, errors.NAIVE_DATETIME),
    ],
)
def test_admit_rejections(world, kwargs, code):
    unit = world[kwargs.get("unit", "unit1")]
    bed = world[kwargs["bed_id"]] if "bed_id" in kwargs else None
    when = T0.replace(tzinfo=None) if kwargs.get("naive") else T0
    assert code_of(lambda: world["care"].admit_patient(unit, kwargs.get("code", "SYN900002"), when, bed)) == code


def test_occupied_bed_rejected_and_nothing_written(world):
    world["care"].admit_patient(world["unit1"], "SYN900003", T0, world["bed1"])  # bitişi yok: sınırsız
    before = world["conn"].execute(select(patients.c.id)).all()
    assert code_of(lambda: world["care"].admit_patient(world["unit1"], "SYN900004", T0 + timedelta(days=3), world["bed1"])) == errors.BED_OCCUPIED
    assert world["conn"].execute(select(patients.c.id)).all() == before  # işlem geri alındı


# ---- Görevler ve bakım katsayısı -------------------------------------------------------------


def _encounter_with_acuity(world):
    enc = world["care"].admit_patient(world["unit1"], "SYN900010", T0, None)
    world["care"].record_acuity(enc, T0, 2, 1.0)
    world["care"].record_acuity(enc, T0 + timedelta(hours=4), 4, 1.5)
    return enc


def test_estimate_uses_latest_known_multiplier_only(world):
    enc = _encounter_with_acuity(world)
    early = world["care"].create_task(enc, world["task_type"], T0 + timedelta(hours=2), T0 + timedelta(hours=3))
    late = world["care"].create_task(enc, world["task_type"], T0 + timedelta(hours=5), T0 + timedelta(hours=6))
    assert early.estimated_minutes == 10.0  # 4. saatteki değerlendirme henüz bilinmiyor
    assert late.estimated_minutes == 15.0  # 10 × 1,5, bir kez
    assert early.unit_id == world["unit1"] and early.status == "pending"


def test_task_rules(world):
    care, tt = world["care"], world["task_type"]
    enc = care.admit_patient(world["unit1"], "SYN900011", T0, None)
    assert code_of(lambda: care.create_task(enc, tt, T0, T0 + timedelta(hours=1))) == errors.NO_ACUITY
    care.record_acuity(enc, T0, 2, 1.0)
    assert code_of(lambda: care.create_task(enc, tt, T0 - timedelta(hours=1), T0)) == errors.ENCOUNTER_CLOSED
    assert code_of(lambda: care.create_task(enc, tt, T0 + timedelta(hours=2), T0 + timedelta(hours=1))) == errors.TASK_ORDER
    task = care.create_task(enc, tt, T0, T0 + timedelta(hours=1))
    assert code_of(lambda: care.complete_task(world["unit1"], task.id, T0 + timedelta(minutes=30), math.nan)) == errors.NOT_FINITE
    assert code_of(lambda: care.complete_task(world["unit1"], task.id, T0 - timedelta(minutes=1), 10)) == errors.TASK_ORDER
    care.complete_task(world["unit1"], task.id, T0 + timedelta(minutes=30), 11.5)
    assert code_of(lambda: care.complete_task(world["unit1"], task.id, T0 + timedelta(minutes=40), 10)) == errors.TASK_TRANSITION
    assert code_of(lambda: care.cancel_task(world["unit1"], task.id)) == errors.TASK_TRANSITION
    done = care_repo.get_task(world["conn"], world["unit1"], task.id)
    assert (done.status, done.actual_minutes) == ("completed", 11.5)


def test_service_isolation(world):
    care = world["care"]
    enc = _encounter_with_acuity(world)
    task = care.create_task(enc, world["task_type"], T0 + timedelta(hours=1), T0 + timedelta(hours=2))
    # Başka servisten okunamaz ve değiştirilemez.
    assert care_repo.get_task(world["conn"], world["unit2"], task.id) is None
    assert code_of(lambda: care.cancel_task(world["unit2"], task.id)) == errors.TASK_NOT_FOUND
    window = (T0, T0 + timedelta(days=1))
    assert [t.id for t in care_repo.list_tasks(world["conn"], world["unit1"], *window)] == [task.id]
    assert care_repo.list_tasks(world["conn"], world["unit2"], *window) == []


def test_cancel_keeps_historical_run_and_assignment(world):
    conn, care = world["conn"], world["care"]
    enc = _encounter_with_acuity(world)
    task = care.create_task(enc, world["task_type"], T0 + timedelta(hours=1), T0 + timedelta(hours=2))
    run_id = conn.execute(
        insert(optimization_runs).values(unit_id=world["unit1"], starts_at=T0, ends_at=T0 + timedelta(hours=12),
            created_at=T0, status="FEASIBLE", config_json={}, solver_seconds=0.1).returning(optimization_runs.c.id)
    ).scalar_one()  # fmt: skip
    conn.execute(insert(assignments).values(run_id=run_id, task_id=task.id, staff_id=world["nurse"],
                                            starts_at=T0 + timedelta(hours=1), ends_at=T0 + timedelta(hours=1, minutes=10)))  # fmt: skip
    snapshot = (conn.execute(select(assignments)).all(), conn.execute(select(optimization_runs)).all())
    care.cancel_task(world["unit1"], task.id)
    assert care_repo.get_task(conn, world["unit1"], task.id).status == "cancelled"
    assert (conn.execute(select(assignments)).all(), conn.execute(select(optimization_runs)).all()) == snapshot


# ---- Vardiya ve yetkinlik -------------------------------------------------------------------


def brk(day_offset=0, after_hours=5, minutes=30):
    start = T0 + timedelta(days=day_offset, hours=after_hours)
    return (start, start + timedelta(minutes=minutes))


def test_shift_from_template_crosses_midnight(world):
    shift_id = world["staff"].add_shift_from_template(world["nurse"], world["unit1"], world["night"], date(2026, 11, 4), [brk()])
    row = world["conn"].execute(select(staff_shifts).where(staff_shifts.c.id == shift_id)).mappings().one()
    assert row["starts_at"] == T0 and row["ends_at"] == T0 + timedelta(hours=12)


def test_shift_rules(world):
    svc = world["staff"]
    svc.add_shift_from_template(world["nurse"], world["unit1"], world["night"], date(2026, 11, 4), [brk()])
    assert code_of(lambda: svc.add_shift_from_template(world["nurse"], world["unit2"], world["night"], date(2026, 11, 4), [brk()])) == errors.SHIFT_OVERLAP
    assert code_of(lambda: svc.add_shift_from_template(world["nurse"], world["unit1"], world["night"], date(2026, 11, 6), [brk(2, 0, 721)])) == errors.BREAK_TOO_LONG
    assert code_of(lambda: svc.add_shift_from_template(world["nurse_inactive"], world["unit1"], world["night"], date(2026, 11, 6), [brk(2)])) == errors.STAFF_INACTIVE
    assert code_of(lambda: svc.add_shift_from_template(world["nurse"], world["unit_closed"], world["night"], date(2026, 11, 6), [brk(2)])) == errors.UNIT_INACTIVE
    svc.add_shift_from_template(world["nurse"], world["unit1"], world["night"], date(2026, 11, 5), [brk(1)])  # bitişik gece: izinli


# ---- Mola: özet somut aralıklardan türetilir, ikisi aynı işlemde yazılır (F6) ------------------


def _shift_and_breaks(world, shift_id):
    conn = world["conn"]
    shift = conn.execute(select(staff_shifts).where(staff_shifts.c.id == shift_id)).mappings().one()
    rows = conn.execute(
        select(staff_unavailability).where(staff_unavailability.c.staff_id == shift["staff_id"], staff_unavailability.c.reason == "Mola")
    ).mappings().all()
    return shift, [Interval(r["starts_at"], r["ends_at"]) for r in rows]


def test_break_summary_equals_stored_break_intervals(world):
    svc = world["staff"]
    shift_id = svc.add_shift_from_template(world["nurse"], world["unit1"], world["night"], date(2026, 11, 4), [brk(0, 3, 15), brk(0, 7, 30)])
    shift, breaks = _shift_and_breaks(world, shift_id)
    assert shift["break_minutes"] == 45 and sum(b.minutes() for b in breaks) == 45
    assert breaks_match_summary(shift["break_minutes"], breaks, Interval(shift["starts_at"], shift["ends_at"]))


def test_shift_without_breaks_has_zero_summary(world):
    shift_id = world["staff"].add_shift_from_template(world["nurse"], world["unit1"], world["night"], date(2026, 11, 4))
    shift, breaks = _shift_and_breaks(world, shift_id)
    assert shift["break_minutes"] == 0 and breaks == []


@pytest.mark.parametrize(
    ("breaks", "code"),
    [
        ([brk(0, -1)], errors.BREAK_OUTSIDE_SHIFT),  # vardiyadan önce
        ([brk(0, 11, 61)], errors.BREAK_OUTSIDE_SHIFT),  # vardiya sonunu aşıyor
        ([brk(0, 5, 30), brk(0, 5.25, 30)], errors.BREAK_OVERLAP),
        ([(T0 + timedelta(hours=5), T0 + timedelta(hours=5, seconds=90))], errors.INVALID_INTERVAL),  # tam dakika değil
        ([(T0 + timedelta(hours=5), T0 + timedelta(hours=5))], errors.INVALID_INTERVAL),
    ],
)  # fmt: skip
def test_invalid_breaks_rejected_and_nothing_written(world, breaks, code):
    conn = world["conn"]
    before = conn.execute(select(staff_shifts.c.id)).all(), conn.execute(select(staff_unavailability.c.id)).all()
    assert code_of(lambda: world["staff"].add_shift_from_template(world["nurse"], world["unit1"], world["night"], date(2026, 11, 4), breaks)) == code
    assert (conn.execute(select(staff_shifts.c.id)).all(), conn.execute(select(staff_unavailability.c.id)).all()) == before


def test_break_rows_are_rolled_back_with_shift(world):
    # Vardiya eklenemezse (çakışma) molalar da yazılmaz: ikisi aynı işlemdedir.
    svc = world["staff"]
    svc.add_shift_from_template(world["nurse"], world["unit1"], world["night"], date(2026, 11, 4), [brk()])
    _, before = _shift_and_breaks(world, world["conn"].execute(select(staff_shifts.c.id)).scalars().first())
    assert code_of(lambda: svc.add_shift_from_template(world["nurse"], world["unit2"], world["night"], date(2026, 11, 4), [brk(0, 8)])) == errors.SHIFT_OVERLAP
    _, after = _shift_and_breaks(world, world["conn"].execute(select(staff_shifts.c.id)).scalars().first())
    assert after == before


def test_break_cannot_be_added_outside_shift_flow_or_left_orphan(world):
    svc = world["staff"]
    start, end = brk()
    assert code_of(lambda: svc.add_unavailability(world["nurse"], start, end, "Mola")) == errors.BREAK_RESERVED
    assert code_of(lambda: svc.add_unavailability(world["nurse"], start, end, " mola ")) == errors.BREAK_RESERVED
    staff_repo.insert_unavailability(world["conn"], world["nurse"], start, end, "Mola")  # doğrudan yazılmış yetim mola
    assert code_of(lambda: svc.add_shift_from_template(world["nurse"], world["unit1"], world["night"], date(2026, 11, 4), [brk(0, 8)])) == errors.BREAK_OVERLAP


def test_exclude_violation_is_translated_when_precheck_is_bypassed(world):
    conn = world["conn"]
    staff_repo.insert_shift(conn, world["nurse"], world["unit1"], None, T0, T0 + timedelta(hours=12), 30)
    from medakis.services._tx import unit_of_work

    @contextmanager
    def begin():
        with conn.begin_nested():
            yield conn

    with pytest.raises(DomainError) as info, unit_of_work(begin) as c:
        staff_repo.insert_shift(c, world["nurse"], world["unit2"], None, T0 + timedelta(hours=6), T0 + timedelta(hours=18), 30)
    assert info.value.code == errors.SHIFT_OVERLAP


def test_skill_validity_through_whole_work(world):
    conn, svc = world["conn"], world["staff"]
    staff_repo.insert_staff_skill(conn, world["nurse"], world["skill"], T0 - timedelta(days=30), T0 + timedelta(minutes=30))
    assert svc.skill_valid_for(world["nurse"], world["skill"], T0, T0 + timedelta(minutes=20))
    assert not svc.skill_valid_for(world["nurse"], world["skill"], T0 + timedelta(minutes=20), T0 + timedelta(minutes=40))


# ---- Eşzamanlılık: iki ayrı işlem aynı personele çakışan vardiya yazar --------------------------


def test_concurrent_overlapping_shifts_one_wins(app_engine):
    owner = migration_engine("medakis_test", allow_prompt=False)
    with owner.begin() as conn:
        f = facility_repo.insert_facility(conn, "Yaris Tesisi")
        unit = facility_repo.insert_unit(conn, f, "Yaris", "inpatient")
        nurse = staff_repo.insert_staff(conn, "STAFF9100", "nurse")
    results: dict[str, object] = {}
    try:
        first = app_engine.connect()
        tx = first.begin()
        staff_repo.insert_shift(first, nurse, unit, None, T0, T0 + timedelta(hours=12), 30)

        def second_writer():
            try:
                with app_engine.begin() as c:
                    staff_repo.insert_shift(c, nurse, unit, None, T0 + timedelta(hours=6), T0 + timedelta(hours=18), 30)
                results["second"] = "committed"
            except Exception as exc:  # noqa: BLE001
                results["second"] = getattr(getattr(exc, "orig", None), "diag", None) and exc.orig.diag.constraint_name

        thread = threading.Thread(target=second_writer)
        thread.start()
        thread.join(timeout=2)
        assert thread.is_alive(), "İkinci yazar, birinci işlem bitene kadar beklemeliydi"
        tx.commit()
        first.close()
        thread.join(timeout=10)
        assert results["second"] == "ex_staff_shifts_staff_overlap"
        with app_engine.connect() as c:
            assert c.execute(select(staff_shifts.c.id).where(staff_shifts.c.staff_id == nurse)).all().__len__() == 1
    finally:
        with owner.begin() as conn:
            conn.execute(text("DELETE FROM staff_shifts WHERE staff_id = :s"), {"s": nurse})
            conn.execute(text("DELETE FROM staff WHERE id = :s"), {"s": nurse})
            conn.execute(text("DELETE FROM units WHERE id = :u"), {"u": unit})
            conn.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": f})
        owner.dispose()
