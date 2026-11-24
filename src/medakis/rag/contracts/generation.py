"""Aşama 8 üretim ve kanıt sözleşmeleri.

- Modelin önerdiği alıntı (`QuoteClaim`) güvenilmeyen veridir; ancak deterministik doğrulamadan geçerse
  `VerifiedQuote` olur. Doğrulanmış alıntı, belgedeki karakter aralığını taşır (şartname: alıntı aralığı).
- Bu nesneler hiçbir atama, onay ya da yazma komutuna dönüştürülmez.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ollama `format` alanına verilen JSON şeması: kanıt denetimi kararı.
EVIDENCE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "destekleniyor": {"type": "boolean"},
        "alintilar": {"type": "array", "items": {"type": "object", "properties": {"parca": {"type": "string"}, "metin": {"type": "string"}},
                                                  "required": ["parca", "metin"]}},
        "eksik": {"type": "string"},
    },
    "required": ["destekleniyor", "alintilar"],
}  # fmt: skip


@dataclass(frozen=True)
class QuoteClaim:
    chunk_id: str
    text: str


@dataclass(frozen=True)
class VerifiedQuote:
    chunk_id: str
    source_key: str
    version: str
    section: str
    doc_start: int  # belge metnindeki aralık [başlangıç, bitiş)
    doc_end: int
    text: str  # belgedeki birebir metin (modelin yazdığı değil)


@dataclass(frozen=True)
class EvidenceVerdict:
    supported: bool
    quotes: tuple[VerifiedQuote, ...]
    problems: tuple[str, ...] = ()
    missing: str = ""

    def __post_init__(self) -> None:
        if self.supported and (not self.quotes or self.problems):
            raise ValueError("Desteklenen karar en az bir doğrulanmış alıntı ister ve sorun taşıyamaz.")


@dataclass(frozen=True)
class ModelDecision:
    """D0: modelin HAM kanıt kararı (doğrulamadan önce). Yalnız değerlendirme kaydı içindir; cevap değildir.

    `claims` kısa etiketlerden parça kimliğine çevrilmiş model önerileridir (güvenilmeyen veri); `problems` doğrulama
    sonucudur (boşsa bütün alıntılar birebir doğrulandı)."""

    supported: bool
    claims: tuple[QuoteClaim, ...]
    missing: str = ""
    problems: tuple[str, ...] = ()
    selected: tuple[str, ...] = ()  # D3 M3: modelin seçtiği cümle kimlikleri (doğrulanmadan önce)
    calls: int = 1  # bu karar için yapılan model çağrısı (D3 M2'de 2 olabilir)

    def as_dict(self) -> dict:
        return {"destekleniyor": self.supported, "alinti_sayisi": len(self.claims) + len(self.selected), "eksik": self.missing,
                "alintilar": [{"parca": c.chunk_id, "metin": c.text} for c in self.claims], "cumleler": list(self.selected),
                "sorunlar": list(self.problems), "cagri": self.calls}  # fmt: skip


@dataclass(frozen=True)
class GroundedResult:
    """Aşama 8.5 hat sonucu. `answered` yalnız birebir doğrulanmış alıntılarla mümkündür (ayıklayıcı cevap)."""

    outcome: "RagOutcome"
    message: str
    answer: str | None = None
    quotes: tuple[VerifiedQuote, ...] = ()
    retrieval_outcome: str = ""
    model_calls: int = 0
    candidate_policy: str = ""
    candidates: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    query_id: str | None = None
    # Aşama 8.6: üretici kullanılamadığında (bellek, zaman aşımı, model erişimi, retrieval_only profili) gösterilen
    # kaynak parçaları. CEVAP DEĞİLDİR; yalnız `unavailable` sonucunda ve açık etiketle taşınır.
    fallback: tuple = ()
    fallback_label: str = ""
    # D0: model çağrıldıysa ham kararı (doğrulama sorunlarıyla). Kullanıcıya gösterilmez; değerlendirme kaydı içindir.
    decision: ModelDecision | None = None

    def __post_init__(self) -> None:
        from medakis.rag.contracts.dto import RagOutcome

        if self.outcome is RagOutcome.ANSWERED and (not self.quotes or not (self.answer or "").strip()):
            raise ValueError("answered sonucu doğrulanmış alıntı ve cevap metni ister.")
        if self.outcome is not RagOutcome.ANSWERED and (self.quotes or self.answer):
            raise ValueError(f"{self.outcome.value} sonucu cevap ya da alıntı taşıyamaz.")
        if self.fallback and (self.outcome is not RagOutcome.UNAVAILABLE or not self.fallback_label):
            raise ValueError("retrieval-only kaynak listesi yalnız etiketli 'unavailable' sonucunda taşınır.")
