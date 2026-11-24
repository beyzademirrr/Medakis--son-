import math
from dataclasses import replace

import pytest

from medakis.config import ConfigError, load_rag_profiles, load_settings


def test_defaults(project_root):
    s = load_settings(env={})
    assert s.project_root == project_root
    assert (s.pg_host, s.pg_port, s.pg_database) == ("127.0.0.1", 5432, "medakis_dev")
    assert s.timezone == "Europe/Istanbul"
    assert s.seed == 42
    assert s.observation_window_minutes == 15
    assert s.forecast_horizons == (30, 60)
    assert s.rag_profile == "primary"


def test_timezone_data_available_on_windows():
    # Windows'ta IANA verisi tzdata paketinden gelir; eksikse ZoneInfo hata verir.
    s = load_settings(env={})
    assert s.tzinfo.key == "Europe/Istanbul"


def test_postgres_env_override():
    s = load_settings(env={"PGHOST": "localhost", "PGPORT": "5433", "PGDATABASE": "medakis_test"})
    assert (s.pg_host, s.pg_port, s.pg_database) == ("localhost", 5433, "medakis_test")


@pytest.mark.parametrize(
    "env",
    [
        {"PGHOST": "db.example.com"},
        {"PGHOST": "192.168.1.10"},
        {"PGPORT": "abc"},
        {"PGPORT": "70000"},
        {"PGDATABASE": "postgres"},
    ],
)
def test_postgres_env_rejected(env):
    with pytest.raises(ConfigError):
        load_settings(env=env)


def test_settings_do_not_expose_password_or_user():
    names = set(load_settings(env={"PGPASSWORD": "x", "PGUSER": "postgres"}).__dataclass_fields__)
    assert not {n for n in names if "pass" in n or "user" in n}


@pytest.mark.parametrize(
    "changes",
    [
        {"timezone": "Mars/Olympus"},
        {"forecast_horizons": (45,)},
        {"forecast_horizons": ()},
        {"observation_window_minutes": 0},
        {"solver_time_limit_seconds": 0},
        {"solver_time_limit_seconds": -1.0},
        {"solver_time_limit_seconds": math.nan},
        {"solver_time_limit_seconds": math.inf},
    ],
)
def test_invalid_settings_rejected(changes):
    with pytest.raises(ConfigError):
        replace(load_settings(env={}), **changes)


def test_rag_profiles_load_and_validate():
    data = load_rag_profiles(load_settings(env={}))
    assert data["measured_on_user_device"] is False
    assert data["profiles"]["primary"]["embed"] == "qwen3-embedding:0.6b"
    assert data["profiles"]["retrieval_only"]["generator"] is None
    with pytest.raises(ConfigError):
        load_rag_profiles(load_settings(env={"MEDAKIS_RAG_PROFILE": "olmayan"}))
