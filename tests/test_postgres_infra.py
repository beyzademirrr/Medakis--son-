"""Çalışan PostgreSQL konteynerine karşı altyapı testleri.

    pytest -m postgres            bağlantı, yetki, ağ ve yapılandırma
    pytest -m postgres_lifecycle  konteyneri durdurur; kalıcılık, yedekleme, geri yükleme

Kullanıcı parolaları test edilmez ve okunmaz. TCP girişi, test süresince rastgele parolayla
oluşturulup sonunda silinen geçici bir rolle sınanır. Yazma denemeleri yalnız medakis_test'te yapılır.
"""

import re
import secrets
import socket
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
DATABASES = ("medakis_dev", "medakis_test", "medakis_demo")
ROLES = ("medakis_owner", "medakis_app", "medakis_rag", "medakis_seed")
from backup_registry import OwnedBackups  # noqa: E402
from pg_target import CONTAINER, PORT, VOLUME  # noqa: E402


def run(*args, stdin=None, timeout=180):
    return subprocess.run(
        args, cwd=ROOT, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )


def psql(sql, db="postgres"):
    """Konteyner içi soket üzerinden süper kullanıcı sorgusu; SQL stdin ile gider."""
    proc = run(
        "docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", db,
        "-v", "ON_ERROR_STOP=1", "-At", "-f", "-", stdin=sql,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def psql_error(sql, db="medakis_test"):
    proc = run(
        "docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", db,
        "-v", "ON_ERROR_STOP=1", "-At", "-f", "-", stdin=sql,
    )
    assert proc.returncode != 0, f"Reddedilmesi gerekirdi: {sql}"
    return proc.stderr


def ps1(script, *args):
    return run(
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts" / "db" / script), *args, timeout=300,
    )


def inspect(fmt):
    proc = run("docker", "inspect", CONTAINER, "--format", fmt)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture(scope="module")
def container():
    if inspect("{{.State.Health.Status}}") != "healthy":
        pytest.fail(f"{CONTAINER} sağlıklı değil")


@pytest.fixture(scope="module")
def temp_role(container):
    """medakis_test'e bağlanabilen, rastgele parolalı geçici rol."""
    name = f"medakis_tmp_{secrets.token_hex(4)}"
    password = secrets.token_hex(24)
    psql(f"CREATE ROLE {name} LOGIN PASSWORD '{password}'; GRANT CONNECT ON DATABASE medakis_test TO {name};")
    yield name, password
    psql(f"REVOKE ALL ON DATABASE medakis_test FROM {name}; DROP ROLE {name};")


def connect(user, password, dbname="medakis_test", host="127.0.0.1"):
    return psycopg.connect(host=host, port=PORT, user=user, password=password, dbname=dbname, connect_timeout=5)


# ---- Yapılandırma ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_server_version_and_data_directory(container):
    assert psql("SHOW server_version_num").startswith("18")
    assert psql("SHOW data_directory") == "/var/lib/postgresql/18/docker"
    assert psql("SHOW data_checksums") == "on"


@pytest.mark.postgres
def test_only_named_volume_is_mounted(container):
    mounts = inspect("{{range .Mounts}}{{.Type}}|{{.Name}}|{{.Destination}};{{end}}")
    assert mounts == f"volume|{VOLUME}|/var/lib/postgresql;"


@pytest.mark.postgres
def test_password_absent_from_container_config(container):
    env = inspect("{{range .Config.Env}}{{println .}}{{end}}").splitlines()
    values = [e.split("=", 1)[1] for e in env if e.startswith("POSTGRES_PASSWORD=")]
    assert values in ([""], []), "Parola konteyner yapılandırmasında kalmamalı"


@pytest.mark.postgres
def test_published_only_on_loopback(container):
    assert run("docker", "port", CONTAINER, "5432/tcp").stdout.strip() == f"127.0.0.1:{PORT}"


@pytest.mark.postgres
def test_not_reachable_on_lan_address(container):
    addresses = {
        info[4][0]
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        if not info[4][0].startswith("127.")
    }
    if not addresses:
        pytest.skip("Loopback dışında IPv4 adresi yok")
    for address in addresses:
        with pytest.raises(OSError):
            socket.create_connection((address, PORT), timeout=2).close()


@pytest.mark.postgres
def test_databases_owner_encoding_and_settings(container):
    for db in DATABASES:
        row = psql(
            "SELECT pg_get_userbyid(datdba), pg_encoding_to_char(encoding) FROM pg_database "
            f"WHERE datname = '{db}'"
        )
        assert row == "medakis_owner|UTF8", db
        assert psql("SHOW timezone", db) == "UTC", db
        assert psql("SHOW lock_timeout", db) == "5s", db
        assert psql("SELECT count(*) FROM pg_extension WHERE extname = 'btree_gist'", db) == "1", db


@pytest.mark.postgres
def test_demo_schema_not_created_yet(container):
    # medakis_dev FAZ 1.2C'de migrate edildi; medakis_demo proje sahibi kararıyla boş kalır.
    for db in ("medakis_demo",):
        count = psql("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'", db)
        assert count == "0", db


@pytest.mark.postgres
def test_roles_least_privilege_and_have_scram_password(container):
    for role in ROLES:
        row = psql(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin, "
            f"rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname = '{role}'"
        )
        assert row == "f|f|f|f|f|t|t", role


# ---- Yetki ----------------------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.parametrize("role", ["medakis_app", "medakis_rag"])
def test_app_roles_cannot_create_objects(container, role):
    err = psql_error(f"SET ROLE {role}; CREATE TABLE public.yetki_denemesi(id int);")
    assert "permission denied" in err


@pytest.mark.postgres
def test_owner_can_create_objects(container):
    psql("BEGIN; SET LOCAL ROLE medakis_owner; CREATE TABLE public.yetki_denemesi(id int); ROLLBACK;", "medakis_test")


@pytest.mark.postgres
def test_public_has_no_database_or_schema_access(container):
    for db in DATABASES:
        assert psql(f"SELECT has_database_privilege('public', '{db}', 'CONNECT')") == "f", db
        assert psql("SELECT has_schema_privilege('public', 'public', 'USAGE')", db) == "f", db
    for db in ("postgres", "template1"):
        assert psql(f"SELECT has_database_privilege('public', '{db}', 'CONNECT')") == "f", db


@pytest.mark.postgres
def test_provision_is_idempotent(container):
    before = psql("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'medakis_%'")
    psql((ROOT / "docker" / "postgres" / "provision.sql").read_text(encoding="utf-8"))
    assert psql("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'medakis_%'") == before
    assert psql("SELECT count(*) FROM pg_database WHERE datname LIKE 'medakis_%'") == "3"


# ---- Host'tan TCP bağlantısı ----------------------------------------------------------------


@pytest.mark.postgres
def test_tcp_connection_from_windows_host(temp_role):
    name, password = temp_role
    with connect(name, password) as conn:
        row = conn.execute("SELECT current_database(), current_user, current_setting('TimeZone')").fetchone()
    assert row == ("medakis_test", name, "UTC")
    assert conn.closed


@pytest.mark.postgres
def test_tcp_uses_scram_and_rejects_wrong_password(temp_role):
    name, _ = temp_role
    assert psql(f"SELECT rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname = '{name}'") == "t"
    with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
        connect(name, "yanlis-parola")


@pytest.mark.postgres
def test_tcp_rejects_superuser_without_correct_password(container):
    with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
        connect("postgres", "yanlis-parola", dbname="postgres")


@pytest.mark.postgres
@pytest.mark.parametrize("db", ["medakis_dev", "medakis_demo", "postgres"])
def test_connect_privilege_enforced_over_tcp(temp_role, db):
    name, password = temp_role
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        connect(name, password, dbname=db)


# ---- Yaşam döngüsü (konteyneri durdurur) -----------------------------------------------------


@pytest.fixture
def marker_table(container):
    token = secrets.token_hex(8)
    psql(
        "DROP TABLE IF EXISTS public.kalicilik_denemesi;"
        "CREATE TABLE public.kalicilik_denemesi(deger text);"
        f"INSERT INTO public.kalicilik_denemesi VALUES ('{token}');",
        "medakis_test",
    )
    yield token
    psql("DROP TABLE IF EXISTS public.kalicilik_denemesi;", "medakis_test")


def wait_up():
    proc = run("docker", "compose", "up", "-d", "--wait")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.postgres_lifecycle
def test_data_survives_restart_and_container_removal(marker_table):
    read = "SELECT deger FROM public.kalicilik_denemesi"
    assert run("docker", "compose", "restart", "postgres").returncode == 0
    wait_up()
    assert psql(read, "medakis_test") == marker_table

    old_id = inspect("{{.Id}}")
    assert run("docker", "compose", "down").returncode == 0, "volume silinmeden kapatma"
    assert run("docker", "volume", "inspect", VOLUME).returncode == 0
    wait_up()
    assert inspect("{{.Id}}") != old_id, "Konteyner gerçekten yeniden oluşturulmalı"
    assert psql(read, "medakis_test") == marker_table


@pytest.mark.postgres_lifecycle
def test_backup_and_restore_round_trip(marker_table):
    owned = OwnedBackups()
    proc = ps1("backup.ps1", "-Database", "medakis_test")
    owned.from_output(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    name = re.search(r"Yedek: backups\\(\S+)", proc.stdout).group(1)
    dump = ROOT / "backups" / name
    assert dump.stat().st_size > 0 and dump.read_bytes()[:5] == b"PGDMP"
    try:
        psql("DROP TABLE public.kalicilik_denemesi;", "medakis_test")

        demo = ps1("restore.ps1", "-Database", "medakis_demo", "-File", f"backups\\{name}", "-Onayla", "-DemoOnayi", "medakis_demo")
        assert demo.returncode == 3 and "gecici olarak devre disi" in demo.stdout, "Demo geri yüklemesi devre dışı olmalı"
        refused = ps1("restore.ps1", "-Database", "medakis_test", "-File", f"backups\\{name}")
        assert refused.returncode != 0, "-Onayla olmadan geri yükleme yapılmamalı"
        assert psql("SELECT to_regclass('public.kalicilik_denemesi') IS NULL", "medakis_test") == "t"

        proc = ps1("restore.ps1", "-Database", "medakis_test", "-File", f"backups\\{name}", "-Onayla")
        owned.from_output(proc.stdout)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert psql("SELECT deger FROM public.kalicilik_denemesi", "medakis_test") == marker_table
        # Geri yükleme sonrası yetkiler korunmalı.
        assert psql("SELECT has_schema_privilege('public', 'public', 'USAGE')", "medakis_test") == "f"
        assert psql("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'", "medakis_test") == "medakis_owner"
        assert "permission denied" in psql_error("SET ROLE medakis_app; CREATE TABLE public.x(id int);")
    finally:
        owned.cleanup()


@pytest.mark.postgres_lifecycle
def test_backup_restore_preserves_migrated_schema_and_privileges(container):
    """Tam şema: yedekle, Alembic ile sil, geri yükle; tablolar, kısıtlar, yetkiler ve sürüm aynı kalmalı."""
    alembic = [sys.executable, "-m", "alembic", "-x", "db=medakis_test"]
    assert run(*alembic, "upgrade", "head").returncode == 0
    summary = (
        "SELECT (SELECT count(*) FROM pg_tables WHERE schemaname = 'public'), "
        "(SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' AND c.contype = 'x'), "
        "(SELECT version_num FROM alembic_version), "
        "has_table_privilege('medakis_rag', 'public.assignments', 'SELECT'), "
        "has_table_privilege('medakis_rag', 'public.rag_queries', 'INSERT'), "
        "has_table_privilege('medakis_app', 'public.plan_approvals', 'UPDATE'), "
        "has_table_privilege('medakis_app', 'public.plan_approvals', 'INSERT')"
    )
    owned = OwnedBackups()
    # Beklenen sürüm sabit yazılmaz: depodaki migration head'i (şu an 0004_triage_cancel_columns).
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    try:  # ekleme ve ilk doğrulama da try içinde: başarısız olursa temizlik (downgrade base) atlanmaz
        psql("INSERT INTO facilities (name) VALUES ('Yedek denemesi');", "medakis_test")
        before = psql(summary, "medakis_test")
        assert before == f"35|4|{head}|f|t|f|t"
        proc = ps1("backup.ps1", "-Database", "medakis_test")
        owned.from_output(proc.stdout)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        name = re.search(r"Yedek: backups\\(\S+)", proc.stdout).group(1)
        assert run(*alembic, "downgrade", "base").returncode == 0
        assert psql("SELECT to_regclass('public.facilities') IS NULL", "medakis_test") == "t"

        proc = ps1("restore.ps1", "-Database", "medakis_test", "-File", f"backups\\{name}", "-Onayla")
        owned.from_output(proc.stdout)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert psql(summary, "medakis_test") == before
        assert psql("SELECT name FROM facilities", "medakis_test") == "Yedek denemesi"
    finally:
        run(*alembic, "downgrade", "base")
        owned.cleanup()


@pytest.mark.postgres_lifecycle
def test_restore_rejects_corrupted_backup(marker_table):
    proc = ps1("backup.ps1", "-Database", "medakis_test")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    name = re.search(r"Yedek: backups\\(\S+)", proc.stdout).group(1)
    dump = ROOT / "backups" / name
    try:
        with dump.open("ab") as handle:
            handle.write(b"bozuk")
        proc = ps1("restore.ps1", "-Database", "medakis_test", "-File", f"backups\\{name}", "-Onayla")
        assert proc.returncode != 0
        assert "SHA-256" in proc.stdout
        assert psql("SELECT deger FROM public.kalicilik_denemesi", "medakis_test") == marker_table
    finally:
        dump.unlink(missing_ok=True)
        dump.with_name(name + ".sha256").unlink(missing_ok=True)
