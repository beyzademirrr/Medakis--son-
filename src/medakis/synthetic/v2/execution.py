"""Kapasiteli yürütme (yürütme katmanı): görev ne zaman, kim tarafından yapılabildi.

Sabit hizmet sırası kuralı uygulanır: görevler (serbest bırakılma anı, öncelik, kimlik) sırasıyla ele
alınır; her görev gerekli rol ve yetkinliğe sahip, o servisteki, en erken boşalan personele verilir.
Amaç fonksiyonu yoktur; kadro, vardiya ve servisler arası dağılım DEĞİŞTİRİLMEZ (Aşama 5'in işidir).

Kullanılabilir zaman, Aşama 3 kapasite fonksiyonuyla (`domain.capacity.staff_capacity`) bulunur:
vardiya ∩ plan − somut mola/izin birleşimi. `break_minutes` ikinci kez düşülmez.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from medakis.domain.capacity import Unavailability, staff_capacity
from medakis.domain.models import Staff, StaffShift
from medakis.domain.time import Interval
from medakis.synthetic.v2 import profiles as P
from medakis.synthetic.v2.model import TaskMeta, rng_for
from medakis.synthetic.v2.profiles import ParamsV2

Tables = dict[str, list[dict[str, Any]]]


class FreeList:
    """Bir personelin boş zamanı: sıralı, örtüşmeyen [başlangıç, bitiş) dakika aralıkları."""

    def __init__(self, intervals: list[tuple[int, int]]):
        self.starts = [s for s, _ in intervals]
        self.ends = [e for _, e in intervals]
        self.booked = 0

    def earliest(self, release: int, duration: int, latest_start: int) -> int | None:
        i = max(0, bisect.bisect_right(self.starts, release) - 1)
        while i < len(self.starts):
            begin = max(self.starts[i], release)
            if begin > latest_start:
                return None
            if self.ends[i] - begin >= duration:
                return begin
            i += 1
        return None

    def book(self, begin: int, duration: int) -> None:
        i = bisect.bisect_right(self.starts, begin) - 1
        s, e = self.starts[i], self.ends[i]
        assert s <= begin and begin + duration <= e
        pieces = [(a, b) for a, b in ((s, begin), (begin + duration, e)) if b > a]
        self.starts[i:i + 1] = [a for a, _ in pieces]
        self.ends[i:i + 1] = [b for _, b in pieces]
        self.booked += duration


@dataclass(frozen=True)
class Context:
    start: datetime
    now: datetime
    horizon: datetime

    def to_min(self, at: datetime) -> int:
        return math.ceil((at - self.start).total_seconds() / 60)

    def to_dt(self, minute: int) -> datetime:
        return self.start + timedelta(minutes=minute)


def _free_lists(t: Tables, ctx: Context) -> dict[int, FreeList]:
    plan = Interval(ctx.start, ctx.horizon + timedelta(days=1))
    shifts_by: dict[int, list[StaffShift]] = defaultdict(list)
    for s in t["staff_shifts"]:
        shifts_by[s["staff_id"]].append(
            StaffShift(s["id"], s["staff_id"], s["unit_id"], s["starts_at"], s["ends_at"], s["break_minutes"])
        )
    unav_by: dict[int, list[Unavailability]] = defaultdict(list)
    for u in t["staff_unavailability"]:
        unav_by[u["staff_id"]].append(Unavailability(u["staff_id"], Interval(u["starts_at"], u["ends_at"]), u["reason"]))
    out = {}
    for row in t["staff"]:
        person = Staff(row["id"], row["synthetic_code"], row["role"], row["active"])
        cap = staff_capacity(person, shifts_by[row["id"]], unav_by[row["id"]], plan)
        out[row["id"]] = FreeList([(ctx.to_min(i.start), ctx.to_min(i.end)) for i in cap.available])
    return out


def execute(params: ParamsV2, t: Tables, ctx: Context, unit_kind: dict[int, str], priorities: dict[int, int],
            discharge_of: dict[int, datetime | None], unit_of_staff: dict[int, int]) -> dict[int, TaskMeta]:  # fmt: skip
    """`care_tasks` satırlarını yerinde tamamlar (durum, gerçek süre, tamamlanma) ve ek kayıtları döndürür."""
    types = {tt["id"]: tt for tt in t["task_types"]}
    skill_code = {s["id"]: s["code"] for s in t["skills"]}
    needs: dict[int, list[str]] = defaultdict(list)
    for r in t["task_skill_requirements"]:
        needs[r["task_type_id"]].append(skill_code[r["skill_id"]])
    validity: dict[tuple[int, str], tuple[datetime, datetime | None]] = {}
    for r in t["staff_skills"]:
        validity[(r["staff_id"], skill_code[r["skill_id"]])] = (r["valid_from"], r["valid_until"])
    role_of = {s["id"]: s["role"] for s in t["staff"]}
    pool: dict[tuple[int, str], list[int]] = defaultdict(list)
    for sid, unit_id in sorted(unit_of_staff.items()):
        pool[(unit_id, role_of[sid])].append(sid)
    free = _free_lists(t, ctx)

    tasks = sorted(t["care_tasks"], key=lambda r: r["id"])
    # Süreler ve klinik iptaller kadrodan bağımsız olarak, görev kimliği sırasıyla önceden çekilir.
    draws = {}
    tz = ZoneInfo(params.timezone)
    rngs: dict[int, np.random.Generator] = {}  # servis başına ayrı akış
    for row in tasks:
        rng = rngs.setdefault(row["unit_id"], rng_for(params, "execution", row["unit_id"]))
        ratio = float(rng.lognormal(0.0, P.ACTUAL_SIGMA))
        due_local_hour = row["due_at"].astimezone(tz).hour
        if P.NIGHT_HOURS[0] <= due_local_hour < P.NIGHT_HOURS[1]:
            ratio *= P.NIGHT_FACTOR
        ratio = float(np.clip(ratio, *P.ACTUAL_RATIO_CLIP))
        draws[row["id"]] = (max(1.0, round(row["estimated_minutes"] * ratio, 2)), rng.random(), rng.random())

    def release_of(row) -> datetime:
        kind = unit_kind[row["unit_id"]]
        return max(row["created_at"], row["due_at"] - timedelta(minutes=P.EARLY_START_MIN[kind]))

    meta: dict[int, TaskMeta] = {}
    order = sorted(tasks, key=lambda r: (release_of(r), priorities[r["id"]], r["id"]))
    for row in order:
        tid = row["id"]
        kind = unit_kind[row["unit_id"]]
        actual, cancel_draw, cancel_pos = draws[tid]
        release = release_of(row)
        base = dict(task_id=tid, unit_kind=kind, priority=priorities[tid], release_at=release, actual_minutes=actual)

        def cancel(outcome: str, at: datetime) -> None:
            if at >= ctx.now:
                meta[tid] = TaskMeta(**base, outcome="pending")
                return
            reason = outcome.removeprefix("cancel_")
            row.update(status="cancelled", completed_at=None, actual_minutes=None, cancelled_at=at, cancel_reason=reason)
            meta[tid] = TaskMeta(**base, outcome=outcome, cancelled_at=at, cancel_reason=reason)

        row.update(status="pending", completed_at=None, actual_minutes=None, cancelled_at=None, cancel_reason=None)
        if cancel_draw < params.clinical_cancel_rate:
            span = (row["due_at"] - row["created_at"]).total_seconds()
            at = row["created_at"] + timedelta(seconds=round(span * cancel_pos))
            cancel("cancel_clinical", max(at, row["created_at"]))
            continue

        duration = math.ceil(actual)
        rel = ctx.to_min(release)
        due = ctx.to_min(row["due_at"])
        latest = due + P.GIVE_UP_AFTER_MIN[kind]
        ttype = types[row["task_type_id"]]
        best: tuple[int, int, int] | None = None
        for sid in pool[(row["unit_id"], ttype["required_role"])]:
            begin = free[sid].earliest(rel, duration, latest)
            if begin is None:
                continue
            if not _skills_ok(validity, sid, needs[row["task_type_id"]], ctx.to_dt(begin), ctx.to_dt(begin + duration)):
                continue
            key = (begin, free[sid].booked, sid)
            if best is None or key < best:
                best = key
        discharge = discharge_of[row["encounter_id"]]
        if best is None:
            give_up = ctx.to_dt(latest)
            if discharge is not None and discharge < give_up:
                cancel("cancel_discharged", max(discharge, row["created_at"]))
            else:
                cancel("cancel_capacity", give_up)
            continue
        begin, _, sid = best
        started = ctx.to_dt(begin)
        completed = started + timedelta(minutes=actual)
        if discharge is not None and completed > discharge:
            cancel("cancel_discharged", max(discharge, row["created_at"]))
            continue
        free[sid].book(begin, duration)
        if completed < ctx.now:
            row.update(status="completed", completed_at=completed, actual_minutes=actual)
            late = begin - due > P.LATE_AFTER_MIN[kind]
            meta[tid] = TaskMeta(**base, outcome="late" if late else "on_time", started_at=started, staff_id=sid)
        else:
            meta[tid] = TaskMeta(**base, outcome="pending", started_at=started if started < ctx.now else None,
                                 staff_id=sid)  # fmt: skip
    return meta


def _skills_ok(validity, sid: int, codes: list[str], begin: datetime, end: datetime) -> bool:
    for code in codes:
        span = validity.get((sid, code))
        if span is None:
            return False
        start, until = span
        if start > begin or (until is not None and until < end):
            return False
    return True
