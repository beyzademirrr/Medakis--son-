"""Hasta, başvuru, bakım değerlendirmesi ve görev erişimi.

Görev okuma ve güncelleme fonksiyonları `unit_id` zorunlu alır: servis izolasyonu imzadan gelir.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection, and_, insert, select, update

from medakis.domain.models import CareTask, Encounter, TaskType
from medakis.repositories.schema import (
    acuity_assessments,
    care_tasks,
    encounters,
    legacy_columns,
    patients,
    task_types,
)

# Revizyon 0003 kolonları: alan modeli bunları bekler ve 0003'te kalan medakis_dev'de de çalışır.
_CARE_TASK_COLUMNS = legacy_columns(care_tasks)
_ENCOUNTER_COLUMNS = legacy_columns(encounters)


def insert_patient(conn: Connection, synthetic_code: str) -> int:
    stmt = insert(patients).values(synthetic_code=synthetic_code).returning(patients.c.id)
    return conn.execute(stmt).scalar_one()


def insert_encounter(
    conn: Connection, patient_id: int, unit_id: int, bed_id: int | None, admitted_at: datetime
) -> int:
    stmt = (
        insert(encounters)
        .values(patient_id=patient_id, unit_id=unit_id, bed_id=bed_id, admitted_at=admitted_at)
        .returning(encounters.c.id)
    )
    return conn.execute(stmt).scalar_one()


def get_encounter(conn: Connection, encounter_id: int) -> Encounter | None:
    row = conn.execute(select(*_ENCOUNTER_COLUMNS).where(encounters.c.id == encounter_id)).mappings().first()
    return Encounter(**row) if row else None


def bed_has_overlapping_stay(conn: Connection, bed_id: int, start: datetime, end: datetime | None) -> bool:
    """[start, end) aralığında aynı yatakta başka yatış var mı? (bitişi NULL olan yatış sınırsızdır)"""
    conditions = [encounters.c.bed_id == bed_id]
    conditions.append((encounters.c.discharged_at.is_(None)) | (encounters.c.discharged_at > start))
    if end is not None:
        conditions.append(encounters.c.admitted_at < end)
    return conn.execute(select(encounters.c.id).where(and_(*conditions)).limit(1)).first() is not None


def insert_acuity(conn: Connection, encounter_id: int, assessed_at: datetime, level: int, multiplier: float) -> int:
    stmt = (
        insert(acuity_assessments)
        .values(encounter_id=encounter_id, assessed_at=assessed_at, level=level, multiplier=multiplier)
        .returning(acuity_assessments.c.id)
    )
    return conn.execute(stmt).scalar_one()


def list_acuity_until(conn: Connection, encounter_id: int, until: datetime) -> list[tuple[datetime, float]]:
    """`until` anına kadar (dahil) yapılmış değerlendirmeler; gelecekteki kayıtlar okunmaz."""
    stmt = (
        select(acuity_assessments.c.assessed_at, acuity_assessments.c.multiplier)
        .where(acuity_assessments.c.encounter_id == encounter_id, acuity_assessments.c.assessed_at <= until)
        .order_by(acuity_assessments.c.assessed_at)
    )
    return [(r.assessed_at, r.multiplier) for r in conn.execute(stmt)]


def insert_task_type(conn: Connection, code: str, name: str, base_minutes: float, required_role: str) -> int:
    stmt = (
        insert(task_types)
        .values(code=code, name=name, base_minutes=base_minutes, required_role=required_role)
        .returning(task_types.c.id)
    )
    return conn.execute(stmt).scalar_one()


def get_task_type(conn: Connection, task_type_id: int) -> TaskType | None:
    stmt = select(task_types.c.id, task_types.c.code, task_types.c.base_minutes, task_types.c.required_role).where(
        task_types.c.id == task_type_id
    )
    row = conn.execute(stmt).mappings().first()
    return TaskType(**row) if row else None


def insert_task(
    conn: Connection,
    encounter_id: int,
    unit_id: int,
    task_type_id: int,
    created_at: datetime,
    due_at: datetime,
    estimated_minutes: float,
) -> int:
    stmt = (
        insert(care_tasks)
        .values(
            encounter_id=encounter_id,
            unit_id=unit_id,
            task_type_id=task_type_id,
            created_at=created_at,
            due_at=due_at,
            estimated_minutes=estimated_minutes,
            status="pending",
        )
        .returning(care_tasks.c.id)
    )
    return conn.execute(stmt).scalar_one()


def get_task(conn: Connection, unit_id: int, task_id: int, for_update: bool = False) -> CareTask | None:
    stmt = select(*_CARE_TASK_COLUMNS).where(care_tasks.c.id == task_id, care_tasks.c.unit_id == unit_id)
    if for_update:
        stmt = stmt.with_for_update()
    row = conn.execute(stmt).mappings().first()
    return CareTask(**row) if row else None


def list_tasks(
    conn: Connection, unit_id: int, start: datetime, end: datetime, status: str | None = None
) -> list[CareTask]:
    """[start, end) aralığında son tarihi olan görevler; yalnız istenen servis."""
    conditions = [care_tasks.c.unit_id == unit_id, care_tasks.c.due_at >= start, care_tasks.c.due_at < end]
    if status is not None:
        conditions.append(care_tasks.c.status == status)
    stmt = select(*_CARE_TASK_COLUMNS).where(*conditions).order_by(care_tasks.c.due_at, care_tasks.c.id)
    return [CareTask(**row) for row in conn.execute(stmt).mappings()]


def mark_completed(conn: Connection, unit_id: int, task_id: int, completed_at: datetime, actual_minutes: float) -> None:
    stmt = (
        update(care_tasks)
        .where(care_tasks.c.id == task_id, care_tasks.c.unit_id == unit_id, care_tasks.c.status == "pending")
        .values(status="completed", completed_at=completed_at, actual_minutes=actual_minutes)
    )
    if conn.execute(stmt).rowcount != 1:
        raise LookupError("Görev güncellenemedi.")


def mark_cancelled(conn: Connection, unit_id: int, task_id: int) -> None:
    stmt = (
        update(care_tasks)
        .where(care_tasks.c.id == task_id, care_tasks.c.unit_id == unit_id, care_tasks.c.status == "pending")
        .values(status="cancelled")
    )
    if conn.execute(stmt).rowcount != 1:
        raise LookupError("Görev güncellenemedi.")
