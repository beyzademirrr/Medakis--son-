"""medakis_seed için işlem içi (commit edilmeyen) yazma yetkisi (FAZ 1.2C, T1/T2).

medakis_test dışında medakis_seed kalıcı olarak yalnız okur. Bir seed yazması tek bir owner
bağlantısında ve tek işlemde yürür:

    danışma kilidi + tablo kilitleri (owner) → sunucu/veritabanı kimliği kontrolü →
    rol üyeliği kontrolü → GRANT (commit edilmez) → SET LOCAL ROLE medakis_seed →
    seed yazmaları (yalnız seed yetkisiyle) → RESET ROLE → REVOKE → yetki kontrolü → COMMIT

Commit edilmemiş GRANT başka oturumlara görünmez; işlem geri alınır veya süreç ölürse
GRANT de geri alınır. Commit edilen bir yetki penceresi yoktur.

Gerekli tek kurulum: medakis_owner, medakis_seed rolüne yalnız SET seçeneğiyle üyedir
(INHERIT FALSE, ADMIN FALSE). Kurulum docker/postgres/provision.sql içindedir.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.repositories.credentials import OWNER_ROLE, SEED_ROLE
from medakis.repositories.schema import SEED_AUDIT_TABLE, SEED_PROTECTED_HISTORY, SEED_RESET_TABLES

_RESET = ", ".join(SEED_RESET_TABLES)
_ALL_SEED_TABLES = [*SEED_RESET_TABLES, *SEED_PROTECTED_HISTORY, SEED_AUDIT_TABLE]
_WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "MAINTAIN", "REFERENCES", "TRIGGER")


def identity_sequences(conn: Connection) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT pg_get_serial_sequence(format('public.%I', c.table_name), 'id') "
            "FROM information_schema.columns c "
            "WHERE c.table_schema = 'public' AND c.column_name = 'id' AND c.is_identity = 'YES' "
            "AND c.table_name = ANY(CAST(:tables AS text[])) ORDER BY c.table_name"
        ),
        {"tables": list(SEED_RESET_TABLES)},
    ).scalars()
    return [r for r in rows if r]


def membership(conn: Connection) -> tuple[bool, bool, bool] | None:
    """(admin, inherit, set) seçenekleri; üyelik yoksa None."""
    row = conn.execute(
        text(
            "SELECT m.admin_option, m.inherit_option, m.set_option FROM pg_auth_members m "
            "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
            "WHERE r.rolname = :seed AND u.rolname = :owner"
        ),
        {"seed": SEED_ROLE, "owner": OWNER_ROLE},
    ).all()
    if not row:
        return None
    if len(row) != 1:
        return (True, True, True)  # birden çok verenli üyelik: beklenmeyen kurulum, reddedilir
    return tuple(bool(v) for v in row[0])  # type: ignore[return-value]


def require_membership(conn: Connection) -> None:
    found = membership(conn)
    if found != (False, False, True):
        raise DomainError(
            errors.SEED_ROLE_SETUP,
            "medakis_owner → medakis_seed rol üyeliği yok veya beklenen biçimde değil "
            "(yalnız SET; INHERIT ve ADMIN kapalı). Yönetici kurulumu gerekir; hiçbir şey değişmedi.",
        )


def grant_scoped(conn: Connection, with_delete: bool) -> None:
    """Yalnız bu işlem için gereken yetkiler. İlk yüklemede DELETE verilmez."""
    privileges = "INSERT, DELETE" if with_delete else "INSERT"
    conn.execute(text(f"GRANT {privileges} ON {_RESET} TO {SEED_ROLE}"))
    conn.execute(text(f"GRANT INSERT ON {SEED_AUDIT_TABLE} TO {SEED_ROLE}"))
    conn.execute(text(f"GRANT USAGE, UPDATE ON SEQUENCE {', '.join(identity_sequences(conn))} TO {SEED_ROLE}"))


def revoke_scoped(conn: Connection) -> None:
    conn.execute(text(f"REVOKE INSERT, DELETE ON {_RESET} FROM {SEED_ROLE}"))
    conn.execute(text(f"REVOKE INSERT ON {SEED_AUDIT_TABLE} FROM {SEED_ROLE}"))
    conn.execute(text(f"REVOKE USAGE, UPDATE ON SEQUENCE {', '.join(identity_sequences(conn))} FROM {SEED_ROLE}"))


def become_seed(conn: Connection) -> None:
    conn.execute(text(f"SET LOCAL ROLE {SEED_ROLE}"))
    if conn.execute(text("SELECT current_user")).scalar_one() != SEED_ROLE:
        raise RuntimeError("Seed rolüne geçilemedi.")


def leave_seed(conn: Connection) -> None:
    conn.execute(text("RESET ROLE"))
    if conn.execute(text("SELECT current_user")).scalar_one() != OWNER_ROLE:
        raise RuntimeError("Owner rolüne dönülemedi.")


def write_open(conn: Connection) -> bool:
    """medakis_seed'in seed tablolarında veya sayaçlarında herhangi bir yazma/kilit yetkisi var mı."""
    tables = conn.execute(
        text(
            "SELECT bool_or(has_table_privilege(:r, format('public.%I', t), p)) "
            "FROM unnest(CAST(:tables AS text[])) t, unnest(CAST(:privs AS text[])) p"
        ),
        {"r": SEED_ROLE, "tables": _ALL_SEED_TABLES, "privs": list(_WRITE_PRIVILEGES)},
    ).scalar_one()
    sequences = conn.execute(
        text("SELECT bool_or(has_sequence_privilege(:r, s, p)) FROM unnest(CAST(:seqs AS text[])) s, unnest(ARRAY['USAGE', 'UPDATE']) p"),
        {"r": SEED_ROLE, "seqs": identity_sequences(conn)},
    ).scalar_one()
    return bool(tables) or bool(sequences)


def require_idle(conn: Connection) -> None:
    if write_open(conn):
        raise RuntimeError("Seed yazma yetkisi işlem sonunda kapatılamadı; işlem geri alınıyor.")
