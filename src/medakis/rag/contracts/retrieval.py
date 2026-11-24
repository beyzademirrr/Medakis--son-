"""Yalnız getirme (retrieval-only) sonuç sözleşmesi (Aşama 7.4).

Aşama 7'de cevap ÜRETİLMEZ: sonuç, doğrulanmış kaynak parçaları ve alıntı aralıklarıdır. `RagAnswer` (üretilmiş
cevap) Aşama 8'in sözleşmesidir. Bu nesneler hiçbir atama, onay veya yazma komutuna dönüştürülmez.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from medakis.rag.contracts.dto import NO_SOURCE_MESSAGE, Timings

RETRIEVAL_ONLY_LABEL = "Yalnız getirme (retrieval-only): cevap üretilmedi; aşağıdakiler kontrollü kaynak parçalarıdır."
CONFLICT_MESSAGE = "Kaynaklar arasında ya da kaynak ile yapılandırılmış kural arasında çatışma var; manuel inceleme gerekir."
UNAVAILABLE_MESSAGE = "Kaynak arama şu anda kullanılamıyor; tahmin, optimizasyon ve onay ekranları etkilenmez."


class RetrievalOutcome(str, Enum):
    FOUND = "found"
    NO_SOURCE = "no_source"
    CONFLICTING_SOURCES = "conflicting_sources"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RetrievedChunk:
    rank: int
    chunk_id: str
    distance: float
    source_key: str
    source_name: str
    doc_type: str
    version: str
    section: str
    valid_from: str
    valid_until: str | None
    quote_start: int  # belge metnindeki alıntı aralığı [başlangıç, bitiş)
    quote_end: int
    text: str


@dataclass(frozen=True)
class RetrievalResult:
    outcome: RetrievalOutcome
    message: str
    chunks: tuple[RetrievedChunk, ...] = ()
    as_of: datetime | None = None
    index_key: str | None = None
    query_id: str | None = None
    timings: Timings = Timings()
    warnings: tuple[str, ...] = ()
    evaluation: bool = False

    def __post_init__(self) -> None:
        if self.outcome is RetrievalOutcome.FOUND and not self.chunks:
            raise ValueError("found sonucu en az bir doğrulanmış parça ister.")
        if self.outcome in (RetrievalOutcome.NO_SOURCE, RetrievalOutcome.UNAVAILABLE, RetrievalOutcome.REJECTED) and self.chunks:
            raise ValueError(f"{self.outcome.value} sonucu parça taşıyamaz.")


__all__ = ["CONFLICT_MESSAGE", "NO_SOURCE_MESSAGE", "RETRIEVAL_ONLY_LABEL", "UNAVAILABLE_MESSAGE", "RetrievalOutcome",
           "RetrievalResult", "RetrievedChunk"]  # fmt: skip
