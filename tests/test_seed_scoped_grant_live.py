"""T1/T2: medakis_dev seed yazmasının işlem içi (commit edilmeyen) yetki yolu: pytest -m postgres

medakis_test, bu dosyadaki testler süresince medakis_dev'in kalıcı yetki durumuna (seed salt okunur)
getirilir ve servis medakis_dev'de kullanılan yolu (owner bağlantısı + SET LOCAL ROLE) izler.
Sonunda medakis_test'in kalıcı test yetkileri geri verilir. medakis_dev'de yalnız katalog okunur.

Rol üyeliği (medakis_owner → medakis_seed, yalnız SET) küme düzeyindedir. Üyelik olmayan bir
sunucuda üyelik gerektiren testler nedeni yazılarak atlanır; o sunucuda üyeliksiz yolun güvenli
biçimde reddettiği ayrıca sınanır.
"""

import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import func, select, text

from medakis.config import load_settings
from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.repositories import audit_repo, seed_access, seed_repo
from medakis.repositories.credentials import SEED_ROLE, get_password
from medakis.repositories.database import migration_engine, runtime_engine, seed_engine
from medakis.repositories.schema import (
    PRIVILEGES,
    SEED_AUDIT_TABLE,
    SEED_IDLE_PRIVILEGES,
    SEED_PROTECTED_HISTORY,
    SEED_RESET_TABLES,
    audit_events,
)
from medakis.services.seed_service import SeedService
from medakis.synthetic.params import SyntheticParams

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
SMALL = SyntheticParams(days=3)
TABLE_PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN")
RESET = ", ".join(SEED_RESET_TABLES)
HISTORY = ", ".join(SEED_PROTECTED_HISTORY)


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


def has_membership(engines):
    with engines["owner"].connect() as conn:
        return seed_access.membership(conn) == (False, False, True)


@pytest.fixture
def membership(engines):
    if not has_membership(engines):
        pytest.skip("medakis_owner → medakis_seed üyeliği bu sunucuda yok (ayrı yönetici adımı: scripts/db/seed_membership.ps1 -Onayla)")


def no_backup(database, identity):
    raise AssertionError("yedek istenmemeliydi")


def make_dev_like(conn):
    """medakis_test'te seed rolünü medakis_dev'deki kalıcı duruma getirir: yalnız SELECT."""
    seqs = ", ".join(seed_access.identity_sequences(conn))
    conn.execute(text(f"REVOKE INSERT, DELETE ON {RESET} FROM medakis_seed"))
    conn.execute(text(f"REVOKE INSERT ON {SEED_AUDIT_TABLE} FROM medakis_seed"))
    conn.execute(text(f"REVOKE MAINTAIN ON {HISTORY} FROM medakis_seed"))
    conn.execute(text(f"REVOKE USAGE, UPDATE ON SEQUENCE {seqs} FROM medakis_seed"))


def restore_test_grants(conn):
    seqs = ", ".join(seed_access.identity_sequences(conn))
    conn.execute(text(f"GRANT INSERT, DELETE ON {RESET} TO medakis_seed"))
    conn.execute(text(f"GRANT INSERT ON {SEED_AUDIT_TABLE} TO medakis_seed"))
    conn.execute(text(f"GRANT MAINTAIN ON {HISTORY} TO medakis_seed"))
    conn.execute(text(f"GRANT USAGE, UPDATE ON SEQUENCE {seqs} TO medakis_seed"))


def clear(engines):
    with engines["owner"].begin() as conn:
        for name in reversed(SEED_RESET_TABLES):
            conn.execute(text(f"DELETE FROM {name}"))


@pytest.fixture
def dev_like(engines, monkeypatch):
    monkeypatch.setattr("medakis.services.seed_service.SEED_PERMANENT_WRITE_DATABASES", frozenset())
    clear(engines)
    with engines["owner"].begin() as conn:
        make_dev_like(conn)
    yield SeedService("medakis_test", engine=engines["seed"], grant_engine=engines["owner"], backup=no_backup)
    with engines["owner"].begin() as conn:
        restore_test_grants(conn)
    clear(engines)


def seed_privileges(conn):
    sql = text("SELECT has_table_privilege('medakis_seed', :t, :p)")
    return {t: {p for p in TABLE_PRIVS if conn.execute(sql, {"t": f"public.{t}", "p": p}).scalar_one()} for t in SEED_IDLE_PRIVILEGES}


def idle(conn):
    return seed_privileges(conn) == {t: set(p) for t, p in SEED_IDLE_PRIVILEGES.items()} and not seed_access.write_open(conn)


def audit_count(engines):
    with engines["owner"].connect() as conn:
        return conn.execute(select(func.count()).select_from(audit_events)).scalar_one()


def snapshot(service, engines):
    s = service.status()
    return s.content_sha256, s.operational_counts, audit_count(engines)


def seed_write_error(engines, sql):
    with engines["seed"].connect() as conn:
        conn.exec_driver_sql("SET lock_timeout = '300ms'")
        try:
            conn.exec_driver_sql(sql)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            return type(exc.orig)
        conn.rollback()
    return None


# Seed işlemi sürerken kilitlenmeyen nesneler: yetki hatası doğrudan görülür.
UNLOCKED_WRITES = [
    "INSERT INTO audit_events (occurred_at, actor, action, entity_type) VALUES (now(), 'x', 'x', 'x')",
    "SELECT nextval(pg_get_serial_sequence('public.facilities', 'id'))",
    "SELECT setval(pg_get_serial_sequence('public.facilities', 'id'), 1)",
    "LOCK TABLE model_runs IN SHARE MODE",
]
# Seed'in kilitlediği tablolar: PostgreSQL önce kilidi bekler (LockNotAvailable); seed bittikten sonra
# aynı yazma yetki hatası verir. İkisi de yazmanın gerçekleşmediğini gösterir.
TABLE_WRITES = [
    "INSERT INTO facilities (name) VALUES ('ucuncu baglanti')",
    "DELETE FROM facilities WHERE false",
    "UPDATE facilities SET name = name WHERE false",
]
BLOCKED = (psycopg.errors.InsufficientPrivilege, psycopg.errors.LockNotAvailable)


class Paused:
    """Seed'i gerçek sayaç güncellemesinden sonra, commit'ten önce bekletir; işlem içi durumu kaydeder."""

    def __init__(self, monkeypatch, run):
        self.reached, self.release, self.outcome, self.inside = threading.Event(), threading.Event(), {}, {}
        real_sync = seed_repo.sync_sequences

        def sync_and_wait(conn):
            real_sync(conn)
            q = lambda sql: conn.execute(text(sql)).scalar_one()  # noqa: E731
            self.inside = {
                "current_user": q("SELECT current_user"),
                "insert": q("SELECT has_table_privilege('medakis_seed', 'public.facilities', 'INSERT')"),
                "delete": q("SELECT has_table_privilege('medakis_seed', 'public.facilities', 'DELETE')"),
                "audit_insert": q("SELECT has_table_privilege('medakis_seed', 'public.audit_events', 'INSERT')"),
                "maintain": q("SELECT has_table_privilege('medakis_seed', 'public.model_runs', 'MAINTAIN')"),
                "history_insert": q("SELECT has_table_privilege('medakis_seed', 'public.model_runs', 'INSERT')"),
            }
            self.reached.set()
            self.release.wait(90)

        monkeypatch.setattr(seed_repo, "sync_sequences", sync_and_wait)

        def target():
            try:
                self.outcome["result"] = run()
            except Exception as exc:  # noqa: BLE001
                self.outcome["error"] = exc

        self.thread = threading.Thread(target=target)
        self.thread.start()
        assert self.reached.wait(120), "seed bekleme noktasına ulaşmadı"

    def finish(self):
        self.release.set()
        self.thread.join(120)
        assert not self.thread.is_alive()


# ---- Kurulum: rol üyeliği yalnız SET ---------------------------------------------------------


def test_membership_is_set_only_and_grants_nothing_else(engines, membership):
    with engines["owner"].connect() as conn:
        assert seed_access.membership(conn) == (False, False, True)  # admin, inherit, set
        q = lambda sql: conn.execute(text(sql)).scalar_one()  # noqa: E731
        assert q("SELECT pg_has_role('medakis_owner', 'medakis_seed', 'SET')") is True
        assert q("SELECT pg_has_role('medakis_owner', 'medakis_seed', 'USAGE')") is False  # miras yok
        assert q("SELECT pg_has_role('medakis_owner', 'medakis_seed', 'MEMBER WITH ADMIN OPTION')") is False
        for role in ("medakis_app", "medakis_rag"):
            assert q(f"SELECT pg_has_role('medakis_owner', '{role}', 'SET')") is False, role
        assert q("SELECT pg_has_role('medakis_seed', 'medakis_owner', 'SET')") is False  # ters yön yok
        assert q("SELECT count(*) FROM pg_auth_members m JOIN pg_roles u ON u.oid = m.member WHERE u.rolname LIKE 'medakis_%'") == 1
        assert q("SELECT rolsuper OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname = 'medakis_owner'") is False


@pytest.mark.parametrize("role", ["medakis_app", "medakis_rag", "medakis_owner"])
def test_seed_role_cannot_become_another_role(role):
    with psycopg.connect(host="127.0.0.1", port=load_settings().pg_port, dbname="medakis_test", user=SEED_ROLE,
                         password=get_password(SEED_ROLE), connect_timeout=5) as conn:  # fmt: skip
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SET ROLE {role}")


def test_without_membership_dev_path_refuses_before_any_change(engines, dev_like):
    if has_membership(engines):
        pytest.skip("Bu sunucuda üyelik kurulu; üyeliksiz red yolu üyeliği olmayan sunucuda sınanır")
    before = snapshot(dev_like, engines)
    with pytest.raises(DomainError) as info:
        dev_like.apply(SMALL)
    assert info.value.code == errors.SEED_ROLE_SETUP
    assert snapshot(dev_like, engines) == before
    with engines["owner"].connect() as conn:
        assert idle(conn)


# ---- T1: GRANT → SET LOCAL ROLE → yazma → RESET ROLE → REVOKE, hepsi tek işlemde -----------------


def test_first_load_runs_as_seed_without_delete_and_is_invisible_to_others(engines, dev_like, monkeypatch, membership):
    paused = Paused(monkeypatch, lambda: dev_like.apply(SMALL))
    try:
        assert paused.inside == {"current_user": "medakis_seed", "insert": True, "delete": False, "audit_insert": True,
                                 "maintain": False, "history_insert": False}  # fmt: skip
        with engines["owner"].connect() as conn:  # başka oturum: yetki görünmüyor
            assert idle(conn)
        for sql in UNLOCKED_WRITES:  # üçüncü bağlantı, aynı seed parolasıyla
            assert seed_write_error(engines, sql) is psycopg.errors.InsufficientPrivilege, sql
        for sql in TABLE_WRITES:
            assert seed_write_error(engines, sql) in BLOCKED, sql
    finally:
        paused.finish()
    assert "error" not in paused.outcome, paused.outcome
    assert paused.outcome["result"].action == "loaded"
    status = dev_like.status()
    assert status.content_sha256 == status.last_seed["content_sha256"] == paused.outcome["result"].content_sha256
    assert status.write_open is False
    with engines["owner"].connect() as conn:
        assert idle(conn)  # commit sonrası kalıcı yetki değişikliği yok
    for sql in TABLE_WRITES + UNLOCKED_WRITES:  # kilit yokken: doğrudan yetki hatası
        assert seed_write_error(engines, sql) is psycopg.errors.InsufficientPrivilege, sql


def test_reset_gets_delete_only_inside_its_transaction(engines, dev_like, monkeypatch, membership):
    dev_like.apply(SMALL)
    paused = Paused(monkeypatch, lambda: dev_like.apply(replace(SMALL, seed=21), reset=True, confirm="medakis_test"))
    try:
        assert paused.inside["delete"] is True and paused.inside["maintain"] is False
        assert seed_write_error(engines, "DELETE FROM facilities WHERE false") in BLOCKED
        assert seed_write_error(engines, UNLOCKED_WRITES[0]) is psycopg.errors.InsufficientPrivilege
    finally:
        paused.finish()
    assert paused.outcome["result"].action == "reset"
    with engines["owner"].connect() as conn:
        assert idle(conn)


def test_rollback_leaves_no_grant_and_no_data_change(engines, dev_like, monkeypatch, membership):
    dev_like.apply(SMALL)
    before = snapshot(dev_like, engines)

    def fail(*args, **kwargs):
        raise RuntimeError("yapay hata")

    monkeypatch.setattr(audit_repo, "insert_event", fail)  # yetki verildikten ve yazmalardan sonra hata
    with pytest.raises(RuntimeError, match="yapay"):
        dev_like.apply(replace(SMALL, seed=22), reset=True, confirm="medakis_test")
    monkeypatch.undo()
    assert snapshot(dev_like, engines) == before
    with engines["owner"].connect() as conn:
        assert idle(conn)


def test_process_kill_mid_transaction_leaves_no_grant_lock_or_data(engines, dev_like, membership):
    before = snapshot(dev_like, engines)
    child = subprocess.Popen([sys.executable, "-u", str(ROOT / "tests" / "seed_kill_child.py"), "load", "42"], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")  # fmt: skip
    lines = []
    reader = threading.Thread(target=lambda: lines.append(child.stdout.readline().strip()))
    reader.start()
    reader.join(120)
    try:
        assert lines == ["READY"], (lines, child.poll())
        with engines["owner"].connect() as conn:
            assert idle(conn)  # süreç işlemin ortasındayken bile başka oturum yetki görmüyor
        for sql in UNLOCKED_WRITES:
            assert seed_write_error(engines, sql) is psycopg.errors.InsufficientPrivilege, sql
    finally:
        child.kill()  # ani sonlandırma: commit yok, finally yok
        child.wait(30)
    deadline = time.monotonic() + 60
    with engines["owner"].connect() as conn:
        while conn.execute(text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :k"), {"k": seed_repo.ADVISORY_LOCK_KEY}).scalar_one():  # fmt: skip
            assert time.monotonic() < deadline, "sunucu öldürülen sürecin işlemini geri almadı"
            conn.rollback()
            time.sleep(0.5)
        conn.rollback()
        assert idle(conn)
    assert snapshot(dev_like, engines) == before
    assert dev_like.apply(SMALL).action == "loaded"  # geride kilit veya yarım iş kalmadı


def test_two_concurrent_dev_path_seeds_write_once(engines, dev_like, membership):
    before = audit_count(engines)
    results, failures = [], []

    def run():
        try:
            svc = SeedService("medakis_test", engine=engines["seed"], grant_engine=engines["owner"], backup=no_backup)
            results.append(svc.apply(SMALL).action)
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(180)
    assert not failures, failures
    assert sorted(results) == ["loaded", "unchanged"]
    assert audit_count(engines) == before + 1
    with engines["owner"].connect() as conn:
        assert idle(conn)


# ---- T2: kimlik ve hedef, yetki değişikliğinden önce -------------------------------------------


def test_identity_mismatch_stops_before_any_grant_or_lock(engines, dev_like, monkeypatch):
    calls = []
    monkeypatch.setattr(seed_access, "grant_scoped", lambda conn, with_delete: calls.append("grant"))
    monkeypatch.setattr(seed_repo, "lock", lambda conn, timeout: calls.append("lock"))
    values = iter(["1:1", "2:1"])
    monkeypatch.setattr(seed_repo, "server_identity", lambda conn: next(values))
    with pytest.raises(DomainError) as info:
        dev_like.apply(SMALL)
    assert info.value.code == errors.SEED_SERVER_MISMATCH and calls == []


def test_wrong_target_database_stops_before_any_grant(engines, dev_like, monkeypatch):
    calls = []
    monkeypatch.setattr(seed_access, "grant_scoped", lambda conn, with_delete: calls.append("grant"))
    names = iter(["medakis_test", "baska_db"])  # ön kontrol doğru, owner bağlantısı farklı veritabanı
    monkeypatch.setattr(seed_repo, "current_database", lambda conn: next(names))
    with pytest.raises(DomainError) as info:
        dev_like.apply(SMALL)
    assert info.value.code == errors.SEED_WRONG_DATABASE and calls == []


def test_stale_permanent_grant_is_refused_before_any_write(engines, dev_like):
    with engines["owner"].begin() as conn:
        conn.execute(text("GRANT INSERT ON facilities TO medakis_seed"))  # eski bir yedekten gelmiş gibi
    try:
        assert dev_like.status().write_open is True
        before = snapshot(dev_like, engines)
        with pytest.raises(DomainError) as info:
            dev_like.apply(SMALL)
        assert info.value.code == errors.SEED_ROLE_SETUP
        assert snapshot(dev_like, engines) == before
    finally:
        with engines["owner"].begin() as conn:
            conn.execute(text("REVOKE INSERT ON facilities FROM medakis_seed"))


# ---- Kalıcı durum ---------------------------------------------------------------------------


def test_test_database_keeps_permanent_seed_privileges(engines):
    with engines["owner"].connect() as conn:
        assert seed_privileges(conn) == {t: set(PRIVILEGES["medakis_seed"][t]) for t in SEED_IDLE_PRIVILEGES}


def test_dev_seed_role_is_permanently_read_only():
    # Yalnız katalog okunur; medakis_dev verisine dokunulmaz.
    engine = seed_engine("medakis_dev", allow_prompt=False)
    with engine.connect() as conn:
        assert seed_repo.current_database(conn) == "medakis_dev"
        assert idle(conn)
    engine.dispose()
