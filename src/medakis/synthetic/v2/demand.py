"""Görev talebi (talep katmanı): hasta bakımı için ne, ne zaman gerekiyordu.

Kadrodan bağımsızdır. Görev `created_at` anı, planın bilindiği andır: en erken yatış, en geç vade
(created ≤ due). Tahmini süre = temel süre × o anda bilinen son bakım katsayısı (1.0.0 kuralı).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from medakis.domain.rules import estimated_minutes, latest_multiplier_at
from medakis.synthetic.v2 import profiles as P
from medakis.synthetic.v2.encounters import RawEncounter, local_at
from medakis.synthetic.v2.model import rng_for
from medakis.synthetic.v2.profiles import ParamsV2


@dataclass(frozen=True)
class DemandTask:
    encounter_index: int
    code: str
    due_at: datetime
    created_at: datetime
    priority: int


def _handover_hours(pattern: str) -> tuple[float, ...]:
    return (7.75, 19.75) if pattern == "12_12" else (7.75, 15.75)


def _days(e: RawEncounter, stop: datetime, tz: ZoneInfo):
    day: date = e.admitted_at.astimezone(tz).date()
    while datetime.combine(day, time(0), tzinfo=tz) < stop:
        yield day
        day += timedelta(days=1)


def plan_tasks(params: ParamsV2, encounters: list[RawEncounter], start: datetime, now: datetime,
               horizon: datetime, tz: ZoneInfo) -> list[DemandTask]:  # fmt: skip
    rngs: dict[int, object] = {}  # servis başına ayrı akış: bir senaryodaki değişiklik diğerini kaydırmaz
    out: list[DemandTask] = []
    for index, e in enumerate(encounters):
        rng = rngs.setdefault(e.unit_id, rng_for(params, "demand", e.unit_id))
        stop = min(e.discharged_at or horizon, horizon)
        events: list[tuple[str, datetime]] = []
        if e.kind == "inpatient":
            has_iv, has_wound = rng.random() < P.INPATIENT_IV_P, rng.random() < P.INPATIENT_WOUND_P
            plan = [("VITAL", h) for h in (0, 4, 8, 12, 16, 20)] + [("MED", h) for h in (9, 13, 21)]
            plan += [("HYGIENE", 7), ("MOBIL", 10), ("MOBIL", 16), ("ROUND", 9)]
            plan += [("IV", h) for h in (10, 22)] if has_iv else []
            plan += [("WOUND", 11)] if has_wound else []
            for h in _handover_hours(params.inpatient_shift_pattern):
                plan += [("HANDOVER", h), ("HANDOVER_SUP", h)]
            for day in _days(e, stop, tz):
                day_plan = plan + ([("SAMPLE", 6)] if rng.random() < P.INPATIENT_SAMPLE_P else [])
                events += [(code, local_at(day, hour, tz)) for code, hour in day_plan]
            events += [("ADMIT", e.admitted_at + timedelta(minutes=10)), ("TRANSF", e.admitted_at + timedelta(minutes=5))]
            if e.discharged_at is not None:
                events += [("DISCH", e.discharged_at - timedelta(minutes=60)),
                           ("TRANSF", e.discharged_at - timedelta(minutes=15))]  # fmt: skip
            priority = 1
        elif e.kind == "emergency":
            tri = e.triage or "green"
            events.append(("TRIAGE", e.admitted_at + timedelta(minutes=5)))
            if rng.random() < P.TRIAGE_SAMPLE_P[tri]:
                events.append(("SAMPLE", e.admitted_at + timedelta(minutes=20)))
            if rng.random() < P.TRIAGE_MED_P[tri]:
                events.append(("MED", e.admitted_at + timedelta(minutes=30)))
            moment = e.admitted_at + timedelta(minutes=15)
            while moment < stop:
                events.append(("VITAL", moment))
                moment += timedelta(minutes=P.TRIAGE_VITAL_INTERVAL_MIN[tri])
            for day in _days(e, stop, tz):
                events += [("ROUND", local_at(day, 10, tz)), ("ROUND", local_at(day, 16, tz))]
                events += [("HANDOVER", local_at(day, 7.75, tz)), ("HANDOVER", local_at(day, 19.75, tz))]
                events.append(("HANDOVER_SUP", local_at(day, 19.75, tz)))
            if e.ed_outcome in ("admit", "transfer") and e.discharged_at is not None:
                events.append(("TRANSF", e.discharged_at - timedelta(minutes=20)))
            priority = P.TRIAGE_PRIORITY[tri]
        else:
            events += [("ADMIT_P", e.admitted_at), ("VITAL", e.admitted_at + timedelta(minutes=2))]
            assert e.exam_end is not None
            events += [(code, e.exam_end + timedelta(minutes=1 + 2 * i)) for i, code in enumerate(e.post_tasks)]
            priority = 1
        for code, due in events:
            if not (e.admitted_at <= due < stop) or due < start or due >= horizon:
                continue
            lookahead = timedelta(hours=P.TASK_LOOKAHEAD_H)
            created = max(e.admitted_at, due - lookahead)
            if created > now:  # t anından sonra açıklanan plan üretilmez
                continue
            out.append(DemandTask(index, code, due, created, priority))
    return out


def estimate(base: float, assessments: list[tuple[datetime, float]], created: datetime) -> float:
    return estimated_minutes(base, latest_multiplier_at(assessments, created))
