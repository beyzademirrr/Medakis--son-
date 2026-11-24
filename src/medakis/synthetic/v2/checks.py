"""Üretici 2.0 denetimleri: 1.0.0 alan kuralları + zaman (Z1–Z7) + korunum (C1–C6) + takvim + rol.

`validate_v2` kesin kuralları döndürür (boş liste = geçerli). İstatistiksel kontroller (S1) ve
kalibrasyon ölçümleri `summarize` içindedir; uyarı niteliğindedir, testi düşürmez.
Kapasite denetimi (C3) Aşama 3'ün `domain.capacity.staff_capacity` fonksiyonuyla bağımsız yapılır.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median
from zoneinfo import ZoneInfo

from medakis.domain.capacity import Unavailability, staff_capacity
from medakis.domain.models import Staff, StaffShift
from medakis.domain.time import Interval
from medakis.synthetic.v2 import profiles as P
from medakis.repositories.schema import CANCEL_REASON_VALUES, TRIAGE_VALUES
from medakis.synthetic.v2.model import CANCEL_REASONS, OUTCOMES, DatasetV2
from medakis.synthetic.validate import validate as validate_v1_rules

EPS = 1e-6


def validate_v2(ds: DatasetV2) -> list[str]:
    issues: list[str] = []

    def issue(message: str) -> None:
        if len(issues) < 100:
            issues.append(message)

    issues += validate_v1_rules(ds)  # type: ignore[arg-type]  # aynı tablo biçimi
    t, now = ds.tables, ds.scenario_now
    tz = ZoneInfo(ds.params.timezone)
    enc = {e["id"]: e for e in t["encounters"]}
    kind_of_unit = {u["id"]: u["kind"] for u in t["units"]}

    # Rol kümesi: yalnız nurse, supervisor, support (hekim yok)
    for s in t["staff"]:
        if s["role"] not in P.ROLES:
            issue(f"staff {s['id']}: modellenmeyen rol {s['role']}")
    for tt in t["task_types"]:
        if tt["required_role"] not in P.ROLES:
            issue(f"task_types {tt['code']}: modellenmeyen rol")

    # Zaman değişmezleri Z1–Z5, C4 ve ek kayıt tutarlılığı
    if set(ds.task_meta) != {r["id"] for r in t["care_tasks"]}:
        issue("task_meta görev kümesiyle aynı değil")
    for row in t["care_tasks"]:
        m = ds.task_meta.get(row["id"])
        e = enc[row["encounter_id"]]
        if m is None:
            continue
        if m.outcome not in OUTCOMES:
            issue(f"care_tasks {row['id']}: bilinmeyen sonuç")
        if not e["admitted_at"] <= row["created_at"]:
            issue(f"Z1 care_tasks {row['id']}: yatıştan önce oluşturulmuş")
        if not row["created_at"] <= row["due_at"]:
            issue(f"Z2 care_tasks {row['id']}: vadeden sonra oluşturulmuş")
        if row["status"] == "completed":
            start = row["completed_at"] - timedelta(minutes=row["actual_minutes"])
            if m.started_at is None or abs((start - m.started_at).total_seconds()) > 1e-3:
                issue(f"Z3 care_tasks {row['id']}: başlama zamanı türetilen aralıkla uyuşmuyor")
            if start < row["created_at"] or start < m.release_at:
                issue(f"Z3 care_tasks {row['id']}: başlama oluşturulmadan önce")
            if not row["completed_at"] > row["created_at"]:
                issue(f"Z4 care_tasks {row['id']}: tamamlanma oluşturulma anına eşit veya önce")
            if e["discharged_at"] is not None and row["completed_at"] > e["discharged_at"]:
                issue(f"Z5 care_tasks {row['id']}: taburculuktan sonra tamamlanmış")
            if start < e["admitted_at"]:
                issue(f"Z5 care_tasks {row['id']}: yatıştan önce başlamış")
            if m.outcome not in ("on_time", "late") or m.staff_id is None:
                issue(f"care_tasks {row['id']}: tamamlanan görevin sonucu/personeli tutarsız")
            if not math.isclose(m.actual_minutes, row["actual_minutes"]):
                issue(f"care_tasks {row['id']}: gerçek süre ek kayıtla uyuşmuyor")
        elif row["status"] == "cancelled":
            if m.cancelled_at is None or m.cancel_reason != CANCEL_REASONS.get(m.outcome):
                issue(f"care_tasks {row['id']}: iptal zamanı/nedeni eksik")
            elif not row["created_at"] <= m.cancelled_at < now:
                issue(f"care_tasks {row['id']}: iptal zamanı aralık dışında")
        elif m.outcome != "pending":
            issue(f"care_tasks {row['id']}: bekleyen görevin sonucu tutarsız")
        # C5: süre oranı
        ratio = m.actual_minutes / row["estimated_minutes"]
        lo, hi = P.ACTUAL_RATIO_CLIP
        if not (lo - 0.01 <= ratio <= hi * P.NIGHT_FACTOR + 0.01) or not math.isfinite(ratio):
            issue(f"C5 care_tasks {row['id']}: süre oranı aralık dışında ({ratio:.3f})")

    issues += [f"C3 {m}" for m in capacity_violations(ds)[:20]]
    issues += schema_0004_issues(ds)

    # Senaryo kuralları
    calendar = {d.day: d for d in ds.calendar}
    for e in t["encounters"]:
        kind = kind_of_unit[e["unit_id"]]
        if kind == "emergency" and e["discharged_at"] is not None:
            if (e["discharged_at"] - e["admitted_at"]).total_seconds() / 60 > P.ED_MAX_STAY_MIN + EPS:
                issue(f"encounters {e['id']}: acilde 8 saati aşan kalış")
        if kind == "outpatient":
            info = calendar.get(e["admitted_at"].astimezone(tz).date())
            if info is None or not info.is_workday:
                issue(f"encounters {e['id']}: poliklinik kapalı günde başvuru")
            elif info.is_half_holiday and e["admitted_at"].astimezone(tz).hour >= 13:
                issue(f"encounters {e['id']}: yarım gün tatilde 13.00 sonrası poliklinik")
    for eid in ds.ed_visits:
        if kind_of_unit[enc[eid]["unit_id"]] != "emergency":
            issue(f"triaj acil dışı başvuruda: {eid}")
    for a in ds.appointments:
        if a.status == "attended" and (a.encounter_id is None or a.arrival_at is None):
            issue(f"randevu {a.id}: gelen hastada başvuru yok")
        if a.status == "no_show" and a.encounter_id is not None:
            issue(f"randevu {a.id}: gelmeyen hastaya başvuru açılmış")
    return issues


def schema_0004_issues(ds: DatasetV2) -> list[str]:
    """Revizyon 0004 kolonları: kısıtların Python karşılığı + ek kayıtlarla birebir eşleme."""
    out: list[str] = []
    kind_of_unit = {u["id"]: u["kind"] for u in ds.tables["units"]}
    for e in ds.tables["encounters"]:
        value = e.get("triage_category", "eksik")
        visit = ds.ed_visits.get(e["id"])
        if kind_of_unit[e["unit_id"]] == "emergency":
            if visit is None or value != visit.triage or value not in TRIAGE_VALUES:
                out.append(f"encounters {e['id']}: triaj kolonu ek kayıtla uyuşmuyor")
        elif value is not None:
            out.append(f"encounters {e['id']}: acil dışı başvuruda triaj")
    for r in ds.tables["care_tasks"]:
        at, reason = r.get("cancelled_at", "eksik"), r.get("cancel_reason", "eksik")
        m = ds.task_meta.get(r["id"])
        if reason is not None and reason not in CANCEL_REASON_VALUES:
            out.append(f"ck_care_tasks_cancel_reason {r['id']}")
        if (at is None) != (reason is None):
            out.append(f"ck_care_tasks_cancel_pair {r['id']}")
        if at is not None and r["status"] != "cancelled":
            out.append(f"ck_care_tasks_cancel_status {r['id']}")
        if at is not None and at != "eksik" and at < r["created_at"]:
            out.append(f"ck_care_tasks_cancel_order {r['id']}")
        if r["status"] == "cancelled" and (at is None or m is None or at != m.cancelled_at or reason != m.cancel_reason):
            out.append(f"care_tasks {r['id']}: iptal kolonları ek kayıtla uyuşmuyor")
    return out[:50]


def _staff_available(ds: DatasetV2) -> dict[int, list[Interval]]:
    t = ds.tables
    plan = Interval(ds.scenario_start, ds.horizon_end + timedelta(days=1))
    shifts: dict[int, list[StaffShift]] = defaultdict(list)
    for s in t["staff_shifts"]:
        shifts[s["staff_id"]].append(StaffShift(s["id"], s["staff_id"], s["unit_id"], s["starts_at"], s["ends_at"], s["break_minutes"]))
    unav: dict[int, list[Unavailability]] = defaultdict(list)
    for u in t["staff_unavailability"]:
        unav[u["staff_id"]].append(Unavailability(u["staff_id"], Interval(u["starts_at"], u["ends_at"]), u["reason"]))
    return {
        s["id"]: list(staff_capacity(Staff(s["id"], s["synthetic_code"], s["role"], s["active"]), shifts[s["id"]], unav[s["id"]], plan).available)
        for s in t["staff"]
    }


def capacity_violations(ds: DatasetV2) -> list[str]:
    """C3: her tamamlanan görevin gerçekleşme aralığı, atanan personelin kullanılabilir zamanı içinde
    ve aynı personelin görevleri birbiriyle örtüşmüyor (Aşama 3 kapasite tanımıyla)."""
    available = _staff_available(ds)
    by_staff: dict[int, list[tuple[datetime, datetime, int]]] = defaultdict(list)
    shift_unit = defaultdict(set)
    for s in ds.tables["staff_shifts"]:
        shift_unit[s["staff_id"]].add(s["unit_id"])
    out = []
    for row in ds.tables["care_tasks"]:
        if row["status"] != "completed":
            continue
        m = ds.task_meta[row["id"]]
        start = row["completed_at"] - timedelta(minutes=row["actual_minutes"])
        by_staff[m.staff_id].append((start, row["completed_at"], row["id"]))  # type: ignore[index]
        if row["unit_id"] not in shift_unit[m.staff_id]:
            out.append(f"görev {row['id']}: personel bu serviste vardiyalı değil")
    for sid, items in by_staff.items():
        items.sort()
        for (s1, e1, a), (s2, _e2, b) in zip(items, items[1:]):
            if s2 < e1:
                out.append(f"personel {sid}: görev {a} ve {b} örtüşüyor")
        free = available[sid]
        for s, e, tid in items:
            if not any(i.start <= s and e <= i.end for i in free):  # type: ignore[operator]
                out.append(f"görev {tid}: personel {sid} kullanılabilir zamanının dışında")
    return out


def conservation(ds: DatasetV2) -> dict[int, dict[str, dict[str, float]]]:
    """C1–C2: servis bazında görev sayısı ve tahmini dakikanın sonuçlara ayrışması."""
    out: dict[int, dict[str, dict[str, float]]] = {}
    for row in ds.tables["care_tasks"]:
        unit = out.setdefault(row["unit_id"], {"count": Counter(), "estimated": Counter()})  # type: ignore[arg-type]
        outcome = ds.task_meta[row["id"]].outcome
        unit["count"][outcome] += 1
        unit["count"]["talep"] += 1
        unit["estimated"][outcome] += row["estimated_minutes"]
        unit["estimated"]["talep"] += row["estimated_minutes"]
    return {u: {k: dict(v) for k, v in d.items()} for u, d in out.items()}


def summarize(ds: DatasetV2) -> dict[int, dict]:
    """Servis bazında üretim özeti ve kalibrasyon ölçümleri (rapor ve kalibrasyon testleri için)."""
    t = ds.tables
    start, now = ds.scenario_start, ds.scenario_now
    period_h = (now - start).total_seconds() / 3600
    beds_by_unit = Counter()
    room_unit = {r["id"]: r["unit_id"] for r in t["rooms"]}
    for b in t["beds"]:
        beds_by_unit[room_unit[b["room_id"]]] += 1
    cons = conservation(ds)
    out: dict[int, dict] = {}
    for u in t["units"]:
        uid = u["id"]
        kind, profile, target = ds.unit_profiles[uid]
        encs = [e for e in t["encounters"] if e["unit_id"] == uid]
        tasks = [r for r in t["care_tasks"] if r["unit_id"] == uid]
        done = [r for r in tasks if r["status"] == "completed"]
        est_done = sum(r["estimated_minutes"] for r in done)
        act_done = sum(r["actual_minutes"] for r in done)
        info: dict = {
            "ad": u["name"], "tur": kind, "profil": profile, "basvuru": len(encs), "gorev": len(tasks),
            "talep_tahmini_dk": round(cons.get(uid, {}).get("estimated", {}).get("talep", 0.0), 1),
            "gerceklesen_gercek_dk": round(act_done, 1),
            "tamamlanan_tahmini_dk": round(est_done, 1),
            "sure_orani": round(act_done / est_done, 4) if est_done else None,
            "sonuclar": dict(sorted(cons.get(uid, {}).get("count", {}).items())),
        }  # fmt: skip
        if kind == "inpatient":
            beds = beds_by_unit[uid]
            occupied = 0.0
            for e in encs:
                a = max(e["admitted_at"], start)
                b = min(e["discharged_at"] or now, now)
                occupied += max(0.0, (b - a).total_seconds() / 3600)
            cutoff = now - timedelta(days=17)
            stays = [(e["discharged_at"] - e["admitted_at"]).total_seconds() / 86400 for e in encs
                     if e["discharged_at"] is not None and start <= e["admitted_at"] < cutoff]  # fmt: skip
            info.update(hedef_doluluk=target, olculen_doluluk=round(occupied / (beds * period_h), 4),
                        ortalama_kalis_gun=round(mean(stays), 3) if stays else None,
                        reddedilen_yatis=sum(1 for r in ds.rejected_admissions if r.unit_id == uid))  # fmt: skip
        if kind == "emergency":
            visits = [ds.ed_visits[e["id"]] for e in encs]
            los = defaultdict(list)
            for e in encs:
                if e["discharged_at"] is not None:
                    los[ds.ed_visits[e["id"]].triage].append((e["discharged_at"] - e["admitted_at"]).total_seconds() / 60)
            info.update(
                gunluk_basvuru=round(len(encs) / (period_h / 24), 2),
                triaj=dict(sorted(Counter(v.triage for v in visits).items())),
                sonuc=dict(sorted(Counter(v.outcome for v in visits).items())),
                yataksiz=sum(1 for v in visits if v.without_bed),
                kalis_medyan_dk={k: round(median(v), 1) for k, v in sorted(los.items())},
                kalis_max_dk=round(max((x for v in los.values() for x in v), default=0.0), 1),
            )
        if kind == "outpatient":
            appts = [a for a in ds.appointments if a.unit_id == uid]
            booked = [a for a in appts if not a.walk_in]
            info.update(
                randevu=len(booked), gelmeyen=sum(1 for a in booked if a.status == "no_show"),
                randevusuz=sum(1 for a in appts if a.walk_in),
                acik_gun=sum(1 for d in ds.calendar if d.is_workday),
            )  # fmt: skip
        out[uid] = info
    return out
