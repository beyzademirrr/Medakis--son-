"""Aşama 5 / U6: Katman B bağımsız doğrulayıcısı.

Çözücüden (layer_b.py) ve OR-Tools'tan bağımsızdır; kuralları ham girdiden (known_view) yeniden kurar. Çözücünün
ara yapılarını, aday listesini veya zaman dilimlerini kullanmaz. Şartname Bölüm 6 kuralları (B1–B7):
  B1 bir görev en fazla bir personele        B5 pencere ve son tarih
  B2 aktif personel, rol ve yetkinlik         B6 görev, vardiya ve atama servisi aynı
  B3 vardiya içinde; mola/izin/görev çakışmaz B7 atanmayan görev kümesi, açık dakikası ve nedeni
  B4 süre yukarı yuvarlanmış tahmini süre
Ek olarak amaç değerleri, kapsam, rezerv ve özet yeniden hesaplanır.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from medakis.domain.capacity import clip, intersect, merge, subtract
from medakis.domain.planning import ACTIVE_STATUSES, ROLES, known_view, plan_problems
from medakis.domain.rules import skill_valid_throughout
from medakis.domain.time import Interval, ensure_utc
from medakis.optimization.layer_b_contracts import (
    GAP_CAPACITY,
    GAP_DEADLINE_PASSED,
    GAP_DEADLINE_TOO_CLOSE,
    GAP_NO_ELIGIBLE_STAFF,
    GAP_NO_SLOT,
    STAGES_B,
    LayerBInputs,
    input_problems,
    task_minutes,
    window_minutes,
)


@dataclass(frozen=True)
class TaskVerification:
    passed: bool
    violations: list[str]


def _hash(solution: Mapping[str, Any]) -> str:
    raw = json.dumps({k: v for k, v in solution.items() if k != "ozet_sha256"}, sort_keys=True, ensure_ascii=True, default=str,
                     separators=(",", ":"))  # fmt: skip
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _floor_min(at: datetime, t: datetime) -> int:
    return math.floor((t - at).total_seconds() / 60 + 1e-9)


def _ceil_min(at: datetime, t: datetime) -> int:
    return math.ceil((t - at).total_seconds() / 60 - 1e-9)


def verify_tasks(inp: LayerBInputs, solution: Mapping[str, Any]) -> TaskVerification:  # noqa: C901, PLR0912, PLR0915
    v: list[str] = []
    at = ensure_utc(inp.at, "Karar anı")
    if solution.get("karar_ani") != at:
        v.append("karar_ani_uyusmuyor")
    roles = {sid: s.role for sid, s in inp.staff.items()}
    view = known_view(inp.plan, at)
    v += [f"girdi_gecersiz:{p}" for p in input_problems(inp)]
    v += [f"girdi_gecersiz_plan:{p}" for p in plan_problems(view, roles, set(inp.units))]
    if v:
        return TaskVerification(False, sorted(set(v)))
    window = Interval(at, at + timedelta(minutes=window_minutes()))

    # -- Ham girdiden vardiya ve uygunsuzluk (çözücüden bağımsız) --
    shift_ivs: dict[tuple[int, int], list[Interval]] = {}
    for s in view.shifts:
        if s.status in ACTIVE_STATUSES and Interval(s.starts_at, s.ends_at).overlaps(window):
            shift_ivs.setdefault((s.staff_id, s.unit_id), []).append(Interval(s.starts_at, s.ends_at))
    unav: dict[int, list[Interval]] = {}
    for u in view.unavailability:
        unav.setdefault(u.staff_id, []).append(Interval(u.starts_at, u.ends_at))

    def free(sid: int, unit: int) -> list[Interval]:
        person = inp.staff.get(sid)
        if person is None or not person.active or (sid, unit) not in shift_ivs:
            return []
        return subtract(merge(clip(shift_ivs[(sid, unit)], window)), unav.get(sid, []))

    def validity(sid: int, skill: str) -> list[Interval]:
        return [Interval(s.valid_from, s.valid_until) for s in inp.skills if s.staff_id == sid and s.skill == skill
                and (s.valid_until is None or s.valid_until > s.valid_from)]  # fmt: skip

    uncertain = set(inp.uncertain_task_ids)
    scope = {t.task_id: t for t in inp.tasks if t.due_at <= window.end and t.task_id not in uncertain}
    later = [t for t in inp.tasks if t.due_at > window.end and t.task_id not in uncertain]
    dur = {tid: task_minutes(t.estimated_minutes) for tid, t in scope.items()}

    # -- Atamalar (B1–B6) --
    seen: set[int] = set()
    per_staff: dict[int, list[tuple[datetime, datetime, int]]] = {}
    for a in solution.get("atamalar", []):
        tid, sid = a.get("gorev"), a.get("personel")
        t = scope.get(tid)
        if t is None:
            v.append(f"B_kapsam_disi_gorev:{tid}")
            continue
        if tid in seen:
            v.append(f"B1_gorev_birden_fazla_atandi:{tid}")
        seen.add(tid)
        start, end = a.get("baslangic"), a.get("bitis")
        if not isinstance(start, datetime) or not isinstance(end, datetime) or end <= start:
            v.append(f"B4_gecersiz_aralik:{tid}")
            continue
        person = inp.staff.get(sid)
        if person is None or not person.active:
            v.append(f"B2_personel_aktif_degil:{tid}:{sid}")
        elif person.role != t.role or a.get("rol") != t.role:
            v.append(f"B2_rol_uyusmuyor:{tid}:{sid}")
        if a.get("servis") != t.unit_id:
            v.append(f"B6_servis_uyusmuyor:{tid}")
        if int(round((end - start).total_seconds() / 60)) != dur[tid] or a.get("sure_dk") != dur[tid] or \
                (end - start) != timedelta(minutes=dur[tid]):  # fmt: skip
            v.append(f"B4_sure_tutarsiz:{tid}")
        if start < at or end > window.end:
            v.append(f"B5_pencere_disi:{tid}")
        if end > t.due_at:
            v.append(f"B5_son_tarih_asildi:{tid}")
        work = Interval(start, end)
        if not any(i.covers(work) for i in merge(shift_ivs.get((sid, t.unit_id), []))):
            v.append(f"B3_vardiya_disi:{tid}:{sid}")  # görevin servisindeki etkin vardiyanın içinde değil
        if any(u.overlaps(work) for u in unav.get(sid, [])):
            v.append(f"B3_uygunsuzlukla_cakisiyor:{tid}:{sid}")  # mola, izin, devamsızlık
        for sk in t.required_skills:
            if not skill_valid_throughout(validity(sid, sk), work):
                v.append(f"B2_yetkinlik_gecersiz:{tid}:{sid}:{sk}")
        if a.get("gorev_turu") != t.task_type or a.get("son_tarih") != t.due_at or list(a.get("gerekli_yetkinlikler", [])) != list(t.required_skills):
            v.append(f"B_alan_tutarsiz:{tid}")
        per_staff.setdefault(sid, []).append((start, end, tid))
    for sid, items in per_staff.items():
        items.sort()
        for (s1, e1, t1), (s2, _, t2) in zip(items, items[1:]):
            if s2 < e1:
                v.append(f"B3_gorev_cakismasi:{sid}:{t1}:{t2}")

    # -- Atanmayanlar (B7): küme, açık dakikası, neden (ham girdiden yeniden) --
    def expected_reason(tid: int) -> str:
        t = scope[tid]
        if t.due_at <= at:
            return GAP_DEADLINE_PASSED
        latest = min(window_minutes(), _floor_min(at, t.due_at))
        if latest < dur[tid]:
            return GAP_DEADLINE_TOO_CLOSE
        end_t = at + timedelta(minutes=latest)
        candidates = [sid for sid, p in inp.staff.items() if p.active and p.role == t.role and free(sid, t.unit_id)]
        eligible, has_slot = False, False
        for sid in candidates:
            ok = [window]
            for sk in t.required_skills:
                vals = [Interval(i.start, i.end if i.end is not None else window.end) for i in validity(sid, sk)
                        if i.start < window.end and (i.end is None or i.end > window.start)]  # fmt: skip
                ok = intersect(ok, clip(vals, window))
            if not ok:
                continue
            eligible = True
            for i in clip(intersect(free(sid, t.unit_id), ok), Interval(at, end_t)):
                if _floor_min(at, i.end) - _ceil_min(at, i.start) >= dur[tid]:
                    has_slot = True
        if not eligible:
            return GAP_NO_ELIGIBLE_STAFF
        return GAP_CAPACITY if has_slot else GAP_NO_SLOT

    gaps = {g.get("gorev"): g for g in solution.get("atanmayanlar", [])}
    if set(gaps) != set(scope) - seen or len(gaps) != len(solution.get("atanmayanlar", [])):
        v.append("B7_atanmayan_kumesi_tutarsiz")
    for tid, g in gaps.items():
        if tid not in scope:
            continue
        if g.get("acik_dk") != dur[tid]:
            v.append(f"B7_acik_dk_tutarsiz:{tid}")
        if g.get("neden") != expected_reason(tid):
            v.append(f"B7_neden_tutarsiz:{tid}")
        t = scope[tid]
        if (g.get("servis"), g.get("rol"), g.get("gorev_turu"), g.get("son_tarih")) != (t.unit_id, t.role, t.task_type, t.due_at):
            v.append(f"B_alan_tutarsiz:{tid}")

    # -- Amaçlar, kapsam, rezerv --
    groups: dict[tuple[int, str], list[int]] = {}
    for (sid, unit) in sorted(shift_ivs):
        if free(sid, unit):
            groups.setdefault((unit, inp.staff[sid].role), []).append(sid)
    load = {(sid, unit): 0 for (unit, _), m in groups.items() for sid in m}
    for a in solution.get("atamalar", []):
        key = (a.get("personel"), a.get("servis"))
        if a.get("gorev") in scope and key in load:
            load[key] += dur[a["gorev"]]
    o1 = sum(dur[tid] for tid in set(scope) - seen)
    o2 = sum(max(load[(s, u)] for s in m) - min(load[(s, u)] for s in m) for (u, _), m in groups.items() if len(m) >= 2)
    declared = solution.get("amac_degerleri", {})
    for name, value in zip(STAGES_B, (o1, o2)):
        if declared.get(name) != value:
            v.append(f"B_amac_degeri_tutarsiz:{name}")
    expected_scope = {"pencerede_son_tarihli_gorev": len(scope), "sonraki_pencere_gorev": len(later),
                      "iptal_zamani_belirsiz_gorev": len(uncertain), "somut_gorev_dk": sum(dur.values()),
                      "atanan_dk": sum(dur[tid] for tid in seen)}  # fmt: skip
    if solution.get("kapsam") != expected_scope:
        v.append("B_kapsam_tutarsiz")
    concrete: dict[tuple[int, str], int] = {}
    for tid, t in scope.items():
        concrete[(t.unit_id, t.role)] = concrete.get((t.unit_id, t.role), 0) + dur[tid]
    expected_reserve = []
    for u in sorted(inp.units):
        for r in ROLES:
            f = inp.forecasts.get((u, r))
            c = concrete.get((u, r), 0)
            expected_reserve.append({"servis": u, "rol": r, "tahmin_dk": None if f is None else round(float(f), 3), "somut_gorev_dk": c,
                                     "rezerv_dk": None if f is None else round(max(0.0, float(f) - c), 3),
                                     "not": "tahmin_yok" if f is None else "kimliksiz_rezerv_atanmaz"})  # fmt: skip
    if solution.get("rezerv") != expected_reserve:
        v.append("B_rezerv_tutarsiz")
    if solution.get("ozet_sha256") != _hash(solution):
        v.append("ozet_uyusmuyor")
    return TaskVerification(not v, sorted(set(v)))
