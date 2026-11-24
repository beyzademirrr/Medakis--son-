"""Saf iş kuralları (veritabanı ve saat bağımsız)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from datetime import datetime

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.domain.time import Interval, ensure_utc

PATIENT_CODE = re.compile(r"^SYN-?[0-9A-Z]+$")
STAFF_CODE = re.compile(r"^STAFF-?[0-9A-Z]+$")
TASK_TRANSITIONS = {"pending": frozenset({"completed", "cancelled"}), "completed": frozenset(), "cancelled": frozenset()}


def require_finite(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DomainError(errors.NOT_FINITE, f"{field} sonlu bir sayı olmalı.")
    return float(value)


def require_positive(value: float, field: str) -> float:
    value = require_finite(value, field)
    if value <= 0:
        raise DomainError(errors.NOT_POSITIVE, f"{field} pozitif olmalı.")
    return value


def require_nonnegative(value: float, field: str) -> float:
    value = require_finite(value, field)
    if value < 0:
        raise DomainError(errors.NEGATIVE, f"{field} negatif olamaz.")
    return value


def require_patient_code(code: str) -> str:
    if not isinstance(code, str) or not PATIENT_CODE.fullmatch(code):
        raise DomainError(errors.INVALID_CODE, "Hasta kodu yalnız sentetik SYN biçiminde olabilir.")
    return code


def require_staff_code(code: str) -> str:
    if not isinstance(code, str) or not STAFF_CODE.fullmatch(code):
        raise DomainError(errors.INVALID_CODE, "Personel kodu yalnız sentetik STAFF biçiminde olabilir.")
    return code


def estimated_minutes(base_minutes: float, multiplier: float) -> float:
    """Görev tahmini = base_minutes × katsayı (bir kez uygulanır), 2 basamağa yuvarlanır."""
    base = require_positive(base_minutes, "Temel süre")
    factor = require_positive(multiplier, "Bakım katsayısı")
    return round(base * factor, 2)


def latest_multiplier_at(assessments: Iterable[tuple[datetime, float]], at: datetime) -> float:
    """`at` anında bilinen en son değerlendirmenin katsayısı. Gelecekteki değerlendirme kullanılmaz."""
    at = ensure_utc(at, "Görev zamanı")
    known = [(ensure_utc(when, "Değerlendirme zamanı"), m) for when, m in assessments]
    known = [(when, m) for when, m in known if when <= at]
    if not known:
        raise DomainError(errors.NO_ACUITY, "Görev anında bilinen bakım gereksinimi değerlendirmesi yok.")
    return max(known, key=lambda item: item[0])[1]


def check_task_transition(current: str, target: str) -> None:
    if target not in TASK_TRANSITIONS.get(current, frozenset()):
        raise DomainError(errors.TASK_TRANSITION, f"Görev durumu '{current}' iken '{target}' yapılamaz.")


def check_break_fits_shift(break_minutes: int, shift: Interval) -> None:
    require_nonnegative(break_minutes, "Mola süresi")
    if break_minutes > shift.minutes():
        raise DomainError(errors.BREAK_TOO_LONG, "Mola süresi vardiya süresinden uzun olamaz.")


def breaks_match_summary(break_minutes: int, breaks: Sequence[Interval], shift: Interval) -> bool:
    """break_minutes özet alanı, vardiya içindeki somut molaların toplamına eşit mi (iki kez düşülmez)."""
    inside = [b for b in breaks if shift.covers(b)]
    return math.isclose(sum(b.minutes() for b in inside), break_minutes)


def has_overlap(intervals: Iterable[Interval]) -> bool:
    ordered = sorted(intervals, key=lambda i: i.start)
    return any(a.overlaps(b) for a, b in zip(ordered, ordered[1:]))


def skill_valid_throughout(validities: Iterable[Interval], work: Interval) -> bool:
    """Yetkinlik işin bütün süresi boyunca geçerli mi? Art arda gelen geçerlilikler birleştirilir."""
    if work.end is None:
        return False
    cursor = work.start
    for validity in sorted(validities, key=lambda v: v.start):
        if validity.start > cursor:
            break
        if validity.end is None or validity.end > cursor:
            cursor = work.end if validity.end is None else max(cursor, validity.end)
        if cursor >= work.end:
            return True
    return cursor >= work.end
