"""Saf alan kuralları (veritabanısız). Örnek değerler elle hesaplanmıştır."""

import math
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.domain.rules import (
    breaks_match_summary,
    check_break_fits_shift,
    check_task_transition,
    estimated_minutes,
    has_overlap,
    latest_multiplier_at,
    require_patient_code,
    require_positive,
    require_staff_code,
    skill_valid_throughout,
)
from medakis.domain.time import Interval, ensure_utc, shift_from_template


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def code_of(call):
    with pytest.raises(DomainError) as info:
        call()
    return info.value.code


def test_estimate_is_base_times_multiplier_once():
    assert estimated_minutes(10, 1.2) == 12.0  # şartnamedeki örnek
    assert estimated_minutes(8, 1.5) == 12.0
    assert estimated_minutes(15, 0.8) == 12.0


def test_capacity_example_by_hand():
    # 2 kişi × 30 dk − toplam 10 dk mola = 50 personel-dakikası; 65 dk talep → 15 dk açık
    capacity = 2 * Interval(utc(2026, 11, 4, 20), utc(2026, 11, 4, 20, 30)).minutes() - 10
    assert capacity == 50 and 65 - capacity == 15


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 0, -1, True, "5"])
def test_non_finite_or_non_positive_rejected(value):
    assert code_of(lambda: require_positive(value, "süre")) in {errors.NOT_FINITE, errors.NOT_POSITIVE}


def test_naive_datetime_rejected_and_aware_converted():
    assert code_of(lambda: ensure_utc(datetime(2026, 11, 4, 20, 0))) == errors.NAIVE_DATETIME
    istanbul = timezone(timedelta(hours=3))
    assert ensure_utc(datetime(2026, 11, 4, 23, 0, tzinfo=istanbul)) == utc(2026, 11, 4, 20, 0)


def test_interval_half_open_and_unbounded():
    a = Interval(utc(2026, 11, 4, 8), utc(2026, 11, 4, 20))
    b = Interval(utc(2026, 11, 4, 20), utc(2026, 11, 5, 8))
    assert not a.overlaps(b)  # biten anda yenisi başlayabilir
    assert Interval(utc(2026, 11, 4, 19), None).overlaps(b)
    assert code_of(lambda: Interval(utc(2026, 11, 4, 8), utc(2026, 11, 4, 8))) == errors.INVALID_INTERVAL
    assert has_overlap([a, Interval(utc(2026, 11, 4, 19), utc(2026, 11, 4, 21))])
    assert not has_overlap([a, b])


def test_night_shift_crosses_midnight_in_utc():
    shift = shift_from_template(date(2026, 11, 4), time(20, 0), 720, "Europe/Istanbul")
    assert shift.start == utc(2026, 11, 4, 17) and shift.end == utc(2026, 11, 5, 5)
    assert shift.end.date() != shift.start.date()


def test_latest_multiplier_ignores_future_assessments():
    history = [(utc(2026, 11, 4, 8), 1.0), (utc(2026, 11, 4, 12), 1.5), (utc(2026, 11, 4, 18), 1.8)]
    assert latest_multiplier_at(history, utc(2026, 11, 4, 13)) == 1.5
    assert latest_multiplier_at(history, utc(2026, 11, 4, 12)) == 1.5  # aynı an bilinir
    assert code_of(lambda: latest_multiplier_at(history, utc(2026, 11, 4, 7))) == errors.NO_ACUITY


@pytest.mark.parametrize(
    "current,target,ok",
    [("pending", "completed", True), ("pending", "cancelled", True), ("completed", "cancelled", False),
     ("cancelled", "completed", False), ("completed", "completed", False)],
)  # fmt: skip
def test_task_transitions(current, target, ok):
    if ok:
        check_task_transition(current, target)
    else:
        assert code_of(lambda: check_task_transition(current, target)) == errors.TASK_TRANSITION


def test_break_rules():
    shift = Interval(utc(2026, 11, 4, 17), utc(2026, 11, 5, 5))
    check_break_fits_shift(30, shift)
    assert code_of(lambda: check_break_fits_shift(721, shift)) == errors.BREAK_TOO_LONG
    breaks = [Interval(utc(2026, 11, 4, 22), utc(2026, 11, 4, 22, 30))]
    assert breaks_match_summary(30, breaks, shift)
    assert not breaks_match_summary(60, breaks, shift)  # mola iki kez düşülmez


def test_skill_must_cover_whole_work_interval():
    work = Interval(utc(2026, 11, 4, 20), utc(2026, 11, 4, 21))
    assert skill_valid_throughout([Interval(utc(2026, 1, 1), None)], work)
    assert not skill_valid_throughout([Interval(utc(2026, 1, 1), utc(2026, 11, 4, 20, 30))], work)  # iş ortasında biter
    assert skill_valid_throughout([Interval(utc(2026, 1, 1), utc(2026, 11, 4, 20, 30)), Interval(utc(2026, 11, 4, 20, 30), None)], work)
    assert not skill_valid_throughout([Interval(utc(2026, 1, 1), utc(2026, 11, 4, 20, 15)), Interval(utc(2026, 11, 4, 20, 30), None)], work)
    assert not skill_valid_throughout([], work)


@pytest.mark.parametrize("code", ["Ahmet Yilmaz", "12345678901", "syn001", ""])
def test_identity_codes_must_be_synthetic(code):
    assert code_of(lambda: require_patient_code(code)) == errors.INVALID_CODE
    assert code_of(lambda: require_staff_code(code)) == errors.INVALID_CODE
    require_patient_code("SYN000001")
    require_staff_code("STAFF0001")
