"""Personel, yetkinlik, vardiya şablonu, vardiya ve uygunsuzluk erişimi."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection, insert, select

from medakis.domain.models import ShiftTemplate, Staff, StaffShift
from medakis.repositories.schema import shift_templates, skills, staff, staff_shifts, staff_skills, staff_unavailability


def insert_staff(conn: Connection, synthetic_code: str, role: str, active: bool = True) -> int:
    stmt = insert(staff).values(synthetic_code=synthetic_code, role=role, active=active).returning(staff.c.id)
    return conn.execute(stmt).scalar_one()


def get_staff(conn: Connection, staff_id: int) -> Staff | None:
    row = conn.execute(select(staff).where(staff.c.id == staff_id)).mappings().first()
    return Staff(**row) if row else None


def insert_skill(conn: Connection, code: str, name: str) -> int:
    return conn.execute(insert(skills).values(code=code, name=name).returning(skills.c.id)).scalar_one()


def insert_staff_skill(
    conn: Connection, staff_id: int, skill_id: int, valid_from: datetime, valid_until: datetime | None
) -> None:
    conn.execute(
        insert(staff_skills).values(staff_id=staff_id, skill_id=skill_id, valid_from=valid_from, valid_until=valid_until)
    )


def list_skill_validities(conn: Connection, staff_id: int, skill_id: int) -> list[tuple[datetime, datetime | None]]:
    stmt = (
        select(staff_skills.c.valid_from, staff_skills.c.valid_until)
        .where(staff_skills.c.staff_id == staff_id, staff_skills.c.skill_id == skill_id)
        .order_by(staff_skills.c.valid_from)
    )
    return [(r.valid_from, r.valid_until) for r in conn.execute(stmt)]


def insert_template(conn: Connection, name: str, start_local, duration_minutes: int) -> int:  # noqa: ANN001
    stmt = (
        insert(shift_templates)
        .values(name=name, start_local=start_local, duration_minutes=duration_minutes)
        .returning(shift_templates.c.id)
    )
    return conn.execute(stmt).scalar_one()


def get_template(conn: Connection, template_id: int) -> ShiftTemplate | None:
    row = conn.execute(select(shift_templates).where(shift_templates.c.id == template_id)).mappings().first()
    return ShiftTemplate(**row) if row else None


def insert_shift(
    conn: Connection,
    staff_id: int,
    unit_id: int,
    template_id: int | None,
    starts_at: datetime,
    ends_at: datetime,
    break_minutes: int,
) -> int:
    stmt = (
        insert(staff_shifts)
        .values(
            staff_id=staff_id,
            unit_id=unit_id,
            template_id=template_id,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=break_minutes,
        )
        .returning(staff_shifts.c.id)
    )
    return conn.execute(stmt).scalar_one()


def staff_has_overlapping_shift(conn: Connection, staff_id: int, start: datetime, end: datetime) -> bool:
    stmt = select(staff_shifts.c.id).where(
        staff_shifts.c.staff_id == staff_id, staff_shifts.c.starts_at < end, staff_shifts.c.ends_at > start
    )
    return conn.execute(stmt.limit(1)).first() is not None


def list_shifts(conn: Connection, unit_id: int, start: datetime, end: datetime) -> list[StaffShift]:
    """[start, end) ile kesişen vardiyalar; yalnız istenen servis."""
    stmt = (
        select(
            staff_shifts.c.id,
            staff_shifts.c.staff_id,
            staff_shifts.c.unit_id,
            staff_shifts.c.starts_at,
            staff_shifts.c.ends_at,
            staff_shifts.c.break_minutes,
        )
        .where(staff_shifts.c.unit_id == unit_id, staff_shifts.c.starts_at < end, staff_shifts.c.ends_at > start)
        .order_by(staff_shifts.c.starts_at, staff_shifts.c.id)
    )
    return [StaffShift(**row) for row in conn.execute(stmt).mappings()]


def insert_unavailability(conn: Connection, staff_id: int, starts_at: datetime, ends_at: datetime, reason: str) -> int:
    stmt = (
        insert(staff_unavailability)
        .values(staff_id=staff_id, starts_at=starts_at, ends_at=ends_at, reason=reason)
        .returning(staff_unavailability.c.id)
    )
    return conn.execute(stmt).scalar_one()


def staff_has_unavailability(conn: Connection, staff_id: int, start: datetime, end: datetime, reason: str) -> bool:
    stmt = select(staff_unavailability.c.id).where(
        staff_unavailability.c.staff_id == staff_id,
        staff_unavailability.c.reason == reason,
        staff_unavailability.c.starts_at < end,
        staff_unavailability.c.ends_at > start,
    )
    return conn.execute(stmt.limit(1)).first() is not None
