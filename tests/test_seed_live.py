"""Seed yönetimi gerçek PostgreSQL'de (medakis_seed rolüyle, yalnız medakis_test): pytest -m postgres"""

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from pg_target import CONTAINER, PORT  # noqa: F401
from sqlalchemy import func, select, text

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.repositories import seed_repo
from medakis.repositories.credentials import SEED_ROLE, get_password
from medakis.repositories.database import migration_engine, runtime_engine, seed_engine
from medakis.repositories.schema import SEED_PROTECTED_HISTORY, audit_events, facilities, model_runs
from medakis.services.seed_service import SEED_ACTION, SeedService
from medakis.synthetic.params import SyntheticParams

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
GOLDEN = "83a619cb04371b892ed32803be9d0d16690bd72cecc6b8f560e9d59897191f42"
SMALL = SyntheticParams(days=3)


def alembic(*args):
    proc = subprocess.run([sys.executable, "-m", "alembic", "-x", "db=medakis_test", *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.fixture(scope="module", autouse=True)
def migrated():
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    yield
    alembic("downgrade", "base")


@pytest.fixture(scope="module")
def engines():
    engines = {"seed": seed_engine("medakis_test", allow_prompt=False), "owner": migration_engine("medakis_test", allow_prompt=False),
               "app": runtime_engine("medakis_app", "medakis_test")}  # fmt: skip
    yield engines
    for engine in engines.values():
        engine.dispose()


def no_backup(database, identity):
    raise AssertionError("medakis_test için yedek istenmemeliydi")


@pytest.fixture
def service(engines):
    with engines["seed"].begin() as conn:
        seed_repo.delete_operational(conn)
    yield SeedService("medakis_test", engine=engines["seed"], backup=no_backup)
    with engines["seed"].begin() as conn:
        seed_repo.delete_operational(conn)


def audit_count(engines):
    with engines["owner"].connect() as conn:
        return conn.execute(select(func.count()).select_from(audit_events)).scalar_one()


def test_load_on_empty_database_matches_generator(service, engines):
    before = audit_count(engines)
    result = service.apply(SyntheticParams())
    assert result.action == "loaded" and result.content_sha256 == GOLDEN
    status = service.status()
    assert status.content_sha256 == GOLDEN  # veritabanından geri okunan satırlar birebir aynı
    assert status.last_seed["content_sha256"] == GOLDEN and status.last_seed["reset"] is False
    assert sum(status.operational_counts.values()) == sum(result.row_counts.values())
    assert all(n == 0 for n in status.protected_counts.values())
    assert audit_count(engines) == before + 1


def test_second_apply_is_noop(service, engines):
    service.apply(SMALL)
    before = audit_count(engines)
    again = service.apply(SMALL)
    assert again.action == "unchanged"
    assert audit_count(engines) == before  # hiçbir şey yazılmadı


def test_different_data_requires_explicit_reset_and_keeps_audit(service, engines):
    first = service.apply(SMALL)
    other = replace(SMALL, seed=7)
    with pytest.raises(DomainError) as info:
        service.apply(other)
    assert info.value.code == errors.SEED_NEEDS_RESET
    with pytest.raises(DomainError) as info:
        service.apply(other, reset=True)
    assert info.value.code == errors.SEED_CONFIRMATION_REQUIRED
    with pytest.raises(DomainError) as info:
        service.apply(other, reset=True, confirm="medakis_dev")
    assert info.value.code == errors.SEED_CONFIRMATION_REQUIRED
    assert service.status().content_sha256 == first.content_sha256  # reddedilenler hiçbir şey değiştirmedi

    audit_before = audit_count(engines)
    result = service.apply(other, reset=True, confirm="medakis_test")
    assert result.action == "reset"
    assert audit_count(engines) == audit_before + 1  # eski denetim kayıtları silinmedi
    last = service.status().last_seed
    assert last["reset"] is True and last["previous_content_sha256"] == first.content_sha256


def test_reset_refused_when_history_exists_and_nothing_deleted(service, engines):
    loaded = service.apply(SMALL)
    with engines["owner"].begin() as conn:
        conn.execute(model_runs.insert().values(
            model_name="baseline", created_at=datetime(2026, 11, 1, tzinfo=UTC), train_start=datetime(2026, 10, 1, tzinfo=UTC),
            train_end=datetime(2026, 10, 10, tzinfo=UTC), test_start=datetime(2026, 10, 11, tzinfo=UTC),
            test_end=datetime(2026, 10, 20, tzinfo=UTC), horizon_minutes=30, mae=1, rmse=2, artifact_path="a", feature_spec="f", seed=42))  # fmt: skip
    try:
        with pytest.raises(DomainError) as info:
            service.apply(replace(SMALL, seed=8), reset=True, confirm="medakis_test")
        assert info.value.code == errors.SEED_HISTORY_PRESENT and "model_runs=1" in info.value.message
        assert service.status().content_sha256 == loaded.content_sha256
    finally:
        with engines["owner"].begin() as conn:
            conn.execute(text("DELETE FROM model_runs"))


def test_foreign_key_blocks_delete_under_history_even_if_precheck_were_skipped(service, engines):
    service.apply(SMALL)
    with engines["owner"].begin() as conn:
        mr = conn.execute(text(
            "INSERT INTO model_runs (model_name, created_at, train_start, train_end, test_start, test_end, horizon_minutes, "
            "mae, rmse, artifact_path, feature_spec, seed) VALUES ('baseline', now(), '2026-10-01Z', '2026-10-10Z', "
            "'2026-10-11Z', '2026-10-20Z', 30, 1, 2, 'a', 'f', 42) RETURNING id")).scalar_one()  # fmt: skip
        conn.execute(text("INSERT INTO workload_forecasts (model_run_id, unit_id, issued_at, horizon_minutes, predicted_minutes) "
                          "VALUES (:m, 1, now(), 30, 10)"), {"m": mr})  # fmt: skip
    try:
        with pytest.raises(Exception) as info, engines["seed"].begin() as conn:
            seed_repo.delete_operational(conn)
        assert isinstance(info.value.orig, psycopg.errors.ForeignKeyViolation)
    finally:
        with engines["owner"].begin() as conn:
            conn.execute(text("DELETE FROM workload_forecasts"))
            conn.execute(text("DELETE FROM model_runs"))


def test_failure_mid_load_rolls_back_everything(service, engines, monkeypatch):
    loaded = service.apply(SMALL)
    before = audit_count(engines)

    def boom(conn):
        raise RuntimeError("yapay hata")

    monkeypatch.setattr(seed_repo, "sync_sequences", boom)
    with pytest.raises(RuntimeError):
        service.apply(replace(SMALL, seed=9), reset=True, confirm="medakis_test")
    assert service.status().content_sha256 == loaded.content_sha256
    assert audit_count(engines) == before


def test_identity_sequences_advanced_after_load(service, engines):
    service.apply(SMALL)
    with engines["app"].connect() as conn:
        max_id = conn.execute(select(func.max(facilities.c.id))).scalar_one()
        new_id = conn.execute(facilities.insert().values(name="Uygulama ekledi").returning(facilities.c.id)).scalar_one()
        conn.rollback()
    assert new_id > max_id


def test_wrong_database_connection_is_refused_before_backup(engines):
    calls = []
    dev_service = SeedService("medakis_dev", engine=engines["seed"], backup=calls.append)  # bağlantı aslında medakis_test
    with pytest.raises(DomainError) as info:
        dev_service.apply(SMALL, confirm="medakis_dev")
    assert info.value.code == errors.SEED_WRONG_DATABASE and calls == []


@pytest.mark.parametrize("database", ["medakis_demo", "postgres"])
def test_seed_role_cannot_connect_to_demo_or_maintenance(database):
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(host="127.0.0.1", port=PORT, dbname=database, user=SEED_ROLE, password=get_password(SEED_ROLE), connect_timeout=5)


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM audit_events", "UPDATE audit_events SET actor = 'x'", "TRUNCATE facilities", "DELETE FROM model_runs",
     "SELECT * FROM rag_documents", "INSERT INTO rag_queries DEFAULT VALUES", "UPDATE care_tasks SET status = 'cancelled'",
     "DROP TABLE facilities", "CREATE TABLE arka_kapi(id int)", "LOCK TABLE audit_events IN SHARE MODE",
     "LOCK TABLE rag_documents IN SHARE MODE"],
)  # fmt: skip
def test_seed_role_limits(engines, sql):
    with engines["seed"].connect() as conn:
        with pytest.raises(Exception) as info:
            conn.exec_driver_sql(sql)
        assert isinstance(info.value.orig, psycopg.errors.InsufficientPrivilege), sql
        conn.rollback()


def test_demo_is_empty():
    # Test izolasyonu (dev ve demo değişmedi) conftest.dev_untouched_by_tests ile oturum başı/sonu
    # karşılaştırılır. "Dev son seed kaydıyla eşleşiyor mu" C5 doğrulamasıdır: seed.py status.
    owner = migration_engine("medakis_demo", allow_prompt=False)
    with owner.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")).scalar_one() == 0
    owner.dispose()
    assert set(SEED_PROTECTED_HISTORY)
