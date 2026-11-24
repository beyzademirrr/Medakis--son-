"""Alan modeli: repository'lerin döndürdüğü değişmez kayıtlar (yalnız 1.2C'de kullanılan alanlar)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class Unit:
    id: int
    facility_id: int
    name: str
    kind: str
    active: bool


@dataclass(frozen=True)
class BedPlacement:
    bed_id: int
    unit_id: int
    bed_active: bool


@dataclass(frozen=True)
class Encounter:
    id: int
    patient_id: int
    unit_id: int
    bed_id: int | None
    admitted_at: datetime
    discharged_at: datetime | None


@dataclass(frozen=True)
class TaskType:
    id: int
    code: str
    base_minutes: float
    required_role: str


@dataclass(frozen=True)
class CareTask:
    id: int
    encounter_id: int
    unit_id: int
    task_type_id: int
    created_at: datetime
    due_at: datetime
    estimated_minutes: float
    actual_minutes: float | None
    status: str
    completed_at: datetime | None
    # Revizyon 0004 (üretici 2.0.0). 1.0.0 kayıtlarında ve 0003 şemasında bilinmez: None.
    cancelled_at: datetime | None = None


@dataclass(frozen=True)
class Staff:
    id: int
    synthetic_code: str
    role: str
    active: bool


@dataclass(frozen=True)
class ShiftTemplate:
    id: int
    name: str
    start_local: time
    duration_minutes: int


@dataclass(frozen=True)
class StaffShift:
    id: int
    staff_id: int
    unit_id: int
    starts_at: datetime
    ends_at: datetime
    break_minutes: int
