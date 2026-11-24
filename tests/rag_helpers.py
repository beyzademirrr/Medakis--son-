"""Aşama 7 RAG testleri için ortak yardımcılar (ağ yok, veritabanı yok)."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus" / "kontrollu"
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def d_tmp() -> Path:
    """Proje veri klasöründe (sistem sürücüsü DIŞINDA) geçici klasör; RAG deposu denetimi gerçek kurallarla çalışır."""
    path = ROOT / "data" / f"test_rag_{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True)
    return path


def remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def entry(key: str, version: str = "1.0", text: str | None = None, **over) -> tuple[dict, str]:
    body = text or f"# Sentetik deneme belgesi {key}\n\nBu belge sentetiktir.\n\n## Bölüm\n\nDeneme metni {key} {version}.\n"
    e = {"source_key": key, "source_name": f"Deneme {key}", "doc_type": "procedure", "version": version,
         "valid_from": "2026-11-01T00:00:00Z", "valid_until": None, "status": "draft", "approved_by": None,
         "approved_at": None, "is_synthetic": True, "path": f"{key}-{version}.md",
         "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "purpose": "kaynak"}  # fmt: skip
    e.update(over)
    return e, body


def write_corpus(folder: Path, items: list[tuple[dict, str]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for e, body in items:
        target = folder / e["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body.encode("utf-8"))
    (folder / "manifest.json").write_text(json.dumps([e for e, _ in items], ensure_ascii=False), encoding="utf-8")
    return folder
