"""Aşama 3: gerçekleşen iş yükünün 15 dakikalık pencerelere dağıtımı (saf hesap, veritabanı yok)."""

import math
import random
from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.domain.models import CareTask, Encounter
from medakis.domain.time import Interval, shift_from_template
from medakis.domain.workload import (
    check_grid_timezone,
    distribute,
    patient_count_at,
    pending_at,
    realized_before_created,
    realized_interval,
    task_shares,
    window_grid,
)

IST = ZoneInfo("Europe/Istanbul")
T0 = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)  # yerel 20:00


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def done(task_id, start_min, minutes, created_min=None, unit=1):
    """`start_min`'den başlayıp `minutes` süren tamamlanmış görev (T0'a göre dakika)."""
    return CareTask(task_id, 1, unit, 1, at(created_min if created_min is not None else start_min - 60), at(start_min),
                    10.0, float(minutes), "completed", at(start_min + minutes))  # fmt: skip


def code_of(call):
    with pytest.raises(DomainError) as info:
        call()
    return info.value.code


# ---- Elle hesaplanan küçük örnek -------------------------------------------------------------


def test_hand_calculated_small_example():
    # Pencereler: [20:00,20:15) [20:15,20:30) [20:30,20:45) [20:45,21:00)
    grid = window_grid(Interval(at(0), at(60)))
    tasks = [
        done(1, 10, 30),   # 20:10–20:40 → 5 / 15 / 10 / 0
        done(2, 15, 15),   # 20:15–20:30 → 0 / 15 / 0 / 0 (tam pencere)
        done(3, 50, 20),   # 20:50–21:10 → 0 / 0 / 0 / 10 (10 dk aralık dışına taşar)
        CareTask(4, 1, 1, 1, at(-30), at(5), 12.0, None, "pending", None),
        CareTask(5, 1, 1, 1, at(-30), at(5), 12.0, None, "cancelled", None),
    ]  # fmt: skip
    obs = distribute(tasks, grid)
    assert [o.observed_minutes for o in obs] == pytest.approx([5.0, 30.0, 10.0, 10.0])
    assert [o.contributing_tasks for o in obs] == [1, 2, 1, 1]
    assert sum(o.observed_minutes for o in obs) == pytest.approx(55.0)  # 30 + 15 + 10 (aralık içi kısım)
    # Aralık genişleyince taşan kısım geri gelir: görev toplamı korunur.
    wide = distribute(tasks, window_grid(Interval(at(0), at(75))))
    assert sum(o.observed_minutes for o in wide) == pytest.approx(65.0)


def test_shares_conserve_task_total_on_random_tasks():
    rng = random.Random(42)
    grid = window_grid(Interval(at(-600), at(1440)))
    for task_id in range(1000):
        start = rng.uniform(-500, 1300)
        task = done(task_id, start, rng.uniform(0.5, 90))
        assert math.isclose(sum(task_shares(task, grid).values()), task.actual_minutes, rel_tol=1e-12)


def test_window_boundaries_are_half_open():
    grid = window_grid(Interval(at(0), at(30)))
    shares = task_shares(done(1, 0, 15), grid)  # 20:00–20:15: ikinci pencereye pay vermez
    assert shares == {0: pytest.approx(15.0)}


def test_only_completed_tasks_are_distributed():
    grid = window_grid(Interval(at(0), at(30)))
    pending = CareTask(1, 1, 1, 1, at(-10), at(5), 10.0, None, "pending", None)
    cancelled = CareTask(2, 1, 1, 1, at(-10), at(5), 10.0, None, "cancelled", None)
    assert realized_interval(pending) is None and realized_interval(cancelled) is None
    assert [o.observed_minutes for o in distribute([pending, cancelled], grid)] == [0.0, 0.0]


def test_available_at_is_latest_completion_of_contributors():
    grid = window_grid(Interval(at(0), at(30)))
    obs = distribute([done(1, 10, 30)], grid)  # 20:10–20:40, biter 20:40
    assert obs[0].available_at == at(40) and obs[1].available_at == at(40)
    empty = distribute([], grid)
    assert [o.available_at for o in empty] == [w.end for w in grid]  # katkı yoksa pencere sonu


def test_realized_start_before_creation_is_flagged_not_changed():
    # K2: kural aynen uygulanır; durum yalnız işaretlenir.
    task = done(1, 0, 20, created_min=10)  # gerçekleşme 20:00'de başlıyor, görev 20:10'da oluşmuş
    assert realized_before_created(task)
    assert realized_interval(task) == Interval(at(0), at(20))
    assert not realized_before_created(done(2, 30, 10, created_min=0))


# ---- Gece yarısı ve zaman dilimi ---------------------------------------------------------------


def test_task_across_local_midnight_is_split_between_local_days():
    local_midnight = datetime(2026, 10, 5, 0, 0, tzinfo=IST).astimezone(UTC)
    start = local_midnight - timedelta(minutes=10)
    task = CareTask(1, 1, 1, 1, start - timedelta(hours=1), start, 20.0, 20.0, "completed", start + timedelta(minutes=20))
    day4 = window_grid(Interval(datetime(2026, 10, 4, tzinfo=IST).astimezone(UTC), local_midnight))
    day5 = window_grid(Interval(local_midnight, datetime(2026, 10, 6, tzinfo=IST).astimezone(UTC)))
    assert sum(task_shares(task, day4).values()) == pytest.approx(10.0)
    assert sum(task_shares(task, day5).values()) == pytest.approx(10.0)


def test_night_shift_window_grid_crosses_midnight():
    shift = shift_from_template(date(2026, 10, 4), time(20, 0), 720, "Europe/Istanbul")
    grid = window_grid(shift)
    assert len(grid) == 48
    assert grid[0].start == datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
    assert grid[-1].end == datetime(2026, 10, 5, 5, 0, tzinfo=UTC)
    assert grid[16].start.astimezone(IST).date() == date(2026, 10, 5)  # yerel 00:00 penceresi


def test_naive_times_and_misaligned_ranges_are_rejected():
    assert code_of(lambda: window_grid(Interval(datetime(2026, 10, 4, 20, 0), at(60)))) == errors.NAIVE_DATETIME
    assert code_of(lambda: window_grid(Interval(at(5), at(60)))) == errors.WINDOW_ALIGNMENT
    assert code_of(lambda: pending_at([], datetime(2026, 10, 4, 20, 0))) == errors.NAIVE_DATETIME


def test_grid_timezone_requires_quarter_hour_offset():
    check_grid_timezone(IST, T0)  # +03:00
    check_grid_timezone(ZoneInfo("Asia/Kathmandu"), T0)  # +05:45: 15'in katı
    assert code_of(lambda: check_grid_timezone(timezone(timedelta(minutes=10)), T0)) == errors.GRID_TIMEZONE


def test_same_instant_in_local_or_utc_lands_in_same_window():
    grid = window_grid(Interval(at(0), at(60)))
    local = CareTask(1, 1, 1, 1, at(-60), at(0), 10.0, 10.0, "completed", at(25).astimezone(IST))
    utc = CareTask(2, 1, 1, 1, at(-60), at(0), 10.0, 10.0, "completed", at(25))
    assert task_shares(local, grid) == task_shares(utc, grid)


# ---- Bekleyen iş ve hasta sayısı ------------------------------------------------------------


def test_pending_at_uses_known_unfinished_tasks_and_reports_uncertain_cancellations():
    t = at(0)
    tasks = [
        CareTask(1, 1, 1, 1, at(-30), at(10), 12.0, None, "pending", None),          # bekliyor
        CareTask(2, 1, 1, 1, at(-30), at(10), 8.0, 9.0, "completed", at(20)),        # t'de bitmemişti
        CareTask(3, 1, 1, 1, at(-30), at(-5), 5.0, 5.0, "completed", at(-1)),        # t'den önce bitti
        CareTask(4, 1, 1, 1, at(30), at(40), 7.0, None, "pending", None),            # t'de bilinmiyordu
        CareTask(5, 1, 1, 1, at(-30), at(15), 6.0, None, "cancelled", None),         # belirsiz (K3)
        CareTask(6, 1, 1, 1, at(-90), at(-60), 4.0, None, "cancelled", None),        # vadesi geçmiş iptal
    ]  # fmt: skip
    result = pending_at(tasks, t)
    assert [x.id for x in result.tasks] == [1, 2] and result.minutes == pytest.approx(20.0)
    assert [x.id for x in result.uncertain_cancelled] == [5] and result.uncertain_cancelled_minutes == pytest.approx(6.0)


def test_patient_count_at_instant():
    encounters = [
        Encounter(1, 1, 1, None, at(-60), None),
        Encounter(2, 2, 1, None, at(-60), at(0)),     # t'de taburcu: sayılmaz
        Encounter(3, 3, 1, None, at(0), at(60)),      # t'de yatış: sayılır
        Encounter(4, 4, 1, None, at(10), None),       # henüz yok
    ]  # fmt: skip
    assert patient_count_at(encounters, at(0)) == 2


def test_empty_inputs_give_empty_results_not_invented_values():
    grid = window_grid(Interval(at(0), at(30)))
    assert [o.contributing_tasks for o in distribute([], grid)] == [0, 0]
    assert pending_at([], at(0)).tasks == () and pending_at([], at(0)).minutes == 0
    assert distribute([done(1, 0, 10)], []) == []
