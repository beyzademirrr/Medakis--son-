"""Parola erişim kuralları (sahte depo ile, ağsız)."""

import logging
import subprocess
import sys

import pytest
from sqlalchemy import make_url

from medakis.config import ConfigError, load_settings
from medakis.repositories import credentials
from medakis.repositories.credentials import APP_ROLE, OWNER_ROLE, RAG_ROLE, CredentialError, get_password
from medakis.repositories.database import migration_engine, runtime_engine, seed_engine

SECRET = "gizli-deneme-degeri-123"


class FakeStdin:
    def __init__(self, tty):
        self.tty = tty

    def isatty(self):
        return self.tty


def use_store(monkeypatch, value=None, error=None):
    def fake_get(service, role):
        assert service == credentials.SERVICE
        if error:
            raise error
        return value

    monkeypatch.setattr(credentials.keyring, "get_password", fake_get)


def forbid_prompt(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("Gizli giriş istenmemeliydi")

    monkeypatch.setattr(credentials.getpass, "getpass", fail)


def test_reads_from_store_without_prompt(monkeypatch):
    use_store(monkeypatch, SECRET)
    forbid_prompt(monkeypatch)
    assert get_password(APP_ROLE, allow_prompt=True) == SECRET


def test_unknown_role_rejected(monkeypatch):
    use_store(monkeypatch, SECRET)
    for role in ("postgres", "root", ""):
        with pytest.raises(CredentialError):
            get_password(role)


def test_missing_entry_without_permission_is_clear_error(monkeypatch):
    use_store(monkeypatch, None)
    forbid_prompt(monkeypatch)
    with pytest.raises(CredentialError, match="store_credentials"):
        get_password(APP_ROLE)


def test_store_failure_falls_back_to_hidden_prompt(monkeypatch):
    use_store(monkeypatch, error=RuntimeError("depo kapalı"))
    monkeypatch.setattr(sys, "stdin", FakeStdin(True))
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: SECRET)
    assert get_password(APP_ROLE, allow_prompt=True) == SECRET


def test_store_failure_without_terminal_does_not_prompt(monkeypatch):
    # Streamlit gibi terminali olmayan süreç: takılmak yerine açık hata.
    use_store(monkeypatch, error=RuntimeError("depo kapalı"))
    monkeypatch.setattr(sys, "stdin", FakeStdin(False))
    forbid_prompt(monkeypatch)
    with pytest.raises(CredentialError, match="okunamadı"):
        get_password(APP_ROLE, allow_prompt=True)


def test_empty_prompt_is_error(monkeypatch):
    use_store(monkeypatch, None)
    monkeypatch.setattr(sys, "stdin", FakeStdin(True))
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: "")
    with pytest.raises(CredentialError, match="girilmedi"):
        get_password(APP_ROLE, allow_prompt=True)


def test_store_error_text_not_leaked_and_chain_cut(monkeypatch):
    use_store(monkeypatch, error=RuntimeError(f"ic hata {SECRET}"))
    with pytest.raises(CredentialError) as info:
        get_password(APP_ROLE)
    assert SECRET not in str(info.value)
    assert info.value.__cause__ is None


def test_environment_password_is_not_used(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", SECRET)
    use_store(monkeypatch, None)
    with pytest.raises(CredentialError):
        get_password(APP_ROLE)


def test_runtime_engine_refuses_owner_and_superuser():
    for role in (OWNER_ROLE, "postgres", "medakis_seed"):
        with pytest.raises(ConfigError):
            runtime_engine(role)


def test_engines_use_expected_roles_and_no_password_in_url(monkeypatch):
    settings = load_settings(env={})
    for engine, role in (
        (runtime_engine(settings=settings), APP_ROLE),
        (runtime_engine(RAG_ROLE, settings=settings), RAG_ROLE),
        (migration_engine("medakis_test", settings=settings), OWNER_ROLE),
        (seed_engine("medakis_test", settings=settings), "medakis_seed"),
    ):
        url = engine.url
        assert url.username == role and url.password is None
        assert url.host == "127.0.0.1"
        assert make_url(url.render_as_string(hide_password=False)).password is None
        assert engine.hide_parameters is True
        engine.dispose()


def test_engine_rejects_unknown_database():
    with pytest.raises(ConfigError):
        migration_engine("postgres", settings=load_settings(env={}))


def test_no_secret_in_logs_on_connection_failure(monkeypatch, caplog):
    # Kapalı porta bağlanma denemesi: parola hata mesajına veya günlüklere girmemeli.
    use_store(monkeypatch, SECRET)
    settings = load_settings(env={"PGPORT": "1"})
    engine = runtime_engine(settings=settings)
    with caplog.at_level(logging.DEBUG), pytest.raises(Exception) as info:
        engine.connect()
    engine.dispose()
    assert SECRET not in str(info.value)
    assert SECRET not in caplog.text


def test_alembic_requires_explicit_database(request):
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=request.config.rootpath,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode != 0
    assert "-x db=" in proc.stdout + proc.stderr
