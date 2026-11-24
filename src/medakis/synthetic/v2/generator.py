"""Üretici 2.0 giriş noktası: takvim → yapı → başvurular → bakım düzeyi → talep → kadro → yürütme.

Talep katmanı (başvuru, bakım düzeyi, görev talebi) kadrodan önce ve ondan bağımsız üretilir;
kadro profili değiştiğinde talep özeti (`demand_sha256`) değişmez.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from medakis.repositories.schema import SEED_RESET_TABLES
from medakis.synthetic.v2 import profiles as P
from medakis.synthetic.v2.calendar import day_infos
from medakis.synthetic.v2.demand import estimate, plan_tasks
from medakis.synthetic.v2.encounters import emergency, inpatient, outpatient
from medakis.synthetic.v2.execution import Context, execute
from medakis.synthetic.v2.model import Appointment, DatasetV2, EdVisit, RejectedAdmission, rng_for
from medakis.synthetic.v2.profiles import GENERATOR_VERSION, ParamsV2
from medakis.synthetic.v2.staffing import (
    build_dictionaries,
    build_shifts,
    build_staff,
    build_structure,
    build_templates,
    unit_specs,
)

__all__ = ["GENERATOR_VERSION", "generate_v2"]


def generate_v2(params: ParamsV2 | None = None) -> DatasetV2:
    params = params or ParamsV2()
    tz = ZoneInfo(params.timezone)
    start = datetime.combine(params.start_day, time(0), tzinfo=tz).astimezone(UTC)
    end_day = params.start_day + timedelta(days=params.days)
    now = datetime.combine(end_day, time(0), tzinfo=tz).astimezone(UTC)
    horizon = now + timedelta(hours=params.pending_horizon_hours)
    warmup = params.inpatient_warmup_days
    calendar = day_infos(params.start_day - timedelta(days=warmup), warmup + params.days + 1)
    period = [d for d in calendar if params.start_day <= d.day < end_day]
    staff_days = [d for d in calendar if params.start_day <= d.day <= end_day]
    t: dict[str, list[dict]] = {name: [] for name in SEED_RESET_TABLES}

    specs = unit_specs(params)
    beds = build_structure(params, specs, t)
    _skill_ids, type_ids = build_dictionaries(t)
    templates = build_templates(params, t)

    # ---- Talep katmanı: başvurular ------------------------------------------------------------
    raw = []
    rejected: list[RejectedAdmission] = []
    calibration: dict[int, float] = {}
    inpatient_calendar = {d.day: d for d in calendar if d.day < end_day}
    for sub, u in enumerate(s for s in specs if s.kind == "inpatient"):
        enc, rej, factor = inpatient(params, u.id, sub, u.target or 0.0, beds[u.id], inpatient_calendar, start, now, tz)
        calibration[u.id] = factor
        raw += enc
        rejected += [RejectedAdmission(u.id, at) for at in rej]
    ed_unit = next(s for s in specs if s.kind == "emergency")
    op_unit = next(s for s in specs if s.kind == "outpatient")
    raw += emergency(params, ed_unit.id, beds[ed_unit.id], period, start, now, tz)
    op_encounters, raw_appointments = outpatient(params, op_unit.id, period, now, tz)
    raw += op_encounters

    order = sorted(range(len(raw)), key=lambda i: (raw[i].admitted_at, raw[i].unit_id, i))
    encounters = [raw[i] for i in order]
    eid_of = {id(e): n + 1 for n, e in enumerate(encounters)}
    for n, e in enumerate(encounters, start=1):
        t["patients"].append({"id": n, "synthetic_code": f"SYN{n:06d}", "is_synthetic": True})
        t["encounters"].append({"id": n, "patient_id": n, "unit_id": e.unit_id, "bed_id": e.bed_id,
                                "admitted_at": e.admitted_at, "discharged_at": e.discharged_at,
                                "triage_category": e.triage if e.kind == "emergency" else None})  # fmt: skip

    # ---- Talep katmanı: bakım düzeyi ----------------------------------------------------------
    acuity_rngs: dict[int, np.random.Generator] = {}  # servis başına ayrı akış
    assessments: dict[int, list[tuple[datetime, float]]] = {}
    for n, e in enumerate(encounters, start=1):
        rng = acuity_rngs.setdefault(e.unit_id, rng_for(params, "acuity", e.unit_id))
        level = e.acuity
        series = [(e.admitted_at, level)]
        if e.kind == "inpatient":
            day = e.admitted_at.astimezone(tz).date() + timedelta(days=1)
            stop = min(e.discharged_at or now, now)
            while True:
                moment = datetime.combine(day, time(8, 0), tzinfo=tz).astimezone(UTC)
                if moment >= stop:
                    break
                if rng.random() < P.ACUITY_DAILY_CHANGE_P:
                    level = int(np.clip(level + rng.choice([-1, 1]), 1, 5))
                series.append((moment, level))
                day += timedelta(days=1)
        assessments[n] = [(when, P.ACUITY_MULTIPLIERS[lvl]) for when, lvl in series]
        for when, lvl in series:
            t["acuity_assessments"].append({"id": len(t["acuity_assessments"]) + 1, "encounter_id": n,
                                            "assessed_at": when, "level": lvl, "multiplier": P.ACUITY_MULTIPLIERS[lvl]})  # fmt: skip

    # ---- Talep katmanı: görevler --------------------------------------------------------------
    demand = plan_tasks(params, encounters, start, now, horizon, tz)
    demand.sort(key=lambda d: (d.due_at, encounters[d.encounter_index].unit_id, d.encounter_index + 1, d.code, d.created_at))
    base = {row["id"]: row["base_minutes"] for row in t["task_types"]}
    priorities: dict[int, int] = {}
    for n, d in enumerate(demand, start=1):
        eid = d.encounter_index + 1
        type_id = type_ids[d.code]
        t["care_tasks"].append({
            "id": n, "encounter_id": eid, "unit_id": encounters[d.encounter_index].unit_id, "task_type_id": type_id,
            "created_at": d.created_at, "due_at": d.due_at, "estimated_minutes": estimate(base[type_id], assessments[eid], d.created_at),
            "actual_minutes": None, "status": "pending", "completed_at": None, "cancelled_at": None, "cancel_reason": None,
        })  # fmt: skip
        priorities[n] = d.priority

    # ---- Kadro (dışsal girdi) ve yürütme ------------------------------------------------------
    plan = build_staff(params, specs, _skill_ids, t, start)
    shortfalls, skipped = build_shifts(params, specs, plan, templates, staff_days, t)
    unit_kind = {u.id: u.kind for u in specs}
    discharge_of = {n: e.discharged_at for n, e in enumerate(encounters, start=1)}
    meta = execute(params, t, Context(start, now, horizon), unit_kind, priorities, discharge_of, plan.unit_of_staff)

    ed_visits = {
        eid_of[id(e)]: EdVisit(eid_of[id(e)], e.triage, e.ed_outcome, e.decision_at, e.bed_id is None)  # type: ignore[arg-type]
        for e in encounters
        if e.kind == "emergency"
    }
    appt_order = sorted(range(len(raw_appointments)),
                        key=lambda i: (raw_appointments[i].scheduled_at or raw_appointments[i].arrival_at, i))  # fmt: skip
    appointments = []
    for n, i in enumerate(appt_order, start=1):
        a = raw_appointments[i]
        appointments.append(Appointment(n, a.unit_id, a.scheduled_at, a.status, a.arrival_at, a.walk_in,
                                        eid_of[id(a.encounter)] if a.encounter is not None else None))  # fmt: skip

    return DatasetV2(
        params=params, tables=t, scenario_start=start, scenario_now=now, horizon_end=horizon, calendar=period,
        unit_profiles={u.id: (u.kind, u.profile, u.target) for u in specs},
        task_meta=meta, ed_visits=ed_visits, appointments=appointments, rejected_admissions=rejected,
        shortfalls=shortfalls, skipped_shifts=skipped, inpatient_rate_factors=calibration,
    )  # fmt: skip
