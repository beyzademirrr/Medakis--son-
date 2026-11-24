"""medakis_seed privileges for synthetic data management (FAZ 1.2C).

DELETE (never TRUNCATE) on the 17 reset tables, read-only on protected history tables,
append-only on audit_events, and setval on the reset tables' identity sequences. The role
itself and its CONNECT rights come from docker/postgres/provision.sql: it can connect to
medakis_dev and medakis_test only.

Revision ID: 0002_seed_role_grants
Revises: 0001_v2_baseline
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_seed_role_grants"
down_revision = "0001_v2_baseline"
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


def _identity_sequences(connection) -> list[str]:
    # Only tables with an identity "id" column own a sequence (staff_skills and
    # task_skill_requirements use composite keys).
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


def upgrade() -> None:
    connection = op.get_bind()
    if not connection.execute(sa.text("SELECT count(*) FROM pg_roles WHERE rolname = 'medakis_seed'")).scalar_one():
        raise RuntimeError("medakis_seed role is missing: run scripts/db/setup.ps1 first.")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON {', '.join(RESET_TABLES)} TO medakis_seed")
    op.execute(f"GRANT SELECT ON {', '.join(PROTECTED_HISTORY)} TO medakis_seed")
    op.execute(f"GRANT SELECT, INSERT ON {AUDIT_TABLE} TO medakis_seed")
    sequences = _identity_sequences(connection)
    op.execute(f"GRANT USAGE, UPDATE ON SEQUENCE {', '.join(sequences)} TO medakis_seed")


def downgrade() -> None:
    connection = op.get_bind()
    tables = RESET_TABLES + PROTECTED_HISTORY + (AUDIT_TABLE,)
    op.execute(f"REVOKE ALL ON {', '.join(tables)} FROM medakis_seed")
    op.execute(f"REVOKE ALL ON SEQUENCE {', '.join(_identity_sequences(connection))} FROM medakis_seed")
