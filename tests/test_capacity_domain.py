"""Aşama 3: personel kapasitesi ve çifte sayımın önlenmesi (saf hesap, veritabanı yok)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from medakis.domain.capacity import (
    Unavailability,
    demand_gap,
    merge,
    staff_capacity,
    subtract,
    unit_capacity,
    window_capacity,
)
from medakis.domain.models import Staff, StaffShift
from medakis.domain.time import Interval, shift_from_template
from medakis.domain.workload import window_grid

IST = ZoneInfo("Europe/Istanbul")
T0 = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)  # yerel 20:00


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def iv(a, b):
    return Interval(at(a), at(b))


NURSE_A = Staff(1, "STAFF001", "nurse", True)
NURSE_B = Staff(2, "STAFF002", "nurse", True)
SUPPORT = Staff(3, "STAFF003", "support", True)


def shift(sid, staff_id, a, b, break_minutes=0, unit=1):
    return StaffShift(sid, staff_id, unit, at(a), at(b), break_minutes)


def un(staff_id, a, b, reason):
    return Unavailability(staff_id, iv(a, b), reason)


def test_specification_example_two_people_thirty_minutes_ten_minutes_break():
    # Şartname §4: 2 kişi × 30 dk − toplam 10 dk mola = 50 dk; talep 65 dk → açık 15 dk.
    plan = iv(0, 30)
    shifts = [shift(1, 1, -60, 600, 10), shift(2, 2, -60, 600, 0)]
    breaks = [un(1, 10, 20, "Mola")]
    summary = unit_capacity([NURSE_A, NURSE_B], shifts, breaks, plan)
    assert summary.total_minutes == pytest.approx(50.0)
    assert demand_gap(summary.total_minutes, 65.0) == pytest.approx(15.0)
    assert demand_gap(summary.total_minutes, 40.0) == 0.0


def test_overlapping_break_and_leave_are_deducted_once():
    plan = iv(0, 120)
    person = staff_capacity(NURSE_A, [shift(1, 1, 0, 120, 30)],
                            [un(1, 30, 60, "Mola"), un(1, 45, 90, "Izin"), un(1, 50, 55, "Izin")], plan)  # fmt: skip
    assert person.work_minutes == 120
    assert person.break_minutes == 30 and person.other_unavailable_minutes == 30  # 60–90 (45–60 molada sayıldı)
    assert person.net_minutes == pytest.approx(60.0)  # 120 − birleşik 60 (30–90)
    assert person.unavailable_minutes + person.net_minutes == person.work_minutes


def test_break_minutes_summary_is_never_deducted_and_mismatch_warns():
    plan = iv(0, 60)
    matched = staff_capacity(NURSE_A, [shift(1, 1, 0, 60, 15)], [un(1, 20, 35, "Mola")], plan)
    assert matched.net_minutes == 45 and matched.warnings == ()
    mismatch = staff_capacity(NURSE_A, [shift(1, 1, 0, 60, 30)], [un(1, 20, 35, "Mola")], plan)
    assert mismatch.net_minutes == 45  # özetteki 30 dk düşülmedi; yalnız somut 15 dk
    assert mismatch.warnings and "uyuşmuyor" in mismatch.warnings[0]
    no_breaks = staff_capacity(NURSE_A, [shift(1, 1, 0, 60, 30)], [], plan)
    assert no_breaks.net_minutes == 60 and no_breaks.warnings


def test_break_outside_work_is_not_deducted():
    person = staff_capacity(NURSE_A, [shift(1, 1, 0, 60)], [un(1, 70, 90, "Mola"), un(2, 10, 20, "Izin")], iv(0, 60))
    assert person.net_minutes == 60  # vardiya dışı mola ve başkasının izni düşülmez


def test_whole_shift_on_leave_is_a_real_zero():
    summary = unit_capacity([NURSE_A], [shift(1, 1, 0, 60)], [un(1, -30, 90, "Izin")], iv(0, 60))
    assert summary.total_minutes == 0.0 and len(summary.staff) == 1  # vardiya var, kapasite gerçekten 0


def test_partial_overlap_with_plan_counts_only_intersection():
    person = staff_capacity(NURSE_A, [shift(1, 1, -30, 20)], [], iv(0, 60))
    assert person.work_minutes == 20 and person.shifts == (iv(0, 20),)


def test_shift_across_midnight_splits_between_local_days():
    night = shift_from_template(date(2026, 10, 4), time(20, 0), 720, "Europe/Istanbul")
    s = StaffShift(1, 1, 1, night.start, night.end, 30)
    brk = Unavailability(1, Interval(night.start + timedelta(hours=5), night.start + timedelta(hours=5, minutes=30)), "Mola")
    midnight = datetime(2026, 10, 5, tzinfo=IST).astimezone(UTC)
    day4 = staff_capacity(NURSE_A, [s], [brk], Interval(datetime(2026, 10, 4, tzinfo=IST).astimezone(UTC), midnight))
    day5 = staff_capacity(NURSE_A, [s], [brk], Interval(midnight, datetime(2026, 10, 6, tzinfo=IST).astimezone(UTC)))
    assert day4.net_minutes == 240  # 20:00–24:00
    assert day5.net_minutes == 480 - 30  # 00:00–08:00, mola 01:00–01:30
    assert day4.net_minutes + day5.net_minutes == 720 - 30


def test_role_breakdown_sums_to_total_and_window_capacity_matches():
    plan = iv(0, 60)
    shifts = [shift(1, 1, 0, 60), shift(2, 2, 0, 60), shift(3, 3, 0, 60)]
    summary = unit_capacity([NURSE_A, NURSE_B, SUPPORT], shifts, [un(2, 0, 15, "Mola")], plan)
    assert summary.by_role == {"nurse": 105.0, "support": 60.0}
    assert sum(summary.by_role.values()) == summary.total_minutes
    grid = window_grid(plan)
    assert window_capacity(summary, grid) == [30.0, 45.0, 45.0, 45.0]
    assert window_capacity(summary, grid, role="nurse") == [15.0, 30.0, 30.0, 30.0]
    assert sum(window_capacity(summary, grid)) == summary.total_minutes


def test_only_staff_with_shift_in_this_unit_are_counted():
    summary = unit_capacity([NURSE_A, NURSE_B], [shift(1, 1, 0, 60, unit=1)], [], iv(0, 60))
    assert [r.staff_id for r in summary.staff] == [1]


def test_empty_inputs():
    summary = unit_capacity([], [], [], iv(0, 60))
    assert summary.total_minutes == 0 and summary.staff == () and summary.by_role == {}


def test_interval_helpers():
    assert merge([iv(0, 10), iv(5, 20), iv(20, 30), iv(40, 50)]) == [iv(0, 30), iv(40, 50)]
    assert subtract([iv(0, 60)], [iv(10, 20), iv(15, 30), iv(50, 70)]) == [iv(0, 10), iv(30, 50)]
