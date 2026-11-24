"""PostgreSQL şemasının SQLite v2 referansıyla yapısal eşdeğerliği (veritabanı sunucusu gerektirmez)."""

import sqlite3

import pytest
from sqlalchemy import Boolean, DateTime, Double, Integer, BigInteger, Text, Time
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.schema import CheckConstraint, CreateIndex, CreateTable

from medakis.repositories.schema import PRIVILEGES, SCHEMA_0004_COLUMNS, legacy_columns, metadata

TABLES = metadata.tables


@pytest.fixture(scope="module")
def sqlite_db(request):
    root = request.config.rootpath
    conn = sqlite3.connect(":memory:")
    conn.executescript((root / "medakis_schema.sql").read_text(encoding="utf-8"))
    yield conn
    conn.close()


def sqlite_tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")]


def test_same_34_tables(sqlite_db):
    assert sorted(sqlite_tables(sqlite_db)) == sorted(TABLES)
    assert len(TABLES) == 34


@pytest.mark.parametrize("name", sorted(TABLES))
def test_columns_order_nullability_and_pk(sqlite_db, name):
    info = list(sqlite_db.execute(f"PRAGMA table_info({name})"))
    table = TABLES[name]
    # SQLite referansı revizyon 0003 düzeyindedir; 0004 kolonları yalnız PostgreSQL'e eklenir.
    assert [r[1] for r in info] == [c.name for c in legacy_columns(table)]
    extra = [c for c in table.columns if c.name in SCHEMA_0004_COLUMNS.get(name, ())]
    assert [c.name for c in extra] == list(SCHEMA_0004_COLUMNS.get(name, ())) and all(c.nullable for c in extra)
    for _, column, _, notnull, _, pk in info:
        assert table.c[column].nullable is not bool(notnull or pk), column
    expected_pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
    assert [c.name for c in table.primary_key.columns] == expected_pk


TIME_SUFFIXES = ("_at", "_from", "_until", "_start", "_end")
TIME_NAMES = {"as_of"}


@pytest.mark.parametrize("name", sorted(TABLES))
def test_type_mapping(sqlite_db, name):
    table = TABLES[name]
    for _, column, sql_type, *_ in sqlite_db.execute(f"PRAGMA table_info({name})"):
        pg_type = table.c[column].type
        if sql_type == "REAL":
            assert isinstance(pg_type, Double), column
        elif sql_type == "TEXT" and column == "start_local":
            assert isinstance(pg_type, Time) and not pg_type.timezone, column
        elif sql_type == "TEXT" and (column.endswith(TIME_SUFFIXES) or column in TIME_NAMES):
            assert isinstance(pg_type, DateTime) and pg_type.timezone, column
        elif sql_type == "TEXT" and column.endswith("_json"):
            assert isinstance(pg_type, JSONB), column
        elif sql_type == "TEXT":
            assert isinstance(pg_type, Text), column
        else:
            assert sql_type == "INTEGER" and isinstance(pg_type, (Integer, BigInteger, Boolean)), column


def test_flags_are_boolean_and_other_integers_are_not(sqlite_db):
    booleans = {f"{t.name}.{c.name}" for t in TABLES.values() for c in t.columns if isinstance(c.type, Boolean)}
    assert booleans == {
        "units.active", "beds.active", "patients.is_synthetic", "staff.active",
        "rag_documents.is_synthetic", "rag_retrieval_hits.cited",
        "rag_eval_cases.answerable", "rag_eval_results.abstention_correct",
    }  # fmt: skip


@pytest.mark.parametrize("name", sorted(TABLES))
def test_foreign_keys(sqlite_db, name):
    grouped = {}
    for row in sqlite_db.execute(f"PRAGMA foreign_key_list({name})"):
        grouped.setdefault(row[0], []).append(row)
    expected = {
        (rows[0][2], tuple(r[3] for r in sorted(rows, key=lambda r: r[1])), tuple(r[4] for r in sorted(rows, key=lambda r: r[1])))
        for rows in grouped.values()
    }
    actual = {
        (fk.referred_table.name, tuple(c.name for c in fk.columns), tuple(e.column.name for e in fk.elements))
        for fk in TABLES[name].foreign_key_constraints
    }
    assert actual == expected


@pytest.mark.parametrize("name", sorted(TABLES))
def test_unique_constraints(sqlite_db, name):
    expected = set()
    for row in sqlite_db.execute(f"PRAGMA index_list({name})"):
        if row[3] == "u":  # UNIQUE kısıtı; PK ve açık indeksler hariç
            expected.add(tuple(r[2] for r in sqlite_db.execute(f"PRAGMA index_info({row[1]})")))
    actual = set()
    for constraint in TABLES[name].constraints:
        if constraint.__class__.__name__ == "UniqueConstraint":
            actual.add(tuple(c.name for c in constraint.columns))
    assert actual == expected


def test_named_indexes(sqlite_db):
    expected = {}
    for name, table in sqlite_db.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
        expected[name] = (table, tuple(r[2] for r in sqlite_db.execute(f"PRAGMA index_info({name})")))
    actual = {
        ix.name: (t.name, tuple(c.name for c in ix.columns))
        for t in TABLES.values()
        for ix in t.indexes
    }
    assert actual == expected
    assert len(actual) == 11


def test_every_double_column_must_be_finite():
    missing = []
    for table in TABLES.values():
        checks = {str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)}
        for column in table.columns:
            if isinstance(column.type, Double):
                wanted = f"{column.name} > '-Infinity'::float8 AND {column.name} < 'Infinity'::float8"
                if wanted not in checks:
                    missing.append(f"{table.name}.{column.name}")
    # 0-1 aralıklı metrikler için BETWEEN NaN ve sonsuzu zaten reddeder; canlı testlerde sınanır.
    assert sorted(missing) == [
        "rag_eval_results.citation_accuracy",
        "rag_eval_results.faithfulness",
        "rag_eval_results.recall_at_5",
        "rag_eval_results.reciprocal_rank_at_10",
    ]


def test_ddl_compiles_with_short_identifiers():
    dialect = postgresql.dialect()
    for table in TABLES.values():
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert " REAL" not in ddl, table.name
        for ix in table.indexes:
            str(CreateIndex(ix).compile(dialect=dialect))
        for constraint in table.constraints:
            if constraint.name:
                assert len(str(constraint.name)) <= 63, constraint.name


def test_only_approved_exclude_constraints():
    found = {
        c.name: c.where is not None
        for t in TABLES.values()
        for c in t.constraints
        if isinstance(c, ExcludeConstraint)
    }
    assert found == {
        "ex_encounters_bed_overlap": True,
        "ex_staff_shifts_staff_overlap": False,
        "ex_assignments_staff_overlap": False,
        "ex_rag_documents_approved_overlap": True,
    }


def test_privilege_table_covers_schema_and_keeps_rag_out_of_business_tables():
    assert set(PRIVILEGES["medakis_app"]) == set(TABLES)
    rag = set(PRIVILEGES["medakis_rag"])
    assert rag <= {t for t in TABLES if t.startswith("rag_")}
    assert not {"assignments", "plan_approvals", "staff", "staffing_gaps", "optimization_runs", "audit_events"} & rag
    from medakis.repositories.schema import SEED_RESET_TABLES

    for role, grants in PRIVILEGES.items():
        for table, privileges in grants.items():
            assert "TRUNCATE" not in privileges, (role, table)
            # Tek DELETE istisnası: medakis_seed, yalnız sıfırlanan 17 operasyonel tabloda (dev/test).
            if "DELETE" in privileges:
                assert role == "medakis_seed" and table in SEED_RESET_TABLES, (role, table)
    # Atama, onay ve denetim kayıtları değiştirilemez; yalnız eklenir.
    for table in ("assignments", "plan_approvals", "audit_events", "staffing_gaps"):
        assert PRIVILEGES["medakis_app"][table] == frozenset({"SELECT", "INSERT"})
