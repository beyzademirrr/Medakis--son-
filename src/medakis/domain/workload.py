"""Gerçekleşen iş yükü (Aşama 3): 15 dakikalık pencereler, orantılı dağıtım, bekleyen iş, hasta sayısı.

Saf hesaplardır; veritabanına bağlanmaz. Bütün zamanlar UTC'dir ve aralıklar [başlangıç, bitiş)'tir.

Simülasyon varsayımı (şartname §4, proje sahibi kararı K2): tamamlanan görevin gerçekleşme aralığı
[completed_at − actual_minutes, completed_at) kabul edilir. Sentetik veride bu başlangıç bazı
görevlerde görevin oluşturulma anından önceye düşer; kural yine de aynen uygulanır ve durum
raporlanır (`realized_before_created`).

Eksik veri ile gerçek sıfır ayrımı: bu modül yalnız verilen kayıtları hesaplar. Bir pencerenin
verisinin bulunup bulunmadığına (kapsama) servis katmanı karar verir.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.domain.models import CareTask, Encounter
from medakis.domain.rules import require_positive
from medakis.domain.time import Interval, ensure_utc

WINDOW_MINUTES = 15
WINDOW = timedelta(minutes=WINDOW_MINUTES)
_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)  # ızgara başlangıcı: UTC çeyrek saatler


@dataclass(frozen=True)
class WindowObservation:
    window: Interval
    observed_minutes: float
    contributing_tasks: int
    # Pencerenin değeri bu andan önce kesin değildir (katkı veren son görevin tamamlanması).
    available_at: datetime


@dataclass(frozen=True)
class PendingWork:
    at: datetime
    minutes: float
    tasks: tuple[CareTask, ...]
    # K3: iptal zamanı BİLİNMEYEN (1.0.0 / revizyon 0003) ve vadesi t'den sonra olan iptal görevlerinin
    # t anında hâlâ açık olup olmadığı bilinemez. Bunlar sayıya katılmaz, ayrıca bildirilir.
    # İptal zamanı bilinen görevler (2.0.0 / revizyon 0004) belirsiz değildir; kesin olarak değerlendirilir.
    uncertain_cancelled: tuple[CareTask, ...]

    @property
    def uncertain_cancelled_minutes(self) -> float:
        return sum(t.estimated_minutes for t in self.uncertain_cancelled)


def check_grid_timezone(tz: tzinfo, at: datetime) -> None:
    """Yerel saat farkı 15 dakikanın katı değilse yerel çeyrek saatler UTC ızgarasına oturmaz."""
    offset = ensure_utc(at).astimezone(tz).utcoffset()
    if offset is None or offset % WINDOW != timedelta(0):
        raise DomainError(errors.GRID_TIMEZONE, "Saat dilimi farkı 15 dakikanın katı değil; pencereler hizalanamaz.")


def is_aligned(value: datetime) -> bool:
    value = ensure_utc(value)
    return (value - _EPOCH) % WINDOW == timedelta(0)


def window_grid(span: Interval) -> list[Interval]:
    """`span` içindeki ardışık 15 dakikalık pencereler. Uçlar pencere sınırında olmalı."""
    if span.end is None:
        raise DomainError(errors.INVALID_INTERVAL, "Sınırsız aralık pencerelere bölünemez.")
    if not (is_aligned(span.start) and is_aligned(span.end)):
        raise DomainError(errors.WINDOW_ALIGNMENT, "Aralık 15 dakikalık pencere sınırlarına hizalı olmalı.")
    count = int((span.end - span.start) / WINDOW)
    return [Interval(span.start + i * WINDOW, span.start + (i + 1) * WINDOW) for i in range(count)]


def realized_interval(task: CareTask) -> Interval | None:
    """Tamamlanan görevin varsayılan gerçekleşme aralığı; tamamlanmamışsa None."""
    if task.status != "completed" or task.completed_at is None or task.actual_minutes is None:
        return None
    minutes = require_positive(task.actual_minutes, "Gerçekleşen süre")
    end = ensure_utc(task.completed_at, "Tamamlanma zamanı")
    return Interval(end - timedelta(minutes=minutes), end)


def realized_before_created(task: CareTask) -> bool:
    """K2 veri kalitesi: türetilen gerçekleşme başlangıcı görevin oluşturulmasından önce mi."""
    interval = realized_interval(task)
    return interval is not None and interval.start < ensure_utc(task.created_at)


def overlap_seconds(a: Interval, b: Interval) -> float:
    start = max(a.start, b.start)
    end = min(a.end, b.end)  # type: ignore[type-var]
    return max(0.0, (end - start).total_seconds())


def task_shares(task: CareTask, grid: Sequence[Interval]) -> dict[int, float]:
    """Görevin pencere indeksine göre payları: actual × (kesişim / gerçekleşme süresi)."""
    interval = realized_interval(task)
    if interval is None or not grid:
        return {}
    total = (interval.end - interval.start).total_seconds()  # type: ignore[operator]
    first = max(0, int((interval.start - grid[0].start) // WINDOW))
    shares: dict[int, float] = {}
    for index in range(first, len(grid)):
        window = grid[index]
        if window.start >= interval.end:  # type: ignore[operator]
            break
        seconds = overlap_seconds(interval, window)
        if seconds > 0:
            shares[index] = task.actual_minutes * seconds / total  # type: ignore[operator]
    return shares


def distribute(tasks: Iterable[CareTask], grid: Sequence[Interval]) -> list[WindowObservation]:
    """Tamamlanan görevlerin dakikalarını pencerelere orantılı dağıtır. İptal ve bekleyenler katılmaz."""
    minutes = [0.0] * len(grid)
    counts = [0] * len(grid)
    available = [w.end for w in grid]
    for task in tasks:
        for index, share in task_shares(task, grid).items():
            minutes[index] += share
            counts[index] += 1
            available[index] = max(available[index], ensure_utc(task.completed_at))  # type: ignore[arg-type]
    return [
        WindowObservation(window, minutes[i], counts[i], available[i])  # type: ignore[arg-type]
        for i, window in enumerate(grid)
    ]


def pending_at(tasks: Iterable[CareTask], at: datetime) -> PendingWork:
    """`at` anında bilinen ve bitmemiş işler (estimated_minutes; katsayı yeniden uygulanmaz).

    Zaman semantiği (yalnız `at` anındaki bilgi; sonraki olaylar geçmişe sızdırılmaz):
    - created_at > at: görev henüz bilinmiyordu → dahil değil.
    - tamamlanan: completed_at > at ise o anda açıktı → bekleyen.
    - iptal, zamanı biliniyor: cancelled_at > at ise o anda açıktı (iptal henüz olmamıştı) → bekleyen;
      cancelled_at <= at ise kapanmıştı → dahil değil. Sonradan iptal edileceği bilgisi kullanılmaz.
    - iptal, zamanı bilinmiyor (K3): vade >= at ise açık olup olmadığı belirsiz → ayrı bildirilir.
    """
    at = ensure_utc(at, "Değerlendirme anı")
    pending: list[CareTask] = []
    uncertain: list[CareTask] = []
    for task in tasks:
        if ensure_utc(task.created_at) > at:
            continue
        if task.status == "cancelled":
            if task.cancelled_at is not None:
                if ensure_utc(task.cancelled_at, "İptal zamanı") > at:
                    pending.append(task)
            elif ensure_utc(task.due_at) >= at:
                uncertain.append(task)
            continue
        if task.status == "pending" or (task.completed_at is not None and ensure_utc(task.completed_at) > at):
            pending.append(task)
    pending.sort(key=lambda t: (t.due_at, t.id))
    return PendingWork(at, sum(t.estimated_minutes for t in pending), tuple(pending), tuple(uncertain))


def patient_count_at(encounters: Iterable[Encounter], at: datetime) -> int:
    """`at` anında serviste açık başvuru sayısı (açıklayıcı değişken; iş yükü ölçüsü değildir)."""
    at = ensure_utc(at, "Değerlendirme anı")
    return sum(
        1
        for e in encounters
        if ensure_utc(e.admitted_at) <= at and (e.discharged_at is None or ensure_utc(e.discharged_at) > at)
    )
