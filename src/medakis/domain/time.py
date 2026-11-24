"""Zaman kuralları: depolama UTC, aralıklar [başlangıç, bitiş), yerel şablondan somut vardiya."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from medakis.domain import errors
from medakis.domain.errors import DomainError


def ensure_utc(value: datetime, field: str = "zaman") -> datetime:
    """Saat dilimi olmayan değeri reddeder; değeri UTC'ye çevirir."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainError(errors.NAIVE_DATETIME, f"{field} saat dilimi bilgisi içermeli (UTC'ye çevrilebilir olmalı).")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class Interval:
    """[start, end) yarı açık aralık. end None ise sınırsızdır."""

    start: datetime
    end: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", ensure_utc(self.start, "başlangıç"))
        if self.end is not None:
            object.__setattr__(self, "end", ensure_utc(self.end, "bitiş"))
            if self.end <= self.start:
                raise DomainError(errors.INVALID_INTERVAL, "Bitiş başlangıçtan sonra olmalı.")

    def overlaps(self, other: Interval) -> bool:
        ends_after_other_starts = self.end is None or self.end > other.start
        starts_before_other_ends = other.end is None or self.start < other.end
        return ends_after_other_starts and starts_before_other_ends

    def covers(self, other: Interval) -> bool:
        if other.start < self.start:
            return False
        if self.end is None:
            return True
        return other.end is not None and other.end <= self.end

    def minutes(self) -> float:
        if self.end is None:
            raise DomainError(errors.INVALID_INTERVAL, "Sınırsız aralığın süresi yoktur.")
        return (self.end - self.start).total_seconds() / 60


def shift_from_template(local_day: date, start_local: time, duration_minutes: int, timezone: str) -> Interval:
    """Yerel takvim günü ve şablon saatinden UTC vardiya üretir; gece yarısını tarih değişimiyle taşır."""
    if not 1 <= duration_minutes <= 1440:
        raise DomainError(errors.INVALID_INTERVAL, "Vardiya süresi 1-1440 dakika olmalı.")
    start = datetime.combine(local_day, start_local, tzinfo=ZoneInfo(timezone))
    start_utc = start.astimezone(UTC)
    return Interval(start_utc, start_utc + timedelta(minutes=duration_minutes))
