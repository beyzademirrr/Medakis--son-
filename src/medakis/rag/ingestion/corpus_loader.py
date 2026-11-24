"""İzinli yerel corpus klasöründen sentetik belge yükleme (Aşama 7.1).

Kurallar:
- Yalnız verilen corpus klasörünün DOĞRUDAN içindeki `.md` dosyaları; alt klasör, `..`, mutlak yol ve sembolik
  bağlantı reddedilir. PDF, OCR ve ağdan alma yoktur.
- UTF-8 (katı), boyut üst sınırı, denetim karakteri yok, ilk başlıkta "Sentetik" işareti.
- Dosya baytlarının SHA-256'sı manifestteki değerle aynı olmalı; değilse belge yüklenmez.
- Sorunlu belge dışarıda kalır ve nedeni raporlanır; manifest yapısı bozuksa hiçbir belge yüklenmez.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from medakis.rag.contracts.documents import DocumentRecord
from medakis.rag.errors import CorpusError
from medakis.rag.ingestion.manifest import parse_entry

MANIFEST = "manifest.json"
MAX_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class LoadedDocument:
    record: DocumentRecord
    text: str


@dataclass(frozen=True)
class CorpusLoad:
    documents: tuple[LoadedDocument, ...]
    problems: tuple[str, ...]  # "<uid ya da sıra>:<kod>"
    manifest_sha256: str

    def by_uid(self) -> dict[str, LoadedDocument]:
        return {d.record.uid: d for d in self.documents}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_document(root: Path, record: DocumentRecord) -> tuple[str | None, str | None]:
    candidate = root / record.path
    if candidate.is_symlink():
        return None, "sembolik_baglanti"
    if not candidate.is_file():
        return None, "dosya_yok"
    if candidate.resolve().parent != root:
        return None, "klasor_disi"
    if candidate.stat().st_size > MAX_BYTES:
        return None, "boyut_siniri"
    data = candidate.read_bytes()
    if sha256_bytes(data) != record.content_sha256:
        return None, "icerik_ozeti_uyusmuyor"
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, "utf8_degil"
    if text.startswith("﻿"):
        return None, "bom_var"
    if _CONTROL.search(text):
        return None, "denetim_karakteri"
    first = next((line for line in text.splitlines() if line.strip()), "")
    if not first.startswith("# ") or "Sentetik" not in first:
        return None, "sentetik_baslik_yok"
    return text, None


def load_corpus(corpus_dir: Path) -> CorpusLoad:
    """Corpus klasörünü manifestiyle yükler. Klasör yolu mutlak olmalıdır."""
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_absolute():
        raise CorpusError("corpus_yolu_mutlak_degil")
    if corpus_dir.is_symlink() or not corpus_dir.is_dir():
        raise CorpusError("corpus_klasoru_yok")
    root = corpus_dir.resolve()
    manifest_path = root / MANIFEST
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise CorpusError("manifest_yok")
    raw = manifest_path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise CorpusError("manifest_boyut_siniri")
    try:
        entries = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CorpusError("manifest_okunamadi") from None
    if not isinstance(entries, list):
        raise CorpusError("manifest_liste_degil")
    documents: list[LoadedDocument] = []
    problems: list[str] = []
    seen: set[str] = set()
    paths: set[str] = set()
    for i, entry in enumerate(entries):
        record, issues = parse_entry(entry)
        if record is None:
            problems += [f"#{i}:{code}" for code in issues]
            continue
        if record.uid in seen:
            problems.append(f"{record.uid}:yinelenen_belge")
            continue
        if record.path in paths:
            problems.append(f"{record.uid}:yinelenen_dosya")
            continue
        text, issue = _read_document(root, record)
        if issue:
            problems.append(f"{record.uid}:{issue}")
            continue
        seen.add(record.uid)
        paths.add(record.path)
        documents.append(LoadedDocument(record, text))  # type: ignore[arg-type]
    return CorpusLoad(tuple(documents), tuple(problems), sha256_bytes(raw))
