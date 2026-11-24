"""Migration 0004 (G2): yapı, kısıtlar ve 1.0.0 kayıtlarıyla geriye dönük uyum.

PostgreSQL'e bağlanılmaz. Migration fonksiyonları sahte bir `op` ile çağrılır; CHECK ifadeleri bellek
içi SQLite'ta (sunucu yok, dosya yok) gerçek satırlarla değerlendirilir.
"""

import ast
import importlib.util
import sqlite3
from datetime import UTC
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, select
from sqlalchemy.dialects import postgresql

from medakis.repositories.schema import (
    CANCEL_REASON_VALUES,
    CONSTRAINTS_0004,
    REVISION_0004,
    SCHEMA_0004_COLUMNS,
    TRIAGE_VALUES,
    care_tasks,
    encounters,
    legacy_columns,
    metadata,
)
from medakis.synthetic.canonical import content_sha256
from medakis.synthetic.generator import generate as generate_v1
from medakis.synthetic.v2 import ParamsV2, generate_v2

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations_pg" / "versions" / "0004_triage_cancel_columns.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("m0004", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class FakeBind:
    def __init__(self, database, used=0, in_tx=True, events=None):
        self.database, self.used, self.sql, self.in_tx = database, used, [], in_tx
        self.events = events if events is not None else []

    def in_transaction(self):
        return self.in_tx

    def execute(self, clause):
        self.events.append(("sql", str(clause)))
        sql = str(clause)
        self.sql.append(sql)
        return FakeResult(self.database if "current_database" in sql else self.used)


class FakeOp:
    def __init__(self, bind):
        self.bind, self.calls = bind, []

    def get_bind(self):
        return self.bind

    def f(self, name):
        return name

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            self.bind.events.append(("op", name))

        return record


class FakeContext:
    def __init__(self, args):
        self.args = args

    def get_x_argument(self, as_dictionary=False):
        return dict(self.args)


def run(direction, database, x=None, used=0, in_tx=True):
    module = load_migration()
    op = FakeOp(FakeBind(database, used, in_tx))
    module.op, module.context = op, FakeContext(x or {})
    getattr(module, direction)()
    return op


# ---- Zincir ve yapı -------------------------------------------------------------------------


def test_revision_chain_and_head():
    module = load_migration()
    assert module.revision == REVISION_0004 and module.down_revision == "0003_seed_write_window"
    assert ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head() == REVISION_0004


def test_upgrade_adds_only_three_nullable_columns_and_five_checks():
    op = run("upgrade", "medakis_test")
    names = [c[0] for c in op.calls]
    assert sorted(set(names)) == ["add_column", "create_check_constraint"]
    added = {(args[0], args[1].name): args[1] for name, args, _ in op.calls if name == "add_column"}
    assert set(added) == {(t, c) for t, cols in SCHEMA_0004_COLUMNS.items() for c in cols}
    assert all(col.nullable and col.server_default is None for col in added.values())
    checks = {args[0]: (args[1], args[2]) for name, args, _ in op.calls if name == "create_check_constraint"}
    assert checks == CONSTRAINTS_0004
    assert not any("GRANT" in s or "REVOKE" in s or "UPDATE " in s for s in op.bind.sql)


def test_migration_has_no_not_valid_and_no_data_changes():
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]  # belge metni hariç
    code = ast.unparse(tree)
    assert "NOT VALID" not in code and "postgresql_not_valid" not in code
    for forbidden in ("op.execute", "UPDATE ", "INSERT ", "DELETE ", "TRUNCATE", "GRANT ", "REVOKE ", "CREATE ROLE"):
        assert forbidden not in code, forbidden


@pytest.mark.parametrize("database", ["medakis_dev", "medakis_demo"])
def test_upgrade_refuses_pinned_databases_without_explicit_approval(database):
    with pytest.raises(RuntimeError, match="sema_0004_onayi"):
        run("upgrade", database)
    with pytest.raises(RuntimeError):
        run("upgrade", database, {"sema_0004_onayi": "medakis_test"})
    assert run("upgrade", database, {"sema_0004_onayi": database}).calls


def _position(events, predicate):
    return next(i for i, e in enumerate(events) if predicate(e))


def test_downgrade_locks_both_tables_before_checking_values():
    op = run("downgrade", "medakis_test", used=0)
    events = op.bind.events
    lock = _position(events, lambda e: e[0] == "sql" and e[1].startswith("LOCK TABLE"))
    check = _position(events, lambda e: e[0] == "sql" and "count(*)" in e[1])
    first_drop = _position(events, lambda e: e[0] == "op" and e[1].startswith("drop_"))
    assert lock < check < first_drop
    assert events[lock][1] == "LOCK TABLE encounters, care_tasks IN ACCESS EXCLUSIVE MODE"
    assert any("lock_timeout" in e[1] for e in events[:lock] if e[0] == "sql")
    assert not any("COMMIT" in e[1].upper() for e in events if e[0] == "sql")  # kilit işlem sonuna kadar


def test_downgrade_with_values_drops_nothing_even_after_locking():
    with pytest.raises(RuntimeError, match="no silent data loss"):
        run("downgrade", "medakis_test", used=1)
    module = load_migration()
    op = FakeOp(FakeBind("medakis_test", 1))
    module.op, module.context = op, FakeContext({})
    with pytest.raises(RuntimeError):
        module.downgrade()
    assert any(e[1].startswith("LOCK TABLE") for e in op.bind.events if e[0] == "sql")
    assert not [c for c in op.calls if c[0].startswith("drop_")]  # veri kaybı yok


def test_downgrade_refuses_outside_a_transaction():
    with pytest.raises(RuntimeError, match="inside the migration transaction"):
        run("downgrade", "medakis_test", in_tx=False)


def test_migration_env_runs_in_single_transaction():
    env = (ROOT / "migrations_pg" / "env.py").read_text(encoding="utf-8")
    assert "context.begin_transaction()" in env and "transaction_per_migration" not in env


def test_lifecycle_test_reads_head_instead_of_hardcoded_revision():
    code = (ROOT / "tests" / "test_postgres_infra.py").read_text(encoding="utf-8")
    assert "0003_seed_write_window|" not in code and "get_current_head()" in code


def test_downgrade_refuses_while_values_exist_and_drops_otherwise():
    with pytest.raises(RuntimeError, match="no silent data loss"):
        run("downgrade", "medakis_test", used=3)
    op = run("downgrade", "medakis_test", used=0)
    dropped = [args for name, args, _ in op.calls if name == "drop_column"]
    assert sorted((t, c) for t, c in dropped) == sorted((t, c) for t, cols in SCHEMA_0004_COLUMNS.items() for c in cols)
    assert len([1 for name, *_ in op.calls if name == "drop_constraint"]) == len(CONSTRAINTS_0004)


# ---- Model ile migration aynı -------------------------------------------------------------


def test_model_columns_and_checks_match_migration():
    for table, cols in SCHEMA_0004_COLUMNS.items():
        t = metadata.tables[table]
        assert [c.name for c in t.columns][-len(cols):] == list(cols)  # sona eklenir (ADD COLUMN gibi)
        assert all(t.c[c].nullable for c in cols)
    model = {
        c.name: (t.name, str(c.sqltext))
        for t in (encounters, care_tasks)
        for c in t.constraints
        if isinstance(c, CheckConstraint) and c.name in CONSTRAINTS_0004
    }
    assert model == CONSTRAINTS_0004
    assert set(TRIAGE_VALUES) == {"red", "yellow", "green"}
    assert set(CANCEL_REASON_VALUES) == {"clinical", "discharged", "capacity"}


def test_legacy_columns_exclude_0004_and_repositories_use_them():
    sql = str(select(*legacy_columns(care_tasks)).compile(dialect=postgresql.dialect()))
    assert "cancel" not in sql
    assert "triage" not in str(select(*legacy_columns(encounters)).compile(dialect=postgresql.dialect()))
    for name in ("care_repo.py", "workload_repo.py"):
        code = (ROOT / "src" / "medakis" / "repositories" / name).read_text(encoding="utf-8")
        assert "select(care_tasks)" not in code and "select(encounters)" not in code, name


# ---- Geriye dönük uyum: CHECK ifadeleri gerçek satırlarla -----------------------------------


def _iso(value):
    return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture()
def checks_db():
    conn = sqlite3.connect(":memory:")
    enc_checks = ", ".join(f"CONSTRAINT {n} CHECK ({sql})" for n, (t, sql) in CONSTRAINTS_0004.items() if t == "encounters")
    task_checks = ", ".join(f"CONSTRAINT {n} CHECK ({sql})" for n, (t, sql) in CONSTRAINTS_0004.items() if t == "care_tasks")
    conn.execute(f"CREATE TABLE encounters (id INTEGER PRIMARY KEY, triage_category TEXT, {enc_checks})")
    conn.execute(
        "CREATE TABLE care_tasks (id INTEGER PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, "
        f"cancelled_at TEXT, cancel_reason TEXT, {task_checks})"
    )
    yield conn
    conn.close()


def _insert(conn, tables):
    conn.executemany(
        "INSERT INTO encounters VALUES (?, ?)", [(e["id"], e.get("triage_category")) for e in tables["encounters"]]
    )
    conn.executemany(
        "INSERT INTO care_tasks VALUES (?, ?, ?, ?, ?)",
        [(r["id"], r["status"], _iso(r["created_at"]), _iso(r.get("cancelled_at")), r.get("cancel_reason"))
         for r in tables["care_tasks"]],
    )  # fmt: skip


def test_all_v1_rows_satisfy_0004_constraints_without_change(checks_db):
    tables = generate_v1().tables
    assert any(r["status"] == "cancelled" for r in tables["care_tasks"])  # zamanı olmayan eski iptaller
    _insert(checks_db, tables)
    assert checks_db.execute("SELECT count(*) FROM care_tasks WHERE cancelled_at IS NOT NULL").fetchone() == (0,)


def test_v2_rows_satisfy_0004_constraints(checks_db):
    tables = generate_v2(ParamsV2(days=7)).tables
    assert any(r["cancel_reason"] for r in tables["care_tasks"]) and any(e["triage_category"] for e in tables["encounters"])
    _insert(checks_db, tables)


@pytest.mark.parametrize(
    ("row", "constraint"),
    [
        ((1, "cancelled", "2026-10-05T10:00:00.000000Z", "2026-10-05T11:00:00.000000Z", "other"), "ck_care_tasks_cancel_reason"),
        ((2, "cancelled", "2026-10-05T10:00:00.000000Z", "2026-10-05T11:00:00.000000Z", None), "ck_care_tasks_cancel_pair"),
        ((3, "cancelled", "2026-10-05T10:00:00.000000Z", None, "capacity"), "ck_care_tasks_cancel_pair"),
        ((4, "pending", "2026-10-05T10:00:00.000000Z", "2026-10-05T11:00:00.000000Z", "capacity"), "ck_care_tasks_cancel_status"),
        ((5, "cancelled", "2026-10-05T10:00:00.000000Z", "2026-10-05T09:59:00.000000Z", "clinical"), "ck_care_tasks_cancel_order"),
    ],
)  # fmt: skip
def test_invalid_cancellation_rows_are_rejected_by_named_check(checks_db, row, constraint):
    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        checks_db.execute("INSERT INTO care_tasks VALUES (?, ?, ?, ?, ?)", row)


def test_invalid_triage_is_rejected(checks_db):
    with pytest.raises(sqlite3.IntegrityError, match="ck_encounters_triage_category"):
        checks_db.execute("INSERT INTO encounters VALUES (1, 'blue')")
    checks_db.execute("INSERT INTO encounters VALUES (2, NULL)")


# ---- Kanonik özet sürüme bağlı ------------------------------------------------------------


def test_legacy_digest_ignores_0004_columns_extended_requires_them():
    tables = generate_v2(ParamsV2(days=3)).tables
    stripped = {t: [{k: v for k, v in r.items() if k not in SCHEMA_0004_COLUMNS.get(t, ())} for r in rows]
                for t, rows in tables.items()}  # fmt: skip
    assert content_sha256(tables) == content_sha256(stripped)
    assert content_sha256(tables, extended=True) != content_sha256(tables)
    with pytest.raises(KeyError):
        content_sha256(stripped, extended=True)
