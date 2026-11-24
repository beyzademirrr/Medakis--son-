"""Denetim kaydı: yalnız ekleme ve okuma. Silme veya güncelleme fonksiyonu yoktur."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, insert, select

from medakis.repositories.schema import audit_events


def insert_event(
    conn: Connection,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | None,
    detail: dict[str, Any],
    occurred_at: datetime | None = None,
) -> int:
    """Ayrıntı yalnız toplu/teknik bilgi içermelidir; hasta ayrıntısı yazılmaz."""
    stmt = (
        insert(audit_events)
        .values(
            occurred_at=occurred_at or datetime.now(UTC),
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail_json=detail,
        )
        .returning(audit_events.c.id)
    )
    return conn.execute(stmt).scalar_one()


def list_events(conn: Connection, action: str) -> list[dict[str, Any]]:
    stmt = select(audit_events).where(audit_events.c.action == action).order_by(audit_events.c.id)
    return [dict(row) for row in conn.execute(stmt).mappings()]
