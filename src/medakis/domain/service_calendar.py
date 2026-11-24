"""Servis çalışma takvimi sözleşmesi (Aşama 5 / U3; yalnız bellek).

Amaç "kapalı servis" ile "plan eksik" ayrımıdır. Kurallar:
- Takvim YETKİLİ bir kaynaktan gelir (source ve approved_by dolu); sistem çalışma saati UYDURMAZ.
- Takvim yoksa vardiya bulunmaması "kapalı" sayılmaz (→ veri eksik).
- Takvim yalnız kendi kapsama döneminde (coverage) ve bilinme anından (known_at) sonra hüküm verir.
- Aynı pencere için birbiriyle çelişen geçerli takvimler varsa güvenli taraf: hüküm verilmez.
Saf alan kodudur: veritabanı, çözücü ve öneri yoktur.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from medakis.domain.capacity import clip, merge, total_minutes
from medakis.domain.time import Interval

OPEN, CLOSED, PARTIAL, UNKNOWN, CONFLICT = "acik", "kapali", "kismi_acik", "bilinmiyor", "celiskili"


@dataclass(frozen=True)
class ServiceCalendar:
    """Bir servisin kapsama dönemi içindeki açık aralıkları (UTC, yarı açık). Dönem içinde, açık aralık
    dışındaki her an KAPALIDIR; dönem dışında takvim hüküm vermez."""

    unit_id: int
    coverage: Interval
    open_intervals: tuple[Interval, ...]
    source: str
    approved_by: str
    known_at: datetime

    def problems(self) -> list[str]:
        out = []
        if not self.source.strip() or not self.approved_by.strip():
            out.append(f"takvim_kaynaksiz:{self.unit_id}")
        if self.coverage.end is None:
            out.append(f"takvim_kapsami_sinirsiz:{self.unit_id}")
        ordered = sorted(self.open_intervals, key=lambda i: i.start)
        for a in ordered:
            if a.end is None or not self.coverage.covers(a):
                out.append(f"takvim_araligi_kapsam_disi:{self.unit_id}")
                break
        for a, b in zip(ordered, ordered[1:]):
            if a.end is not None and b.start < a.end:
                out.append(f"takvim_araliklari_cakisiyor:{self.unit_id}")
                break
        return out

    def open_in(self, window: Interval) -> list[Interval]:
        return merge(clip(self.open_intervals, window))


@dataclass(frozen=True)
class CalendarState:
    state: str  # acik / kapali / kismi_acik / bilinmiyor / celiskili
    open_parts: tuple[Interval, ...]  # pencere içindeki açık bölümler (bilinmiyorsa boş)
    open_minutes: float | None
    sources: tuple[str, ...]
    warnings: tuple[str, ...]


def calendar_state(calendars: Sequence[ServiceCalendar], unit_id: int, window: Interval, at: datetime) -> CalendarState:
    """`at` anında bilinen ve pencereyi tamamen kapsayan geçerli takvimlere göre pencerenin durumu."""
    warnings: list[str] = []
    usable: list[ServiceCalendar] = []
    for c in calendars:
        if c.unit_id != unit_id or c.known_at > at or c.coverage.end is None or not c.coverage.covers(window):
            continue
        problems = c.problems()
        if problems:  # geçersiz takvim hüküm vermez (güvenli taraf: bilinmiyor)
            warnings += problems
            continue
        usable.append(c)
    if not usable:
        return CalendarState(UNKNOWN, (), None, (), tuple(sorted(set(warnings))))
    parts = {tuple(c.open_in(window)) for c in usable}
    sources = tuple(sorted({f"{c.source} (onay: {c.approved_by})" for c in usable}))
    if len(parts) > 1:
        return CalendarState(CONFLICT, (), None, sources, tuple(sorted(set(warnings + [f"takvim_celiskili:{unit_id}"]))))
    open_parts = next(iter(parts))
    minutes = total_minutes(open_parts)
    state = CLOSED if minutes == 0 else OPEN if minutes == window.minutes() else PARTIAL
    return CalendarState(state, open_parts, minutes, sources, tuple(sorted(set(warnings))))


def weekly_calendar(unit_id: int, days: Sequence[tuple[date, str]], weekly_hours: Mapping[int, tuple[time, time]],
                    half_day_close: time, timezone: str, source: str, approved_by: str,
                    known_at: datetime) -> ServiceCalendar:  # fmt: skip
    """Haftalık çalışma saatlerinden takvim. Saatler ÇAĞIRANDAN gelir (varsayılan yok).

    days: (yerel gün, tatil türü) — tatil türü "yok" / "tam" / "yarim_13" (resmî tatil takviminden).
    weekly_hours: haftanın günü (0=Pazartesi) → (açılış, kapanış) yerel saat; olmayan gün kapalı.
    Tam gün tatil kapalıdır; yarım gün tatilde kapanış min(kapanış, half_day_close) olur.
    """
    tz = ZoneInfo(timezone)
    if not days:
        raise ValueError("Takvim için en az bir gün gerekir.")
    ordered = sorted(days)
    first, last = ordered[0][0], ordered[-1][0]
    coverage = Interval(datetime.combine(first, time(0), tzinfo=tz),
                        datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz))  # fmt: skip
    opens = []
    for day, kind in ordered:
        if kind not in ("yok", "tam", "yarim_13"):
            raise ValueError(f"Bilinmeyen tatil türü: {kind}")
        hours = weekly_hours.get(day.weekday())
        if kind == "tam" or hours is None:
            continue
        start, end = hours
        if kind == "yarim_13":
            end = min(end, half_day_close)
        if end <= start:
            continue
        opens.append(Interval(datetime.combine(day, start, tzinfo=tz), datetime.combine(day, end, tzinfo=tz)))
    return ServiceCalendar(unit_id, coverage, tuple(opens), source, approved_by, known_at)


def always_open(unit_id: int, coverage: Interval, source: str, approved_by: str, known_at: datetime) -> ServiceCalendar:
    """7/24 açık servis (ör. yataklı, acil) için takvim; yine yetkili kaynak gerekir."""
    return ServiceCalendar(unit_id, coverage, (coverage,), source, approved_by, known_at)
