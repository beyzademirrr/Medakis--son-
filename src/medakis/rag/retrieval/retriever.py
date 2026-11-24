"""Kaynak doğrulamalı retrieval ve güvenli kaçınma (Aşama 7.4). Cevap ÜRETİLMEZ.

Akış (her adım fail-closed):
1. Soru denetimi (yalnız metin, uzunluk, denetim karakteri) → aksi `rejected`.
2. Profil modelinin TEK `ready` indeksi; corpus özeti, model digest'i, boyut ve önekler indeks kimliğiyle aynı
   olmalı; eşik dev kümesinde kalibre edilmiş olmalı (değerlendirme kipi hariç) → aksi `unavailable`.
3. `as_of` anında izinli belgeler (approved, içerik bütün, geçerli; draft/retired asla). Sürüm çakışması veya
   onaylı içeriğin değişmesi → `conflicting_sources`. İzinli belge yoksa → `no_source` (gömme bile yapılmaz).
4. Sorgu gömme (önekli, truncate=false) → filtreli cosine top-k (Chroma `where doc_uid $in`).
5. Her sonuç YENİDEN doğrulanır: indeks eşlemesinde var mı, metadata/hash/metin aynı mı, belgedeki alıntı aralığı
   metinle birebir aynı mı. Bütünlük ihlali → bütün sorgu `unavailable`. Filtre dışı sonuç (savunma) atılır.
6. Tekrarlar ayıklanır; dev eşiği uygulanır; en fazla 5 parça. Hiç parça kalmazsa `no_source`.
7. Getirilen belgede kural çatışması işareti varsa `conflicting_sources` (kaynaklar gösterilir).
Her sorgu ekleme-yalnız sorgu kaydına yazılır. Bu modül hiçbir iş verisine, onaya veya atamaya erişemez.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from medakis.rag.chunking.chunker import ChunkConfig, chunk_corpus, corpus_sha256, sha256_text
from medakis.rag.contracts.documents import require_utc
from medakis.rag.contracts.dto import NO_SOURCE_MESSAGE, Timings
from medakis.rag.contracts.metadata_store import MetadataStore
from medakis.rag.contracts.providers import MeasuredEmbedder
from medakis.rag.contracts.retrieval import (
    CONFLICT_MESSAGE,
    RETRIEVAL_ONLY_LABEL,
    UNAVAILABLE_MESSAGE,
    RetrievalOutcome,
    RetrievalResult,
    RetrievedChunk,
)
from medakis.rag.errors import EmbeddingUnavailable, LedgerError, RagError
from medakis.rag.ingestion.document_policy import eligible_documents
from medakis.rag.vector_store.chroma_store import ChromaVectorStore
from medakis.rag.vector_store.index_identity import collection_name

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
REJECTED_MESSAGE = "Soru kabul edilmedi: yalnız 1–{n} karakterlik düz metin sorulabilir."


@dataclass(frozen=True)
class RetrievalConfig:
    top_k: int = 10
    max_chunks: int = 5
    max_query_chars: int = 500
    refill: bool = False  # D1 deneyi: ayıklamadan sonra eksilen adayları tamamla (yalnız değerlendirme kipi)
    refill_max: int = 80


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Retriever:
    def __init__(self, metadata: MetadataStore, vectors: ChromaVectorStore, embedder: MeasuredEmbedder, model: str,
                 config: RetrievalConfig = RetrievalConfig(), clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:  # fmt: skip
        self.metadata, self.vectors, self.embedder, self.model = metadata, vectors, embedder, model
        self.config, self.clock = config, clock

    def retrieve(self, question: str, as_of: datetime | None = None, *, evaluation: bool = False) -> RetrievalResult:
        started = time.perf_counter()
        state: dict = {"question": question, "as_of": as_of, "evaluation": evaluation, "warnings": [], "hits": [],
                       "index_key": None, "retrieval_ms": 0.0}  # fmt: skip
        try:
            result = self._run(state, started)
        except RagError as exc:  # beklenmeyen RAG hatası: fail-closed
            result = self._result(state, started, RetrievalOutcome.UNAVAILABLE, UNAVAILABLE_MESSAGE, warn=f"rag_hatasi:{exc.code}")
        qid = self.metadata.append_query_log({
            "soru": question if isinstance(question, str) else repr(question), "as_of": _iso(result.as_of),
            "sonuc": result.outcome.value, "indeks": result.index_key, "kip": "degerlendirme" if evaluation else "normal",
            "isabetler": state["hits"], "uyarilar": list(result.warnings), "zaman": _iso(self.clock()),
            "sure_ms": {"getirme": result.timings.retrieval_ms, "toplam": result.timings.total_ms},
        })  # fmt: skip
        return RetrievalResult(result.outcome, result.message, result.chunks, result.as_of, result.index_key, qid,
                               result.timings, result.warnings, result.evaluation)  # fmt: skip

    # ---- Adımlar ------------------------------------------------------------------------------------------

    def _result(self, state: dict, started: float, outcome: RetrievalOutcome, message: str, chunks=(), warn: str | None = None) -> RetrievalResult:
        if warn:
            state["warnings"].append(warn)
        total = (time.perf_counter() - started) * 1000
        timings = Timings(min(state["retrieval_ms"], total), 0.0, total)
        as_of = state["as_of"] if isinstance(state["as_of"], datetime) else None
        return RetrievalResult(outcome, message, tuple(chunks), as_of, state["index_key"], None, timings,
                               tuple(state["warnings"]), state["evaluation"])  # fmt: skip

    def _run(self, state: dict, started: float) -> RetrievalResult:
        R = RetrievalOutcome
        q = state["question"]
        rejected = REJECTED_MESSAGE.format(n=self.config.max_query_chars)
        if not isinstance(q, str) or not q.strip() or len(q) > self.config.max_query_chars or _CONTROL.search(q):
            return self._result(state, started, R.REJECTED, rejected, warn="soru_gecersiz")
        if state["as_of"] is None:
            state["as_of"] = self.clock()
        try:
            require_utc(state["as_of"], "as_of")
        except ValueError:
            state["as_of"] = None
            return self._result(state, started, R.REJECTED, rejected, warn="as_of_utc_degil")
        as_of: datetime = state["as_of"]

        registry = self.metadata.registry()
        keys = registry.ready_keys(self.model)
        if len(keys) != 1:
            return self._result(state, started, R.UNAVAILABLE, UNAVAILABLE_MESSAGE,
                                warn="hazir_indeks_yok" if not keys else "birden_cok_hazir_indeks")  # fmt: skip
        key = state["index_key"] = keys[0]
        manifest = registry.manifest(key)
        corpus = self.metadata.corpus()
        chunks = chunk_corpus(corpus.documents, ChunkConfig(**manifest["parcalama"]))
        if corpus_sha256(chunks) != manifest["corpus_sha256"]:
            return self._result(state, started, R.UNAVAILABLE, UNAVAILABLE_MESSAGE, warn="indeks_guncel_degil")
        try:
            info = self.embedder.describe()
        except EmbeddingUnavailable as exc:
            return self._result(state, started, R.UNAVAILABLE, UNAVAILABLE_MESSAGE, warn=f"embedding:{exc.code}")
        expected = manifest["embedding"]
        if {k: getattr(info, k) for k in expected} != expected:
            return self._result(state, started, R.UNAVAILABLE, UNAVAILABLE_MESSAGE, warn="model_kimligi_degisti")
        threshold = registry.threshold(key)
        if threshold is None and not state["evaluation"]:
            return self._result(state, started, R.UNAVAILABLE, UNAVAILABLE_MESSAGE, warn="esik_kalibre_edilmedi")

        try:
            states, ledger_problems = self.metadata.document_states()
        except LedgerError as exc:
            return self._result(state, started, R.UNAVAILABLE, UNAVAILABLE_MESSAGE, warn=f"onay_izi:{exc.code}")
        elig = eligible_documents(states, as_of, ledger_problems)
        if elig.conflicts:
            state["warnings"].extend(elig.conflicts)
            return self._result(state, started, R.CONFLICTING_SOURCES, CONFLICT_MESSAGE)
        if not elig.allowed:
            return self._result(state, started, R.NO_SOURCE, NO_SOURCE_MESSAGE, warn="izinli_belge_yok")

        t0 = time.perf_counter()
        try:
            vector, _count = self.embedder.embed_one(info.query_prefix + q)
            col = self.vectors.open(collection_name(key), key)
        except EmbeddingUnavailable as exc:
            outcome = R.REJECTED if exc.code == "baglam_asildi" else R.UNAVAILABLE
            return self._result(state, started, outcome, rejected if outcome is R.REJECTED else UNAVAILABLE_MESSAGE,
                                warn=f"embedding:{exc.code}")  # fmt: skip

        mapping = {m["chunk_id"]: m for m in manifest["parcalar"]}
        docs = corpus.by_uid()
        allowed = {s.record.uid: s.record for s in elig.allowed}
        # D1 (deneysel, varsayılan KAPALI, yalnız değerlendirme kipi): ayıklamadan sonra tekil aday sayısı top_k'nın
        # altındaysa Chroma'dan iki kat sonuç istenir (en fazla `refill_max` ya da koleksiyon büyüklüğü). Her turda
        # bütün sonuçlar baştan AYNI denetimlerden geçer; tek bir bütünlük ihlali yine bütün sorguyu durdurur.
        refill = self.config.refill and state["evaluation"]
        cap = min(self.config.refill_max, self.vectors.count(col)) if refill else self.config.top_k
        n = self.config.top_k
        while True:
            hits = self.vectors.query(col, vector, n, list(elig.allowed_uids))
            log: list[dict] = []
            extra_warn: list[str] = []
            kept: list[tuple] = []
            seen_ids: set[str] = set()
            seen_text: set[str] = set()
            for h in hits:
                m, md = mapping.get(h.vector_id), h.metadata
                doc = docs.get(m["doc_uid"]) if m else None
                intact = (m is not None and doc is not None and md.get("index_key") == key and md.get("doc_uid") == m["doc_uid"]
                          and md.get("chunk_sha256") == m["sha256"] == sha256_text(h.document)
                          and doc.text[m["char_start"]:m["char_end"]] == h.document and m["section"] and doc.record.version)  # fmt: skip
                if not intact:
                    state["hits"] = log + [{"kimlik": h.vector_id, "uzaklik": h.distance, "durum": "butunluk_ihlali"}]
                    state["retrieval_ms"] = (time.perf_counter() - t0) * 1000
                    return self._result(state, started, R.UNAVAILABLE, UNAVAILABLE_MESSAGE, warn=f"indeks_butunlugu_bozuk:{h.vector_id}")
                if m["doc_uid"] not in allowed:
                    log.append({"kimlik": h.vector_id, "uzaklik": h.distance, "durum": "filtre_disi"})
                    extra_warn.append(f"filtre_disi_sonuc_atildi:{m['doc_uid']}")
                    continue
                if h.vector_id in seen_ids or m["sha256"] in seen_text:
                    continue
                seen_ids.add(h.vector_id)
                seen_text.add(m["sha256"])
                passes = state["evaluation"] or h.distance <= threshold
                log.append({"kimlik": h.vector_id, "uzaklik": h.distance, "durum": "uygun" if passes else "esik_disi"})
                if passes:
                    kept.append((h, m, allowed[m["doc_uid"]]))
            if not refill or len(seen_ids) >= self.config.top_k or len(hits) < n or n >= cap:
                break
            n = min(n * 2, cap)
        state["retrieval_ms"] = (time.perf_counter() - t0) * 1000
        state["hits"] = log
        state["warnings"].extend(extra_warn)
        if refill and n > self.config.top_k:
            state["warnings"].append(f"aday_tamamlama:n={n}")
        limit = self.config.top_k if state["evaluation"] else self.config.max_chunks
        kept = kept[:limit]
        if state["evaluation"]:
            state["warnings"].append("degerlendirme_kipi_esiksiz")
        if not kept:
            return self._result(state, started, R.NO_SOURCE, NO_SOURCE_MESSAGE, warn="yeterli_kanit_yok")
        out = [RetrievedChunk(i + 1, h.vector_id, h.distance, rec.source_key, rec.source_name, rec.doc_type, rec.version,
                              m["section"], _iso(rec.valid_from), _iso(rec.valid_until), m["char_start"], m["char_end"],
                              h.document) for i, (h, m, rec) in enumerate(kept)]  # fmt: skip
        conflicts = sorted({rec.uid for _, _, rec in kept if rec.rule_conflict})
        if conflicts:
            state["warnings"].extend(f"kural_catismasi:{u}" for u in conflicts)
            return self._result(state, started, R.CONFLICTING_SOURCES, CONFLICT_MESSAGE, out)
        return self._result(state, started, R.FOUND, RETRIEVAL_ONLY_LABEL, out)
