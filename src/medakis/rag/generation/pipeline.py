"""Aşama 8.5: kanıt denetimi ve güvenli cevap hattı (YALNIZ geliştirme/test kipi).

Akış: retrieval (aday bulma) → aday politikası (nihai cevaplanabilirlik kararından AYRI) → kanıt denetimi (Gemma, şemalı
JSON) → deterministik alıntı doğrulaması → sonuç.

1. Aday bulma: retrieval DEĞERLENDİRME kipinde çağrılır; bütün metadata, onay, geçerlilik, bütünlük, sürüm çakışması ve
   kural çatışması denetimleri uygulanır, yalnız cosine eşiği uygulanmaz. `unavailable`, `conflicting_sources`,
   `rejected` ve retrieval'ın kendi `no_source`'u (izinli belge yok / sonuç yok) OLDUĞU GİBİ döner; model çağrılmaz.
   Hiçbir politika bu kararları aşamaz: politika ancak retrieval `found` döndürdükten SONRA devreye girer.
2. Aday politikası:
   - `esik`: açıkça verilen (dev'de kalibre edilmiş, kayda YAZILMAMIŞ) cosine eşiği altındaki adaylar; aday yoksa
     `no_source` ve model çağrısı yok.
   - `aday_deneyi` (deney): eşikten bağımsız ilk k doğrulanmış aday kanıt denetçisine gider. Yalnız cosine'ın verdiği
     ret aşılabilir; güvenlik/hata kararları aşılamaz (1).
3. Kanıt denetimi: istem bütçeye sığmazsa ya da model hatası, şema dışı çıktı olursa `unavailable` (fail-closed).
4. Alıntı doğrulaması: yalnız isteme giren parçalardan, birebir alıntılar kabul edilir. Model `destekleniyor=false`
   derse ya da tek bir alıntı geçersizse `no_source`. Cevap AYIKLAYICIDIR: doğrulanmış alıntıların kendisi ve kaynakları;
   serbest metin üretilmez.
Hat hiçbir iş verisine, onaya, atamaya ya da optimizasyon kaydına erişmez; yalnız retrieval'ın sorgu kaydı yazılır.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from medakis.rag.contracts.dto import NO_SOURCE_MESSAGE, RagOutcome
from medakis.rag.contracts.generation import EVIDENCE_SCHEMA, GroundedResult, ModelDecision, QuoteClaim
from medakis.rag.contracts.retrieval import UNAVAILABLE_MESSAGE, RetrievalOutcome, RetrievalResult, RetrievedChunk
from medakis.rag.errors import RagError
from medakis.rag.generation import methods as M
from medakis.rag.generation.mode import GenerationMode, require_enabled
from medakis.rag.generation.ollama_generator import parse_evidence
from medakis.rag.generation.prompt import build_evidence_prompt
from medakis.rag.generation.quotes import verify_quotes

POLICY_THRESHOLD = "esik"
POLICY_CANDIDATES = "aday_deneyi"
POLICIES = (POLICY_THRESHOLD, POLICY_CANDIDATES)
ANSWER_LABEL = "Kaynaklı cevap (doğrulanmış alıntılar; geliştirme/test kipi):"
RETRIEVAL_ONLY_FALLBACK = ("Yalnız getirme (retrieval-only): üretici kullanılamadı; aşağıdakiler CEVAP DEĞİLDİR, yalnız "
                           "denetlenmiş kaynak parçalarıdır.")  # fmt: skip

_PASS_THROUGH = {
    RetrievalOutcome.NO_SOURCE: RagOutcome.NO_SOURCE,
    RetrievalOutcome.CONFLICTING_SOURCES: RagOutcome.CONFLICTING_SOURCES,
    RetrievalOutcome.UNAVAILABLE: RagOutcome.UNAVAILABLE,
    RetrievalOutcome.REJECTED: RagOutcome.REJECTED,
}


class _BudgetSpent(Exception):
    """Toplam işlem bütçesi bir model çağrısından önce tükendi."""


class EvidenceGenerator(Protocol):
    def chat_json(self, messages: Sequence[dict], schema: dict) -> dict[str, Any]: ...


class EvidencePipeline:
    def __init__(self, retriever: Any, generator: EvidenceGenerator | None, mode: GenerationMode, candidate_k: int = 5,
                 lifecycle: Any = None, budget_s: float = 120.0, clock: Any = time.monotonic, method: str = M.METHOD_V2) -> None:  # fmt: skip
        self.mode = require_enabled(mode)  # kapalı kipte hat oluşturulamaz
        if method not in M.METHODS:
            raise ValueError(f"bilinmeyen kanıt yöntemi: {method}")
        self.method = method  # D3: varsayılan M0 (kanit_denetimi_v2); deneysel yöntemler yalnız açıkça seçilir
        self.retriever, self.generator, self.candidate_k = retriever, generator, candidate_k
        self.lifecycle = lifecycle  # Aşama 8.6: üretimden önce embedding'i boşaltır ve doğrular (sıralı yükleme)
        self.budget_s, self.clock = budget_s, clock  # KM31: retrieval + yaşam döngüsü + üretim (yeniden deneme dahil) ≤ 120 sn
        self.model_calls = 0

    @staticmethod
    def _fallback(candidates: list[RetrievedChunk], warning: str, base: dict, model_calls: int = 0) -> GroundedResult:
        """Aşama 8.6 retrieval-only geri dönüşü: üretici yoksa ya da güvenle çalışamazsa kaynak parçaları CEVAP OLMADAN,
        açık etiketle döner. Karar `unavailable`dır; A6 aday/eşik mantığı bundan etkilenmez."""
        return GroundedResult(RagOutcome.UNAVAILABLE, UNAVAILABLE_MESSAGE, model_calls=model_calls, warnings=(warning,),
                              fallback=tuple(candidates), fallback_label=RETRIEVAL_ONLY_FALLBACK, **base)  # fmt: skip

    def retrieve(self, question: str, as_of: datetime | None = None) -> RetrievalResult:
        return self.retriever.retrieve(question, as_of, evaluation=True)

    def answer(self, question: str, as_of: datetime | None = None, policy: str = POLICY_THRESHOLD, threshold: float | None = None) -> GroundedResult:
        started = self.clock()  # bütçe retrieval'dan önce başlar
        return self.decide(question, self.retrieve(question, as_of), policy, threshold, started=started)

    def decide(self, question: str, retrieval: RetrievalResult, policy: str, threshold: float | None = None,
               started: float | None = None) -> GroundedResult:  # fmt: skip
        self._started = self.clock() if started is None else started
        if policy not in POLICIES:
            raise ValueError(f"bilinmeyen aday politikası: {policy}")
        base = {"retrieval_outcome": retrieval.outcome.value, "candidate_policy": policy, "query_id": retrieval.query_id}
        if retrieval.outcome is not RetrievalOutcome.FOUND:  # güvenlik/hata kararları ve retrieval no_source: model yok
            outcome = _PASS_THROUGH[retrieval.outcome]
            return GroundedResult(outcome, retrieval.message, warnings=tuple(retrieval.warnings), **base)
        chunks = sorted(retrieval.chunks, key=lambda c: c.rank)
        if policy == POLICY_THRESHOLD:
            if threshold is None:
                return GroundedResult(RagOutcome.UNAVAILABLE, UNAVAILABLE_MESSAGE, warnings=("esik_verilmedi",), **base)
            candidates = [c for c in chunks if c.distance <= threshold][: self.candidate_k]
            if not candidates:
                return GroundedResult(RagOutcome.NO_SOURCE, NO_SOURCE_MESSAGE, warnings=("esik_alti_aday_yok",), **base)
        else:
            candidates = chunks[: self.candidate_k]
        return self._check(question, candidates, base)

    def _build(self, question: str, candidates: list[RetrievedChunk]):
        if self.method == M.METHOD_SENTENCES:
            return build_evidence_prompt(question, candidates, system=M.SENTENCE_SYSTEM, block=M.sentence_block, version=M.SENTENCE_VERSION)
        if self.method == M.METHOD_TWO_STEP:
            return build_evidence_prompt(question, candidates, system=M.DECISION_SYSTEM, version=M.DECISION_VERSION)
        return build_evidence_prompt(question, candidates)  # M0 ve M1: kanit_denetimi_v2

    def _call(self, messages, schema, calls: list[int]) -> dict:
        """Tek model çağrısı; KALAN toplam bütçeyle (M2'de iki çağrı aynı 120 sn bütçeyi paylaşır)."""
        remaining = self.budget_s - (self.clock() - self._started)
        if remaining <= 0:
            raise _BudgetSpent()
        self.model_calls += 1
        calls[0] += 1
        if getattr(self.generator, "supports_budget", False):
            return self.generator.chat_json(messages, schema, budget_s=remaining)["veri"]
        return self.generator.chat_json(messages, schema)["veri"]

    def _labelled(self, claims, built) -> tuple[QuoteClaim, ...]:
        # Kısa numara (K1…) deterministik olarak parça kimliğine çevrilir; bilinmeyen etiket olduğu gibi kalır ve
        # doğrulamada `parca_getirilmedi` olur. Alıntı yine YALNIZ o parçada birebir aranır (gevşetme yok).
        return tuple(QuoteClaim(built.labels.get(cl.chunk_id.strip().upper(), cl.chunk_id), cl.text) for cl in claims)

    def _check(self, question: str, candidates: list[RetrievedChunk], base: dict) -> GroundedResult:
        if self.generator is None:  # retrieval_only profili: model hiç çağrılmaz
            return self._fallback(candidates, "profil:retrieval_only", base)
        try:
            built = self._build(question, candidates)
        except RagError as exc:
            return self._fallback(candidates, f"istem:{exc.code}", base)
        base = {**base, "candidates": built.chunk_ids}
        if self.lifecycle is not None:
            try:
                self.lifecycle.before_generation()
            except RagError as exc:  # boşaltma doğrulanamadı ya da Ollama erişilemedi: üretim yapılmaz
                return self._fallback(candidates, f"yasam_dongusu:{exc.code}", base)
        given = [c for c in candidates if c.chunk_id in set(built.chunk_ids)]
        calls = [0]
        selected: tuple[str, ...] = ()
        claims: tuple[QuoteClaim, ...] = ()
        try:
            if self.method == M.METHOD_SENTENCES:
                supported, selected, missing = M.parse_sentences(self._call(built.messages, M.SENTENCE_SCHEMA, calls))
                verdict = M.verify_sentences(selected, M.sentence_map(built.labels, given), supported, missing)
            elif self.method == M.METHOD_TWO_STEP:
                supported, missing = M.parse_decision(self._call(built.messages, M.DECISION_SCHEMA, calls))
                if supported:
                    extract = build_evidence_prompt(question, given, system=M.EXTRACT_SYSTEM, version=M.EXTRACT_VERSION)
                    claims = self._labelled(M.parse_extract(self._call(extract.messages, M.EXTRACT_SCHEMA, calls)), built)
                verdict = verify_quotes(claims, given, claimed_supported=supported, missing=missing)
            else:
                schema = M.STRICT_SCHEMA if self.method == M.METHOD_STRICT else EVIDENCE_SCHEMA
                supported, claims, missing = parse_evidence(self._call(built.messages, schema, calls))
                claims = self._labelled(claims, built)
                verdict = verify_quotes(claims, given, claimed_supported=supported, missing=missing)
        except _BudgetSpent:  # toplam işlem bütçesi tükendi: (yeni) üretim yapılmaz
            return self._fallback(candidates, "butce_asildi", base, model_calls=calls[0])
        except RagError as exc:
            return self._fallback(candidates, f"uretici:{exc.code}", base, model_calls=calls[0])
        n = calls[0]
        decision = ModelDecision(supported, claims, missing, verdict.problems, selected, n)  # D0: ham karar kaydı
        if verdict.supported:
            lines = [f"“{q.text}” [{q.source_key}@{q.version} / {q.section} / {q.doc_start}–{q.doc_end}]" for q in verdict.quotes]
            return GroundedResult(RagOutcome.ANSWERED, ANSWER_LABEL, "\n".join(lines), verdict.quotes, model_calls=n,
                                  decision=decision, **base)  # fmt: skip
        warn = tuple(f"sahte_alinti:{p}" for p in verdict.problems) if verdict.problems else ("kanit_yetersiz",)
        return GroundedResult(RagOutcome.NO_SOURCE, NO_SOURCE_MESSAGE, model_calls=n, warnings=warn, decision=decision, **base)
