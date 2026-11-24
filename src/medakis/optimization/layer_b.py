"""Aşama 5 / U6: Katman B — bilinen bekleyen görevler için görev ataması UYGULANABİLİRLİK DENETİMİ (CP-SAT).

Tasarım §1 ve §10 (U6) ile şartname Bölüm 6. Katman A'dan bağımsızdır ve onu değiştirmez.
- Girdi: karar anı t, [t, t+60) penceresi ve t anında bilinen plan (known_view). Son onaylı yerleşim kullanılır:
  servisler arası transfer ve Katman A önerileri UYGULANMAZ.
- Kapsam: t'de bekleyen ve son tarihi pencere içinde (≤ t+60) olan somut görevler. Son tarihi daha sonra olan
  görevler sayılır ama atanmaz. İptal zamanı bilinmeyen görevler belirsizdir; atanmaz.
- Kurallar: bir görev en fazla bir personele; personel aktif ve rolü eşleşir; bütün yetkinlikler işin tüm
  süresinde geçerli; iş görevin servisindeki etkin vardiya içinde, mola/izin/devamsızlıkla ve aynı kişinin başka
  göreviyle çakışmaz (NoOverlap); başlangıç ≥ t, bitiş ≤ min(son tarih, t+60). Süre yukarı yuvarlanır.
- Amaç (sözlük sıralı, değer sabitleme): O1 atanmayan görev dakikası, O2 (servis, rol) gruplarında yük farkı.
- Uygun personel yoksa kural gevşetilmez; atanmayan her görev açık dakikası ve nedeniyle raporlanır.
- Rezerv = max(0, ŷ − somut görev dakikası); kimliksiz rezerv iş atanmış gösterilmez.
- Çıktı yalnız denetimdir: gerçek atama yok, tabloya yazma yok, insan incelemesi zorunlu. Bağımsız doğrulayıcı
  (layer_b_verifier.py) geçmeden sonuç gösterilmez. UNKNOWN plan üretmez.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ortools.sat.python import cp_model

from medakis.domain.capacity import clip, intersect, merge, subtract
from medakis.domain.planning import ACTIVE_STATUSES, ROLES, known_view, plan_problems
from medakis.domain.time import Interval, ensure_utc
from medakis.optimization import layer_b_verifier
from medakis.optimization.comparison import SYNTHETIC_NOTICE
from medakis.optimization.layer_b_contracts import (
    GAP_CAPACITY,
    GAP_DEADLINE_PASSED,
    GAP_DEADLINE_TOO_CLOSE,
    GAP_NO_ELIGIBLE_STAFF,
    GAP_NO_SLOT,
    STAGES_B,
    LayerBInputs,
    TaskSpec,
    input_problems,
    task_minutes,
    window_minutes,
)

OPTIMAL, FEASIBLE, INFEASIBLE, UNKNOWN, MODEL_INVALID = "OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"


@dataclass
class _Prepared:
    at: datetime
    window: Interval
    horizon: int
    in_scope: list[TaskSpec]
    later: list[int]
    duration: dict[int, int]
    deadline: dict[int, int]  # görev → en geç bitiş (dk, t'ye göre; pencereyle kırpılmış)
    slots: dict[tuple[int, int], list[tuple[int, int]]]  # (görev, personel) → [(en erken başlangıç, en geç bitiş)]
    eligible: dict[int, list[int]]
    groups: dict[tuple[int, str], list[int]]  # (servis, rol) → vardiyada ve kullanılabilir personel


def _canonical_hash(content: Mapping[str, Any]) -> str:
    raw = json.dumps(content, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def solution_hash(solution: Mapping[str, Any]) -> str:
    return _canonical_hash({k: v for k, v in solution.items() if k != "ozet_sha256"})


def _minute(at: datetime, t: datetime, up: bool) -> int:
    value = (t - at).total_seconds() / 60
    return math.ceil(value - 1e-9) if up else math.floor(value + 1e-9)


def _availability(inp: LayerBInputs, window: Interval) -> dict[tuple[int, int], list[Interval]]:
    """(personel, servis) → pencerede kullanılabilir aralıklar: etkin vardiya ∩ pencere − bütün uygunsuzluklar."""
    view = known_view(inp.plan, window.start)
    shifts: dict[tuple[int, int], list[Interval]] = {}
    for s in view.shifts:
        if s.status in ACTIVE_STATUSES and s.interval.overlaps(window):
            shifts.setdefault((s.staff_id, s.unit_id), []).append(s.interval)
    unav: dict[int, list[Interval]] = {}
    for u in view.unavailability:
        unav.setdefault(u.staff_id, []).append(Interval(u.starts_at, u.ends_at))
    out = {}
    for (sid, unit), ivs in shifts.items():
        person = inp.staff.get(sid)
        if person is None or not person.active:
            continue  # aktif olmayan personel aday değildir
        free = subtract(merge(clip(ivs, window)), unav.get(sid, []))
        if free:
            out[(sid, unit)] = free
    return out


def _skill_windows(inp: LayerBInputs, sid: int, required: tuple[str, ...], window: Interval) -> list[Interval]:
    """Bütün gerekli yetkinliklerin aynı anda geçerli olduğu aralıklar (pencereyle kırpılmış)."""
    current = [window]
    for skill in required:
        valid = [Interval(s.valid_from, s.valid_until if s.valid_until is not None else window.end)
                 for s in inp.skills if s.staff_id == sid and s.skill == skill
                 and (s.valid_until is None or s.valid_until > s.valid_from) and (s.valid_until is None or s.valid_until > window.start)
                 and s.valid_from < window.end]  # fmt: skip
        current = intersect(current, clip(valid, window))
        if not current:
            return []
    return current


def prepare(inp: LayerBInputs) -> _Prepared:
    at = inp.at
    horizon = window_minutes()
    window = Interval(at, at + timedelta(minutes=horizon))
    avail = _availability(inp, window)
    uncertain = set(inp.uncertain_task_ids)
    in_scope = sorted((t for t in inp.tasks if t.due_at <= window.end and t.task_id not in uncertain), key=lambda t: t.task_id)
    later = sorted(t.task_id for t in inp.tasks if t.due_at > window.end and t.task_id not in uncertain)
    duration = {t.task_id: task_minutes(t.estimated_minutes) for t in in_scope}
    deadline = {t.task_id: min(horizon, _minute(at, t.due_at, up=False)) for t in in_scope}
    groups: dict[tuple[int, str], list[int]] = {}
    for (sid, unit) in sorted(avail):
        groups.setdefault((unit, inp.staff[sid].role), []).append(sid)
    slots: dict[tuple[int, int], list[tuple[int, int]]] = {}
    eligible: dict[int, list[int]] = {}
    for t in in_scope:
        end_t = at + timedelta(minutes=max(deadline[t.task_id], 0))
        for sid in groups.get((t.unit_id, t.role), []):
            sk = _skill_windows(inp, sid, t.required_skills, window)
            if not sk:
                continue
            eligible.setdefault(t.task_id, []).append(sid)
            if deadline[t.task_id] <= 0:
                continue
            usable = clip(intersect(avail[(sid, t.unit_id)], sk), Interval(at, end_t))
            spans = [(_minute(at, i.start, up=True), _minute(at, i.end, up=False)) for i in usable]
            spans = [(lo, hi) for lo, hi in spans if hi - lo >= duration[t.task_id]]
            if spans:
                slots[(t.task_id, sid)] = spans
    return _Prepared(at, window, horizon, in_scope, later, duration, deadline, slots, eligible, groups)


def gap_reason(prep: _Prepared, task: TaskSpec) -> str:
    tid = task.task_id
    if task.due_at <= prep.at:
        return GAP_DEADLINE_PASSED
    if prep.deadline[tid] < prep.duration[tid]:
        return GAP_DEADLINE_TOO_CLOSE
    if not prep.eligible.get(tid):
        return GAP_NO_ELIGIBLE_STAFF
    if not any((tid, sid) in prep.slots for sid in prep.eligible[tid]):
        return GAP_NO_SLOT
    return GAP_CAPACITY


@dataclass
class _Run:
    status: str
    objective: float | None
    wall_s: float
    solver: cp_model.CpSolver


def _run_solver(model: cp_model.CpModel, time_limit_s: float, overrides: Mapping[str, Any] | None) -> _Run:
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    for key, value in (overrides or {}).items():
        setattr(solver.parameters, key, value)
    status = solver.status_name(solver.solve(model))
    found = status in (OPTIMAL, FEASIBLE)
    return _Run(status, solver.objective_value if found else None, solver.wall_time, solver)


def _empty_output(inp: LayerBInputs) -> dict[str, Any]:
    at = inp.at
    return {
        "karar_ani": at, "pencere_baslangic": at, "pencere_bitis": at + timedelta(minutes=window_minutes()), "durum": None,
        "cozucu_asamalari": [], "sozluk_sirali_tam": False, "nedenler": [], "uyarilar": [],
        "dogrulama": {"calisti": False, "gecti": False, "ihlaller": []}, "denetim_gosterilebilir": False, "cozum": None,
        "gercek_atama_yok": True, "kayit_yok": True, "insan_incelemesi_gerekli": True, "sentetik_uyari": SYNTHETIC_NOTICE,
    }  # fmt: skip


def components(prep: _Prepared) -> list[tuple[list[tuple[int, str]], list[int]]]:
    """Bağımsız alt problemler: ortak personeli olan (servis, rol) grupları birleşir (aynı pencerede iki serviste
    vardiyası olan kişi). Toplam amaçlar ayrıştığından bileşen başına sözlük sıralı çözüm küresel çözüme eşdeğerdir.
    Dönüş: [(gruplar, en az bir aday zaman aralığı olan görevler)]."""
    parent = {g: g for g in prep.groups}

    def find(g):
        while parent[g] != g:
            parent[g] = parent[parent[g]]
            g = parent[g]
        return g

    owner: dict[int, tuple[int, str]] = {}
    for g, members in sorted(prep.groups.items()):
        for sid in members:
            if sid in owner:
                parent[find(g)] = find(owner[sid])
            else:
                owner[sid] = g
    comps: dict[tuple[int, str], list[tuple[int, str]]] = {}
    for g in sorted(prep.groups):
        comps.setdefault(find(g), []).append(g)
    out = []
    for gs in comps.values():
        keys = set(gs)
        tasks = [t.task_id for t in prep.in_scope if (t.unit_id, t.role) in keys
                 and any((t.task_id, sid) in prep.slots for sid in prep.eligible.get(t.task_id, []))]  # fmt: skip
        out.append((gs, tasks))
    return out


def _solve_component(prep: _Prepared, gs: list[tuple[int, str]], task_ids: list[int], time_limit_s: float,
                     overrides: Mapping[str, Any] | None) -> tuple[list[dict[str, Any]], dict[int, tuple[int, int]] | None, bool]:  # fmt: skip
    """Bir bileşenin sözlük sıralı çözümü: (aşama kayıtları, görev → (personel, başlangıç dk), sözlük sırası tam mı)."""
    model = cp_model.CpModel()
    wanted = set(task_ids)
    unit_of = {t.task_id: t.unit_id for t in prep.in_scope}
    presence: dict[tuple[int, int, int], cp_model.IntVar] = {}
    starts: dict[tuple[int, int, int], cp_model.IntVar] = {}
    intervals_of: dict[int, list] = {}
    for (tid, sid), spans in sorted(prep.slots.items()):
        if tid not in wanted:
            continue
        d = prep.duration[tid]
        for k, (lo, hi) in enumerate(spans):  # her kullanılabilir aralık için isteğe bağlı sabit süreli aralık
            key = (tid, sid, k)
            presence[key] = model.new_bool_var(f"p_{tid}_{sid}_{k}")
            starts[key] = model.new_int_var(lo, hi - d, f"s_{tid}_{sid}_{k}")
            interval = model.new_optional_fixed_size_interval_var(starts[key], d, presence[key], f"i_{tid}_{sid}_{k}")
            intervals_of.setdefault(sid, []).append(interval)
    for sid in sorted(intervals_of):
        model.add_no_overlap(intervals_of[sid])  # aynı personelin görevleri çakışmaz
    assigned = {}
    for tid in task_ids:
        a = model.new_bool_var(f"a_{tid}")
        model.add(sum(b for (t, _, _), b in presence.items() if t == tid) == a)  # en fazla bir personel ve bir aralık
        assigned[tid] = a
    spread = []
    for unit, role in gs:
        loads = []
        for sid in prep.groups[(unit, role)]:
            terms = [prep.duration[tid] * b for (tid, s_, _), b in presence.items() if s_ == sid and unit_of[tid] == unit]
            var = model.new_int_var(0, prep.horizon, f"l_{sid}_{unit}")
            model.add(var == sum(terms))
            loads.append(var)
        if len(loads) >= 2:
            hi = model.new_int_var(0, prep.horizon, f"hi_{unit}_{role}")
            lo = model.new_int_var(0, prep.horizon, f"lo_{unit}_{role}")
            model.add_max_equality(hi, loads)
            model.add_min_equality(lo, loads)
            spread.append(hi - lo)
    objectives = {STAGES_B[0]: sum(prep.duration[tid] * (1 - assigned[tid]) for tid in task_ids), STAGES_B[1]: sum(spread)}
    variables = {("p", *k): v for k, v in presence.items()} | {("s", *k): v for k, v in starts.items()}
    stages: list[dict[str, Any]] = []
    values: dict[tuple, int] | None = None
    complete = True
    for name in STAGES_B:
        expr = objectives[name]
        model.minimize(expr)
        model.clear_hints()
        if values is not None:
            for key, var in variables.items():
                model.add_hint(var, values[key])
        run = _run_solver(model, time_limit_s, overrides)
        stages.append({"asama": name, "durum": run.status, "amac_degeri": run.objective, "sure_sn": run.wall_s})
        if run.status not in (OPTIMAL, FEASIBLE):
            complete = False
            if values is None:  # UNKNOWN/INFEASIBLE: bu bileşende plan yok
                return stages, None, False
            break  # O2 bulunamadı: O1 çözümü korunur, sözlük sırası tam değil
        if run.status != OPTIMAL:
            complete = False
        values = {key: int(run.solver.value(var)) for key, var in variables.items()}
        model.add(expr <= int(round(run.objective)))  # type: ignore[arg-type]  # bu aşamanın değeri sabitlenir
    assert values is not None
    chosen = {tid: (sid, values[("s", tid, sid, k)]) for (tid, sid, k) in presence if values[("p", tid, sid, k)] == 1}
    return stages, chosen, complete


def _aggregate(per_stage: Mapping[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Bileşen aşamalarının özeti: amaç toplamı, süre toplamı, en kötü durum."""
    out = []
    for name in STAGES_B:
        rows = per_stage[name]
        found = all(r["durum"] in (OPTIMAL, FEASIBLE) for r in rows)
        worst = next((r["durum"] for r in rows if r["durum"] not in (OPTIMAL, FEASIBLE)), FEASIBLE)
        optimal = all(r["durum"] == OPTIMAL for r in rows)
        out.append({"asama": name, "durum": OPTIMAL if optimal else (FEASIBLE if found else worst),
                    "amac_degeri": sum(r["amac_degeri"] for r in rows) if found else None,
                    "sure_sn": round(sum(r["sure_sn"] for r in rows), 4), "optimal": optimal, "bilesen": len(rows)})  # fmt: skip
    return out


def check_tasks(inp: LayerBInputs, solver_overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Katman B denetimi. Sonuç yalnız çözücü OPTIMAL/FEASIBLE ve bağımsız doğrulayıcı geçerse gösterilebilir."""
    out = _empty_output(inp)
    try:
        at = ensure_utc(inp.at, "Karar anı")
    except Exception as exc:  # noqa: BLE001
        out["durum"], out["nedenler"] = MODEL_INVALID, [f"gecersiz_karar_ani:{exc}"]
        return out
    problems = input_problems(inp)
    roles = {sid: s.role for sid, s in inp.staff.items()}
    problems += [f"gecersiz_plan:{p}" for p in plan_problems(known_view(inp.plan, at), roles, set(inp.units))]
    if problems:
        out["durum"], out["nedenler"] = MODEL_INVALID, problems
        return out
    prep = prepare(inp)
    cfg = inp.config
    assert cfg is not None
    comps = components(prep)
    chosen: dict[int, tuple[int, int]] = {}
    complete = True
    per_stage: dict[str, list[dict[str, Any]]] = {name: [] for name in STAGES_B}
    for gs, task_ids in comps:
        stages, picked, full = _solve_component(prep, gs, task_ids, cfg.time_limit_s, solver_overrides)
        for st in stages:
            per_stage[st["asama"]].append(st)
        if picked is None:  # tek bir bileşende bile plan yoksa bütün denetim plan üretmez (UNKNOWN plan üretmez)
            status = stages[0]["durum"]
            out["durum"] = status if status in (INFEASIBLE, UNKNOWN, MODEL_INVALID) else UNKNOWN
            out["nedenler"] = [f"cozucu:{stages[0]['asama']}:{status}:bilesen={'|'.join(f'{u}:{r}' for u, r in gs)}"]
            out["cozucu_asamalari"] = [st for st in _aggregate(per_stage) if st["bilesen"]]  # çalışmayan aşama gösterilmez
            return out
        complete = complete and full
        chosen.update(picked)
    out["cozucu_asamalari"] = _aggregate(per_stage)
    for st in out["cozucu_asamalari"]:
        if not st["optimal"]:
            out["uyarilar"].append(f"asama_optimal_degil:{st['asama']}")
    out["durum"] = OPTIMAL if complete else FEASIBLE
    out["sozluk_sirali_tam"] = complete
    solution = build_solution(inp, prep, chosen)
    check = layer_b_verifier.verify_tasks(inp, solution)
    violations = list(check.violations)
    modelled = {tid for _, ids in comps for tid in ids}
    fixed_gap = sum(prep.duration[t.task_id] for t in prep.in_scope if t.task_id not in modelled)  # aday aralığı olmayanlar
    for stage in out["cozucu_asamalari"]:
        if stage["optimal"] and stage["amac_degeri"] is not None:
            offset = fixed_gap if stage["asama"] == STAGES_B[0] else 0
            if int(round(stage["amac_degeri"])) + offset != solution["amac_degerleri"][stage["asama"]]:
                violations.append(f"asama_degeri_nihai_cozumle_uyusmuyor:{stage['asama']}")
    out["dogrulama"] = {"calisti": True, "gecti": not violations, "ihlaller": sorted(set(violations))}
    if violations:
        out["nedenler"] = ["dogrulayici_reddetti"]
        return out
    out["denetim_gosterilebilir"] = True
    out["cozum"] = solution
    return out


def build_solution(inp: LayerBInputs, prep: _Prepared, chosen: Mapping[int, tuple[int, int]]) -> dict[str, Any]:
    """chosen: görev → (personel, başlangıç dk). Doğrulayıcının denetleyeceği içerik ve açıklama alanları."""
    at = prep.at
    tasks = {t.task_id: t for t in prep.in_scope}
    assignments = []
    for tid, (sid, start) in sorted(chosen.items()):
        t = tasks[tid]
        d = prep.duration[tid]
        assignments.append({"gorev": tid, "personel": sid, "servis": t.unit_id, "rol": t.role, "gorev_turu": t.task_type,
                            "baslangic": at + timedelta(minutes=start), "bitis": at + timedelta(minutes=start + d), "sure_dk": d,
                            "son_tarih": t.due_at, "gerekli_yetkinlikler": list(t.required_skills)})  # fmt: skip
    gaps = [{"gorev": t.task_id, "servis": t.unit_id, "rol": t.role, "gorev_turu": t.task_type, "acik_dk": prep.duration[t.task_id],
             "son_tarih": t.due_at, "neden": gap_reason(prep, t)} for t in prep.in_scope if t.task_id not in chosen]  # fmt: skip
    load: dict[tuple[int, int], int] = {(sid, unit): 0 for (unit, _), members in prep.groups.items() for sid in members}
    for a in assignments:
        load[(a["personel"], a["servis"])] += a["sure_dk"]
    spread = sum(max(load[(s, u)] for s in m) - min(load[(s, u)] for s in m) for (u, _), m in prep.groups.items() if len(m) >= 2)
    concrete: dict[tuple[int, str], int] = {}
    for t in prep.in_scope:
        concrete[(t.unit_id, t.role)] = concrete.get((t.unit_id, t.role), 0) + prep.duration[t.task_id]
    reserve = []
    for u in sorted(inp.units):
        for r in ROLES:
            f = inp.forecasts.get((u, r))
            c = concrete.get((u, r), 0)
            reserve.append({"servis": u, "rol": r, "tahmin_dk": None if f is None else round(float(f), 3), "somut_gorev_dk": c,
                            "rezerv_dk": None if f is None else round(max(0.0, float(f) - c), 3),
                            "not": "tahmin_yok" if f is None else "kimliksiz_rezerv_atanmaz"})  # fmt: skip
    solution = {
        "karar_ani": at,
        "atamalar": assignments,
        "atanmayanlar": gaps,
        "amac_degerleri": {STAGES_B[0]: sum(g["acik_dk"] for g in gaps), STAGES_B[1]: spread},
        "kapsam": {"pencerede_son_tarihli_gorev": len(prep.in_scope), "sonraki_pencere_gorev": len(prep.later),
                   "iptal_zamani_belirsiz_gorev": len(set(inp.uncertain_task_ids)),
                   "somut_gorev_dk": sum(prep.duration.values()), "atanan_dk": sum(a["sure_dk"] for a in assignments)},
        "rezerv": reserve,
    }  # fmt: skip
    solution["ozet_sha256"] = solution_hash(solution)
    return solution
