"""Seed korumaları ve sıfırlama kapsamı (veritabanına yazmadan)."""

import subprocess
import sys
from pathlib import Path

import pytest

from medakis.config import ConfigError
from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.repositories.database import SEEDABLE_DATABASES, runtime_engine, seed_engine
from medakis.repositories.schema import (
    PRIVILEGES,
    SEED_AUDIT_TABLE,
    SEED_PROTECTED_HISTORY,
    SEED_RESET_TABLES,
    metadata,
)
from medakis.services.seed_service import SeedService

ROOT = Path(__file__).resolve().parents[1]
OPERATIONAL = {t for t in metadata.tables if not t.startswith("rag_")}


def run_cli(*args, stdin=""):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "db" / "seed.py"), *args],
        cwd=ROOT, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )  # fmt: skip


def test_reset_scope_covers_operational_tables_exactly_once():
    assert len(SEED_RESET_TABLES) == 17 and len(set(SEED_RESET_TABLES)) == 17
    groups = set(SEED_RESET_TABLES) | set(SEED_PROTECTED_HISTORY) | {SEED_AUDIT_TABLE}
    assert groups == OPERATIONAL and len(OPERATIONAL) == 24
    assert SEED_AUDIT_TABLE not in SEED_RESET_TABLES
    assert not set(SEED_PROTECTED_HISTORY) & set(SEED_RESET_TABLES)


def test_reset_order_respects_foreign_keys():
    position = {name: i for i, name in enumerate(SEED_RESET_TABLES)}
    for name in SEED_RESET_TABLES:
        for fk in metadata.tables[name].foreign_keys:
            parent = fk.column.table.name
            assert parent in position, f"{name} sıfırlanan bir tabloya değil {parent} tablosuna bağlı"
            assert position[parent] < position[name], f"{parent}, {name} tablosundan önce yüklenmeli"


def test_protected_history_references_reset_tables():
    # FK, tarihsel kayıt varken operasyonel satırların silinmesini engeller (audit_events hariç).
    for name in ("workload_forecasts", "optimization_runs", "assignments", "staffing_gaps"):
        parents = {fk.column.table.name for fk in metadata.tables[name].foreign_keys}
        assert parents & set(SEED_RESET_TABLES), name


def test_seed_privileges_least_privilege():
    seed = PRIVILEGES["medakis_seed"]
    assert {t for t, p in seed.items() if "DELETE" in p} == set(SEED_RESET_TABLES)
    assert seed[SEED_AUDIT_TABLE] == frozenset({"SELECT", "INSERT"})
    assert all(seed[t] == frozenset({"SELECT", "MAINTAIN"}) for t in SEED_PROTECTED_HISTORY)  # MAINTAIN: yalnız LOCK için
    for role in ("medakis_app", "medakis_rag"):
        assert not [t for t, p in PRIVILEGES[role].items() if "MAINTAIN" in p], role
    assert not [t for t in seed if t.startswith("rag_")]
    assert not [t for grants in PRIVILEGES.values() for t, p in grants.items() if "TRUNCATE" in p]
    assert not [t for t, p in PRIVILEGES["medakis_app"].items() if "DELETE" in p]


@pytest.mark.parametrize("database", ["medakis_demo", "postgres", "medakis_DEMO", ""])
def test_seed_engine_and_service_refuse_other_databases(database):
    assert database not in SEEDABLE_DATABASES
    with pytest.raises(ConfigError):
        seed_engine(database)
    with pytest.raises(DomainError) as info:
        SeedService(database)
    assert info.value.code == errors.SEED_DATABASE_NOT_ALLOWED


def test_runtime_engine_cannot_use_seed_role():
    with pytest.raises(ConfigError):
        runtime_engine("medakis_seed")


def test_cli_refuses_demo_before_connecting():
    proc = run_cli("apply", "--database", "medakis_demo")
    assert proc.returncode == 1 and "SEED_DATABASE_NOT_ALLOWED" in proc.stdout


def test_cli_has_no_force_or_env_override():
    proc = run_cli("apply", "--database", "medakis_test", "--force")
    assert proc.returncode != 0 and "unrecognized arguments" in proc.stderr
    source = (ROOT / "scripts" / "db" / "seed.py").read_text(encoding="utf-8")
    assert "environ" not in source and "PGDATABASE" not in source


def test_dev_write_requires_written_confirmation_before_any_write():
    # Onay yoksa veya yanlışsa, veri üretilmeden ve veritabanına yazılmadan reddedilir.
    for args in (("--onay", "yanlis"), ()):
        proc = run_cli("apply", "--database", "medakis_dev", *args, stdin="\n")
        assert proc.returncode == 1 and "SEED_CONFIRMATION_REQUIRED" in proc.stdout


def test_seed_engine_used_only_by_seed_service():
    users = [
        p.relative_to(ROOT).as_posix()
        for p in [*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]
        if "seed_engine(" in p.read_text(encoding="utf-8")
    ]
    assert sorted(users) == ["src/medakis/repositories/database.py", "src/medakis/services/seed_service.py"]


def test_seed_repo_never_truncates_or_cascades():
    import ast

    tree = ast.parse((ROOT / "src" / "medakis" / "repositories" / "seed_repo.py").read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef)) and node.body and isinstance(node.body[0], ast.Expr)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            assert "TRUNCATE" not in node.value.upper() and "CASCADE" not in node.value.upper()
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
            assert name.lower() != "truncate"


def test_dev_idle_privileges_are_read_only():
    from medakis.repositories.schema import SEED_IDLE_PRIVILEGES, SEED_PERMANENT_WRITE_DATABASES

    assert set(SEED_IDLE_PRIVILEGES) == set(PRIVILEGES["medakis_seed"])
    assert all(p == frozenset({"SELECT"}) for p in SEED_IDLE_PRIVILEGES.values())
    assert SEED_PERMANENT_WRITE_DATABASES == frozenset({"medakis_test"})


def test_seed_grant_engine_used_only_by_seed_service():
    users = sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "src").rglob("*.py")
        if "seed_grant_engine(" in p.read_text(encoding="utf-8")
    )
    assert users == ["src/medakis/repositories/database.py", "src/medakis/services/seed_service.py"]


def test_sequence_sync_never_lowers_a_sequence():
    # setval işlemle geri alınmaz; değer en az mevcut sayaç değeri olmalı (F1).
    source = (ROOT / "src" / "medakis" / "repositories" / "seed_repo.py").read_text(encoding="utf-8")
    body = source[source.index("def sync_sequences"):]
    assert "GREATEST(" in body and "pg_sequence_last_value" in body


# ---- T1/T2/R1: yapısal kurallar ---------------------------------------------------------------


def test_no_committed_seed_grant_window_exists():
    # Yetki yalnız seed işleminin kendi transaction'ında verilir; ayrı commit eden bir pencere yoktur.
    access = (ROOT / "src" / "medakis" / "repositories" / "seed_access.py").read_text(encoding="utf-8")
    assert ".begin(" not in access and ".commit(" not in access and 'text("COMMIT' not in access
    assert "MAINTAIN ON" not in access  # dev yolunda kilitleri owner alır; seed'e MAINTAIN verilmez
    cli = (ROOT / "scripts" / "db" / "seed.py").read_text(encoding="utf-8")
    assert "yetki-kapat" not in cli and "close_write" not in cli


def test_scoped_write_order_is_identity_membership_lock_grant_role_load_revoke():
    source = (ROOT / "src" / "medakis" / "services" / "seed_service.py").read_text(encoding="utf-8")
    body = source[source.index("def _write_with_scoped_grant"):source.index("def _load")]
    steps = ["engine.begin()", "self._guard(", "require_membership(", "seed_repo.lock(", "grant_scoped(",
             "become_seed(", "self._load(", "leave_seed(", "revoke_scoped(", "require_idle("]  # fmt: skip
    positions = [body.index(s) for s in steps]
    assert positions == sorted(positions), dict(zip(steps, positions))
    assert body.count("engine.begin()") == 1  # tek bağlantı, tek işlem


def test_membership_is_separate_admin_step_with_set_only():
    provision = (ROOT / "docker" / "postgres" / "provision.sql").read_text(encoding="utf-8")
    membership = (ROOT / "docker" / "postgres" / "seed_membership.sql").read_text(encoding="utf-8")
    assert "GRANT medakis_seed TO" not in provision  # provision testleri gerçek sunucuda üyelik kurmasın
    assert "'GRANT medakis_seed TO medakis_owner WITH INHERIT FALSE, SET TRUE, ADMIN FALSE'" in membership
    code = chr(10).join(line for line in membership.splitlines() if not line.lstrip().startswith("--"))
    statements = code.upper()
    for forbidden in ("SUPERUSER", "CREATE ROLE", "ALTER ROLE", "GRANT MEDAKIS_OWNER", " ON TABLE", " ON SCHEMA", " ON DATABASE"):
        assert forbidden not in statements, forbidden
    assert statements.count("GRANT ") == 1
    # B5: tek ifade (DO bloğu); doğrulama aynı ifadede, beklenmeyen üyelik GRANT'tan önce reddedilir.
    assert code.strip().startswith("DO $$") and code.strip().endswith("$$;") and code.count("$$;") == 1
    assert code.index("unexpected medakis role membership") < code.index("EXECUTE 'GRANT") < code.index("membership verification failed")


def test_membership_is_not_run_by_general_setup_and_needs_explicit_confirmation():
    setup = (ROOT / "scripts" / "db" / "setup.ps1").read_text(encoding="utf-8")
    code = chr(10).join(line for line in setup.splitlines() if not line.lstrip().startswith("#"))
    assert "seed_membership" not in code  # genel kurulum üyelik değiştirmez (yalnız yorumda yönerge var)
    admin = (ROOT / "scripts" / "db" / "seed_membership.ps1").read_text(encoding="utf-8")
    assert "--single-transaction" in admin and "ON_ERROR_STOP=1" in admin
    assert admin.index("if (-not $Onayla)") < admin.index("psql -U postgres")


def test_restore_checks_dump_before_safety_backup_and_restore():
    restore = (ROOT / "scripts" / "db" / "restore.ps1").read_text(encoding="utf-8")
    order = [restore.index("check_dump.py"), restore.index("backup.ps1\" -Database"), restore.index("pg_restore -U postgres")]
    assert order == sorted(order)


def test_backup_publishes_only_verified_unique_files():
    script = (ROOT / "scripts" / "db" / "backup.ps1").read_text(encoding="utf-8")
    assert "NewGuid()" in script and "pg_restore --list" in script and ".partial" in script
    order = [script.index("pg_restore --list $remote"), script.index("docker compose cp \"postgres:"), script.index("[IO.File]::Move")]
    assert order == sorted(order)


def test_dev_and_demo_restore_refusal_is_the_first_statement():
    restore = (ROOT / "scripts" / "db" / "restore.ps1").read_text(encoding="utf-8")
    body = restore[restore.index("param("):]
    refusal = body.index("if ($Database -ne 'medakis_test')")
    for later in ("Set-Location", "Test-Path", "Get-FileHash", "check_dump.py", "backup.ps1\" -Database", "docker compose", "pg_restore -U"):
        assert body.index(later) > refusal, later
    assert "exit 3" in body[refusal:refusal + 400]
