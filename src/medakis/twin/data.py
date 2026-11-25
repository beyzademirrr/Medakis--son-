"""Dijital Hastane İkizi: ortak veri sağlayıcı.

Tek kaynak sentetik veri üreticisidir (üretici 2.1; `generate_v21`). Birimler, personel, roller, geçerlilik tarihli
yetkinlikler, vardiyalar, molalar/izinler, hasta kayıtları ve bakım görevleri buradan okunur; hiçbir ekran kendi
personel ya da birim listesini üretmez. Hesaplar mevcut kurallarla aynıdır: görev talebi = görevin tahmini dakikası,
kapasite = vardiyadaki personel-dakika − mola/izin, etkin kapasite = kapasite × kullanım oranı (Katman A yapılandırması).
Veri setinde bulunmayan yerleşim/kamera varsayımları `config/dijital_ikiz.json` dosyasındadır.
"""

from __future__ import annotations

import bisect
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cached_property
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config" / "dijital_ikiz.json"
MOMENTS_FILE = ROOT / "docs" / "veri" / "asama5_guvenli_duzeltme2.json"
ROLE_TR = {"nurse": "Hemşire", "support": "Destek personeli", "supervisor": "Sorumlu hemşire"}
KIND_TR = {"inpatient": "Yataklı", "emergency": "Acil", "outpatient": "Ayaktan"}
UTILIZATION = 0.85


def nurses_short(staff_minutes: float | None, window: int = 60) -> int:
    """Personel-dakika açığını eksik hemşire sayısına çevirir: pencerede bir hemşire `window` dakika verir; yukarı yuvarlanır.
    Yarım dakikanın altındaki sayısal artık açık sayılmaz."""
    if not staff_minutes or staff_minutes < 0.5:
        return 0
    return math.ceil(staff_minutes / window - 1e-9)


def nurses_short_work(work_minutes: float | None, window: int = 60) -> int:
    """İş dakikası açığı (talep − etkin kapasite) → eksik hemşire: bir hemşire pencerede `window` × kullanım oranı iş verir."""
    return nurses_short((work_minutes or 0.0) / UTILIZATION, window)
SOURCE_LABEL = "Sentetik Demo · üretici 2.1 (veritabanı gerekmez)"


@dataclass(frozen=True)
class Unit:
    id: int
    name: str
    kind: str
    camera: str
    area: str
    capacity: int
    color: str
    rect: tuple[int, int, int, int]
    per_patient: float


@dataclass(frozen=True)
class Shift:
    staff_id: int
    unit_id: int
    start: datetime
    end: datetime
    template: str


@dataclass(frozen=True)
class Absence:
    staff_id: int
    start: datetime
    end: datetime
    reason: str


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> float:
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds() / 60)


class TwinData:
    """Sentetik hastanenin salt okunur, indeksli görünümü (bir kez yüklenir)."""

    def __init__(self, dataset: Any | None = None) -> None:
        if dataset is None:
            from medakis.synthetic.v21 import generate_v21

            dataset = generate_v21()
        self.ds = dataset
        self.tables = dataset.base.tables
        self.tz = ZoneInfo(dataset.base.params.timezone)
        self.config = load_config()
        cfg_units = self.config["birimler"]
        self.units: dict[int, Unit] = {}
        for u in self.tables["units"]:
            c = cfg_units[str(u["id"])]
            self.units[u["id"]] = Unit(u["id"], u["name"].replace("Yatakli", "Yataklı"), u["kind"], c["kamera"], c["alan"], int(c["kapasite"]),
                                       c["renk"], tuple(c["kat_plani"]), float(c["hasta_basi_kisi"]))  # fmt: skip
        self.staff = {s["id"]: {"id": s["id"], "kod": s["synthetic_code"], "rol": s["role"], "aktif": s["active"]} for s in self.tables["staff"]}
        templates = {t["id"]: t["name"] for t in self.tables["shift_templates"]}
        self.shifts = sorted((Shift(r["staff_id"], r["unit_id"], r["starts_at"], r["ends_at"], templates.get(r["template_id"], "?"))
                              for r in self.tables["staff_shifts"]), key=lambda s: s.start)  # fmt: skip
        self._shift_starts = [s.start for s in self.shifts]
        self.absences: dict[int, list[Absence]] = defaultdict(list)
        for r in self.tables["staff_unavailability"]:
            self.absences[r["staff_id"]].append(Absence(r["staff_id"], r["starts_at"], r["ends_at"], r["reason"]))
        skill_code = {s["id"]: s["code"] for s in self.tables["skills"]}
        self.skill_names = {s["code"]: s["name"] for s in self.tables["skills"]}
        self.skills: dict[int, list[tuple[str, datetime, datetime | None]]] = defaultdict(list)
        for r in self.tables["staff_skills"]:
            self.skills[r["staff_id"]].append((skill_code[r["skill_id"]], r["valid_from"], r["valid_until"]))
        self.task_role = {t["id"]: t["required_role"] for t in self.tables["task_types"]}

    # ---- İndeksler ------------------------------------------------------------------------------------------

    @cached_property
    def _tasks_by_unit(self) -> dict[int, tuple[list[datetime], list[dict]]]:
        out: dict[int, list[dict]] = defaultdict(list)
        for t in self.tables["care_tasks"]:
            out[t["unit_id"]].append(t)
        res = {}
        for u, rows in out.items():
            rows.sort(key=lambda t: t["due_at"])
            res[u] = ([t["due_at"] for t in rows], rows)
        return res

    @cached_property
    def _encounters_by_unit(self) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = defaultdict(list)
        for e in self.tables["encounters"]:
            out[e["unit_id"]].append(e)
        return out

    # ---- Sorgular -------------------------------------------------------------------------------------------

    def moments(self) -> list[datetime]:
        runs = json.loads(MOMENTS_FILE.read_text(encoding="utf-8"))["calistirmalar"]
        return [datetime.fromisoformat(r["an"]) for r in runs]

    def local(self, t: datetime) -> datetime:
        return t.astimezone(self.tz)

    def skills_valid(self, staff_id: int, at: datetime) -> list[str]:
        return sorted({c for c, f, u in self.skills.get(staff_id, []) if f <= at and (u is None or u > at)})

    def absence_at(self, staff_id: int, start: datetime, end: datetime) -> list[Absence]:
        return [a for a in self.absences.get(staff_id, []) if a.start < end and start < a.end]

    def shifts_overlapping(self, start: datetime, end: datetime) -> list[Shift]:
        lo = bisect.bisect_left(self._shift_starts, start - timedelta(hours=13))
        hi = bisect.bisect_left(self._shift_starts, end)
        return [s for s in self.shifts[lo:hi] if s.start < end and start < s.end]

    def staff_on_duty(self, at: datetime, minutes: int = 60) -> list[dict]:
        """`at` ile `at+minutes` arasında vardiyası olan personel: birim, rol, vardiya, mola/izin ve geçerli yetkinlikler."""
        end = at + timedelta(minutes=minutes)
        rows = []
        for s in self.shifts_overlapping(at, end):
            p = self.staff[s.staff_id]
            ab = self.absence_at(s.staff_id, at, end)
            izin = any(a.reason == "Izin" for a in ab)
            mola = [a for a in ab if a.reason == "Mola"]
            rows.append({"id": p["id"], "kod": p["kod"], "rol": p["rol"], "birim": s.unit_id, "vardiya": s.template,
                         "baslangic": s.start, "bitis": s.end, "izinli": izin, "mola": [(a.start, a.end) for a in mola],
                         "molada": any(a.start <= at < a.end for a in mola), "yetkinlikler": self.skills_valid(s.staff_id, at),
                         "kullanilabilir_dk": 0.0 if izin else max(0.0, _overlap(s.start, s.end, at, end) - sum(_overlap(a.start, a.end, at, end) for a in mola))})  # fmt: skip
        return rows

    def census(self, unit_id: int, at: datetime) -> int:
        """`at` anında birimde aktif hasta kaydı sayısı."""
        return sum(1 for e in self._encounters_by_unit.get(unit_id, []) if e["admitted_at"] <= at and (e["discharged_at"] is None or e["discharged_at"] > at))

    def tasks_window(self, unit_id: int, start: datetime, end: datetime, role: str | None = None) -> list[dict]:
        """Son tarihi pencerede olan, `start` anında bilinen ve iptal/tamamlanma ile kapanmamış görevler."""
        due, rows = self._tasks_by_unit.get(unit_id, ([], []))
        lo, hi = bisect.bisect_left(due, start), bisect.bisect_left(due, end)
        out = []
        for t in rows[lo:hi]:
            if t["created_at"] > start:
                continue
            if t["status"] == "cancelled" and t["cancelled_at"] and t["cancelled_at"] <= start:
                continue
            if role and self.task_role.get(t["task_type_id"]) != role:
                continue
            out.append(t)
        return out

    def hourly_matrix(self, day: date, role: str | None = "nurse") -> list[dict]:
        """Birim × saat: talep dakikası (görev tahmini süresi) ve etkin kapasite (personel-dakika × kullanım oranı)."""
        start_local = datetime(day.year, day.month, day.day, tzinfo=self.tz)
        out = []
        for h in range(24):
            h0 = (start_local + timedelta(hours=h)).astimezone(UTC)
            h1 = h0 + timedelta(hours=1)
            staff = [s for s in self.staff_on_duty(h0, 60) if role is None or s["rol"] == role]
            for u in self.units:
                demand = sum(t["estimated_minutes"] for t in self.tasks_window(u, h0, h1, role))
                cap = sum(s["kullanilabilir_dk"] for s in staff if s["birim"] == u)
                eff = cap * UTILIZATION
                out.append({"birim": u, "saat": h, "baslangic": h0, "talep_dk": round(demand, 1), "kapasite_dk": round(cap, 1),
                            "etkin_kapasite_dk": round(eff, 1), "oran": None if eff == 0 else round(demand / eff, 3),
                            "acik_dk": round(max(0.0, demand - eff), 1), "eksik_hemsire": nurses_short_work(max(0.0, demand - eff)),
                            "rezerv_dk": round(max(0.0, eff - demand), 1),
                            "personel": sum(1 for s in staff if s["birim"] == u and s["kullanilabilir_dk"] > 0)})  # fmt: skip
        return out
