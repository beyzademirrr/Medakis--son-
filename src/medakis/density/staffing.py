"""Yoğunluğa duyarlı geçici görevlendirme: sentetik personel, iş yükü göstergesi, CP-SAT modeli ve bağımsız doğrulayıcı.

Karar kuralı: kamera yoğunluğu TEK BAŞINA aktarım gerekçesi değildir. Hedef alan ancak (i) doluluğu kritik ve
(ii) personel başına bekleyen görev eşiği aşılmışsa destek ister. Kaynak alan asgari personel sayısının altına düşemez.
Personelin rolü/yetkinliği, vardiyası ve mevcut görevi korunur; bir kişi tek alana aktarılır. Sistem aktarımı UYGULAMAZ;
öneri yönetici onayına sunulur.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ortools.sat.python import cp_model

from medakis.density.simulator import AREAS

AREA_NAMES = {a.key: a.name for a in AREAS}
CAPACITY = {a.key: a.capacity for a in AREAS}
MIN_STAFF = {"acil": 4, "poliklinik": 3, "yatakli": 4, "bekleme": 1, "destek": 2}
PATIENTS_PER_STAFF = {"acil": 4.0, "poliklinik": 8.0, "yatakli": 5.0, "bekleme": 20.0, "destek": 10.0}
SKILL_FOR = {"acil": "acil_triyaj", "poliklinik": "poliklinik", "yatakli": "yatakli_bakim", "bekleme": "hasta_yonlendirme",
             "destek": "destek"}  # fmt: skip
TASK_PER_STAFF_LIMIT = 3.0


@dataclass(frozen=True)
class Staff:
    id: str
    role: str
    area: str
    skills: frozenset[str]
    on_shift: bool
    busy: bool  # devredilemeyen aktif görevde


def roster(seed: int = 11) -> list[Staff]:
    """Tohumlu sentetik personel listesi (gerçek kişi değildir)."""
    rng = random.Random(seed)
    plan = {"acil": 5, "poliklinik": 7, "yatakli": 6, "bekleme": 2, "destek": 4}
    out, n = [], 1
    for area, count in plan.items():
        for i in range(count):
            role = "destek_personeli" if area in ("destek", "bekleme") else "hemsire"
            skills = {SKILL_FOR[area]}
            if role == "hemsire" and rng.random() < 0.55:
                skills.add("acil_triyaj")
            if rng.random() < 0.3:
                skills.add("hasta_yonlendirme")
            out.append(Staff(f"P-{n:02d}", role, area, frozenset(skills), on_shift=rng.random() > 0.12, busy=rng.random() < 0.25))
            n += 1
    return out


def pending_tasks(counts: dict[str, int]) -> dict[str, int]:
    """Sentetik bekleyen görev göstergesi: alan doluluğundan türetilir (simülasyon)."""
    return {k: max(0, round(counts[k] * {"acil": 0.6, "poliklinik": 0.35, "yatakli": 0.5, "bekleme": 0.1, "destek": 0.3}[k])) for k in counts}


def area_status(counts: dict[str, int], staff: list[Staff]) -> list[dict]:
    tasks = pending_tasks(counts)
    rows = []
    for a in AREAS:
        active = [s for s in staff if s.area == a.key and s.on_shift]
        occ = counts[a.key] / a.capacity
        need = max(MIN_STAFF[a.key], math.ceil(counts[a.key] / PATIENTS_PER_STAFF[a.key]))
        load = tasks[a.key] / max(1, len(active))
        rows.append({"alan": a.key, "ad": a.name, "kisi": counts[a.key], "kapasite": a.capacity, "doluluk": occ,
                     "seviye": "kritik" if occ >= 0.85 else "uyari" if occ >= 0.6 else "normal", "aktif_personel": len(active),
                     "gereken_personel": need, "bekleyen_gorev": tasks[a.key], "personel_basina_gorev": round(load, 2)})  # fmt: skip
    return rows


def needs_support(row: dict) -> bool:
    """Yoğunluk TEK BAŞINA yetmez: kritik doluluk VE personel başına görev eşiği VE personel açığı birlikte gerekir."""
    return row["seviye"] == "kritik" and row["personel_basina_gorev"] > TASK_PER_STAFF_LIMIT and row["gereken_personel"] > row["aktif_personel"]


@dataclass(frozen=True)
class Transfer:
    staff_id: str
    source: str
    target: str


def optimize(counts: dict[str, int], staff: list[Staff], target: str | None = None, max_moves: int = 2,
             pinned: tuple[str, ...] = ()) -> dict:  # fmt: skip
    """CP-SAT: hedef alanın açığını kapatmak için en az maliyetli geçici aktarım. `pinned` yönetici tarafından
    seçilen personeldir; uygun değilse çözüm yoktur (güvenli ret)."""
    status = {r["alan"]: r for r in area_status(counts, staff)}
    targets = [target] if target else [k for k, r in status.items() if needs_support(r)]
    if not targets:
        return {"durum": "gerek_yok", "aktarimlar": [], "gerekce": "Destek gerektiren alan yok (yoğunluk + iş yükü + personel açığı birlikte sağlanmadı)."}
    tgt = targets[0]
    deficit = max(0, status[tgt]["gereken_personel"] - status[tgt]["aktif_personel"])
    if deficit == 0:
        return {"durum": "gerek_yok", "aktarimlar": [], "gerekce": f"{AREA_NAMES[tgt]} için personel açığı yok."}
    m = cp_model.CpModel()
    cand = [s for s in staff if s.area != tgt and s.on_shift and not s.busy and SKILL_FOR[tgt] in s.skills]
    x = {s.id: m.NewBoolVar(s.id) for s in cand}
    for pid in pinned:
        if pid not in x:
            return {"durum": "uygun_degil", "aktarimlar": [], "gerekce": f"{pid} hedef alan için uygun değil (yetkinlik, vardiya, görev ya da alan)."}
        m.Add(x[pid] == 1)
    m.Add(sum(x.values()) <= min(deficit, max_moves))
    for a in AREAS:
        if a.key == tgt:
            continue
        here = [s for s in cand if s.area == a.key]
        if here:
            m.Add(status[a.key]["aktif_personel"] - sum(x[s.id] for s in here) >= max(MIN_STAFF[a.key], status[a.key]["gereken_personel"]))
    # amaç: açığı kapat; düşük iş yüklü alanlardan al
    cost = {s.id: int(100 * status[s.area]["personel_basina_gorev"]) for s in cand}
    m.Maximize(1000 * sum(x.values()) - sum(cost[i] * v for i, v in x.items()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    solver.parameters.num_workers = 1
    st = solver.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"durum": "cozum_yok", "aktarimlar": [], "gerekce": "Kısıtları sağlayan aktarım bulunamadı (güvenli ret)."}
    moves = [Transfer(s.id, s.area, tgt) for s in cand if solver.Value(x[s.id])]
    if not moves:
        return {"durum": "uygun_personel_yok", "aktarimlar": [], "gerekce": "Asgari kapasiteyi bozmadan aktarılabilecek uygun personel yok."}
    return {"durum": "oneri", "hedef": tgt, "acik": deficit, "aktarimlar": [m.__dict__ for m in moves], "cozucu": solver.StatusName(st),
            "gerekce": f"{AREA_NAMES[tgt]}: doluluk %{round(100 * status[tgt]['doluluk'])}, personel başına {status[tgt]['personel_basina_gorev']} "
                       f"bekleyen görev, gereken {status[tgt]['gereken_personel']} / aktif {status[tgt]['aktif_personel']} personel."}  # fmt: skip


def verify(plan: dict, counts: dict[str, int], staff: list[Staff]) -> list[str]:
    """Bağımsız doğrulayıcı: çözücüden bağımsız olarak bütün kuralları yeniden denetler."""
    problems, by_id = [], {s.id: s for s in staff}
    moves = plan.get("aktarimlar", [])
    status = {r["alan"]: r for r in area_status(counts, staff)}
    if len({m["staff_id"] for m in moves}) != len(moves):
        problems.append("ayni_personel_birden_cok_aktarim")
    for mv in moves:
        s = by_id.get(mv["staff_id"])
        if s is None:
            problems.append(f"bilinmeyen_personel:{mv['staff_id']}")
            continue
        if s.area != mv["source"] or s.area == mv["target"]:
            problems.append(f"kaynak_alan_hatali:{s.id}")
        if not s.on_shift:
            problems.append(f"vardiya_disi:{s.id}")
        if s.busy:
            problems.append(f"aktif_gorevde:{s.id}")
        if SKILL_FOR[mv["target"]] not in s.skills:
            problems.append(f"yetkinlik_yok:{s.id}")
    for a in AREAS:
        out = sum(1 for mv in moves if mv["source"] == a.key)
        if out and status[a.key]["aktif_personel"] - out < max(MIN_STAFF[a.key], status[a.key]["gereken_personel"]):
            problems.append(f"asgari_kapasite_ihlali:{a.key}")
    if moves and not needs_support(status[moves[0]["target"]]) and not plan.get("yonetici_istegi"):
        problems.append("hedef_destek_kosulu_saglanmiyor")
    return problems
