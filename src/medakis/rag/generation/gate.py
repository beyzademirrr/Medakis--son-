"""D5 mimari deneyi: reranker kabul/ret kapısı ve Gemma'sız doğrulanmış kaynak sunumu (YALNIZ geliştirme/test kipi).

Bu modül hattın varsayılanına BAĞLANMAZ; deney betikleri açıkça çağırır. Hiçbir model yüklemez (skorlar dışarıdan gelir).
- Kapı yalnız retrieval `found` döndürdüyse çalışır. `unavailable`, `conflicting_sources`, `rejected` ve retrieval
  `no_source` olduğu gibi geçer; kapı ve sonraki katmanlar bunları AŞAMAZ.
- Kapı yalnız retrieval'ın doğruladığı adayları yeniden SIRALAR; aday ekleyemez. Skoru eksik ya da sonlu olmayan aday
  varsa sonuç `unavailable` (teknik hata), kaçınma sayılmaz.
- Olumlu kapı kararı yalnız bir izin sinyalidir: Gemma kullanılırsa alıntılar yine birebir doğrulanır (gevşetme yok).
- Kaynak sunumu (Gemma'sız): yalnız doğrulanmış parçanın birebir metni, kaynak kimliği, sürümü, bölümü ve belge
  aralığıyla döner; serbest metin ya da çıkarım ÜRETİLMEZ. Bu bir CEVAP DEĞİLDİR; doğruluğu insan incelemesi bekler.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass

from medakis.rag.contracts.dto import NO_SOURCE_MESSAGE
from medakis.rag.contracts.retrieval import UNAVAILABLE_MESSAGE, RetrievalOutcome, RetrievalResult, RetrievedChunk

GATE_REJECTED = "kapi_reddi"
GATE_TECHNICAL = "kapi_teknik_hata"
PRESENTATION_LABEL = ("Doğrulanmış kaynak parçası (geliştirme/test kipi). Bu bir cevap DEĞİLDİR: parçanın soruyu "
                      "cevapladığı doğrulanmadı; kaynak metni olduğu gibi gösterilir.")  # fmt: skip


@dataclass(frozen=True)
class GateDecision:
    retrieval: RetrievalResult  # kapıdan sonra (yeniden sıralanmış ya da reddedilmiş) retrieval sonucu
    accepted: bool
    top_score: float | None


def apply_gate(retrieval: RetrievalResult, scores: Mapping[str, float], threshold_score: float | None, k: int = 5) -> GateDecision:
    """`threshold_score` skor uzayındadır (yüksek = iyi): kabul ⇔ en yüksek skor ≥ eşik. Eşik yoksa ret."""
    if retrieval.outcome is not RetrievalOutcome.FOUND:
        return GateDecision(retrieval, False, None)
    chunks = sorted(retrieval.chunks, key=lambda c: c.rank)
    vals = [scores.get(c.chunk_id) for c in chunks]
    if any(v is None or not math.isfinite(v) for v in vals):
        bad = RetrievalResult(RetrievalOutcome.UNAVAILABLE, UNAVAILABLE_MESSAGE, (), retrieval.as_of, retrieval.index_key, retrieval.query_id,
                              retrieval.timings, tuple(retrieval.warnings) + (GATE_TECHNICAL,), retrieval.evaluation)  # fmt: skip
        return GateDecision(bad, False, None)
    order = sorted(zip(chunks, vals), key=lambda cv: (-cv[1], cv[0].rank))
    top = order[0][1]
    if threshold_score is None or top < threshold_score:
        rej = RetrievalResult(RetrievalOutcome.NO_SOURCE, NO_SOURCE_MESSAGE, (), retrieval.as_of, retrieval.index_key, retrieval.query_id,
                              retrieval.timings, tuple(retrieval.warnings) + (GATE_REJECTED,), retrieval.evaluation)  # fmt: skip
        return GateDecision(rej, False, top)
    kept = tuple(dataclasses.replace(c, rank=i) for i, (c, _) in enumerate(order[:k], 1))
    ok = RetrievalResult(RetrievalOutcome.FOUND, retrieval.message, kept, retrieval.as_of, retrieval.index_key, retrieval.query_id,
                         retrieval.timings, tuple(retrieval.warnings), retrieval.evaluation)  # fmt: skip
    return GateDecision(ok, True, top)


@dataclass(frozen=True)
class SourcePresentation:
    outcome: str  # "kaynak_sunuldu" | retrieval/kapı sonucu (no_source, unavailable, conflicting_sources, rejected)
    label: str
    chunks: tuple[RetrievedChunk, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome == "kaynak_sunuldu" and (not self.chunks or self.label != PRESENTATION_LABEL):
            raise ValueError("kaynak sunumu en az bir doğrulanmış parça ve 'cevap değildir' etiketi ister")
        if self.outcome != "kaynak_sunuldu" and self.chunks:
            raise ValueError(f"{self.outcome} sonucu kaynak parçası taşıyamaz")


def present_sources(gated: GateDecision, n: int = 1) -> SourcePresentation:
    """Gemma'sız mimari: kapı kabul ettiyse en üst n doğrulanmış parçayı BİREBİR sunar; aksi halde sonucu aynen geçirir."""
    r = gated.retrieval
    if not gated.accepted or r.outcome is not RetrievalOutcome.FOUND:
        return SourcePresentation(r.outcome.value, r.message, (), tuple(r.warnings))
    return SourcePresentation("kaynak_sunuldu", PRESENTATION_LABEL, tuple(sorted(r.chunks, key=lambda c: c.rank)[:n]), tuple(r.warnings))
