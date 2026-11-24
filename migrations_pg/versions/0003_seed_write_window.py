"""medakis_seed: lock rights on history tables, read-only by default outside medakis_test (FAZ 1.2C fixes).

Privileges only; no table, column or constraint changes.

- MAINTAIN on the protected history tables. It is needed only for LOCK TABLE ... IN SHARE MODE,
  so that no session can add history rows while a seed transaction runs (review finding F2).
  MAINTAIN grants no INSERT/UPDATE/DELETE.
- Outside medakis_test the role becomes read-only: INSERT/DELETE on reset tables, INSERT on
  audit_events, MAINTAIN on history tables and sequence rights are revoked. The seed service
  opens these rights as medakis_owner only for the duration of one seed run and closes them
  afterwards (src/medakis/repositories/seed_access.py), so the seed credential alone cannot
  bypass the confirmation, backup and history checks (review finding F3).

Revision ID: 0003_seed_write_window
Revises: 0002_seed_role_grants
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_seed_write_window"
down_revision = "0002_seed_role_grants"
branch_labels = None
depends_on = None

RESET_TABLES = (
    "facilities", "units", "rooms", "beds", "patients", "encounters", "acuity_assessments",
    "staff", "skills", "staff_skills", "shift_templates", "staff_shifts", "staff_unavailability",
    "task_types", "task_skill_requirements", "care_tasks", "workload_observations",
)
PROTECTED_HISTORY = (
    "model_runs", "workload_forecasts", "optimization_runs", "assignments", "staffing_gaps", "plan_approvals",
)
AUDIT_TABLE = "audit_events"
PERMANENT_WRITE_DATABASES = ("medakis_test",)


def _identity_sequences(connection) -> list[str]:
    rows = connection.execute(
        sa.text(
            "SELECT pg_get_serial_sequence(format('public.%I', c.table_name), 'id') "
            "FROM information_schema.columns c "
            "WHERE c.table_schema = 'public' AND c.column_name = 'id' AND c.is_identity = 'YES' "
            "AND c.table_name = ANY(CAST(:tables AS text[])) ORDER BY c.table_name"
        ),
        {"tables": list(RESET_TABLES)},
    ).scalars()
    return [r for r in rows if r]


def _permanent(connection) -> bool:
    return connection.execute(sa.text("SELECT current_database()")).scalar_one() in PERMANENT_WRITE_DATABASES


def upgrade() -> None:
    connection = op.get_bind()
    op.execute(f"GRANT MAINTAIN ON {', '.join(PROTECTED_HISTORY)} TO medakis_seed")
    if _permanent(connection):
        return
    op.execute(f"REVOKE INSERT, DELETE ON {', '.join(RESET_TABLES)} FROM medakis_seed")
    op.execute(f"REVOKE INSERT ON {AUDIT_TABLE} FROM medakis_seed")
    op.execute(f"REVOKE MAINTAIN ON {', '.join(PROTECTED_HISTORY)} FROM medakis_seed")
    op.execute(f"REVOKE USAGE, UPDATE ON SEQUENCE {', '.join(_identity_sequences(connection))} FROM medakis_seed")


def downgrade() -> None:
    connection = op.get_bind()
    op.execute(f"REVOKE MAINTAIN ON {', '.join(PROTECTED_HISTORY)} FROM medakis_seed")
    if _permanent(connection):
        return
    op.execute(f"GRANT INSERT, DELETE ON {', '.join(RESET_TABLES)} TO medakis_seed")
    op.execute(f"GRANT INSERT ON {AUDIT_TABLE} TO medakis_seed")
    op.execute(f"GRANT USAGE, UPDATE ON SEQUENCE {', '.join(_identity_sequences(connection))} TO medakis_seed")
