"""Üretici 2.0 veri kümesi: şema uyumlu tablolar ve şemada karşılığı olmayan bellek içi ek kayıtlar.

Triaj kategorisi ve iptal zamanı/nedeni revizyon 0004 kolonlarına yazılır (tablolarda doğrudan
bulunur). Başlama zamanı, görevi yapan personel, randevular ve reddedilen yatışlar yalnız bellekte
kalır (G2 kararı). İçerik özeti 1.0.0 ile aynı kanonik fonksiyonu 0004 kolonlarıyla kullanır.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import numpy as np

from medakis.repositories.schema import SEED_RESET_TABLES
from medakis.synthetic.canonical import content_sha256
from medakis.synthetic.v2.calendar import DayInfo
from medakis.synthetic.v2.profiles import ParamsV2

STREAMS = {
    "inpatient": 101, "emergency": 102, "outpatient": 103, "acuity": 104, "demand": 105,
    "skills": 106, "shifts": 107, "breaks": 108, "leave": 109, "execution": 110,
}  # fmt: skip

OUTCOMES = ("on_time", "late", "cancel_clinical", "cancel_discharged", "cancel_capacity", "pending")
CANCEL_REASONS = {"cancel_clinical": "clinical", "cancel_discharged": "discharged", "cancel_capacity": "capacity"}


def rng_for(params: ParamsV2, stream: str, sub: int = 0) -> np.random.Generator:
    return np.random.default_rng([params.seed, STREAMS[stream], sub])


@dataclass(frozen=True)
class TaskMeta:
    task_id: int
    unit_kind: str
    priority: int
    release_at: datetime
    actual_minutes: float  # yürütme akışından çekilen süre (tamamlanmasa da)
    outcome: str
    started_at: datetime | None = None
    staff_id: int | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None


@dataclass(frozen=True)
class EdVisit:
    encounter_id: int
    triage: str
    outcome: str  # "discharge" | "admit" | "transfer"
    decision_at: datetime
    without_bed: bool


@dataclass(frozen=True)
class Appointment:
    id: int
    unit_id: int
    scheduled_at: datetime | None  # randevusuzda None
    status: str  # "attended" | "no_show"
    arrival_at: datetime | None
    walk_in: bool
    encounter_id: int | None


@dataclass(frozen=True)
class RejectedAdmission:
    unit_id: int
    at: datetime


@dataclass(frozen=True)
class StaffingShortfall:
    unit_id: int
    day: date
    slot: str
    role: str
    needed: int
    planned: int


@dataclass
class DatasetV2:
    params: ParamsV2
    tables: dict[str, list[dict[str, Any]]]
    scenario_start: datetime
    scenario_now: datetime
    horizon_end: datetime
    calendar: list[DayInfo]
    unit_profiles: dict[int, tuple[str, str, float | None]]  # unit_id -> (kind, profil, hedef doluluk)
    task_meta: dict[int, TaskMeta] = field(default_factory=dict)
    ed_visits: dict[int, EdVisit] = field(default_factory=dict)
    appointments: list[Appointment] = field(default_factory=list)
    rejected_admissions: list[RejectedAdmission] = field(default_factory=list)
    shortfalls: list[StaffingShortfall] = field(default_factory=list)
    skipped_shifts: int = 0
    inpatient_rate_factors: dict[int, float] = field(default_factory=dict)  # kalibrasyon çarpanı (yataklı servis)

    def content_sha256(self) -> str:
        return content_sha256(self.tables, extended=True)

    def row_counts(self) -> dict[str, int]:
        return {t: len(self.tables[t]) for t in SEED_RESET_TABLES}

    def triage(self) -> dict[int, str]:
        return {eid: v.triage for eid, v in self.ed_visits.items()}

    def extras_sha256(self) -> str:
        payload = {
            "task_meta": [asdict(m) for _, m in sorted(self.task_meta.items())],
            "ed_visits": [asdict(v) for _, v in sorted(self.ed_visits.items())],
            "appointments": [asdict(a) for a in self.appointments],
            "rejected": [asdict(r) for r in self.rejected_admissions],
            "shortfalls": [asdict(s) for s in self.shortfalls],
            "skipped_shifts": self.skipped_shifts,
            "inpatient_rate_factors": sorted(self.inpatient_rate_factors.items()),
        }
        return _digest(payload)

    def demand_sha256(self) -> str:
        """Talep katmanının özeti: başvurular, bakım düzeyi, görev talebi, triaj, randevular.

        Kadro profilinden bağımsız olmalıdır (tasarım §9.5).
        """
        keys = ("id", "encounter_id", "unit_id", "task_type_id", "created_at", "due_at", "estimated_minutes")
        payload = {
            "encounters": self.tables["encounters"],
            "acuity": self.tables["acuity_assessments"],
            "tasks": [{k: t[k] for k in keys} for t in self.tables["care_tasks"]],
            "triage": sorted(self.triage().items()),
            "appointments": [asdict(a) for a in self.appointments],
        }
        return _digest(payload)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return format(round(value, 6), ".6f")
    raise TypeError(type(value).__name__)


def _normalize(value: Any) -> Any:
    if isinstance(value, float):
        return format(round(value, 6), ".6f")
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def _digest(payload: Any) -> str:
    raw = json.dumps(_normalize(payload), sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()
