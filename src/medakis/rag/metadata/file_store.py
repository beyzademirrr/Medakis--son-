"""Dosya tabanlı RAG metadata deposu (Aşama 7; PostgreSQL kalıcılığı S17 onayına bağlı).

Kaynaklar (her çağrıda yeniden okunur, önbellek yok):
- belge tanımları: kontrollü corpus manifesti ve dosyaları;
- belge durumu: `belge_onaylari.jsonl` (proje sahibinin yerel onay izi);
- indeksler: `indeksler/` + `indeks_olaylari.jsonl`;
- sorgu kaydı: `sorgu_kaydi.jsonl` (ekleme-yalnız; soru, as_of, sonuç, isabetler, süreler).
Depo klasörü `location.check_store_dir` ile denetlenmiş mutlak yoldur.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from medakis.rag.contracts.documents import DocumentState
from medakis.rag.ingestion.approval_ledger import ApprovalLedger, effective_states
from medakis.rag.ingestion.corpus_loader import CorpusLoad, load_corpus
from medakis.rag.vector_store.index_registry import IndexRegistry

LEDGER = "belge_onaylari.jsonl"
QUERY_LOG = "sorgu_kaydi.jsonl"


class FileMetadataStore:
    def __init__(self, corpus_dir: Path, store_dir: Path, ledger_name: str = LEDGER) -> None:
        self.corpus_dir = Path(corpus_dir)
        self.store_dir = Path(store_dir)
        self.ledger = ApprovalLedger(self.store_dir / ledger_name)
        self._registry = IndexRegistry(self.store_dir)

    def corpus(self) -> CorpusLoad:
        return load_corpus(self.corpus_dir)

    def document_states(self) -> tuple[dict[str, DocumentState], list[str]]:
        corpus = self.corpus()
        return effective_states([d.record for d in corpus.documents], self.ledger.events())

    def registry(self) -> IndexRegistry:
        return self._registry

    def append_query_log(self, entry: dict[str, Any]) -> str:
        path = self.store_dir / QUERY_LOG
        n = len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0
        qid = uuid.uuid4().hex
        line = json.dumps({"sira": n, "sorgu_kimligi": qid, **entry}, ensure_ascii=False, sort_keys=True, default=str)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
        return qid

    def query_log(self) -> list[dict[str, Any]]:
        path = self.store_dir / QUERY_LOG
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
