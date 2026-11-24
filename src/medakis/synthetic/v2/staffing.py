"""Fiziksel yapı, görev sözlüğü, personel, yetkinlik, vardiya, somut mola ve izin üretimi.

Kadro ve vardiya planı DIŞSAL girdidir (profil tabloları); üretim boyunca değişmez, birikmiş işe göre
uyarlanmaz. Kapasite tanımı Aşama 3 ile aynıdır: somut mola ve izin aralıkları kapasiteden düşülür,
`break_minutes` yalnız özettir ve somut molaların toplamına eşit üretilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from medakis.domain.time import Interval, shift_from_template
from medakis.synthetic.v2 import profiles as P
from medakis.synthetic.v2.calendar import DayInfo
from medakis.synthetic.v2.model import StaffingShortfall, rng_for
from medakis.synthetic.v2.profiles import ParamsV2

Tables = dict[str, list[dict[str, Any]]]


@dataclass(frozen=True)
class UnitSpec:
    id: int
    name: str
    kind: str
    profile: str
    target: float | None
    pool_key: str  # staff_pool anahtar öneki


def unit_specs(params: ParamsV2) -> list[UnitSpec]:
    specs = [
        UnitSpec(i + 1, f"Yatakli Servis {chr(65 + i)}", "inpatient", name, target, "inpatient")
        for i, (name, target) in enumerate(params.inpatient_profiles)
    ]
    n = len(specs)
    specs.append(UnitSpec(n + 1, "Acil Servis", "emergency", params.ed_staffing_profile, None, "emergency"))
    specs.append(UnitSpec(n + 2, "Ayaktan Klinik", "outpatient", "randevu", None, "outpatient"))
    return specs


def build_structure(params: ParamsV2, specs: list[UnitSpec], t: Tables) -> dict[int, list[int]]:
    t["facilities"].append({"id": 1, "name": "SYN Egitim Tesisi 2", "timezone": params.timezone})
    beds: dict[int, list[int]] = {}
    for u in specs:
        t["units"].append({"id": u.id, "facility_id": 1, "name": u.name, "kind": u.kind, "active": True})
        beds[u.id] = []

    def room(unit_id: int, code: str, count: int) -> None:
        rid = len(t["rooms"]) + 1
        t["rooms"].append({"id": rid, "unit_id": unit_id, "code": code})
        for letter in "ABCDEFGH"[:count]:
            bid = len(t["beds"]) + 1
            t["beds"].append({"id": bid, "room_id": rid, "code": f"{code}-{letter}", "active": True})
            beds[unit_id].append(bid)

    for u in specs:
        if u.kind == "inpatient":
            for i in range(params.inpatient_rooms_per_unit):
                room(u.id, f"Y{u.id}-{i + 1:02d}", params.inpatient_beds_per_room)
        elif u.kind == "emergency":
            for i in range((params.ed_beds + 1) // 2):
                room(u.id, f"A-{i + 1:02d}", min(2, params.ed_beds - 2 * i))
        else:
            for i in range(params.outpatient_rooms):
                room(u.id, f"P-{i + 1:02d}", 0)
    return beds


def build_dictionaries(t: Tables) -> tuple[dict[str, int], dict[str, int]]:
    skill_ids = {code: i + 1 for i, code in enumerate(P.SKILLS)}
    for code, name in P.SKILLS.items():
        t["skills"].append({"id": skill_ids[code], "code": code, "name": name})
    for code, (name, base, role, needed) in P.TASK_TYPES.items():
        tid = len(t["task_types"]) + 1
        t["task_types"].append({"id": tid, "code": code, "name": name, "base_minutes": base, "required_role": role,
                                "duration_source": "synthetic_assumption"})  # fmt: skip
        for skill in needed:
            t["task_skill_requirements"].append({"task_type_id": tid, "skill_id": skill_ids[skill]})
    return skill_ids, {row["code"]: row["id"] for row in t["task_types"]}


@dataclass(frozen=True)
class Template:
    id: int
    name: str
    start: time
    minutes: int
    break_slices: int


def build_templates(params: ParamsV2, t: Tables) -> dict[str, dict[str, Template]]:
    """Senaryo türü -> vardiya adı -> şablon. Acil her zaman 12/12 kullanır."""
    out: dict[str, dict[str, Template]] = {}
    by_name: dict[str, Template] = {}

    def template(name: str, hour: int, minutes: int, slices: int) -> Template:
        if name not in by_name:
            by_name[name] = Template(len(by_name) + 1, name, time(hour, 0), minutes, slices)
            t["shift_templates"].append(
                {"id": by_name[name].id, "name": name, "start_local": time(hour, 0), "duration_minutes": minutes}
            )
        return by_name[name]

    out["inpatient"] = {slot: template(*spec) for slot, spec in P.SHIFT_PATTERNS[params.inpatient_shift_pattern].items()}
    out["emergency"] = {slot: template(*spec) for slot, spec in P.SHIFT_PATTERNS["12_12"].items()}
    out["outpatient"] = {"clinic": template("POLIKLINIK", 8, 540, 1), "half": template("POLIKLINIK_YARIM", 8, 300, 0)}
    return out


@dataclass
class StaffPlan:
    pools: dict[tuple[int, str], list[int]]  # (unit_id, rol) -> personel
    unit_of_staff: dict[int, int]


def build_staff(params: ParamsV2, specs: list[UnitSpec], skill_ids: dict[str, int], t: Tables,
                start: datetime) -> StaffPlan:  # fmt: skip
    pools: dict[tuple[int, str], list[int]] = {}
    unit_of: dict[int, int] = {}
    for u in specs:
        for key, size in params.staff_pool.items():
            kind, role = key.split(":")
            if kind != u.pool_key:
                continue
            members = []
            for _ in range(size):
                sid = len(t["staff"]) + 1
                t["staff"].append({"id": sid, "synthetic_code": f"STAFF{sid:04d}", "role": role, "active": True})
                members.append(sid)
                unit_of[sid] = u.id
            pools[(u.id, role)] = members

    valid_from = start - timedelta(days=365)
    first_inpatient = specs[0].id
    expiring = set(pools.get((first_inpatient, "nurse"), [])[:2])  # iki hemşirenin IV yetkinliği dönem içinde biter
    kind_of = {u.id: u.kind for u in specs}
    for (unit_id, role), members in sorted(pools.items()):
        kind = kind_of[unit_id]
        rng = rng_for(params, "skills", unit_id * 10 + P.ROLES.index(role))
        for sid in members:
            granted: list[str] = []
            if role == "nurse":
                granted = ["MON", "MED", "SAMPLE"]
                if rng.random() < P.NURSE_SKILL_P["IV"] or sid in expiring:
                    granted.append("IV")
                if rng.random() < P.NURSE_SKILL_P["WOUND"]:
                    granted.append("WOUND")
                if kind == "emergency" or (kind == "inpatient" and rng.random() < P.NURSE_SKILL_P["TRIAGE_inpatient"]):
                    granted.append("TRIAGE")
            elif role == "supervisor":
                granted = ["MON", "MED"]
            for code in granted:
                until = start + timedelta(days=20) if (code == "IV" and sid in expiring) else None
                t["staff_skills"].append(
                    {"staff_id": sid, "skill_id": skill_ids[code], "valid_from": valid_from, "valid_until": until}
                )
    return StaffPlan(pools, unit_of)


def _need(kind: str, profile: str, role: str, slot: str, info: DayInfo) -> int:
    if kind == "inpatient":
        day, night, weekend_day = P.INPATIENT_STAFFING.get(role, (0, 0, 0))
        if slot == "night":
            return night
        return weekend_day if (info.is_weekend or info.is_full_holiday) else day
    day, night = P.ED_STAFFING_PROFILES[profile].get(role, (0, 0))
    return day if slot == "day" else night


def build_shifts(params: ParamsV2, specs: list[UnitSpec], plan: StaffPlan, templates: dict[str, dict[str, Template]],
                 calendar: list[DayInfo], t: Tables) -> tuple[list[StaffingShortfall], int]:  # fmt: skip
    """Rotasyonla planlı vardiyalar; ihtiyaç karşılanamazsa açık yalnız raporlanır (kadro değiştirilmez)."""
    # servis başına ayrı akışlar: bir servisin kadro profili diğer servislerin vardiyalarını kaydırmaz
    streams = {u.id: (rng_for(params, "shifts", u.id), rng_for(params, "breaks", u.id), rng_for(params, "leave", u.id))
               for u in specs}  # fmt: skip
    shortfalls: list[StaffingShortfall] = []
    skipped = 0
    for offset, info in enumerate(calendar):
        for u in specs:
            rng, brng, lrng = streams[u.id]
            for role in P.ROLES:
                members = plan.pools.get((u.id, role), [])
                if not members:
                    continue
                planned: list[tuple[int, Template, str]] = []
                if u.kind == "outpatient":
                    if not info.is_workday:
                        continue
                    tmpl = templates["outpatient"]["half" if info.is_half_holiday else "clinic"]
                    need = params.outpatient_nurses_per_day if role == "nurse" else 0
                    chosen = [members[(offset + j) % len(members)] for j in range(min(need, len(members)))]
                    planned = [(sid, tmpl, "clinic") for sid in chosen]
                    if need > len(chosen):
                        shortfalls.append(StaffingShortfall(u.id, info.day, "clinic", role, need, len(chosen)))
                else:
                    n = len(members)
                    for slot, tmpl in templates[u.kind].items():
                        need = _need(u.kind, u.profile, role, slot, info)
                        if need == 0:
                            continue
                        available = [sid for i, sid in enumerate(members) if P.ROTATION[(offset + (i * 7) // n) % 7] == slot]
                        chosen = available[:need]
                        planned += [(sid, tmpl, slot) for sid in chosen]
                        if len(chosen) < need:
                            shortfalls.append(StaffingShortfall(u.id, info.day, slot, role, need, len(chosen)))
                for sid, tmpl, _slot in planned:
                    if rng.random() < params.shift_no_show_rate:
                        skipped += 1
                        continue
                    shift = shift_from_template(info.day, tmpl.start, tmpl.minutes, params.timezone)
                    total_break = params.break_minutes_per_12h * tmpl.break_slices
                    t["staff_shifts"].append({
                        "id": len(t["staff_shifts"]) + 1, "staff_id": sid, "unit_id": u.id, "template_id": tmpl.id,
                        "starts_at": shift.start, "ends_at": shift.end, "break_minutes": total_break,
                    })  # fmt: skip
                    _breaks(params, shift, tmpl, sid, brng, t)
                    if lrng.random() < params.short_leave_rate:
                        _short_leave(params, shift, sid, lrng, t)
    return shortfalls, skipped


def _breaks(params: ParamsV2, shift: Interval, tmpl: Template, sid: int, brng, t: Tables) -> None:
    for j in range(tmpl.break_slices):
        center = tmpl.minutes * (j + 1) / (tmpl.break_slices + 1)
        offset = int(center // 15) * 15 - 30 + 15 * int(brng.integers(0, 5))
        offset = max(15, min(offset, tmpl.minutes - params.break_minutes_per_12h - 15))
        begin = shift.start + timedelta(minutes=offset)
        t["staff_unavailability"].append({
            "id": len(t["staff_unavailability"]) + 1, "staff_id": sid, "starts_at": begin,
            "ends_at": begin + timedelta(minutes=params.break_minutes_per_12h), "reason": "Mola",
        })  # fmt: skip


def _short_leave(params: ParamsV2, shift: Interval, sid: int, lrng, t: Tables) -> None:
    length = min(params.short_leave_minutes, int(shift.minutes()) - 30)
    if length <= 0:
        return
    slots = (int(shift.minutes()) - length) // 15
    begin = shift.start + timedelta(minutes=15 * int(lrng.integers(0, slots + 1)))
    t["staff_unavailability"].append({
        "id": len(t["staff_unavailability"]) + 1, "staff_id": sid, "starts_at": begin,
        "ends_at": begin + timedelta(minutes=length), "reason": "Izin",
    })  # fmt: skip
