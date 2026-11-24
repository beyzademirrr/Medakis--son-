"""RAG metadata deposu arayüzü (Aşama 7.5).

RAG paketi yalnız bu arayüzü bilir; SQL kütüphanesi import etmez. Aşama 7'deki tek uygulama dosya tabanlıdır
(`rag.metadata.file_store`). PostgreSQL uygulaması S17 onayıyla `repositories/` katmanında, sabit parametreli
sorgular ve `medakis_rag` rolüyle yazılacak ve aynı sözleşme testlerinden geçecektir (KM29).
Her çağrı kaynağı YENİDEN okur: emekliye ayırma veya içerik değişikliği bir sonraki sorguda etkilidir.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from medakis.rag.contracts.documents import DocumentState
from medakis.rag.ingestion.corpus_loader import CorpusLoad
from medakis.rag.vector_store.index_registry import IndexRegistry


@runtime_checkable
class MetadataStore(Protocol):
    def corpus(self) -> CorpusLoad: ...

    def document_states(self) -> tuple[dict[str, DocumentState], list[str]]:
        """(etkin durumlar, tutarsızlıklar). Onay izi bozuksa LedgerError (çağıran fail-closed davranır)."""
        ...

    def registry(self) -> IndexRegistry: ...

    def append_query_log(self, entry: dict[str, Any]) -> str:
        """Ekleme-yalnız sorgu kaydı; sorgu kimliğini döndürür."""
        ...
