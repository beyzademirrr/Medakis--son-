"""Sistem/iskelet durum bilgisi.

UI'nin gösterdiği aşama planı, modül listesi ve yapılandırma özeti burada üretilir.
Ağ bağlantısı, model yükleme veya veritabanı oluşturma yapmaz.
"""

from __future__ import annotations

import importlib
import platform
import sys
from dataclasses import dataclass

from medakis import __version__
from medakis.config import Settings, load_rag_profiles

LAST_COMPLETED_STAGE = 6  # Aşama 4–6 geliştirme dallarında tamamlandı ve kabul edildi; main'e birleştirme ayrı karar


@dataclass(frozen=True)
class Stage:
    number: int
    title: str


STAGES: tuple[Stage, ...] = (
    Stage(1, "Ortam, modüler iskelet, temel Türkçe Streamlit"),
    Stage(2, "PostgreSQL veri katmanı, Alembic migration, deterministik sentetik veri"),
    Stage(3, "İş yükü ve kapasite ekranları"),
    Stage(4, "30/60 dakika baseline ve Random Forest, zamansal test"),
    Stage(5, "CP-SAT, açıklar, bağımsız sonuç doğrulayıcı"),
    Stage(6, "Yönetici onayı, plan hash'i, denetim ve iptal akışı"),
    Stage(7, "Kontrollü corpus, Chroma indeksleme, retrieval ve metrik aracı"),
    Stage(8, "Kaynaklı Ollama cevabı, CPU/hafif kip, saldırı/arıza testleri"),
    Stage(9, "Kör değerlendirme, Qwen/BGE karşılaştırması, donanım ölçümü, teslim"),
)

MODULES: tuple[str, ...] = (
    "medakis.ui",
    "medakis.services",
    "medakis.domain",
    "medakis.repositories",
    "medakis.synthetic",
    "medakis.forecasting",
    "medakis.optimization",
    "medakis.approvals",
    "medakis.rag",
    "medakis.rag.ingestion",
    "medakis.rag.chunking",
    "medakis.rag.embeddings",
    "medakis.rag.vector_store",
    "medakis.rag.retrieval",
    "medakis.rag.generation",
    "medakis.rag.citations",
    "medakis.rag.evaluation",
    "medakis.rag.contracts",
)


def stage_rows() -> list[dict[str, str]]:
    def status(n: int) -> str:
        return "Tamamlandı (prototip)" if n <= LAST_COMPLETED_STAGE else "Planlandı"

    return [{"Aşama": str(s.number), "Kapsam": s.title, "Durum": status(s.number)} for s in STAGES]


def module_rows() -> list[dict[str, str]]:
    rows = []
    for name in MODULES:
        module = importlib.import_module(name)
        doc = (module.__doc__ or "").strip().splitlines()
        rows.append({"Modül": name, "Sorumluluk": doc[0] if doc else ""})
    return rows


def settings_rows(settings: Settings) -> list[dict[str, str]]:
    return [
        {"Ayar": "Uygulama sürümü", "Değer": __version__},
        {"Ayar": "Python", "Değer": f"{platform.python_version()} ({sys.platform})"},
        {"Ayar": "Veritabanı", "Değer": f"PostgreSQL 18, {settings.pg_host}:{settings.pg_port}/{settings.pg_database}"},
        {"Ayar": "Uygulama bağlantısı", "Değer": "henüz kullanılmıyor (arayüz Aşama 3'te bağlanacak)"},
        {"Ayar": "Saat dilimi (gösterim)", "Değer": settings.timezone},
        {"Ayar": "Depolama saati", "Değer": "UTC (TIMESTAMPTZ)"},
        {"Ayar": "Seed", "Değer": str(settings.seed)},
        {"Ayar": "Gözlem penceresi", "Değer": f"{settings.observation_window_minutes} dakika"},
        {"Ayar": "Tahmin ufukları", "Değer": ", ".join(f"{h} dk" for h in settings.forecast_horizons)},
        {"Ayar": "Çözücü süre sınırı", "Değer": f"{settings.solver_time_limit_seconds:g} sn"},
        {"Ayar": "Seçili RAG profili", "Değer": settings.rag_profile},
    ]


def rag_profile_rows(settings: Settings) -> tuple[list[dict[str, str]], bool]:
    """Profil tablosu ve 'cihazda ölçüldü mü' bilgisi. Model çalıştırmaz."""
    data = load_rag_profiles(settings)
    rows = [
        {
            "Profil": name,
            "Embedding": p.get("embed") or "-",
            "Üretici": p.get("generator") or "yok (yalnız retrieval)",
            "Seçili": "evet" if name == settings.rag_profile else "",
        }
        for name, p in data["profiles"].items()
    ]
    return rows, bool(data.get("measured_on_user_device", False))
