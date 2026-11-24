"""Tesis, servis, oda ve yatak erişimi (SQLAlchemy Core, parametreli)."""

from __future__ import annotations

from sqlalchemy import Connection, insert, select

from medakis.domain.models import BedPlacement, Unit
from medakis.repositories.schema import beds, facilities, rooms, units


def insert_facility(conn: Connection, name: str, timezone: str = "Europe/Istanbul") -> int:
    return conn.execute(insert(facilities).values(name=name, timezone=timezone).returning(facilities.c.id)).scalar_one()


def insert_unit(conn: Connection, facility_id: int, name: str, kind: str, active: bool = True) -> int:
    stmt = insert(units).values(facility_id=facility_id, name=name, kind=kind, active=active).returning(units.c.id)
    return conn.execute(stmt).scalar_one()


def insert_room(conn: Connection, unit_id: int, code: str) -> int:
    return conn.execute(insert(rooms).values(unit_id=unit_id, code=code).returning(rooms.c.id)).scalar_one()


def insert_bed(conn: Connection, room_id: int, code: str, active: bool = True) -> int:
    return conn.execute(insert(beds).values(room_id=room_id, code=code, active=active).returning(beds.c.id)).scalar_one()


def get_unit(conn: Connection, unit_id: int) -> Unit | None:
    row = conn.execute(select(units).where(units.c.id == unit_id)).mappings().first()
    return Unit(**row) if row else None


def get_bed_placement(conn: Connection, bed_id: int) -> BedPlacement | None:
    stmt = (
        select(beds.c.id.label("bed_id"), rooms.c.unit_id, beds.c.active.label("bed_active"))
        .join(rooms, rooms.c.id == beds.c.room_id)
        .where(beds.c.id == bed_id)
    )
    row = conn.execute(stmt).mappings().first()
    return BedPlacement(**row) if row else None
