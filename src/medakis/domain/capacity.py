"""Personel kapasitesi (Aşama 3): vardiya ∩ plan penceresi − birleşik uygunsuzluk, çifte sayım olmadan.

Kurallar (şartname §4 "Kapasite ve çifte sayım"):
- Kapasite, personelin bu servisteki vardiyalarının plan penceresiyle kesişiminden, o kesişime düşen
  bütün uygunsuzluk aralıklarının (Mola, Izin, diğer) BİRLEŞİMİ çıkarılarak bulunur. Çakışan mola ve
  izin bir kez düşülür.
- staff_shifts.break_minutes yalnız özet alanıdır; asla düşülmez. Somut molalarla uyuşmazsa uyarı üretir.
- Personel yalnız vardiyasının kayıtlı olduğu servisin kapasitesine sayılır.
Saf hesaplardır; veritabanına bağlanmaz.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from medakis.domain.models import Staff, StaffShift
from medakis.domain.rules import breaks_match_summary
from medakis.domain.time import Interval

BREAK_REASON = "Mola"
LEAVE_REASON = "Izin"


@dataclass(frozen=True)
class Unavailability:
    staff_id: int
    interval: Interval
    reason: str


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Örtüşen veya bitişik aralıkları birleştirir (sınırlı aralıklar)."""
    ordered = sorted(intervals, key=lambda i: (i.start, i.end))
    merged: list[Interval] = []
    for item in ordered:
        if merged and item.start <= merged[-1].end:  # type: ignore[operator]
            if item.end > merged[-1].end:  # type: ignore[operator]
                merged[-1] = Interval(merged[-1].start, item.end)
        else:
            merged.append(item)
    return merged


def clip(intervals: Iterable[Interval], window: Interval) -> list[Interval]:
    """Her aralığın `window` ile kesişimi (boş kesişimler atılır)."""
    out = []
    for item in intervals:
        start, end = max(item.start, window.start), min(item.end, window.end)  # type: ignore[type-var]
        if end > start:
            out.append(Interval(start, end))
    return out


def intersect(a: Sequence[Interval], b: Sequence[Interval]) -> list[Interval]:
    """İki birleşik aralık kümesinin kesişimi."""
    out = []
    for x in merge(a):
        out.extend(clip(merge(b), x))
    return merge(out)


def subtract(base: Sequence[Interval], remove: Sequence[Interval]) -> list[Interval]:
    """`base` birleşiminden `remove` birleşimini çıkarır."""
    result = merge(base)
    for cut in merge(remove):
        next_result = []
        for item in result:
            if cut.end <= item.start or cut.start >= item.end:  # type: ignore[operator]
                next_result.append(item)
                continue
            if cut.start > item.start:
                next_result.append(Interval(item.start, cut.start))
            if cut.end < item.end:  # type: ignore[operator]
                next_result.append(Interval(cut.end, item.end))  # type: ignore[arg-type]
        result = next_result
    return result


def total_minutes(intervals: Iterable[Interval]) -> float:
    return sum(i.minutes() for i in merge(intervals))


@dataclass(frozen=True)
class StaffCapacity:
    staff_id: int
    synthetic_code: str
    role: str
    shifts: tuple[Interval, ...]  # plan penceresiyle kesişen vardiya parçaları
    work_minutes: float
    break_minutes: float  # vardiya içindeki somut molalar (birleşik)
    other_unavailable_minutes: float  # mola dışı uygunsuzluk; molayla çakışan kısım molada sayılmıştır
    net_minutes: float
    available: tuple[Interval, ...]  # net çalışılabilir aralıklar
    warnings: tuple[str, ...] = field(default=())

    @property
    def unavailable_minutes(self) -> float:
        return self.break_minutes + self.other_unavailable_minutes


@dataclass(frozen=True)
class CapacitySummary:
    total_minutes: float
    by_role: dict[str, float]
    staff: tuple[StaffCapacity, ...]


def staff_capacity(
    person: Staff, shifts: Sequence[StaffShift], unavailability: Sequence[Unavailability], plan: Interval
) -> StaffCapacity:
    own_shifts = [s for s in shifts if s.staff_id == person.id]
    own_unavailable = [u for u in unavailability if u.staff_id == person.id]
    shift_intervals = [Interval(s.starts_at, s.ends_at) for s in own_shifts]
    work = merge(clip(shift_intervals, plan))
    breaks = [u.interval for u in own_unavailable if u.reason == BREAK_REASON]
    everything = [u.interval for u in own_unavailable]
    break_part = intersect(work, breaks)
    unavailable_part = intersect(work, everything)
    available = subtract(work, unavailable_part)

    warnings = []
    for shift, interval in zip(own_shifts, shift_intervals):
        if not breaks_match_summary(shift.break_minutes, breaks, interval):
            warnings.append(
                f"Vardiya {shift.id}: mola özeti ({shift.break_minutes} dk) somut molalarla uyuşmuyor; "
                "kapasitede yalnız somut molalar düşüldü."
            )
    work_minutes = total_minutes(work)
    break_minutes = total_minutes(break_part)
    unavailable_minutes = total_minutes(unavailable_part)
    return StaffCapacity(
        staff_id=person.id,
        synthetic_code=person.synthetic_code,
        role=person.role,
        shifts=tuple(work),
        work_minutes=work_minutes,
        break_minutes=break_minutes,
        other_unavailable_minutes=unavailable_minutes - break_minutes,
        net_minutes=total_minutes(available),
        available=tuple(available),
        warnings=tuple(warnings),
    )


def unit_capacity(
    staff: Sequence[Staff], shifts: Sequence[StaffShift], unavailability: Sequence[Unavailability], plan: Interval
) -> CapacitySummary:
    """Servis kapasitesi: `shifts` yalnız bu servisin vardiyaları olmalıdır."""
    on_shift = {s.staff_id for s in shifts}
    people = sorted((p for p in staff if p.id in on_shift), key=lambda p: (p.role, p.synthetic_code))
    rows = tuple(staff_capacity(p, shifts, unavailability, plan) for p in people)
    rows = tuple(r for r in rows if r.work_minutes > 0)
    by_role: dict[str, float] = {}
    for row in rows:
        by_role[row.role] = by_role.get(row.role, 0.0) + row.net_minutes
    return CapacitySummary(sum(r.net_minutes for r in rows), dict(sorted(by_role.items())), rows)


def window_capacity(summary: CapacitySummary, grid: Sequence[Interval], role: str | None = None) -> list[float]:
    """Her pencerede kullanılabilir personel-dakikası (isteğe bağlı rol filtresi)."""
    values = []
    for window in grid:
        minutes = 0.0
        for row in summary.staff:
            if role is None or row.role == role:
                minutes += sum(i.minutes() for i in clip(row.available, window))
        values.append(minutes)
    return values


def demand_gap(capacity_minutes: float, demand_minutes: float) -> float:
    """Toplam talep açığı (yalnız toplam düzey; görev bazlı somut açık Aşama 5'tedir)."""
    return max(0.0, demand_minutes - capacity_minutes)
