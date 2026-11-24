"""PostgreSQL şemasının gerçek sunucuda doğrulanması: pytest -m postgres

Bütün yazmalar medakis_test içindedir ve geri alınır. Roller gerçek parolalarıyla, Windows
Credential Manager'dan okunarak bağlanır. Parolalar çıktıya yazılmaz.
"""

import secrets
import subprocess
import sys
from pathlib import Path

import keyring
import psycopg
import pytest
from pg_target import CONTAINER, PORT  # noqa: F401
from sqlalchemy import Boolean, DateTime, Double, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError

from medakis.repositories.credentials import ALL_ROLES, APP_ROLE, OWNER_ROLE, RAG_ROLE, SERVICE, get_password
from medakis.repositories.database import migration_engine, runtime_engine
from medakis.repositories.schema import PRIVILEGES, metadata

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = "medakis_test"
TABLES = metadata.tables
HASH = "a" * 64

SEED = f"""
INSERT INTO facilities VALUES (1, 'Demo', 'Europe/Istanbul');
INSERT INTO units VALUES (1, 1, 'Yatakli', 'inpatient', true), (2, 1, 'Acil', 'emergency', true);
INSERT INTO rooms VALUES (1, 1, 'O1');
INSERT INTO beds VALUES (1, 1, 'Y1', true), (2, 1, 'Y2', true);
INSERT INTO patients VALUES (1, 'SYN001', true), (2, 'SYN002', true);
INSERT INTO encounters VALUES (1, 1, 1, 1, '2026-11-04T17:00:00Z', '2026-11-05T17:00:00Z'), (2, 2, 2, NULL, '2026-11-04T17:00:00Z', NULL);
INSERT INTO acuity_assessments VALUES (1, 1, '2026-11-04T17:00:00Z', 2, 1.2);
INSERT INTO staff VALUES (1, 'STAFF001', 'nurse', true), (2, 'STAFF002', 'nurse', true);
INSERT INTO skills VALUES (1, 'S1', 'Demo yetkinlik');
INSERT INTO staff_skills VALUES (1, 1, '2026-01-01T00:00:00Z', NULL);
INSERT INTO shift_templates VALUES (1, 'Gece', '20:00', 720);
INSERT INTO staff_shifts VALUES (1, 1, 1, 1, '2026-11-04T17:00:00Z', '2026-11-05T05:00:00Z', 30);
INSERT INTO staff_unavailability VALUES (1, 1, '2026-11-04T22:00:00Z', '2026-11-04T22:30:00Z', 'Mola');
INSERT INTO task_types VALUES (1, 'T1', 'Sentetik gorev', 10, 'nurse', 'synthetic_assumption');
INSERT INTO task_skill_requirements VALUES (1, 1);
INSERT INTO care_tasks VALUES
  (1, 1, 1, 1, '2026-11-04T17:00:00Z', '2026-11-04T18:00:00Z', 12, 12, 'completed', '2026-11-04T17:30:00Z'),
  (2, 1, 1, 1, '2026-11-04T17:00:00Z', '2026-11-04T19:00:00Z', 12, NULL, 'pending', NULL),
  (3, 1, 1, 1, '2026-11-04T17:00:00Z', '2026-11-04T19:00:00Z', 12, NULL, 'pending', NULL);
INSERT INTO workload_observations VALUES (1, 1, '2026-11-04T17:00:00Z', '2026-11-04T17:15:00Z', 12, 1, '2026-11-04T17:15:00Z');
INSERT INTO model_runs VALUES (1, 'baseline', '2026-11-04T17:00:00Z', '2026-10-01T00:00:00Z', '2026-10-20T00:00:00Z',
  '2026-10-21T00:00:00Z', '2026-10-31T00:00:00Z', 30, 1, 2, 'artifacts/demo', 'lag4', 42);
INSERT INTO workload_forecasts VALUES (1, 1, 1, '2026-11-04T17:15:00Z', 30, 24);
INSERT INTO optimization_runs VALUES
  (1, 1, 1, '2026-11-04T17:15:00Z', '2026-11-04T17:45:00Z', '2026-11-04T17:15:00Z', 'FEASIBLE', 0, '{{}}', 0.1),
  (2, 1, 1, '2026-11-04T17:15:00Z', '2026-11-04T17:45:00Z', '2026-11-04T17:15:00Z', 'FEASIBLE', 0, '{{}}', 0.1);
INSERT INTO assignments VALUES (1, 1, 1, 1, '2026-11-04T17:15:00Z', '2026-11-04T17:27:00Z');
INSERT INTO staffing_gaps VALUES (1, 1, NULL, 'nurse', 12, 'forecast_reserve');
INSERT INTO audit_events VALUES (1, '2026-11-04T17:15:00Z', 'demo', 'optimize', 'run', '1', '{{}}');
INSERT INTO plan_approvals VALUES (1, 1, 'approved', 'manager', '2026-11-04T18:00:00Z', '{HASH}', 'demo');
INSERT INTO rag_documents VALUES (1, 'P1', 'Demo', 'procedure', '1', '2026-11-01T00:00:00Z', '2026-12-01T00:00:00Z',
  'approved', 'manager', '2026-11-01T00:00:00Z', '{HASH}', 'corpus/demo.md', true);
INSERT INTO rag_chunks VALUES (1, 1, 0, 'S1', 'Synthetic text', '{HASH}', 3);
INSERT INTO rag_indexes VALUES (1, 'qwen_v1', 'qwen3-embedding:0.6b', 'digest', 1024, 'cosine', '{{}}', '{HASH}', 'ready', '2026-11-04T00:00:00Z');
INSERT INTO rag_index_chunks VALUES (1, 1, 'vector1');
INSERT INTO rag_queries VALUES (1, 1, '2026-11-04T18:00:00Z', '2026-11-04T18:00:00Z', 'Question', 'answered', 'Answer',
  'gemma3:4b', 'digest', 'v1', 1, 2, 3, '{{}}');
INSERT INTO rag_retrieval_hits VALUES (1, 1, 1, 0.2, true);
INSERT INTO rag_eval_sets VALUES (1, 'demo', '1', '{HASH}', '2026-11-04T00:00:00Z');
INSERT INTO rag_eval_cases VALUES (1, 1, 'Q1', 'test', 'procedure', 'Question', '2026-11-04T18:00:00Z', true, '[1]', '[]');
INSERT INTO rag_eval_runs VALUES (1, 1, 1, '2026-11-04T18:00:00Z', 'gemma3:4b', '{{}}', '{{}}');
INSERT INTO rag_eval_results VALUES (1, 1, 1, 1, 1, 1, 1, 3, NULL, 'human', 'demo');
"""


def alembic(*args):
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db={TEST_DB}", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )  # fmt: skip
    return proc


def run_alembic(*args):
    proc = alembic(*args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout + proc.stderr


def table_count(conn):
    return conn.execute(
        text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'")
    ).scalar_one()


@pytest.fixture(scope="module")
def owner_engine():
    engine = migration_engine(TEST_DB, allow_prompt=False)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT current_database()")).scalar_one() == TEST_DB
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def migrated(owner_engine):
    run_alembic("downgrade", "base")
    run_alembic("upgrade", "head")
    yield
    run_alembic("downgrade", "base")


@pytest.fixture
def db(owner_engine):
    """Örnek veri yüklü, test sonunda geri alınan bağlantı."""
    with owner_engine.connect() as conn:
        transaction = conn.begin()
        conn.exec_driver_sql(SEED)
        yield conn
        transaction.rollback()


@pytest.fixture(scope="module")
def app_engine():
    engine = runtime_engine(APP_ROLE, TEST_DB)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def rag_engine():
    engine = runtime_engine(RAG_ROLE, TEST_DB)
    yield engine
    engine.dispose()


def rejected(conn, sql, exc=IntegrityError, constraint=None):
    """Reddedilmeli; constraint verilmişse red tam olarak o kısıttan gelmeli (yanlış nedenle geçmesin)."""
    with pytest.raises(exc) as info, conn.begin_nested():
        conn.exec_driver_sql(sql)
    if constraint is not None:
        assert info.value.orig.diag.constraint_name == constraint


def accepted(conn, sql):
    with conn.begin_nested():
        conn.exec_driver_sql(sql)


# ---- Parola deposu ve gerçek girişler -------------------------------------------------------


def test_windows_credential_manager_round_trip():
    backend = keyring.get_keyring()
    assert type(backend).__name__ == "WinVaultKeyring"
    service, value = f"medakis-test-{secrets.token_hex(6)}", secrets.token_hex(24)
    keyring.set_password(service, "deneme", value)
    try:
        assert keyring.get_password(service, "deneme") == value
    finally:
        keyring.delete_password(service, "deneme")
    assert keyring.get_password(service, "deneme") is None


@pytest.mark.parametrize("role", ALL_ROLES)
def test_real_role_logs_in_with_stored_password(role):
    with psycopg.connect(host="127.0.0.1", port=PORT, dbname=TEST_DB, user=role, password=get_password(role)) as conn:
        row = conn.execute(
            "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    assert row == (role, False, False, False)
    assert keyring.get_password(SERVICE, role)


def test_runtime_engines_connect_as_low_privilege_roles(app_engine, rag_engine):
    for engine, role in ((app_engine, APP_ROLE), (rag_engine, RAG_ROLE)):
        with engine.connect() as conn:
            assert conn.execute(text("SELECT current_user")).scalar_one() == role
            assert conn.execute(text("SHOW timezone")).scalar_one() == "UTC"


def test_passwords_absent_from_tool_output_and_server_logs():
    outputs = [run_alembic("current")]
    proc = subprocess.run(
        [sys.executable, "scripts/db/store_credentials.py", "--kontrol"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )  # fmt: skip
    assert proc.returncode == 0, "Credential Manager kayıtları doğrulanamadı"
    outputs.append(proc.stdout + proc.stderr)
    logs = subprocess.run(
        ["docker", "logs", CONTAINER], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    outputs.append(logs.stdout + logs.stderr)
    for role in ALL_ROLES:
        secret = get_password(role)
        assert not [o for o in outputs if secret in o], f"{role} parolası çıktıda göründü"


# ---- Alembic --------------------------------------------------------------------------------


def test_upgrade_downgrade_cycle(owner_engine):
    with owner_engine.connect() as conn:
        assert table_count(conn) == 34
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0004_triage_cancel_columns"
    run_alembic("downgrade", "base")
    with owner_engine.connect() as conn:
        assert table_count(conn) == 0
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 0
        # Süper kullanıcının kurduğu eklenti migration'a ait değildir; kalmalıdır.
        assert conn.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'btree_gist'")).scalar_one() == 1
    run_alembic("upgrade", "head")
    with owner_engine.connect() as conn:
        assert table_count(conn) == 34
    assert "0004_triage_cancel_columns (head)" in run_alembic("current")


def test_upgrade_is_idempotent_at_head(owner_engine):
    run_alembic("upgrade", "head")
    with owner_engine.connect() as conn:
        assert table_count(conn) == 34


def test_model_and_migration_have_no_drift_per_alembic():
    assert "No new upgrade operations detected" in run_alembic("check")


def _catalog(conn, schema):
    conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", public')
    constraints = conn.execute(
        text(
            "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid, true) "
            "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = :s AND c.conrelid <> 0 AND c.contype <> 'n' "
            "AND c.conrelid::regclass::text <> 'alembic_version'"
        ),
        {"s": schema},
    ).all()
    indexes = conn.execute(
        text(
            "SELECT tablename, indexname, replace(indexdef, :p, '') FROM pg_indexes "
            "WHERE schemaname = :s AND tablename <> 'alembic_version'"
        ),
        {"s": schema, "p": f"{schema}."},
    ).all()
    columns = conn.execute(
        text(
            "SELECT table_name, ordinal_position, column_name, data_type, is_nullable, "
            "regexp_replace(coalesce(column_default, ''), '^nextval.*$', 'nextval'), identity_generation "
            "FROM information_schema.columns WHERE table_schema = :s AND table_name <> 'alembic_version'"
        ),
        {"s": schema},
    ).all()
    return set(map(tuple, constraints)), set(map(tuple, indexes)), set(map(tuple, columns))


def test_catalog_matches_sqlalchemy_model(owner_engine):
    """Alembic'in kurduğu şema ile modelin ürettiği şema katalogda birebir aynı olmalı."""
    schema = f"karsilastirma_{secrets.token_hex(4)}"
    with owner_engine.connect() as conn:
        tx = conn.begin()
        try:
            migrated_catalog = _catalog(conn, "public")
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            translated = conn.execution_options(schema_translate_map={None: schema})
            metadata.create_all(translated)
            model_catalog = _catalog(conn, schema)
        finally:
            tx.rollback()
    for migrated_part, model_part in zip(migrated_catalog, model_catalog, strict=True):
        assert migrated_part == model_part
    constraints, indexes, columns = migrated_catalog
    assert len({c[0] for c in columns}) == 34
    assert len([i for i in indexes if i[1].startswith("idx_")]) == 11
    assert len([c for c in constraints if c[1].startswith("ex_")]) == 4


def test_tables_owned_by_owner_role(owner_engine):
    with owner_engine.connect() as conn:
        owners = conn.execute(
            text("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'")
        ).scalars().all()
    assert owners == [OWNER_ROLE]


# ---- Sentetik veri ve iş kuralı kısıtları ---------------------------------------------------


def test_seed_populates_all_34_tables(db):
    for name in TABLES:
        assert db.execute(text(f"SELECT count(*) FROM {name}")).scalar_one() >= 1, name


PORTED_REJECTIONS = {
    "synthetic_only": "UPDATE patients SET is_synthetic = false WHERE id = 1",
    "foreign_key": "INSERT INTO rooms VALUES (2, 999, 'X')",
    "negative_task_minutes": "UPDATE task_types SET base_minutes = -1 WHERE id = 1",
    "shift_order": "UPDATE staff_shifts SET ends_at = starts_at WHERE id = 1",
    "unique_assignment": "INSERT INTO assignments VALUES (2, 1, 1, 2, '2026-11-04T17:30:00Z', '2026-11-04T17:40:00Z')",
    "invalid_horizon": "UPDATE workload_forecasts SET horizon_minutes = 45 WHERE id = 1",
    "completed_requires_duration": "UPDATE care_tasks SET status = 'completed' WHERE id = 2",
    "unit_encounter_mismatch": "UPDATE care_tasks SET unit_id = 2 WHERE id = 1",
    "training_order": "UPDATE model_runs SET test_start = train_start WHERE id = 1",
    "nonpositive_gap": "UPDATE staffing_gaps SET missing_minutes = 0 WHERE id = 1",
    "invalid_solver_status": "UPDATE optimization_runs SET status = 'SUCCESS' WHERE id = 1",
    "duplicate_skill": "INSERT INTO task_skill_requirements VALUES (1, 1)",
    "referenced_staff_delete": "DELETE FROM staff WHERE id = 1",
    "rag_synthetic_only": "UPDATE rag_documents SET is_synthetic = false WHERE id = 1",
    "rag_validity": "UPDATE rag_documents SET valid_until = valid_from WHERE id = 1",
    "rag_approval_required": "UPDATE rag_documents SET approved_by = NULL WHERE id = 1",
    "rag_hash_length": "UPDATE rag_documents SET content_sha256 = 'bad' WHERE id = 1",
    "rag_chunk_fk": "UPDATE rag_chunks SET document_id = 999 WHERE id = 1",
    "empty_chunk": "UPDATE rag_chunks SET text_content = '  ' WHERE id = 1",
    "index_dimensions": "UPDATE rag_indexes SET dimensions = 0 WHERE id = 1",
    "duplicate_vector": "INSERT INTO rag_index_chunks VALUES (1, 1, 'vector2')",
    "invalid_query_outcome": "UPDATE rag_queries SET outcome = 'hallucinated' WHERE id = 1",
    "answered_requires_text": "UPDATE rag_queries SET answer = NULL WHERE id = 1",
    "negative_latency": "UPDATE rag_queries SET total_ms = -1 WHERE id = 1",
    "rank_positive": "UPDATE rag_retrieval_hits SET rank = 0 WHERE query_id = 1",
    "eval_range": "UPDATE rag_eval_results SET recall_at_5 = 1.1 WHERE run_id = 1",
    "eval_split": "UPDATE rag_eval_cases SET split = 'train' WHERE id = 1",
    "approval_decision": "UPDATE plan_approvals SET decision = 'auto' WHERE id = 1",
    "approval_manager": "UPDATE plan_approvals SET manager_actor = '' WHERE id = 1",
    "approval_hash": "UPDATE plan_approvals SET plan_sha256 = 'bad' WHERE id = 1",
}


PORTED_CONSTRAINTS = {
    "synthetic_only": "ck_patients_is_synthetic",
    "foreign_key": "fk_rooms_unit_id",
    "negative_task_minutes": "ck_task_types_base_minutes_positive",
    "shift_order": "ck_staff_shifts_order",
    "unique_assignment": "uq_assignments_run_id_task_id",
    "invalid_horizon": "ck_workload_forecasts_horizon",
    "completed_requires_duration": "ck_care_tasks_completion",
    "unit_encounter_mismatch": "fk_care_tasks_encounter_id_unit_id",
    "training_order": "ck_model_runs_chronology",
    "nonpositive_gap": "ck_staffing_gaps_missing_minutes_positive",
    "invalid_solver_status": "ck_optimization_runs_status",
    "duplicate_skill": "pk_task_skill_requirements",
    "referenced_staff_delete": "fk_staff_skills_staff_id",
    "rag_synthetic_only": "ck_rag_documents_is_synthetic",
    "rag_validity": "ck_rag_documents_validity",
    "rag_approval_required": "ck_rag_documents_approval_required",
    "rag_hash_length": "ck_rag_documents_content_sha256_format",
    "rag_chunk_fk": "fk_rag_chunks_document_id",
    "empty_chunk": "ck_rag_chunks_text_content",
    "index_dimensions": "ck_rag_indexes_dimensions",
    "duplicate_vector": "pk_rag_index_chunks",
    "invalid_query_outcome": "ck_rag_queries_outcome",
    "answered_requires_text": "ck_rag_queries_answered_requires_text",
    "negative_latency": "ck_rag_queries_total_ms_nonnegative",
    "rank_positive": "ck_rag_retrieval_hits_rank",
    "eval_range": "ck_rag_eval_results_recall_at_5",
    "eval_split": "ck_rag_eval_cases_split",
    "approval_decision": "ck_plan_approvals_decision",
    "approval_manager": "ck_plan_approvals_manager_actor",
    "approval_hash": "ck_plan_approvals_plan_sha256_format",
}


def test_ported_rejection_list_is_complete():
    assert len(PORTED_REJECTIONS) == 30
    assert set(PORTED_REJECTIONS) == set(PORTED_CONSTRAINTS)


@pytest.mark.parametrize("label", sorted(PORTED_REJECTIONS))
def test_rejections_ported_from_sqlite_verifier(db, label):
    rejected(db, PORTED_REJECTIONS[label], constraint=PORTED_CONSTRAINTS[label])


def _columns(type_):
    return sorted(f"{t.name}.{c.name}" for t in TABLES.values() for c in t.columns if isinstance(c.type, type_))


DOUBLE_COLUMNS = _columns(Double)
TIME_COLUMNS = _columns(DateTime)
JSON_COLUMNS = _columns(JSONB)
BOOLEAN_COLUMNS = _columns(Boolean)


def test_column_inventories():
    assert (len(DOUBLE_COLUMNS), len(JSON_COLUMNS), len(BOOLEAN_COLUMNS)) == (20, 8, 8)
    assert len(TIME_COLUMNS) >= 30


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("column", DOUBLE_COLUMNS)
def test_non_finite_numbers_rejected_everywhere(db, column, value):
    table, name = column.split(".")
    with pytest.raises(IntegrityError) as info, db.begin_nested():
        db.exec_driver_sql(f"UPDATE {table} SET {name} = '{value}'")
    # Red, bu kolonun kendi kısıtından gelmeli: sonlu kısıtı veya 0-1 / mesafe aralığı.
    assert info.value.orig.diag.constraint_name.startswith(f"ck_{table}_{name}")


def test_nan_really_passes_a_plain_positive_check_in_postgres(db):
    # Sonlu kısıtının neden gerekli olduğunun kanıtı.
    assert db.execute(text("SELECT 'NaN'::float8 > 0, 'Infinity'::float8 > 0")).one() == (True, True)


@pytest.mark.parametrize("value", ["yarin", "2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z", ""])
@pytest.mark.parametrize("column", TIME_COLUMNS)
def test_invalid_timestamps_rejected_everywhere(db, column, value):
    table, name = column.split(".")
    rejected(db, f"UPDATE {table} SET {name} = '{value}'", DataError)


def test_timestamps_are_stored_as_utc(db):
    row = db.execute(
        text("SELECT admitted_at AT TIME ZONE 'UTC' FROM encounters WHERE id = 1")
    ).scalar_one()
    db.exec_driver_sql("UPDATE encounters SET admitted_at = '2026-11-04T20:00:00+03:00' WHERE id = 1")
    assert db.execute(text("SELECT admitted_at AT TIME ZONE 'UTC' FROM encounters WHERE id = 1")).scalar_one() == row
    assert db.execute(text("SELECT data_type FROM information_schema.columns WHERE table_name = 'encounters' AND column_name = 'admitted_at'")).scalar_one() == "timestamp with time zone"


@pytest.mark.parametrize("column", JSON_COLUMNS)
def test_invalid_json_rejected(db, column):
    table, name = column.split(".")
    rejected(db, f"UPDATE {table} SET {name} = '{{bozuk'", DataError)


@pytest.mark.parametrize("column", BOOLEAN_COLUMNS)
def test_non_boolean_values_rejected(db, column):
    table, name = column.split(".")
    rejected(db, f"UPDATE {table} SET {name} = 'belki'", DataError)


def test_invalid_local_shift_time_rejected(db):
    rejected(db, "UPDATE shift_templates SET start_local = '25:00'", DataError)


def _foreign_keys():
    return sorted(
        f"{t.name}.{fk.parent.name}"
        for t in TABLES.values()
        for fk in t.foreign_keys
        if fk.constraint is not None and len(fk.constraint.columns) == 1
    )


@pytest.mark.parametrize("column", _foreign_keys())
def test_every_single_column_foreign_key_enforced(db, column):
    table, name = column.split(".")
    # Tek satır güncellenir: birden çok satır EXCLUDE'a takılıp testi yanlış nedenle geçirebilir.
    one_row = f"ctid = (SELECT ctid FROM {table} ORDER BY ctid DESC LIMIT 1)"  # Son satırda başka FK bağımlılığı yok.
    rejected(db, f"UPDATE {table} SET {name} = 999999 WHERE {one_row}", constraint=f"fk_{table}_{name}")


@pytest.mark.parametrize(
    "case",
    [
        ("INSERT INTO patients VALUES (9, 'Ahmet Yilmaz', true)", "ck_patients_synthetic_code_format"),
        ("INSERT INTO patients VALUES (9, '12345678901', true)", "ck_patients_synthetic_code_format"),
        ("INSERT INTO staff VALUES (9, 'Ayse Demir', 'nurse', true)", "ck_staff_synthetic_code_format"),
        ("INSERT INTO staff VALUES (9, 'SYN001', 'nurse', true)", "ck_staff_synthetic_code_format"),
    ],
)
def test_real_looking_identities_rejected(db, case):
    rejected(db, case[0], constraint=case[1])


def test_duration_source_must_remain_synthetic_assumption(db):
    rejected(db, "UPDATE task_types SET duration_source = 'clinical_standard'", constraint="ck_task_types_duration_source")


def test_service_isolation_composite_foreign_key(db):
    # Görev, başka servisin başvurusuna bağlanamaz; aynı servis kabul edilir.
    rejected(
        db,
        "INSERT INTO care_tasks VALUES (9, 2, 1, 1, '2026-11-04T17:00:00Z', '2026-11-04T18:00:00Z', 5, NULL, 'pending', NULL)",
        constraint="fk_care_tasks_encounter_id_unit_id",
    )
    accepted(
        db,
        "INSERT INTO care_tasks VALUES (9, 2, 2, 1, '2026-11-04T17:00:00Z', '2026-11-04T18:00:00Z', 5, NULL, 'pending', NULL)",
    )


def test_cancelled_task_cannot_have_completion_and_is_allowed_without(db):
    accepted(db, "UPDATE care_tasks SET status = 'cancelled' WHERE id = 2")
    rejected(db, "UPDATE care_tasks SET status = 'cancelled' WHERE id = 1", constraint="ck_care_tasks_completion")


# ---- EXCLUDE kuralları ve tanımlı istisnaları -------------------------------------------------


def _shift(i, staff, start, end):
    return f"INSERT INTO staff_shifts VALUES ({i}, {staff}, 1, 1, '{start}', '{end}', 0)"


def test_exclude_shift_overlap_for_same_staff(db):
    rejected(db, _shift(9, 1, "2026-11-05T04:00:00Z", "2026-11-05T06:00:00Z"), constraint="ex_staff_shifts_staff_overlap")


def test_exclude_shift_overlap_across_midnight(db):
    accepted(db, _shift(9, 2, "2026-11-05T21:00:00Z", "2026-11-06T01:00:00Z"))
    rejected(db, _shift(10, 2, "2026-11-05T23:30:00Z", "2026-11-06T00:30:00Z"), constraint="ex_staff_shifts_staff_overlap")


def test_exclude_shift_adjacent_allowed(db):
    # [başlangıç, bitiş): biten anda yeni vardiya başlayabilir.
    accepted(db, _shift(9, 1, "2026-11-05T05:00:00Z", "2026-11-05T06:00:00Z"))


def test_exclude_shift_other_staff_allowed(db):
    accepted(db, _shift(9, 2, "2026-11-04T17:00:00Z", "2026-11-05T05:00:00Z"))


def _stay(i, bed, start, end):
    bed_sql = "NULL" if bed is None else bed
    end_sql = "NULL" if end is None else f"'{end}'"
    return f"INSERT INTO encounters VALUES ({i}, 2, 1, {bed_sql}, '{start}', {end_sql})"


def test_exclude_bed_overlap(db):
    rejected(db, _stay(9, 1, "2026-11-05T10:00:00Z", "2026-11-05T20:00:00Z"), constraint="ex_encounters_bed_overlap")


def test_exclude_bed_open_ended_stay_blocks_later_stays(db):
    # discharged_at NULL = hasta hâlâ yatıyor: sonraki bütün tarihlerle çakışır.
    accepted(db, _stay(9, 2, "2026-11-05T10:00:00Z", None))
    rejected(db, _stay(10, 2, "2027-01-01T00:00:00Z", "2027-01-02T00:00:00Z"), constraint="ex_encounters_bed_overlap")


def test_exclude_bed_null_bed_exempt(db):
    # Yatak atanmamış başvurular (acil, ayaktan) çakışma sayılmaz.
    accepted(db, _stay(9, None, "2026-11-04T17:00:00Z", "2026-11-05T17:00:00Z"))
    accepted(db, _stay(10, None, "2026-11-04T17:00:00Z", "2026-11-05T17:00:00Z"))


def test_exclude_bed_adjacent_and_other_bed_allowed(db):
    accepted(db, _stay(9, 1, "2026-11-05T17:00:00Z", "2026-11-06T17:00:00Z"))
    accepted(db, _stay(10, 2, "2026-11-04T17:00:00Z", "2026-11-05T17:00:00Z"))


def _assign(i, run, task, staff, start, end):
    return f"INSERT INTO assignments VALUES ({i}, {run}, {task}, {staff}, '{start}', '{end}')"


def test_exclude_assignment_overlap_same_run_same_staff(db):
    rejected(db, _assign(9, 1, 2, 1, "2026-11-04T17:20:00Z", "2026-11-04T17:30:00Z"), constraint="ex_assignments_staff_overlap")


def test_exclude_assignment_adjacent_allowed(db):
    accepted(db, _assign(9, 1, 2, 1, "2026-11-04T17:27:00Z", "2026-11-04T17:39:00Z"))


def test_exclude_assignment_alternative_run_allowed(db):
    # Alternatif çözüm çalışmaları aynı anda etkin plan değildir; aynı personel zamanı kullanabilir.
    accepted(db, _assign(9, 2, 1, 1, "2026-11-04T17:15:00Z", "2026-11-04T17:27:00Z"))


def test_exclude_assignment_other_staff_allowed(db):
    accepted(db, _assign(9, 1, 2, 2, "2026-11-04T17:15:00Z", "2026-11-04T17:27:00Z"))


def _doc(i, key, version, start, end, status):
    end_sql = "NULL" if end is None else f"'{end}'"
    approved = "'manager', '2026-11-01T00:00:00Z'" if status == "approved" else "NULL, NULL"
    return (
        f"INSERT INTO rag_documents VALUES ({i}, '{key}', 'Demo', 'procedure', '{version}', '{start}', {end_sql}, "
        f"'{status}', {approved}, '{HASH}', 'corpus/demo.md', true)"
    )


def test_exclude_approved_document_overlap(db):
    rejected(db, _doc(9, "P1", "2", "2026-11-15T00:00:00Z", None, "approved"), constraint="ex_rag_documents_approved_overlap")


def test_exclude_document_draft_and_retired_exempt(db):
    accepted(db, _doc(9, "P1", "2", "2026-11-15T00:00:00Z", None, "draft"))
    accepted(db, _doc(10, "P1", "3", "2026-11-15T00:00:00Z", None, "retired"))


def test_exclude_document_promoting_overlapping_draft_rejected(db):
    accepted(db, _doc(9, "P1", "2", "2026-11-15T00:00:00Z", None, "draft"))
    rejected(db, "UPDATE rag_documents SET status = 'approved', approved_by = 'm', approved_at = now() WHERE id = 9", constraint="ex_rag_documents_approved_overlap")


def test_exclude_document_adjacent_version_and_other_key_allowed(db):
    accepted(db, _doc(9, "P1", "2", "2026-12-01T00:00:00Z", None, "approved"))
    accepted(db, _doc(10, "P2", "1", "2026-11-15T00:00:00Z", None, "approved"))


def test_overlapping_unavailability_is_not_constrained(db):
    # İzin ve mola aynı anda kaydedilebilir; bu bilinçli olarak EXCLUDE değildir.
    accepted(db, "INSERT INTO staff_unavailability VALUES (9, 1, '2026-11-04T22:10:00Z', '2026-11-04T23:00:00Z', 'Izin')")


# ---- Rol yetkileri --------------------------------------------------------------------------

ALL_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN")


@pytest.mark.parametrize("role", [APP_ROLE, RAG_ROLE, "medakis_seed"])
def test_privilege_matrix_matches_design_for_every_table(owner_engine, role):
    with owner_engine.connect() as conn:
        for table in TABLES:
            actual = {
                p
                for p in ALL_PRIVILEGES
                if conn.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"), {"r": role, "t": f"public.{table}", "p": p}
                ).scalar_one()
            }
            assert actual == set(PRIVILEGES[role].get(table, frozenset())), (role, table)


def _denied(engine, sql):
    with engine.connect() as conn:
        with pytest.raises(ProgrammingError) as info:
            conn.exec_driver_sql(sql)
        assert isinstance(info.value.orig, psycopg.errors.InsufficientPrivilege), sql
        conn.rollback()


RAG_FORBIDDEN = {
    "assignments": {
        "select": "SELECT * FROM assignments",
        "insert": "INSERT INTO assignments VALUES (9, 1, 1, 1, now(), now() + interval '1 minute')",
        "update": "UPDATE assignments SET staff_id = 2",
        "delete": "DELETE FROM assignments",
        "truncate": "TRUNCATE assignments",
        "drop": "DROP TABLE assignments",
        "alter": "ALTER TABLE assignments DROP CONSTRAINT ex_assignments_staff_overlap",
    },
    "plan_approvals": {
        "select": "SELECT * FROM plan_approvals",
        "insert": f"INSERT INTO plan_approvals VALUES (9, 1, 'approved', 'rag', now(), '{HASH}', '')",
        "update": "UPDATE plan_approvals SET decision = 'approved'",
        "delete": "DELETE FROM plan_approvals",
        "truncate": "TRUNCATE plan_approvals",
        "drop": "DROP TABLE plan_approvals",
        "alter": "ALTER TABLE plan_approvals DISABLE TRIGGER ALL",
    },
}


@pytest.mark.parametrize(
    "table,action", [(t, a) for t, actions in RAG_FORBIDDEN.items() for a in actions]
)
def test_rag_role_cannot_touch_assignments_or_approvals(rag_engine, table, action):
    _denied(rag_engine, RAG_FORBIDDEN[table][action])


@pytest.mark.parametrize(
    "table", ["staff", "staff_shifts", "staff_skills", "care_tasks", "optimization_runs", "staffing_gaps", "audit_events", "patients"]
)
def test_rag_role_cannot_read_or_write_business_tables(rag_engine, table):
    _denied(rag_engine, f"SELECT * FROM {table}")
    _denied(rag_engine, f"DELETE FROM {table}")


def test_rag_role_cannot_create_objects_or_change_documents(rag_engine):
    _denied(rag_engine, "CREATE TABLE arka_kapi(id int)")
    _denied(rag_engine, "UPDATE rag_documents SET status = 'approved'")
    _denied(rag_engine, "UPDATE rag_chunks SET text_content = 'x'")


def test_rag_role_can_read_documents_and_log_queries(rag_engine):
    with rag_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM rag_documents")).scalar_one() == 0
        # Yetki geçer, hata yalnız olmayan indeksten gelir: yazma izninin kanıtı.
        with pytest.raises(IntegrityError) as info:
            conn.exec_driver_sql(
                "INSERT INTO rag_queries (index_id, asked_at, as_of, question, outcome, prompt_version, retrieval_ms, "
                "generation_ms, total_ms, config_json) VALUES (999, now(), now(), 'q', 'no_source', 'v1', 0, 0, 0, '{}')"
            )
        assert isinstance(info.value.orig, psycopg.errors.ForeignKeyViolation)
        conn.rollback()


def test_app_role_inserts_with_identity_and_cannot_delete(app_engine):
    with app_engine.connect() as conn:
        new_id = conn.execute(
            text("INSERT INTO facilities (name) VALUES ('Gecici') RETURNING id")
        ).scalar_one()
        assert new_id >= 1
        conn.rollback()
    _denied(app_engine, "DELETE FROM facilities")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE assignments SET staff_id = 2",
        "UPDATE plan_approvals SET decision = 'approved'",
        "DELETE FROM plan_approvals",
        "UPDATE audit_events SET actor = 'x'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
        "INSERT INTO rag_queries (index_id, asked_at, as_of, question, outcome, prompt_version, retrieval_ms, "
        "generation_ms, total_ms, config_json) VALUES (1, now(), now(), 'q', 'no_source', 'v1', 0, 0, 0, '{}')",
        "CREATE TABLE arka_kapi(id int)",
        "DROP TABLE assignments",
        "ALTER TABLE care_tasks DROP COLUMN status",
    ],
)
def test_app_role_limits(app_engine, sql):
    _denied(app_engine, sql)


def test_dev_kept_at_0003_and_demo_empty():
    # medakis_dev'in verisi seed testlerinde doğrulanır (son seed kaydıyla aynı olmalı).
    # Tasarım kararı D11: medakis_dev revizyon 0003'te kalır; 0004 yalnız açık onayla uygulanabilir.
    engine = migration_engine("medakis_dev", allow_prompt=False)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003_seed_write_window"
    engine.dispose()
    engine = migration_engine("medakis_demo", allow_prompt=False)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")).scalar_one() == 0
    engine.dispose()


def test_alembic_guards_demo_and_dev_downgrade():
    proc = alembic("current")
    assert proc.returncode == 0
    demo = subprocess.run([sys.executable, "-m", "alembic", "-x", "db=medakis_demo", "current"], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)  # fmt: skip
    assert demo.returncode != 0 and "demo_onayi" in demo.stdout + demo.stderr
    dev = subprocess.run([sys.executable, "-m", "alembic", "-x", "db=medakis_dev", "downgrade", "base"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", timeout=120)  # fmt: skip
    assert dev.returncode != 0 and "onay=medakis_dev" in dev.stdout + dev.stderr
