"""Üretilen veri kümesini, servislerin kullandığı aynı alan kurallarıyla bağımsız denetler.

Seed yüklemesi servis katmanını atlayıp toplu yazdığı için bu denetim zorunludur; ardından
veritabanı kısıtları ikinci savunma hattıdır.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from medakis.domain.errors import DomainError
from medakis.domain.rules import (
    PATIENT_CODE,
    STAFF_CODE,
    breaks_match_summary,
    estimated_minutes,
    has_overlap,
    latest_multiplier_at,
)
from medakis.domain.time import Interval
from medakis.synthetic.generator import SyntheticDataset


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def validate(dataset: SyntheticDataset) -> list[str]:
    """Kural ihlallerini döndürür; boş liste = veri kümesi geçerli."""
    t, now = dataset.tables, dataset.scenario_now
    issues: list[str] = []

    def issue(message: str) -> None:
        if len(issues) < 50:
            issues.append(message)

    for table, rows in t.items():
        for row in rows:
            for column, value in row.items():
                if isinstance(value, float) and not math.isfinite(value):
                    issue(f"{table}.{column}: sonlu değil")
                if isinstance(value, datetime) and not _aware(value):
                    issue(f"{table}.{column}: saat dilimi yok")

    for p in t["patients"]:
        if not PATIENT_CODE.fullmatch(p["synthetic_code"]) or p["is_synthetic"] is not True:
            issue(f"patients {p['id']}: sentetik kod kuralı")
    for s in t["staff"]:
        if not STAFF_CODE.fullmatch(s["synthetic_code"]):
            issue(f"staff {s['id']}: sentetik kod kuralı")

    room_unit = {r["id"]: r["unit_id"] for r in t["rooms"]}
    bed_unit = {b["id"]: room_unit[b["room_id"]] for b in t["beds"]}
    stays_by_bed: dict[int, list[Interval]] = defaultdict(list)
    encounter = {}
    for e in t["encounters"]:
        encounter[e["id"]] = e
        try:
            stay = Interval(e["admitted_at"], e["discharged_at"])
        except DomainError:
            issue(f"encounters {e['id']}: geçersiz aralık")
            continue
        if e["admitted_at"] >= now:
            issue(f"encounters {e['id']}: senaryo anından sonra yatış")
        if e["bed_id"] is not None:
            if bed_unit.get(e["bed_id"]) != e["unit_id"]:
                issue(f"encounters {e['id']}: yatak başka servise ait")
            stays_by_bed[e["bed_id"]].append(stay)
    for bed, stays in stays_by_bed.items():
        if has_overlap(stays):
            issue(f"beds {bed}: örtüşen yatış")

    assessments: dict[int, list[tuple[datetime, float]]] = defaultdict(list)
    for a in t["acuity_assessments"]:
        assessments[a["encounter_id"]].append((a["assessed_at"], a["multiplier"]))
        if a["assessed_at"] > now:
            issue(f"acuity {a['id']}: gelecekteki değerlendirme")

    shifts_by_staff: dict[int, list[Interval]] = defaultdict(list)
    for s in t["staff_shifts"]:
        shifts_by_staff[s["staff_id"]].append(Interval(s["starts_at"], s["ends_at"]))
    for sid, shifts in shifts_by_staff.items():
        if has_overlap(shifts):
            issue(f"staff {sid}: örtüşen vardiya")
    breaks_by_staff: dict[int, list[Interval]] = defaultdict(list)
    for u in t["staff_unavailability"]:
        if u["reason"] == "Mola":
            breaks_by_staff[u["staff_id"]].append(Interval(u["starts_at"], u["ends_at"]))
    for s in t["staff_shifts"]:
        shift = Interval(s["starts_at"], s["ends_at"])
        if s["break_minutes"] > shift.minutes():
            issue(f"staff_shifts {s['id']}: mola vardiyadan uzun")
        if not breaks_match_summary(s["break_minutes"], breaks_by_staff[s["staff_id"]], shift):
            issue(f"staff_shifts {s['id']}: mola özeti somut molalarla uyuşmuyor")

    base = {tt["id"]: tt["base_minutes"] for tt in t["task_types"]}
    for task in t["care_tasks"]:
        e = encounter[task["encounter_id"]]
        if task["unit_id"] != e["unit_id"]:
            issue(f"care_tasks {task['id']}: servis başvuruyla uyuşmuyor")
        if task["created_at"] > now:
            issue(f"care_tasks {task['id']}: senaryo anından sonra oluşturulmuş")
        if task["created_at"] < e["admitted_at"]:
            issue(f"care_tasks {task['id']}: yatıştan önce oluşturulmuş")
        expected = estimated_minutes(base[task["task_type_id"]], latest_multiplier_at(assessments[e["id"]], task["created_at"]))
        if task["estimated_minutes"] != expected:
            issue(f"care_tasks {task['id']}: tahmini süre kurala uymuyor")
        if task["status"] == "completed":
            if task["completed_at"] is None or task["actual_minutes"] is None:
                issue(f"care_tasks {task['id']}: tamamlanma bilgisi eksik")
            elif task["completed_at"] < task["created_at"] or task["completed_at"] >= now:
                issue(f"care_tasks {task['id']}: tamamlanma zamanı geçersiz")
        elif task["completed_at"] is not None or task["actual_minutes"] is not None:
            issue(f"care_tasks {task['id']}: tamamlanmamış görevde gerçekleşme bilgisi")
    return issues
