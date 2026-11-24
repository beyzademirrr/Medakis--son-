"""Aşama 3 iş yükü ve kapasite okumaları. YALNIZ SELECT; yazma fonksiyonu yoktur.

Bütün görev sorguları `unit_id` zorunlu alır (servis izolasyonu). Aralıklar [başlangıç, bitiş)'tir.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, and_, func, literal_column, or_, select, text

from medakis.domain.capacity import Unavailability
from medakis.domain.models import CareTask, Encounter, ShiftTemplate, Staff, Unit
from medakis.domain.time import Interval
from medakis.repositories.schema import (
    acuity_assessments,
    audit_events,
    care_tasks,
    encounters,
    shift_templates,
    skills,
    staff,
    staff_skills,
    staff_unavailability,
    legacy_columns,
    task_skill_requirements,
    task_types,
    units,
)

SEED_ACTION = "synthetic_seed_applied"
_ONE_MINUTE = literal_column("interval '1 minute'")

# Revizyon 0003 kolonları: alan modeli bunları bekler ve 0003'te kalan medakis_dev'de de çalışır.
_CARE_TASK_COLUMNS = legacy_columns(care_tasks)
_ENCOUNTER_COLUMNS = legacy_columns(encounters)


def list_units(conn: Connection) -> list[Unit]:
    stmt = select(units.c.id, units.c.facility_id, units.c.name, units.c.kind, units.c.active).order_by(units.c.id)
    return [Unit(**row) for row in conn.execute(stmt).mappings()]


def list_templates(conn: Connection) -> list[ShiftTemplate]:
    stmt = select(
        shift_templates.c.id, shift_templates.c.name, shift_templates.c.start_local, shift_templates.c.duration_minutes
    ).order_by(shift_templates.c.id)
    return [ShiftTemplate(**row) for row in conn.execute(stmt).mappings()]


def task_type_info(conn: Connection) -> dict[int, dict[str, str]]:
    stmt = select(task_types.c.id, task_types.c.code, task_types.c.name, task_types.c.required_role)
    return {row["id"]: dict(row) for row in conn.execute(stmt).mappings()}


def completed_tasks_overlapping(conn: Connection, unit_id: int, start: datetime, end: datetime) -> list[CareTask]:
    """Gerçekleşme aralığı [completed_at − actual, completed_at) ile [start, end) kesişen tamamlanan görevler."""
    realized_start = care_tasks.c.completed_at - care_tasks.c.actual_minutes * _ONE_MINUTE
    stmt = (
        select(*_CARE_TASK_COLUMNS)
        .where(
            care_tasks.c.unit_id == unit_id,
            care_tasks.c.status == "completed",
            care_tasks.c.completed_at > start,
            realized_start < end,
        )
        .order_by(care_tasks.c.completed_at, care_tasks.c.id)
    )
    return [CareTask(**row) for row in conn.execute(stmt).mappings()]


def has_cancel_time_column(conn: Connection) -> bool:
    """Revizyon 0004 kolonu `care_tasks.cancelled_at` var mı (katalogdan; 0003'te yok)."""
    return bool(
        conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'care_tasks' AND column_name = 'cancelled_at'"
            )
        ).scalar_one()
    )


def unfinished_statement(unit_id: int, at: datetime, with_cancel_time: bool):  # noqa: ANN201
    """Aday görevler (üst küme); kesin zaman semantiği domain.workload.pending_at içindedir.

    0003 (with_cancel_time=False): iptal → vadesi `at`'ten sonra olanlar (K3 belirsizliği için).
    0004: ayrıca iptal zamanı `at`'ten sonra olanlar (o anda hâlâ açıktı). Kolon yoksa hiç anılmaz.
    """
    cancelled = care_tasks.c.status == "cancelled"
    if with_cancel_time:
        columns = [*_CARE_TASK_COLUMNS, care_tasks.c.cancelled_at]
        cancelled_open = and_(cancelled, or_(care_tasks.c.due_at >= at, care_tasks.c.cancelled_at > at))
    else:
        columns = _CARE_TASK_COLUMNS
        cancelled_open = and_(cancelled, care_tasks.c.due_at >= at)
    return (
        select(*columns)
        .where(
            care_tasks.c.unit_id == unit_id,
            care_tasks.c.created_at <= at,
            or_(
                care_tasks.c.status == "pending",
                and_(care_tasks.c.status == "completed", care_tasks.c.completed_at > at),
                cancelled_open,
            ),
        )
        .order_by(care_tasks.c.due_at, care_tasks.c.id)
    )


def tasks_known_unfinished_at(conn: Connection, unit_id: int, at: datetime) -> list[CareTask]:
    """`at` anında bilinen ve o anda bitmemiş olabilecek görevler; iptal zamanı varsa onunla birlikte."""
    stmt = unfinished_statement(unit_id, at, has_cancel_time_column(conn))
    return [CareTask(**row) for row in conn.execute(stmt).mappings()]


def encounters_open_at(conn: Connection, unit_id: int, at: datetime) -> list[Encounter]:
    stmt = select(*_ENCOUNTER_COLUMNS).where(
        encounters.c.unit_id == unit_id,
        encounters.c.admitted_at <= at,
        or_(encounters.c.discharged_at.is_(None), encounters.c.discharged_at > at),
    )
    return [Encounter(**row) for row in conn.execute(stmt).mappings()]


def staff_by_ids(conn: Connection, ids: Sequence[int]) -> list[Staff]:
    if not ids:
        return []
    stmt = select(staff.c.id, staff.c.synthetic_code, staff.c.role, staff.c.active).where(staff.c.id.in_(list(ids)))
    return [Staff(**row) for row in conn.execute(stmt).mappings()]


def unavailability_overlapping(conn: Connection, staff_ids: Sequence[int], start: datetime, end: datetime) -> list[Unavailability]:
    if not staff_ids:
        return []
    stmt = (
        select(staff_unavailability)
        .where(
            staff_unavailability.c.staff_id.in_(list(staff_ids)),
            staff_unavailability.c.starts_at < end,
            staff_unavailability.c.ends_at > start,
        )
        .order_by(staff_unavailability.c.staff_id, staff_unavailability.c.starts_at)
    )
    return [
        Unavailability(row["staff_id"], Interval(row["starts_at"], row["ends_at"]), row["reason"])
        for row in conn.execute(stmt).mappings()
    ]


def skills_valid_at(conn: Connection, staff_ids: Sequence[int], at: datetime) -> dict[int, list[str]]:
    """`at` anında geçerli personel yetkinlikleri (salt okunur): valid_from ≤ at ve (valid_until yok ya da > at)."""
    if not staff_ids:
        return {}
    stmt = (
        select(staff_skills.c.staff_id, skills.c.name)
        .join(skills, skills.c.id == staff_skills.c.skill_id)
        .where(
            staff_skills.c.staff_id.in_(list(staff_ids)),
            staff_skills.c.valid_from <= at,
            or_(staff_skills.c.valid_until.is_(None), staff_skills.c.valid_until > at),
        )
        .order_by(staff_skills.c.staff_id, skills.c.name)
    )
    out: dict[int, list[str]] = {}
    for row in conn.execute(stmt).mappings():
        names = out.setdefault(row["staff_id"], [])
        if row["name"] not in names:
            names.append(row["name"])
    return out


def task_type_skill_names(conn: Connection) -> dict[int, list[str]]:
    """Görev türü → gerekli yetkinlik adları (salt okunur)."""
    stmt = (
        select(task_skill_requirements.c.task_type_id, skills.c.name)
        .join(skills, skills.c.id == task_skill_requirements.c.skill_id)
        .order_by(task_skill_requirements.c.task_type_id, skills.c.name)
    )
    out: dict[int, list[str]] = {}
    for row in conn.execute(stmt).mappings():
        out.setdefault(row["task_type_id"], []).append(row["name"])
    return out


def latest_acuity_at(conn: Connection, encounter_ids: Sequence[int], at: datetime) -> dict[int, dict[str, Any]]:
    """Başvuru başına `at` anında bilinen EN SON bakım değerlendirmesi (salt okunur). Gelecekteki kayıt kullanılmaz."""
    if not encounter_ids:
        return {}
    stmt = (
        select(acuity_assessments.c.encounter_id, acuity_assessments.c.assessed_at, acuity_assessments.c.level,
               acuity_assessments.c.multiplier)
        .where(acuity_assessments.c.encounter_id.in_(list(encounter_ids)), acuity_assessments.c.assessed_at <= at)
        .order_by(acuity_assessments.c.encounter_id, acuity_assessments.c.assessed_at)
    )  # fmt: skip
    return {row["encounter_id"]: dict(row) for row in conn.execute(stmt).mappings()}  # sıralı: son kayıt kalır


def last_seed_record(conn: Connection) -> dict[str, Any] | None:
    stmt = (
        select(audit_events.c.occurred_at, audit_events.c.entity_id, audit_events.c.detail_json)
        .where(audit_events.c.action == SEED_ACTION)
        .order_by(audit_events.c.id.desc())
        .limit(1)
    )
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def task_time_bounds(conn: Connection) -> tuple[datetime | None, datetime | None]:
    """Seed kaydı yoksa kapsama için yedek bilgi: en erken oluşturma ve en geç olay zamanı."""
    stmt = select(func.min(care_tasks.c.created_at), func.max(func.coalesce(care_tasks.c.completed_at, care_tasks.c.created_at)))
    low, high = conn.execute(stmt).one()
    return low, high
