"""Synthetic generator 2.0.0 fields: triage category, cancellation time and reason.

Only three nullable columns and five CHECK constraints. No other schema, role or privilege change.

- encounters.triage_category: NULL or 'red' / 'yellow' / 'green'.
- care_tasks.cancelled_at, care_tasks.cancel_reason: both NULL or both set; only on cancelled
  tasks; reason is 'clinical' / 'discharged' / 'capacity'; cancelled_at >= created_at.

Backward compatibility: every constraint is satisfied when the new columns are NULL, so all
generator 1.0.0 rows (including cancelled tasks without a time) stay valid without any data
change. ADD CONSTRAINT therefore validates existing rows immediately and cannot fail on them;
NOT VALID is not used (it would leave constraints unvalidated and differ from the model).
Existing table-level grants cover the new columns; no GRANT/REVOKE is issued.

Safety: the design decision keeps medakis_dev and medakis_demo at revision 0003. Upgrading
them requires an explicit "-x sema_0004_onayi=<database>". Downgrade refuses to drop the
columns while any row still holds a value in them (no silent data loss).

Downgrade concurrency: both tables are locked ACCESS EXCLUSIVE (the lock DROP COLUMN needs anyway)
BEFORE the value check. Alembic runs the whole downgrade in one transaction (env.py,
transactional DDL), so the lock is held from the check until the columns are dropped and committed;
no other session can write (or read) a value in between. lock_timeout bounds the wait.

Revision ID: 0004_triage_cancel_columns
Revises: 0003_seed_write_window
"""

from alembic import context, op
import sqlalchemy as sa

revision = "0004_triage_cancel_columns"
down_revision = "0003_seed_write_window"
branch_labels = None
depends_on = None

PROTECTED_DATABASES = ("medakis_dev", "medakis_demo")
LOCKED_TABLES = ("encounters", "care_tasks")
LOCK_TIMEOUT = "10s"
TRIAGE_CHECK = "triage_category IS NULL OR triage_category IN ('red','yellow','green')"
CANCEL_CHECKS = {
    "ck_care_tasks_cancel_reason": "cancel_reason IS NULL OR cancel_reason IN ('clinical','discharged','capacity')",
    "ck_care_tasks_cancel_pair": "(cancelled_at IS NULL) = (cancel_reason IS NULL)",
    "ck_care_tasks_cancel_status": "cancelled_at IS NULL OR status = 'cancelled'",
    "ck_care_tasks_cancel_order": "cancelled_at IS NULL OR cancelled_at >= created_at",
}


def _database() -> str:
    return op.get_bind().execute(sa.text("SELECT current_database()")).scalar_one()


def _require_explicit_approval(database: str) -> None:
    if database in PROTECTED_DATABASES:
        approval = context.get_x_argument(as_dictionary=True).get("sema_0004_onayi")
        if approval != database:
            raise RuntimeError(
                f"{database} is kept at revision 0003 by design; pass -x sema_0004_onayi={database} to upgrade it."
            )


def upgrade() -> None:
    _require_explicit_approval(_database())
    op.add_column("encounters", sa.Column("triage_category", sa.Text(), nullable=True))
    op.add_column("care_tasks", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("care_tasks", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.create_check_constraint(op.f("ck_encounters_triage_category"), "encounters", TRIAGE_CHECK)
    for name, condition in CANCEL_CHECKS.items():
        op.create_check_constraint(op.f(name), "care_tasks", condition)


def downgrade() -> None:
    connection = op.get_bind()
    if not connection.in_transaction():
        raise RuntimeError("Downgrade must run inside the migration transaction (lock and drop together).")
    # Kilit önce: kontrol ile DROP arasında başka bir oturum değer yazamaz (işlem sonuna kadar tutulur).
    connection.execute(sa.text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    connection.execute(sa.text(f"LOCK TABLE {', '.join(LOCKED_TABLES)} IN ACCESS EXCLUSIVE MODE"))
    used = connection.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM encounters WHERE triage_category IS NOT NULL) + "
            "(SELECT count(*) FROM care_tasks WHERE cancelled_at IS NOT NULL OR cancel_reason IS NOT NULL)"
        )
    ).scalar_one()
    if used:
        raise RuntimeError(
            f"{used} rows hold revision 0004 values; reseed with generator 1.0.0 before downgrading (no silent data loss)."
        )
    for name in reversed(CANCEL_CHECKS):
        op.drop_constraint(op.f(name), "care_tasks", type_="check")
    op.drop_constraint(op.f("ck_encounters_triage_category"), "encounters", type_="check")
    op.drop_column("care_tasks", "cancel_reason")
    op.drop_column("care_tasks", "cancelled_at")
    op.drop_column("encounters", "triage_category")
