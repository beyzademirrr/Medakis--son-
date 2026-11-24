"""Deterministik sentetik operasyon verisi üreticisi (saf fonksiyon).

Veritabanına bağlanmaz, gerçek saati okumaz, küresel rastgelelik kullanmaz. Her veri akışı
kendi tohumundan türetilir: bir akıştaki değişiklik diğerlerini kaydırmaz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from medakis.domain.rules import estimated_minutes, latest_multiplier_at
from medakis.domain.time import Interval, shift_from_template
from medakis.repositories.schema import SEED_RESET_TABLES
from medakis.synthetic.canonical import content_sha256
from medakis.synthetic.params import ACUITY_MULTIPLIERS, SKILLS, TASK_TYPES, SyntheticParams

STREAMS = {
    "inpatient": 1, "emergency": 2, "outpatient": 3, "acuity": 4, "tasks": 5,
    "skills": 6, "shifts": 7, "breaks": 8, "leave": 9,
}  # fmt: skip
ACUITY_DISTRIBUTION = {
    "inpatient": [0.15, 0.35, 0.30, 0.15, 0.05],
    "emergency": [0.10, 0.30, 0.35, 0.18, 0.07],
    "outpatient": [0.50, 0.40, 0.10, 0.0, 0.0],
}
# Saat başına acil başvuru yoğunluğu çarpanı (yerel saat; sentetik varsayım)
EMERGENCY_PROFILE = [0.4, 0.3, 0.3, 0.3, 0.3, 0.4, 0.6, 0.8, 1.0, 1.1, 1.2, 1.2,
                     1.2, 1.1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.5, 1.4, 1.2, 0.9, 0.6]  # fmt: skip
UNITS = ((1, "Yatakli Servis", "inpatient"), (2, "Acil Servis", "emergency"), (3, "Ayaktan Klinik", "outpatient"))


@dataclass(frozen=True)
class SyntheticDataset:
    params: SyntheticParams
    tables: dict[str, list[dict[str, Any]]]
    scenario_start: datetime
    scenario_now: datetime

    def content_sha256(self) -> str:
        return content_sha256(self.tables)

    def row_counts(self) -> dict[str, int]:
        return {t: len(self.tables[t]) for t in SEED_RESET_TABLES}


def _rng(params: SyntheticParams, stream: str) -> np.random.Generator:
    return np.random.default_rng([params.seed, STREAMS[stream]])


def _minutes(value: float) -> timedelta:
    return timedelta(minutes=int(round(value)))


def generate(params: SyntheticParams | None = None) -> SyntheticDataset:
    params = params or SyntheticParams()
    tz = ZoneInfo(params.timezone)
    start = datetime.combine(params.start_day, time(0), tzinfo=tz).astimezone(UTC)
    now = datetime.combine(params.start_day + timedelta(days=params.days), time(0), tzinfo=tz).astimezone(UTC)
    t: dict[str, list[dict[str, Any]]] = {name: [] for name in SEED_RESET_TABLES}

    # ---- Fiziksel yapı -----------------------------------------------------------------------
    t["facilities"].append({"id": 1, "name": "SYN Egitim Tesisi", "timezone": params.timezone})
    for uid, name, kind in UNITS:
        t["units"].append({"id": uid, "facility_id": 1, "name": name, "kind": kind, "active": True})
    beds_by_unit: dict[int, list[int]] = {1: [], 2: []}

    def add_room(unit_id: int, code: str, bed_count: int) -> None:
        room_id = len(t["rooms"]) + 1
        t["rooms"].append({"id": room_id, "unit_id": unit_id, "code": code})
        for letter in "ABCDEFGH"[:bed_count]:
            bed_id = len(t["beds"]) + 1
            t["beds"].append({"id": bed_id, "room_id": room_id, "code": f"{code}-{letter}", "active": True})
            beds_by_unit[unit_id].append(bed_id)

    for i in range(params.inpatient_rooms):
        add_room(1, f"Y-{i + 1:02d}", params.beds_per_room)
    for i in range(math.ceil(params.emergency_beds / 2)):
        add_room(2, f"A-{i + 1:02d}", min(2, params.emergency_beds - 2 * i))
    add_room(3, "P-01", 0)

    # ---- Başvurular ---------------------------------------------------------------------------
    encounters: list[dict[str, Any]] = []

    def add_encounter(unit_id: int, kind: str, bed_id: int | None, admitted: datetime, discharged: datetime | None):
        patient_id = len(t["patients"]) + 1
        t["patients"].append({"id": patient_id, "synthetic_code": f"SYN{patient_id:06d}", "is_synthetic": True})
        encounters.append({
            "id": len(encounters) + 1, "patient_id": patient_id, "unit_id": unit_id, "bed_id": bed_id,
            "admitted_at": admitted, "discharged_at": discharged, "_kind": kind,
        })  # fmt: skip

    rng = _rng(params, "inpatient")
    for bed_id in beds_by_unit[1]:
        cursor = start + _minutes(rng.uniform(0, 360))
        while cursor < now:
            stay_hours = float(np.clip(rng.lognormal(math.log(72), 0.5), 12, 14 * 24))
            end = cursor + _minutes(stay_hours * 60)
            if end >= now:
                add_encounter(1, "inpatient", bed_id, cursor, None)
                break
            add_encounter(1, "inpatient", bed_id, cursor, end)
            cursor = end + _minutes(rng.uniform(60, 18 * 60))

    rng = _rng(params, "emergency")
    bed_free_at = {b: start for b in beds_by_unit[2]}
    for hour in range(params.days * 24):
        hour_start = start + timedelta(hours=hour)
        local_hour = hour_start.astimezone(tz).hour
        for _ in range(int(rng.poisson(params.emergency_arrivals_per_hour * EMERGENCY_PROFILE[local_hour]))):
            admitted = hour_start + _minutes(rng.uniform(0, 59))
            end = admitted + _minutes(rng.uniform(120, 480))
            free = [b for b in beds_by_unit[2] if bed_free_at[b] <= admitted]
            bed = free[0] if free else None
            discharged = None if end >= now else end
            if bed is not None:
                bed_free_at[bed] = datetime.max.replace(tzinfo=UTC) if discharged is None else discharged
            add_encounter(2, "emergency", bed, admitted, discharged)

    rng = _rng(params, "outpatient")
    for offset in range(params.days):
        day = params.start_day + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        opening = datetime.combine(day, time(9, 0), tzinfo=tz).astimezone(UTC)
        visits = sorted(float(x) for x in rng.uniform(0, 450, params.outpatient_visits_per_weekday))
        for minute in visits:
            admitted = opening + _minutes(minute)
            add_encounter(3, "outpatient", None, admitted, admitted + _minutes(rng.uniform(30, 90)))

    encounters.sort(key=lambda e: (e["admitted_at"], e["unit_id"], e["id"]))
    renumber = {e["id"]: i + 1 for i, e in enumerate(encounters)}
    for e in encounters:
        e["id"] = renumber[e["id"]]
    t["encounters"] = [{k: v for k, v in e.items() if not k.startswith("_")} for e in encounters]

    # ---- Bakım gereksinimi değerlendirmeleri ---------------------------------------------------
    rng = _rng(params, "acuity")
    assessments: dict[int, list[tuple[datetime, float]]] = {}
    for e in encounters:
        level = int(rng.choice(5, p=ACUITY_DISTRIBUTION[e["_kind"]])) + 1
        series = [(e["admitted_at"], level)]
        if e["_kind"] == "inpatient":
            day = e["admitted_at"].astimezone(tz).date() + timedelta(days=1)
            stop = e["discharged_at"] or now
            while True:
                moment = datetime.combine(day, time(8, 0), tzinfo=tz).astimezone(UTC)
                if moment >= stop or moment >= now:
                    break
                if rng.random() < 0.3:
                    level = int(np.clip(level + rng.choice([-1, 1]), 1, 5))
                series.append((moment, level))
                day += timedelta(days=1)
        assessments[e["id"]] = [(when, ACUITY_MULTIPLIERS[lvl]) for when, lvl in series]
        for when, lvl in series:
            t["acuity_assessments"].append({
                "id": len(t["acuity_assessments"]) + 1, "encounter_id": e["id"], "assessed_at": when,
                "level": lvl, "multiplier": ACUITY_MULTIPLIERS[lvl],
            })  # fmt: skip

    # ---- Personel, yetkinlik, şablon -----------------------------------------------------------
    pools: dict[str, list[int]] = {}
    for key, size in params.staff_pool.items():
        kind, role = key.split(":")
        pools[key] = []
        for _ in range(size):
            sid = len(t["staff"]) + 1
            t["staff"].append({"id": sid, "synthetic_code": f"STAFF{sid:04d}", "role": role, "active": True})
            pools[key].append(sid)
    skill_ids = {code: i + 1 for i, code in enumerate(SKILLS)}
    for code, name in SKILLS.items():
        t["skills"].append({"id": skill_ids[code], "code": code, "name": name})

    rng = _rng(params, "skills")
    valid_from = start - timedelta(days=365)
    expiring = set(pools["inpatient:nurse"][:2])  # iki hemşirenin IV yetkinliği senaryo içinde sona erer
    for key, members in pools.items():
        kind, role = key.split(":")
        for sid in members:
            granted: list[str] = []
            if role == "nurse":
                granted = ["MON", "MED"]
                if rng.random() < 0.8 or sid in expiring:
                    granted.append("IV")
                if rng.random() < 0.6:
                    granted.append("WOUND")
                if kind == "emergency" or rng.random() < 0.2:
                    granted.append("TRIAGE")
            elif role == "supervisor":
                granted = ["MON", "MED"]
            for code in granted:
                until = start + timedelta(days=20) if (code == "IV" and sid in expiring) else None
                t["staff_skills"].append(
                    {"staff_id": sid, "skill_id": skill_ids[code], "valid_from": valid_from, "valid_until": until}
                )

    templates = {"day": (1, "GUNDUZ", time(8, 0), 720), "night": (2, "GECE", time(20, 0), 720),
                 "clinic": (3, "POLIKLINIK", time(8, 30), 540)}  # fmt: skip
    for tid, name, start_local, duration in templates.values():
        t["shift_templates"].append({"id": tid, "name": name, "start_local": start_local, "duration_minutes": duration})

    for code, (name, base, role, needed) in TASK_TYPES.items():
        tid = len(t["task_types"]) + 1
        t["task_types"].append({"id": tid, "code": code, "name": name, "base_minutes": base, "required_role": role,
                                "duration_source": "synthetic_assumption"})  # fmt: skip
        for skill in needed:
            t["task_skill_requirements"].append({"task_type_id": tid, "skill_id": skill_ids[skill]})
    type_ids = {row["code"]: row["id"] for row in t["task_types"]}

    # ---- Vardiyalar ve molalar -----------------------------------------------------------------
    # 7 günlük döngü: 2 gündüz, 2 gece, 3 izin. Kişiler döngüye kaydırılarak yerleşir.
    cycle = ("day", "day", "night", "night", None, None, None)
    rng = _rng(params, "shifts")
    brng = _rng(params, "breaks")
    unit_of_kind = {kind: uid for uid, _, kind in UNITS}
    shifts_by_staff: dict[int, list[Interval]] = {}
    for offset in range(params.days + 1):  # son gün: planlama ufku için ertesi günün vardiyaları
        day = params.start_day + timedelta(days=offset)
        for key, members in pools.items():
            kind, role = key.split(":")
            if kind == "outpatient":
                if day.weekday() >= 5:
                    continue
                need = params.shift_staffing.get(f"{key}:clinic", 0)
                planned = [(members[(offset + j) % len(members)], "clinic") for j in range(min(need, len(members)))]
            else:
                planned = []
                for slot in ("day", "night"):
                    need = params.shift_staffing.get(f"{key}:{slot}", 0)
                    available = [sid for i, sid in enumerate(members) if cycle[(offset + i) % 7] == slot]
                    planned += [(sid, slot) for sid in available[:need]]
            for sid, slot in planned:
                if rng.random() < 0.03:  # planlanmış ama gerçekleşmeyen vardiya: kadro açığı dönemleri
                    continue
                tid, _, start_local, duration = templates[slot]
                shift = shift_from_template(day, start_local, duration, params.timezone)
                shift_id = len(t["staff_shifts"]) + 1
                t["staff_shifts"].append({
                    "id": shift_id, "staff_id": sid, "unit_id": unit_of_kind[kind], "template_id": tid,
                    "starts_at": shift.start, "ends_at": shift.end, "break_minutes": params.break_minutes,
                })  # fmt: skip
                shifts_by_staff.setdefault(sid, []).append(shift)
                break_start = shift.start + timedelta(hours=5) + _minutes(15 * int(brng.integers(0, 5)))
                t["staff_unavailability"].append({
                    "id": len(t["staff_unavailability"]) + 1, "staff_id": sid, "starts_at": break_start,
                    "ends_at": break_start + timedelta(minutes=params.break_minutes), "reason": "Mola",
                })  # fmt: skip

    lrng = _rng(params, "leave")
    for sid in sorted(shifts_by_staff):
        for offset in range(params.days):
            if lrng.random() >= 0.05:
                continue
            day = params.start_day + timedelta(days=offset)
            leave = Interval(
                datetime.combine(day, time(8, 0), tzinfo=tz).astimezone(UTC),
                datetime.combine(day, time(20, 0), tzinfo=tz).astimezone(UTC),
            )
            if any(leave.overlaps(s) for s in shifts_by_staff[sid]):
                continue
            t["staff_unavailability"].append({
                "id": len(t["staff_unavailability"]) + 1, "staff_id": sid, "starts_at": leave.start,
                "ends_at": leave.end, "reason": "Izin",
            })  # fmt: skip

    # ---- Bakım görevleri -----------------------------------------------------------------------
    rng = _rng(params, "tasks")
    horizon = now + timedelta(hours=params.pending_horizon_hours)
    base_of = {row["id"]: row["base_minutes"] for row in t["task_types"]}

    def schedule(e: dict[str, Any]) -> list[tuple[str, datetime]]:
        events: list[tuple[str, datetime]] = []
        stop = e["discharged_at"] or horizon
        if e["_kind"] == "inpatient":
            has_iv, has_wound = rng.random() < 0.3, rng.random() < 0.2
            plan = [("VITAL", h) for h in (0, 4, 8, 12, 16, 20)] + [("MED", h) for h in (9, 13, 21)]
            plan += [("HYGIENE", 7), ("MOBIL", 10), ("MOBIL", 16), ("ROUND", 9)]
            plan += [("IV", h) for h in (10, 22)] if has_iv else []
            plan += [("WOUND", 11)] if has_wound else []
            day = e["admitted_at"].astimezone(tz).date()
            while True:
                day_start = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
                if day_start >= stop:
                    break
                for code, hour in plan:
                    events.append((code, datetime.combine(day, time(hour, 0), tzinfo=tz).astimezone(UTC)))
                day += timedelta(days=1)
        elif e["_kind"] == "emergency":
            events.append(("TRIAGE", e["admitted_at"] + timedelta(minutes=5)))
            if rng.random() < 0.6:
                events.append(("MED", e["admitted_at"] + timedelta(minutes=45)))
            moment = e["admitted_at"] + timedelta(minutes=30)
            while moment < stop:
                events.append(("VITAL", moment))
                moment += timedelta(minutes=60)
        else:
            events.append(("VITAL", e["admitted_at"] + timedelta(minutes=5)))
        return sorted(
            [(c, due) for c, due in events if e["admitted_at"] <= due < stop and due < horizon],
            key=lambda item: (item[1], item[0]),
        )

    for e in encounters:
        for code, due in schedule(e):
            # Rutin bakım planı en çok 12 saat önceden bilinir (yatıştan önce bilinemez).
            created = max(e["admitted_at"], due - timedelta(hours=12))
            if created > now:  # t anından sonra açıklanan kayıt üretilmez
                continue
            multiplier = latest_multiplier_at(assessments[e["id"]], created)
            estimate = estimated_minutes(base_of[type_ids[code]], multiplier)
            row = {
                "id": len(t["care_tasks"]) + 1, "encounter_id": e["id"], "unit_id": e["unit_id"],
                "task_type_id": type_ids[code], "created_at": created, "due_at": due,
                "estimated_minutes": estimate, "actual_minutes": None, "status": "pending", "completed_at": None,
            }  # fmt: skip
            if due < now:
                if rng.random() < 0.05:
                    row["status"] = "cancelled"
                else:
                    completed = due + _minutes(float(np.clip(rng.normal(5, 10), -15, 45)))
                    completed = max(completed, created)
                    if completed < now:
                        night = due.astimezone(tz).hour < 6
                        actual = estimate * float(rng.lognormal(0, 0.2)) * (1.15 if night else 1.0)
                        row.update(status="completed", completed_at=completed, actual_minutes=max(1.0, round(actual, 2)))
            t["care_tasks"].append(row)

    return SyntheticDataset(params=params, tables=t, scenario_start=start, scenario_now=now)
