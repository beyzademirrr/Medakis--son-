"""Docker yapılandırmasının statik güvenlik testleri (Docker gerektirmez)."""

import re

import pytest
import yaml


def _service(project_root):
    data = yaml.safe_load((project_root / "compose.yaml").read_text(encoding="utf-8"))
    return data, data["services"]["postgres"]


def test_image_official_pinned_major_18_with_digest(project_root):
    _, svc = _service(project_root)
    assert re.fullmatch(r"postgres:18\.\d+-trixie@sha256:[0-9a-f]{64}", svc["image"]), svc["image"]


def test_port_bound_to_loopback_only(project_root):
    _, svc = _service(project_root)
    assert svc["ports"], "port tanımı olmalı"
    assert all(str(p).startswith("127.0.0.1:") for p in svc["ports"]), svc["ports"]


def test_named_volume_on_pg18_data_parent(project_root):
    data, svc = _service(project_root)
    # PostgreSQL 18 imajı için doğru hedef; eski /var/lib/postgresql/data veriyi kaybettirir.
    assert svc["volumes"] == ["pgdata:/var/lib/postgresql"]
    assert data["volumes"]["pgdata"]["name"] == "medakis_pgdata"


def test_healthcheck_and_restart_policy(project_root):
    _, svc = _service(project_root)
    assert svc["restart"] == "unless-stopped"
    test = svc["healthcheck"]["test"]
    assert "pg_isready" in test and "127.0.0.1" in test


def test_no_literal_password_in_compose(project_root):
    _, svc = _service(project_root)
    assert svc["environment"]["POSTGRES_PASSWORD"] == "${MEDAKIS_PG_SUPERUSER_PASSWORD:-}"
    assert "POSTGRES_HOST_AUTH_METHOD" not in svc["environment"]


def test_env_example_has_no_password_values(project_root):
    keys = [
        ln.split("=", 1)[0].strip()
        for ln in (project_root / ".env.example").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    assert keys
    assert not [k for k in keys if re.search("PASS|SECRET|TOKEN|KEY", k, re.I)], keys


def test_secret_and_backup_paths_are_ignored(project_root):
    lines = (project_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    for rule in (".env", "*.env", "backups/", "*.dump"):
        assert rule in lines, rule


def test_db_scripts_are_ascii_and_store_no_password(project_root):
    # Windows PowerShell 5.1, BOM'suz UTF-8 betikleri yanlış okur; psql stdin'i de ASCII kalmalı.
    paths = [*(project_root / "scripts" / "db").glob("*.ps1"), *(project_root / "docker" / "postgres").glob("*.sql")]
    names = sorted(p.name for p in paths)
    assert names == ["backup.ps1", "prova.ps1", "provision.sql", "restore.ps1", "seed_membership.ps1", "seed_membership.sql", "setup.ps1"]
    for path in paths:
        text = path.read_bytes().decode("utf-8")
        assert text.isascii(), path.name
        assert not re.search(r"(?i)\bPASSWORD\s+'", text), path.name
        assert "Set-Content" not in text and "Out-File" not in text, path.name


def test_provision_does_not_create_application_schema(project_root):
    sql = (project_root / "docker" / "postgres" / "provision.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE" not in sql.upper()


@pytest.mark.parametrize(
    ("probe", "expected_exit", "expected_codes"),
    [("probe_gecer.py", 0, "0, 0, 0, 0"), ("probe_kalir.py", 1, "0, 1, 0, 0")],
)
def test_prova_test_exit_code_reflects_failing_group(project_root, probe, expected_exit, expected_codes):
    # B4: prova.ps1 -Islem test, herhangi bir pytest grubu başarısızsa sıfırdan farklı çıkmalı.
    # Prova kümesi gerekmez: hedef dosyalar veritabanına bağlanmaz.
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        pytest.skip("docker CLI yok")
    target = f"docker/prova/cikis_kodu/{probe}"
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(project_root / "scripts" / "db" / "prova.ps1"),
         "-Islem", "test", "-Hedef", target],
        cwd=project_root, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert proc.returncode == expected_exit, proc.stdout + proc.stderr
    assert f"cikis kodlari (varsayilan, postgres, prova_rol, lifecycle): {expected_codes}" in proc.stdout, proc.stdout
