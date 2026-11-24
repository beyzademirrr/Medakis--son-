"""Üç senaryonun başvuru üretimi (talep katmanı): yataklı, acil, poliklinik.

Kadrodan bağımsızdır: hiçbir personel, vardiya veya kapasite bilgisi okunmaz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from medakis.synthetic.v2 import profiles as P
from medakis.synthetic.v2.calendar import DayInfo
from medakis.synthetic.v2.model import rng_for
from medakis.synthetic.v2.profiles import ParamsV2


@dataclass
class RawEncounter:
    unit_id: int
    kind: str
    bed_id: int | None
    admitted_at: datetime
    discharged_at: datetime | None
    acuity: int
    triage: str | None = None
    ed_outcome: str | None = None
    decision_at: datetime | None = None
    exam_end: datetime | None = None
    post_tasks: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawAppointment:
    unit_id: int
    scheduled_at: datetime | None
    status: str
    arrival_at: datetime | None
    walk_in: bool
    encounter: RawEncounter | None


def local_at(day: date, hour: float, tz: ZoneInfo) -> datetime:
    whole = int(hour * 60)
    return datetime.combine(day, time(whole // 60, whole % 60), tzinfo=tz).astimezone(UTC)


def minutes(value: float) -> timedelta:
    return timedelta(minutes=int(round(value)))


def erlang_b(servers: int, load: float) -> float:
    b = 1.0
    for k in range(1, servers + 1):
        b = load * b / (k + load * b)
    return b


def offered_load_for(target_occupancy: float, servers: int, occupied_share: float = 1.0) -> float:
    """Hasta dolu yatak payı hedefe eşit olacak sunulan yük (Erlang kayıp modeli).

    Yatak, hasta çıktıktan sonra hazırlık süresince de meşguldür: `occupied_share` = kalış /
    (kalış + hazırlık). Taşınan meşguliyetin yalnız bu payı doluluk sayılır.
    """
    want = target_occupancy * servers / occupied_share
    lo, hi = 0.0, 4.0 * servers
    for _ in range(100):
        mid = (lo + hi) / 2
        if mid * (1 - erlang_b(servers, mid)) < want:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def discharge_calendar_shift_days() -> float:
    """Taburculuk takvim kurallarının ham kalışa eklediği beklenen süre (gün).

    - Saat penceresi: ham taburculuk saati gün içine yaklaşık tekdüze dağılır (ortalama 12.00);
      pencere 10-16 ortalaması 13.00'tür → yaklaşık +1 saat.
    - Hafta sonu: ham gün cumartesi ise (1/7) 1 - KEEP olasılıkla pazartesiye (+2 gün), pazar ise
      (1/7) aynı olasılıkla pazartesiye (+1 gün) ertelenir (tek çekiliş).
    Kalibrasyon: ham log-normal ortalaması ALOS − bu kayma alınır; böylece ölçülen ortalama kalış
    kaynak değerine (4,0 gün) yaklaşır (G1 sapması +0,21 gün / +3,6 puan doluluk düzeltmesi).
    """
    lo, hi = P.INPATIENT_DISCHARGE_WINDOW_H
    window = ((lo + hi) / 2 - 12) / 24
    weekend = (1 - P.INPATIENT_WEEKEND_DISCHARGE_KEEP) * (2 + 1) / 7
    return window + weekend


def _day_factor(info: DayInfo) -> float:
    mean = sum(P.INPATIENT_WEEKDAY_FACTOR) / 7
    if info.is_full_holiday:
        return P.INPATIENT_HOLIDAY_FACTOR / mean
    return P.INPATIENT_WEEKDAY_FACTOR[info.weekday] / mean


@dataclass(frozen=True)
class _Candidate:
    """Baskın Poisson sürecinden önceden çekilmiş aday yatış (ortak rastgele sayılar)."""

    admitted: datetime
    accept: float
    stay_h: float
    windows: tuple[float, float, float, float]
    keep: float
    acuity: int


CALIBRATION_MAX_FACTOR = 2.0  # aday süreç, analitik hızın en çok bu katı
CALIBRATION_STEPS = 18


def inpatient(
    params: ParamsV2, unit_id: int, sub: int, target: float, beds: list[int],
    calendar: dict[date, DayInfo], start: datetime, now: datetime, tz: ZoneInfo,
) -> tuple[list[RawEncounter], list[datetime], float]:  # fmt: skip
    """Geliş tabanlı yatış; yatak doluysa yatış reddedilir (veritabanına yazılmaz).

    Kalibrasyon (G2): analitik hız Erlang kayıp modelinden gelir (hazırlık süresi dahil). Takvim
    dalgalanması ve örnekleme gürültüsü nedeniyle ölçülen doluluk hedeften sapabildiği için hız
    çarpanı, yalnız yatış süreci yeniden oynatılarak ikiye bölme ile hedefe oturtulur. Adaylar
    baskın bir süreçten bir kez çekilir ve inceltilir (ortak rastgele sayılar): çarpan arttıkça kabul
    edilen adaylar yalnız artar; sonuç seed başına deterministiktir. Görevler bu adımı etkilemez.
    Dönüş: (başvurular, reddedilen yatış zamanları, kullanılan hız çarpanı).
    """
    rng = rng_for(params, "inpatient", sub)
    busy_days = params.inpatient_alos_days + P.BED_TURNAROUND_MIN / 1440
    load = offered_load_for(target, len(beds), params.inpatient_alos_days / busy_days)
    daily = load / busy_days
    sigma = params.inpatient_los_sigma
    raw_mean_days = params.inpatient_alos_days - discharge_calendar_shift_days()
    mu = math.log(raw_mean_days * 24) - sigma**2 / 2
    weights = np.array(P.INPATIENT_ADMIT_HOUR_WEIGHTS, dtype=float)
    weights /= weights.sum()
    lo_h, hi_h = P.INPATIENT_DISCHARGE_WINDOW_H

    candidates: list[_Candidate] = []
    for day in sorted(calendar):
        drawn = []
        for _ in range(int(rng.poisson(CALIBRATION_MAX_FACTOR * daily * _day_factor(calendar[day])))):
            hour = int(rng.choice(24, p=weights)) + float(rng.uniform(0, 1))
            drawn.append(_Candidate(
                local_at(day, hour, tz), float(rng.random()),
                float(np.clip(rng.lognormal(mu, sigma), *P.INPATIENT_LOS_CLIP_H)),
                tuple(float(rng.uniform(lo_h, hi_h)) for _ in range(4)),  # type: ignore[arg-type]
                float(rng.random()), int(rng.choice(5, p=P.INPATIENT_ACUITY)) + 1,
            ))  # fmt: skip
        candidates += sorted(drawn, key=lambda c: c.admitted)

    def simulate(factor: float) -> tuple[list[RawEncounter], list[datetime], float]:
        threshold = factor / CALIBRATION_MAX_FACTOR
        bed_free = {b: datetime.min.replace(tzinfo=UTC) for b in beds}
        out: list[RawEncounter] = []
        rejected: list[datetime] = []
        occupied_h = 0.0
        for c in candidates:
            if c.accept >= threshold or c.admitted >= now:
                continue
            raw = (c.admitted + timedelta(hours=c.stay_h)).astimezone(tz)
            out_day, w = raw.date(), 0
            discharge = local_at(out_day, c.windows[w], tz)
            if discharge < c.admitted + timedelta(hours=P.INPATIENT_LOS_CLIP_H[0]):
                out_day += timedelta(days=1)
                w += 1
                discharge = local_at(out_day, c.windows[w], tz)
            while out_day.weekday() >= 5 and c.keep >= P.INPATIENT_WEEKEND_DISCHARGE_KEEP:
                out_day += timedelta(days=1)
                w = min(w + 1, 3)
                discharge = local_at(out_day, c.windows[w], tz)
            free = [b for b in beds if bed_free[b] <= c.admitted]
            if not free:
                if c.admitted >= start:
                    rejected.append(c.admitted)
                continue
            bed = free[0]
            bed_free[bed] = discharge + timedelta(minutes=P.BED_TURNAROUND_MIN)
            if discharge <= start:
                continue
            a, b = max(c.admitted, start), min(discharge, now)
            occupied_h += max(0.0, (b - a).total_seconds() / 3600)
            out.append(RawEncounter(unit_id, "inpatient", bed, c.admitted, None if discharge >= now else discharge, c.acuity))
        occupancy = occupied_h / (len(beds) * (now - start).total_seconds() / 3600)
        return out, rejected, occupancy

    lo, hi = 0.5, CALIBRATION_MAX_FACTOR
    best: tuple[float, float] | None = None  # (sapma, çarpan)
    for _ in range(CALIBRATION_STEPS):
        mid = (lo + hi) / 2
        occupancy = simulate(mid)[2]
        if best is None or abs(occupancy - target) < best[0]:
            best = (abs(occupancy - target), mid)
        if occupancy < target:
            lo = mid
        else:
            hi = mid
    assert best is not None
    out, rejected, _ = simulate(best[1])
    return out, rejected, best[1]


def emergency(
    params: ParamsV2, unit_id: int, beds: list[int], days: list[DayInfo], start: datetime, now: datetime, tz: ZoneInfo
) -> list[RawEncounter]:
    rng = rng_for(params, "emergency")
    by_day = {d.day: d for d in days}
    mix = P.TRIAGE_PROFILES[params.ed_triage_profile]
    levels = list(mix)
    probs = [mix[k] for k in levels]
    bed_free = {b: start for b in beds}
    out: list[RawEncounter] = []
    hour = start
    while hour < now:
        local = hour.astimezone(tz)
        info = by_day[local.date()]
        factor = P.ED_WEEKDAY_FACTOR[info.weekday]
        if info.is_full_holiday:
            factor *= P.ED_HOLIDAY_FACTOR
        elif info.after_holiday:
            factor *= P.ED_AFTER_HOLIDAY_FACTOR
        lam = (params.ed_base_arrivals_per_hour * P.ED_VOLUME_PROFILES[params.ed_volume_profile]
               * P.ED_HOURLY_PROFILE[local.hour] * factor)  # fmt: skip
        for _ in range(int(rng.poisson(lam))):
            admitted = hour + minutes(rng.uniform(0, 59))
            triage = levels[int(rng.choice(len(levels), p=probs))]
            acuity = int(rng.choice(P.TRIAGE_ACUITY[triage]))
            decision = float(rng.lognormal(math.log(P.TRIAGE_LOS_MEDIAN_H[triage] * 60), P.TRIAGE_LOS_SIGMA))
            outcome = ("discharge", "admit", "transfer")[int(rng.choice(3, p=P.TRIAGE_OUTCOME[triage]))]
            wait = float(rng.uniform(10, P.ED_MAX_TRANSFER_WAIT_MIN)) if outcome != "discharge" else 0.0
            total = float(np.clip(decision + wait, P.ED_MIN_STAY_MIN, P.ED_MAX_STAY_MIN))
            decision_part = max(1.0, total - wait)
            end = admitted + minutes(total)
            free = [b for b in beds if bed_free[b] <= admitted]
            bed = free[0] if free else None
            if bed is not None:
                bed_free[bed] = end
            out.append(RawEncounter(
                unit_id, "emergency", bed, admitted, None if end >= now else end, acuity,
                triage=triage, ed_outcome=outcome, decision_at=admitted + minutes(decision_part),
            ))  # fmt: skip
        hour += timedelta(hours=1)
    return out


def outpatient(
    params: ParamsV2, unit_id: int, days: list[DayInfo], now: datetime, tz: ZoneInfo
) -> tuple[list[RawEncounter], list[RawAppointment]]:
    """Randevu takvimi, gelmeme, geç gelme ve randevusuz başvuru. Tatil ve hafta sonu kapalı."""
    rng = rng_for(params, "outpatient")
    mean, sd, lo, hi = P.OUTPATIENT_LATENESS
    encounters: list[RawEncounter] = []
    appointments: list[RawAppointment] = []
    post_codes = list(P.OUTPATIENT_POST_TASK_P)

    def visit(arrival: datetime, slot: datetime) -> RawEncounter:
        exam_start = max(slot, arrival + timedelta(minutes=10))
        exam_end = exam_start + minutes(rng.uniform(*P.OUTPATIENT_EXAM_MIN))
        post = tuple(c for c in post_codes if rng.random() < P.OUTPATIENT_POST_TASK_P[c])
        leave = exam_end + timedelta(minutes=P.OUTPATIENT_POST_TASK_BUFFER_MIN if post else 5)
        acuity = int(rng.choice(5, p=P.OUTPATIENT_ACUITY)) + 1
        return RawEncounter(unit_id, "outpatient", None, arrival, None if leave >= now else leave, acuity,
                            exam_end=exam_end, post_tasks=post)  # fmt: skip

    for info in days:
        if not info.is_workday:
            continue
        sessions = [s for s in P.OUTPATIENT_SESSIONS if not (info.is_half_holiday and s[2] > 13)]
        attended = 0
        open_minutes: list[tuple[datetime, datetime]] = []
        for h0, m0, h1, m1 in sessions:
            s_start = local_at(info.day, h0 + m0 / 60, tz)
            s_end = local_at(info.day, h1 + m1 / 60, tz)
            open_minutes.append((s_start, s_end))
            count = int((s_end - s_start).total_seconds() // 60 // params.outpatient_slot_minutes)
            for _room in range(params.outpatient_rooms):
                for k in range(count):
                    slot = s_start + timedelta(minutes=k * params.outpatient_slot_minutes)
                    if rng.random() >= params.outpatient_fill_rate:
                        continue
                    if rng.random() < params.outpatient_no_show_rate:
                        appointments.append(RawAppointment(unit_id, slot, "no_show", None, False, None))
                        continue
                    arrival = slot + minutes(float(np.clip(rng.normal(mean, sd), lo, hi)))
                    if arrival >= now:
                        continue
                    enc = visit(arrival, slot)
                    encounters.append(enc)
                    appointments.append(RawAppointment(unit_id, slot, "attended", arrival, False, enc))
                    attended += 1
        for _ in range(int(rng.poisson(params.outpatient_walk_in_rate * attended))):
            s_start, s_end = open_minutes[int(rng.integers(0, len(open_minutes)))]
            arrival = s_start + minutes(rng.uniform(0, (s_end - s_start).total_seconds() / 60 - 30))
            if arrival >= now:
                continue
            enc = visit(arrival, arrival)
            encounters.append(enc)
            appointments.append(RawAppointment(unit_id, None, "attended", arrival, True, enc))
    return encounters, appointments
