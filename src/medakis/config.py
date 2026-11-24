"""Merkezi yapılandırma.

PostgreSQL bağlantı adresi, saat dilimi, seed, tahmin ufukları ve çözücü süre sınırı burada
tutulur; UI içine sabit gömülmez. Bu modül dosya veya ağ kaynağı açmaz. Parola burada
tutulmaz (bkz. medakis.repositories.credentials).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ENV_RAG_PROFILE = "MEDAKIS_RAG_PROFILE"
# Aşama 7: RAG yerel deposu (Chroma, belge onay izi, indeks kaydı). Mutlak yol olmalı; sistem sürücüsü yasaktır
# (denetim medakis.rag.vector_store.location içinde; başka sürücüye sessiz geçiş yoktur).
ENV_RAG_STORE = "MEDAKIS_RAG_STORE"
# libpq'nun standart değişkenleri. Kullanıcı adı ve parola bilerek okunmaz: rolü kod seçer.
ENV_PG_HOST = "PGHOST"
ENV_PG_PORT = "PGPORT"
ENV_PG_DATABASE = "PGDATABASE"

ALLOWED_DATABASES = frozenset({"medakis_dev", "medakis_test", "medakis_demo"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

ALLOWED_HORIZONS = frozenset({30, 60})


class ConfigError(ValueError):
    """Geçersiz yapılandırma değeri."""


@dataclass(frozen=True)
class Settings:
    project_root: Path
    artifacts_dir: Path
    rag_profiles_path: Path
    corpus_dir: Path
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_database: str = "medakis_dev"
    timezone: str = "Europe/Istanbul"
    seed: int = 42
    observation_window_minutes: int = 15
    forecast_horizons: tuple[int, ...] = (30, 60)
    solver_time_limit_seconds: float = 10.0
    rag_profile: str = "primary"
    rag_store_dir: Path | None = None  # load_settings: <proje kökü>/data/rag (mutlak) ya da MEDAKIS_RAG_STORE
    rag_min_free_gb: float = 30.0  # RAG deposunun bulunduğu sürücüde korunacak en az boş alan

    def __post_init__(self) -> None:
        if self.pg_host not in LOOPBACK_HOSTS:
            raise ConfigError(f"PostgreSQL yalnız bu bilgisayarda çalışır; geçersiz adres: {self.pg_host!r}")
        if not 1 <= self.pg_port <= 65535:
            raise ConfigError(f"Geçersiz PostgreSQL portu: {self.pg_port!r}")
        if self.pg_database not in ALLOWED_DATABASES:
            raise ConfigError(f"Geçersiz veritabanı: {self.pg_database!r}. Geçerli: {sorted(ALLOWED_DATABASES)}")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"Geçersiz saat dilimi: {self.timezone!r}") from exc
        if not self.forecast_horizons or not set(self.forecast_horizons) <= ALLOWED_HORIZONS:
            raise ConfigError(f"Tahmin ufukları yalnız {sorted(ALLOWED_HORIZONS)} olabilir.")
        if self.observation_window_minutes <= 0:
            raise ConfigError("Gözlem penceresi pozitif olmalıdır.")
        limit = self.solver_time_limit_seconds
        if not (isinstance(limit, (int, float)) and math.isfinite(limit) and limit > 0):
            raise ConfigError("Çözücü süre sınırı pozitif ve sonlu olmalıdır.")

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load_settings(root: Path | None = None, env: dict[str, str] | None = None) -> Settings:
    """Varsayılan ayarları üretir; PGHOST, PGPORT, PGDATABASE ve MEDAKIS_RAG_PROFILE ile geçersiz kılınabilir."""
    root = (root or PROJECT_ROOT).resolve()
    env = os.environ if env is None else env

    raw_port = env.get(ENV_PG_PORT, "").strip() or "5432"
    try:
        pg_port = int(raw_port)
    except ValueError:
        raise ConfigError(f"Geçersiz PostgreSQL portu: {raw_port!r}") from None

    return Settings(
        project_root=root,
        artifacts_dir=root / "artifacts",
        rag_profiles_path=root / "config" / "rag_profiles.json",
        corpus_dir=root / "corpus",
        pg_host=env.get(ENV_PG_HOST, "").strip() or "127.0.0.1",
        pg_port=pg_port,
        pg_database=env.get(ENV_PG_DATABASE, "").strip() or "medakis_dev",
        rag_profile=env.get(ENV_RAG_PROFILE, "").strip() or "primary",
        rag_store_dir=Path(env.get(ENV_RAG_STORE, "").strip()) if env.get(ENV_RAG_STORE, "").strip() else root / "data" / "rag",
    )


def load_rag_profiles(settings: Settings) -> dict[str, Any]:
    """config/rag_profiles.json dosyasını okur ve seçili profilin varlığını doğrular.

    Model indirmez, Ollama'ya bağlanmaz.
    """
    path = settings.rag_profiles_path
    if not path.is_file():
        raise ConfigError(f"RAG profil dosyası bulunamadı: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    if settings.rag_profile not in profiles:
        raise ConfigError(
            f"RAG profili {settings.rag_profile!r} yok. Geçerli profiller: {sorted(profiles)}"
        )
    return data
