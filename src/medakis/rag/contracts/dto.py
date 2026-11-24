"""RAG cevap DTO'ları.

RAG yalnız bu değişmez (frozen) nesneleri döndürür. Bu nesneler hiçbir atama,
onay veya veritabanı yazma komutuna dönüştürülmez.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

NO_SOURCE_MESSAGE = "Kontrollü kaynaklarda bu soruyu yanıtlayacak bilgi bulunamadı."


class RagOutcome(str, Enum):
    """rag_queries.outcome CHECK kısıtıyla birebir aynı değerler."""

    ANSWERED = "answered"
    NO_SOURCE = "no_source"
    CONFLICTING_SOURCES = "conflicting_sources"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Citation:
    source_key: str
    source_name: str
    section: str
    version: str
    valid_from: str
    valid_until: str | None
    chunk_id: int
    quote_start: int
    quote_end: int

    def __post_init__(self) -> None:
        for name in ("source_key", "source_name", "section", "version", "valid_from"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"Citation alanı boş olamaz: {name}")
        if not (0 <= self.quote_start < self.quote_end):
            raise ValueError("Alıntı aralığı geçersiz: 0 <= başlangıç < bitiş olmalıdır.")


@dataclass(frozen=True)
class Timings:
    retrieval_ms: float = 0.0
    generation_ms: float = 0.0
    total_ms: float = 0.0

    def __post_init__(self) -> None:
        for name in ("retrieval_ms", "generation_ms", "total_ms"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0):
                raise ValueError(f"Süre negatif olmayan sonlu sayı olmalıdır: {name}")


@dataclass(frozen=True)
class RagAnswer:
    outcome: RagOutcome
    answer: str | None
    citations: tuple[Citation, ...] = ()
    query_id: int | None = None
    timings: Timings = field(default_factory=Timings)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RagOutcome):
            raise TypeError("outcome RagOutcome olmalıdır.")
        if self.outcome is RagOutcome.ANSWERED:
            if not (self.answer and self.answer.strip()):
                raise ValueError("answered sonucu boş cevap içeremez.")
            if not self.citations:
                raise ValueError("Kaynak yoksa cevap üretilmez: answered en az bir citation ister.")
        elif self.citations and self.outcome is not RagOutcome.CONFLICTING_SOURCES:
            raise ValueError(f"{self.outcome.value} sonucu citation taşıyamaz.")
