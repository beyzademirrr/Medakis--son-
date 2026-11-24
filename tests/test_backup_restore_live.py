"""T3, R1 ve tam veriyle geri yükleme provası (yalnız medakis_test).

    pytest -m postgres            eşzamanlı yedek, yarım/eksik/bozuk yedeğin reddi, eski sürüm yedeğin reddi
    pytest -m postgres_lifecycle  tam sentetik veri: yedekle → boz → geri yükle → doğrula

restore.ps1 testlerde yalnız medakis_test'e karşı çalıştırılır. medakis_dev ve medakis_demo için ret
kuralları, yedek dosyası üzerinde (veritabanına bağlanmadan) check_dump ile sınanır.
"""

import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from backup_registry import OwnedBackups
from sqlalchemy import func, select, text

from medakis.repositories import seed_access, seed_repo
from medakis.repositories.database import migration_engine, runtime_engine, seed_engine
from medakis.repositories.schema import SEED_RESET_TABLES, audit_events, facilities
from medakis.services.seed_service import SeedService
from medakis.synthetic.params import SyntheticParams

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
GOLDEN = "83a619cb04371b892ed32803be9d0d16690bd72cecc6b8f560e9d59897191f42"
spec = importlib.util.spec_from_file_location("check_dump", ROOT / "scripts" / "db" / "check_dump.py")
check_dump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_dump)


def alembic(*args):
    proc = subprocess.run([sys.executable, "-m", "alembic", "-x", "db=medakis_test", *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr


def ps1(script, *args):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "db" / script), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )  # fmt: skip


def backup(owned, *extra):
    proc = ps1("backup.ps1", "-Database", "medakis_test", *extra)
    owned.from_output(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return BACKUPS / re.search(r"Yedek: backups\\(\S+)", proc.stdout).group(1)


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


@pytest.fixture
def owned():
    """Yalnız bu testin oluşturduğu yedekler silinir (B3)."""
    registry = OwnedBackups()
    yield registry
    registry.cleanup()


def no_backup(database, identity):
    raise AssertionError("yedek istenmemeliydi")


def clear(engines):
    with engines["owner"].begin() as conn:
        for name in reversed(SEED_RESET_TABLES):
            conn.execute(text(f"DELETE FROM {name}"))


def state(engines):
    """Geri yükleme karşılaştırması: içerik, sayımlar, denetim, sayaçlar, sürüm, üç rolün yetkileri."""
    privs = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"]
    with engines["owner"].connect() as conn:
        q = lambda sql, **p: conn.execute(text(sql), p)  # noqa: E731
        from medakis.synthetic.canonical import content_sha256

        return {
            "content": content_sha256(seed_repo.read_tables(conn)),
            "counts": seed_repo.operational_counts(conn),
            "audit": [tuple(r) for r in q("SELECT id, actor, action, entity_id, detail_json::text FROM audit_events ORDER BY id")],
            "sequences": {s: q("SELECT pg_sequence_last_value(CAST(:s AS regclass))", s=s).scalar_one() for s in seed_access.identity_sequences(conn)},
            "alembic": q("SELECT version_num FROM alembic_version").scalar_one(),
            "privileges": sorted(tuple(r) for r in q(
                "SELECT r, t.tablename, p FROM unnest(CAST(:roles AS text[])) r, pg_tables t, unnest(CAST(:privs AS text[])) p "
                "WHERE t.schemaname = 'public' AND has_table_privilege(r, format('public.%I', t.tablename), p)",
                roles=["medakis_app", "medakis_rag", "medakis_seed"], privs=privs)),
            "sequence_privileges": sorted(tuple(r) for r in q(
                "SELECT r, s, p FROM unnest(CAST(:roles AS text[])) r, unnest(CAST(:seqs AS text[])) s, unnest(ARRAY['USAGE','UPDATE','SELECT']) p "
                "WHERE has_sequence_privilege(r, s, p)", roles=["medakis_app", "medakis_rag", "medakis_seed"],
                seqs=seed_access.identity_sequences(conn))),
        }  # fmt: skip


# ---- T3: benzersiz ad, yarım/eksik/bozuk yedeğin reddi ------------------------------------------


@pytest.mark.postgres
def test_concurrent_backups_get_distinct_complete_files(owned):
    results = []

    def run():
        proc = ps1("backup.ps1", "-Database", "medakis_test")
        owned.from_output(proc.stdout)
        results.append(proc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(300)
    assert [p.returncode for p in results] == [0, 0, 0], [p.stdout + p.stderr for p in results]
    names = [re.search(r"Yedek: backups\\(\S+)", p.stdout).group(1) for p in results]
    assert len(set(names)) == 3
    for name in names:
        assert re.fullmatch(r"medakis_test-\d{8}T\d{6}Z-[0-9a-f]{12}\.dump", name)
        assert check_dump.check("medakis_test", BACKUPS / name) == []  # SHA eşleşiyor, arşiv baştan sona okunuyor
    assert not [n for n in names if (BACKUPS / f"{n}.partial").exists()]


@pytest.mark.postgres
def test_restore_refuses_partial_missing_sha_and_unreadable_backups(engines, owned):
    clear(engines)
    SeedService("medakis_test", engine=engines["seed"], backup=no_backup).apply(SyntheticParams(days=1))
    good = backup(owned)
    before = state(engines)

    partial = owned.add(good.with_name(good.name + ".partial"))
    shutil.copy(good, partial)
    no_sha = owned.add(good.with_name("eksik-" + good.name))
    shutil.copy(good, no_sha)
    truncated = owned.add_dump(good.with_name("kesik-" + good.name))
    data = good.read_bytes()[: len(good.read_bytes()) // 2]
    truncated.write_bytes(data)
    truncated.with_name(truncated.name + ".sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {truncated.name}\n", encoding="utf-8")

    for path, reason in ((partial, "SHA-256"), (no_sha, "SHA-256"), (truncated, "okunamad")):
        proc = ps1("restore.ps1", "-Database", "medakis_test", "-File", str(path.relative_to(ROOT)), "-Onayla")
        owned.from_output(proc.stdout)
        assert proc.returncode != 0, path.name
        assert reason in proc.stdout, proc.stdout
        assert "guvenlik yedegi" not in proc.stdout.lower(), "ret, güvenlik yedeğinden ve geri yüklemeden önce olmalı"
    assert check_dump.check("medakis_test", partial)[0].startswith("yarım kalmış")
    assert state(engines) == before
    clear(engines)


# ---- R1: eski sürüm ve geniş seed yetkili yedek, dev/demo'ya işlem başlamadan reddedilir --------


@pytest.mark.postgres
def test_old_revision_backup_with_wide_seed_grants_is_refused_for_dev_and_demo(engines, owned):
    alembic("downgrade", "0002_seed_role_grants")  # 0003 öncesi: medakis_seed'in dev'de kalıcı yazma yetkisi vardı
    try:
        old = backup(owned)
    finally:
        alembic("upgrade", "head")
    for target in ("medakis_dev", "medakis_demo"):
        problems = check_dump.check(target, old)  # arşiv kuralları önce; hedef veritabanına bağlanılmaz
        assert any("şema sürümü 0002_seed_role_grants" in p for p in problems), problems
        assert any("yazma/kilit/sayaç yetkisi" in p for p in problems), problems
        assert any("başka bir veritabanına ait (medakis_test)" in p for p in problems), problems
    text_ = check_dump.read_archive(old)[0]
    assert len(check_dump.seed_write_grants(text_)) >= 17


# ---- Tam sentetik veriyle yedekleme ve geri yükleme provası (izole hedef: medakis_test) ---------


@pytest.mark.postgres_lifecycle
def test_full_dataset_backup_and_restore_rehearsal(engines, owned):
    clear(engines)
    service = SeedService("medakis_test", engine=engines["seed"], backup=no_backup)
    assert service.apply(SyntheticParams()).content_sha256 == GOLDEN  # 28 gün, seed=42, 24.955 satır
    with engines["seed"].connect() as conn:
        identity = seed_repo.server_identity(conn)
    before = state(engines)
    assert before["content"] == GOLDEN and sum(before["counts"].values()) == 24955
    assert [a[2] for a in before["audit"]].count("synthetic_seed_applied") == 1

    dump = backup(owned, "-BeklenenKimlik", identity)
    assert check_dump.check("medakis_test", dump) == []

    # Bozma: farklı veriyle sıfırla ve uygulama eklemesi yap.
    service.apply(replace(SyntheticParams(), days=2, seed=7), reset=True, confirm="medakis_test")
    with engines["app"].begin() as conn:
        conn.execute(facilities.insert().values(name="Geri yuklemede kaybolmali"))
    assert state(engines)["content"] != GOLDEN

    proc = ps1("restore.ps1", "-Database", "medakis_test", "-File", str(dump.relative_to(ROOT)), "-Onayla")
    owned.from_output(proc.stdout)  # güvenlik yedeği de bu testindir
    assert proc.returncode == 0, proc.stdout + proc.stderr
    engines["owner"].dispose()
    engines["seed"].dispose()
    engines["app"].dispose()

    after = state(engines)
    assert after == before  # içerik, sayımlar, denetim, sayaçlar, sürüm ve yetkiler aynen
    for name, value in after["sequences"].items():
        table = name.split(".")[1].removesuffix("_id_seq")
        with engines["owner"].connect() as conn:
            max_id = conn.execute(text(f"SELECT max(id) FROM {table}")).scalar_one()
        assert max_id is None or value >= max_id, name
    status = service.status()
    assert status.content_sha256 == status.last_seed["content_sha256"] == GOLDEN
    assert service.apply(SyntheticParams()).action == "unchanged"  # geri yüklenen veri seed ile tutarlı
    with engines["app"].connect() as conn:  # sayaçlar geri yüklendi: uygulama eklemesi çakışmaz
        conn.execute(facilities.insert().values(name="Geri yukleme sonrasi"))
        conn.rollback()
    with engines["owner"].connect() as conn:
        assert conn.execute(select(func.count()).select_from(audit_events)).scalar_one() == len(before["audit"])
    clear(engines)


# ---- B1/B2 (kapanış kararı): dev ve demo geri yüklemesi devre dışı, ilk adımda red --------------


@pytest.mark.postgres
@pytest.mark.parametrize("target", ["medakis_dev", "medakis_demo"])
def test_dev_and_demo_restore_refused_before_any_action(owned, target):
    clean = backup(owned)  # geçerli, doğrulanmış bir medakis_test yedeği bile reddedilmeli
    attempts = [
        ["-File", str(clean.relative_to(ROOT)), "-Onayla", "-DemoOnayi", "medakis_demo"],
        ["-File", r"backups\olmayan-dosya.dump", "-Onayla"],
        ["-File", str(clean.relative_to(ROOT))],
    ]
    for args in attempts:
        proc = ps1("restore.ps1", "-Database", target, *args)
        owned.from_output(proc.stdout)
        assert proc.returncode == 3, proc.stdout + proc.stderr
        assert "gecici olarak devre disi" in proc.stdout and "Hicbir islem yapilmadi" in proc.stdout
        assert "Yedek:" not in proc.stdout and "guvenlik yedegi" not in proc.stdout.lower()
        assert "Yedek denetimi" not in proc.stdout and "SHA-256" not in proc.stdout
    # dev/demo içeriği ve yetkileri: conftest.dev_untouched_by_tests oturum sonunda karşılaştırır.


# ---- B3: test temizliği yalnız testin kendi yedeklerini siler ------------------------------------


@pytest.mark.postgres
def test_cleanup_removes_only_this_tests_backups_and_keeps_unrelated_ones():
    registry = OwnedBackups()
    ours = backup(registry)
    # Test sürerken başka bir sürecin yedeği oluşmuş gibi (ör. dev güvenlik yedeği):
    unrelated = BACKUPS / f"medakis_dev-20990101T000000Z-{uuid.uuid4().hex[:12]}.dump"
    unrelated.write_bytes(b"PGDMP-ilgisiz")
    unrelated_sha = unrelated.with_name(unrelated.name + ".sha256")
    unrelated_sha.write_text(f"{hashlib.sha256(b'PGDMP-ilgisiz').hexdigest()}  {unrelated.name}\n", encoding="utf-8")
    try:
        registry.cleanup()
        assert not ours.exists() and not ours.with_name(ours.name + ".sha256").exists()
        assert unrelated.exists() and unrelated_sha.exists()  # ilgisiz yedek korundu
        with pytest.raises(ValueError):
            registry.add(ROOT / "README.md")  # backups/ dışı dosya kayda alınamaz
    finally:
        unrelated.unlink(missing_ok=True)  # bu testin kendi oluşturduğu dosyalar
        unrelated_sha.unlink(missing_ok=True)
