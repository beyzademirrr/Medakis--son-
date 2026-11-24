"""FAZ 1.2C bağımsız inceleme bulguları F1–F4 için gerçek PostgreSQL testleri: pytest -m postgres

Tüm yazmalar medakis_test'tedir. medakis_dev'de yalnız katalog (yetki) okunur.
"""

import re
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import func, select, text

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.repositories import audit_repo, seed_repo
from medakis.repositories.database import migration_engine, runtime_engine, seed_engine
from medakis.repositories.schema import (
    SEED_RESET_TABLES,
    audit_events,
    facilities,
    metadata,
    patients,
)
from medakis.services.seed_service import SeedService
from medakis.synthetic.generator import generate
from medakis.synthetic.params import SyntheticParams

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
SMALL = SyntheticParams(days=3)
IDENTITY_TABLES = [n for n in SEED_RESET_TABLES if "id" in metadata.tables[n].c and metadata.tables[n].c.id.identity is not None]
MODEL_RUN_SQL = (
    "INSERT INTO model_runs (model_name, created_at, train_start, train_end, test_start, test_end, horizon_minutes, "
    "mae, rmse, artifact_path, feature_spec, seed) VALUES ('baseline', now(), '2026-10-01Z', '2026-10-10Z', "
    "'2026-10-11Z', '2026-10-20Z', 30, 1, 2, 'a', 'f', 42)"
)


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


def clear(engines):
    with engines["owner"].begin() as conn:
        conn.execute(text("DELETE FROM workload_forecasts"))
        conn.execute(text("DELETE FROM model_runs"))
    with engines["seed"].begin() as conn:
        seed_repo.delete_operational(conn)


@pytest.fixture
def service(engines):
    clear(engines)
    yield SeedService("medakis_test", engine=engines["seed"], backup=no_backup)
    clear(engines)


def audit_count(engines):
    with engines["owner"].connect() as conn:
        return conn.execute(select(func.count()).select_from(audit_events)).scalar_one()


def snapshot(service, engines):
    s = service.status()
    return s.content_sha256, s.operational_counts, s.protected_counts, audit_count(engines)


def max_ids(engines):
    with engines["owner"].connect() as conn:
        return {n: conn.execute(text(f"SELECT max(id) FROM {n}")).scalar_one() for n in IDENTITY_TABLES}


def sequence_values(engines):
    sql = text("SELECT pg_sequence_last_value(CAST(pg_get_serial_sequence(:t, 'id') AS regclass))")
    with engines["owner"].connect() as conn:
        return {n: conn.execute(sql, {"t": f"public.{n}"}).scalar_one() for n in IDENTITY_TABLES}


def fail(*args, **kwargs):
    raise RuntimeError("yapay hata")


# ---- F1: setval geri alınamaz; sayaç bu yüzden yalnız ileri gider ----------------------------


def test_rollback_after_real_sequence_sync_keeps_rows_audit_and_safe_sequences(service, engines, monkeypatch):
    service.apply(SMALL)
    old_max = max_ids(engines)
    before = snapshot(service, engines)
    smaller = replace(SMALL, days=1, seed=11)
    new_tables = generate(smaller).tables
    lower = [n for n in IDENTITY_TABLES if new_tables[n] and old_max[n] and max(r["id"] for r in new_tables[n]) < old_max[n]]
    assert lower, "senaryo, yeni verinin bazı tablolarda daha küçük id'lerle bitmesini gerektirir"

    calls = []
    real_sync = seed_repo.sync_sequences

    def sync_then_record(conn):
        real_sync(conn)  # gerçek setval çalışır
        calls.append("sync")

    monkeypatch.setattr(seed_repo, "sync_sequences", sync_then_record)
    monkeypatch.setattr(audit_repo, "insert_event", fail)  # sayaçtan ve içerik doğrulamasından sonra hata
    with pytest.raises(RuntimeError, match="yapay"):
        service.apply(smaller, reset=True, confirm="medakis_test")
    monkeypatch.undo()
    assert calls == ["sync"]

    assert snapshot(service, engines) == before  # satırlar ve denetim kaydı aynen kaldı
    seq = sequence_values(engines)
    assert all(old_max[n] is None or (seq[n] or 0) >= old_max[n] for n in IDENTITY_TABLES), seq  # sayaç eski verinin altına inmedi
    # Koşulsuz: küçülen tablolardan patients'a uygulama rolüyle gerçek ekleme. Sayaç eski verinin
    # altına inseydi yeni id mevcut bir satırla çakışırdı (birincil anahtar hatası).
    assert "patients" in lower
    with engines["app"].connect() as conn:
        new_id = conn.execute(
            patients.insert().values(synthetic_code="SYNF1KONTROL").returning(patients.c.id)
        ).scalar_one()
        assert new_id > old_max["patients"]
        conn.rollback()
    # Küçülen her tablo için sayacın bir sonraki değeri eski en büyük id'nin üstünde (owner, test veritabanı).
    with engines["owner"].connect() as conn:
        for name in lower:
            nxt = conn.execute(text("SELECT nextval(CAST(pg_get_serial_sequence(:t, 'id') AS regclass))"), {"t": f"public.{name}"}).scalar_one()
            assert nxt > old_max[name], name
        conn.rollback()


def test_failed_first_load_leaves_empty_tables_and_audit(service, engines, monkeypatch):
    before = snapshot(service, engines)
    monkeypatch.setattr(audit_repo, "insert_event", fail)
    with pytest.raises(RuntimeError, match="yapay"):
        service.apply(SMALL)
    monkeypatch.undo()
    assert snapshot(service, engines) == before
    with engines["app"].connect() as conn:
        conn.execute(facilities.insert().values(name="Boş veritabanı"))
        conn.rollback()
    # Sayaç boşlukları içerik özetini etkilemez: aynı parametreler yine aynı veriyi üretir.
    result = service.apply(SMALL)
    assert result.action == "loaded" and service.status().content_sha256 == result.content_sha256


def test_sequences_never_move_backwards_on_successful_reset(service, engines):
    service.apply(SMALL)
    old_max = max_ids(engines)
    service.apply(replace(SMALL, days=1, seed=11), reset=True, confirm="medakis_test")
    seq = sequence_values(engines)
    assert all(old_max[n] is None or (seq[n] or 0) >= old_max[n] for n in IDENTITY_TABLES), seq


# ---- F2: seed işlemi boyunca başka yazıcılar bekler ------------------------------------------


def _paused_seed(monkeypatch, run):
    """Seed'i gerçek sayaç güncellemesinden sonra, commit'ten önce bekletir."""
    reached, release, outcome = threading.Event(), threading.Event(), {}
    real_sync = seed_repo.sync_sequences

    def sync_and_wait(conn):
        real_sync(conn)
        reached.set()
        release.wait(60)

    monkeypatch.setattr(seed_repo, "sync_sequences", sync_and_wait)

    def target():
        try:
            outcome["result"] = run()
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    assert reached.wait(90), "seed bekleme noktasına ulaşmadı"
    return release, thread, outcome


def _app_error(engines, sql):
    with engines["app"].connect() as conn:
        conn.exec_driver_sql("SET lock_timeout = '700ms'")
        try:
            conn.exec_driver_sql(sql)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            return type(exc.orig)
        conn.rollback()
    return None


def test_app_writes_wait_while_seed_transaction_runs(service, engines, monkeypatch):
    service.apply(SMALL)
    release, thread, outcome = _paused_seed(
        monkeypatch, lambda: service.apply(replace(SMALL, seed=12), reset=True, confirm="medakis_test")
    )
    try:
        assert _app_error(engines, "INSERT INTO facilities (name) VALUES ('yarış')") is psycopg.errors.LockNotAvailable
        assert _app_error(engines, "UPDATE care_tasks SET status = status WHERE id = 1") is psycopg.errors.LockNotAvailable
        assert _app_error(engines, MODEL_RUN_SQL) is psycopg.errors.LockNotAvailable  # bağımsız tarihsel kayıt da bekler
        with engines["app"].connect() as conn:  # okuma engellenmez; commit edilmemiş içerik görünmez
            assert conn.execute(select(func.count()).select_from(facilities)).scalar_one() > 0
    finally:
        release.set()
        thread.join(90)
    assert "error" not in outcome, outcome
    status = service.status()
    assert status.content_sha256 == status.last_seed["content_sha256"] == outcome["result"].content_sha256
    assert all(n == 0 for n in status.protected_counts.values())
    # Seed bittikten sonra uygulama normal yazabilir.
    assert _app_error(engines, "INSERT INTO facilities (name) VALUES ('sonra')") is None


@pytest.mark.parametrize("sql", ["INSERT INTO facilities (name) VALUES ('işlem açık')", MODEL_RUN_SQL])
def test_seed_stops_without_changes_when_app_transaction_is_open(service, engines, sql):
    service.apply(SMALL)
    before = snapshot(service, engines)
    impatient = SeedService("medakis_test", engine=engines["seed"], backup=no_backup, lock_timeout_ms=700)
    app = engines["app"].connect()
    tx = app.begin()
    try:
        app.exec_driver_sql(sql)
        with pytest.raises(DomainError) as info:
            impatient.apply(replace(SMALL, seed=13), reset=True, confirm="medakis_test")
        assert info.value.code == errors.SEED_LOCKED
    finally:
        tx.rollback()
        app.close()
    assert snapshot(service, engines) == before


def test_history_committed_by_app_before_seed_lock_stops_reset(service, engines, monkeypatch):
    # Ön kontrolden sonra, seed kilidinden önce tarihsel kayıt eklenirse işlem içi kontrol yakalar.
    service.apply(SMALL)
    before_content = service.status().content_sha256
    real_lock = seed_repo.lock

    def app_commits_history_then_lock(conn, timeout):
        with engines["app"].begin() as app:
            app.exec_driver_sql(MODEL_RUN_SQL)
        real_lock(conn, timeout)

    monkeypatch.setattr(seed_repo, "lock", app_commits_history_then_lock)
    with pytest.raises(DomainError) as info:
        service.apply(replace(SMALL, seed=17), reset=True, confirm="medakis_test")
    assert info.value.code == errors.SEED_HISTORY_PRESENT
    monkeypatch.undo()
    assert service.status().content_sha256 == before_content


def test_two_concurrent_seeds_write_once(service, engines):
    before = audit_count(engines)
    results, failures = [], []

    def run():
        try:
            results.append(SeedService("medakis_test", engine=engines["seed"], backup=no_backup).apply(SMALL).action)
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
    status = service.status()
    assert status.content_sha256 == status.last_seed["content_sha256"]


def test_server_identity_change_during_seed_is_refused(service, engines, monkeypatch):
    before = snapshot(service, engines)
    values = iter(["1:1", "2:1"])
    monkeypatch.setattr(seed_repo, "server_identity", lambda conn: next(values))
    with pytest.raises(DomainError) as info:
        service.apply(SMALL)
    assert info.value.code == errors.SEED_SERVER_MISMATCH
    monkeypatch.undo()
    assert snapshot(service, engines) == before


# ---- F3/T1/T2: işlem içi yetki testleri tests/test_seed_scoped_grant_live.py dosyasındadır ----


# ---- F4: yedek, seed bağlantısının sunucusundan alınmalı -------------------------------------


def _backup(*args):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "db" / "backup.ps1"),
         "-Database", "medakis_test", *args], cwd=ROOT, capture_output=True, text=True, timeout=300)  # fmt: skip


def _backup_files():
    folder = ROOT / "backups"
    return set(folder.iterdir()) if folder.exists() else set()


def test_backup_script_refuses_other_server_identity(engines):
    with engines["seed"].connect() as conn:
        real = seed_repo.server_identity(conn)
    sysid, oid = real.split(":")
    known = _backup_files()
    for wrong in (f"{int(sysid) + 1}:{oid}", f"{sysid}:{int(oid) + 1}"):
        proc = _backup("-BeklenenKimlik", wrong)
        assert proc.returncode == 2 and "seed baglantisinin sunucusu degil" in proc.stdout, proc.stdout + proc.stderr
    assert _backup_files() == known  # hiçbir dosya bırakılmadı
    proc = _backup("-BeklenenKimlik", real)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    name = re.search(r"Yedek: backups\\(\S+)", proc.stdout).group(1)
    created = [ROOT / "backups" / name, ROOT / "backups" / f"{name}.sha256"]
    assert all(p.exists() for p in created) and set(created) <= _backup_files() - known
    for p in created:
        p.unlink()  # yalnız bu testin oluşturduğu, adı çıktıdan okunan medakis_test yedeği


def test_service_passes_its_own_server_identity_to_backup(service, engines):
    calls = []
    checked = SeedService("medakis_test", engine=engines["seed"], backup=lambda db, ident: calls.append((db, ident)), backup_required=True)
    checked.apply(SMALL)
    assert calls == []  # boş veritabanında silinecek veri yok, yedek gerekmez
    checked.apply(replace(SMALL, seed=15), reset=True, confirm="medakis_test")
    with engines["owner"].connect() as conn:
        expected = seed_repo.server_identity(conn)
    assert calls == [("medakis_test", expected)]


def test_failed_backup_stops_reset_before_any_write(service, engines):
    def failing_backup(db, ident):
        raise DomainError(errors.SEED_BACKUP_FAILED, "yapay")

    checked = SeedService("medakis_test", engine=engines["seed"], backup=failing_backup, backup_required=True)
    checked.apply(SMALL)
    before = snapshot(service, engines)
    with pytest.raises(DomainError) as info:
        checked.apply(replace(SMALL, seed=16), reset=True, confirm="medakis_test")
    assert info.value.code == errors.SEED_BACKUP_FAILED
    assert snapshot(service, engines) == before
