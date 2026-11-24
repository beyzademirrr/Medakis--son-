"""D0 ölçüm sözleşmesi ve D1 aday tamamlama (ağsız; sahte embedding ve üretici; gerçek Chroma; geçici depo ve TEST onayları)."""

import gc
import uuid

import pytest
import rag_helpers as H

from medakis.rag.chunking.chunker import ChunkConfig, chunk_corpus
from medakis.rag.contracts.dto import RagOutcome
from medakis.rag.contracts.retrieval import RetrievalOutcome as R
from medakis.rag.embeddings.fake_provider import FakeEmbedder
from medakis.rag.evaluation import contract as K
from medakis.rag.evaluation.contract import OutcomeClass as C
from medakis.rag.generation.mode import GenerationMode
from medakis.rag.generation.pipeline import POLICY_CANDIDATES, EvidencePipeline
from medakis.rag.ingestion.approval_ledger import APPROVE
from medakis.rag.metadata.file_store import FileMetadataStore
from medakis.rag.retrieval.retriever import RetrievalConfig, Retriever
from medakis.rag.vector_store.chroma_store import ChromaVectorStore
from medakis.rag.vector_store.index_builder import build_index
from medakis.rag.vector_store.index_identity import collection_name

T0, MODEL = H.T0, "sahte-embed"
SOURCES = ("SYN-YET-ACIK@1.0", "SYN-PRC-ONAY@1.0", "SYN-ROL-DESTEK@1.0", "SYN-AKS-GOREV@1.0")
Q = "Katman B'de rezerv nasıl hesaplanır?"


# ---- D0: sınıflandırma --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ans", [True, False])
def test_technical_security_and_conflict_are_separate_classes(ans):
    assert K.classify(ans, "unavailable").klass is C.TEKNIK_HATA
    assert K.classify(ans, "rejected").klass is C.GUVENLIK_REDDI
    assert K.classify(ans, "conflicting_sources").klass is C.KAYNAK_CATISMASI


def test_out_of_scope_classes():
    assert K.classify(False, "no_source").klass is C.BILINCLI_KACINMA
    assert K.classify(False, "answered").klass is C.YANLIS_KABUL


def test_answered_without_human_review_is_pending_not_correct():
    got = K.classify(True, "answered")
    assert got.klass is C.CEVAP_INCELEME_BEKLIYOR and got.klass is not C.DOGRU_CEVAP
    assert K.classify(True, "answered", human_label="dogru").klass is C.DOGRU_CEVAP
    assert K.classify(True, "answered", human_label="yanlis").klass is C.YANLIS_KANITLI_CEVAP
    with pytest.raises(ValueError):
        K.classify(True, "answered", human_label="belki")


@pytest.mark.parametrize("warnings, gold, reason", [
    (("esik_alti_aday_yok",), None, "esik_alti"),
    (("kanit_yetersiz",), False, "aday_havuzunda_gold_yok"),
    (("sahte_alinti:alinti_yok",), True, "bos_alinti"),
    (("sahte_alinti:1:alinti_parcada_yok",), True, "sahte_alinti"),
    (("kanit_yetersiz",), True, "kanit_yetersiz"),
    (("yeterli_kanit_yok",), True, "diger"),
    (("yeterli_kanit_yok",), None, "kayitta_yok"),
])  # fmt: skip
def test_false_rejection_subreasons(warnings, gold, reason):
    got = K.classify(True, "no_source", warnings, gold)
    assert got.klass is C.YANLIS_RET and got.reason == reason


def test_summary_excludes_technical_errors_from_abstention_and_withholds_semantic_accuracy():
    rows = [{"ans": False, "sonuc": "no_source"}, {"ans": False, "sonuc": "unavailable"}, {"ans": False, "sonuc": "answered"},
            {"ans": True, "sonuc": "answered"}, {"ans": True, "sonuc": "no_source", "w": ("kanit_yetersiz",)}]  # fmt: skip
    for r in rows:
        got = K.classify(r["ans"], r["sonuc"], r.get("w", ()), True)
        r["sinif"], r["alt_neden"] = got.klass, got.reason
    s = K.summarize_contract(rows)
    assert s["bilincli_kacinma_orani_karar_verilen"] == 0.5  # teknik hata paydadan çıkar
    assert s["bilincli_kacinma_orani_tum_kaynak_disi"] == 0.333
    assert s["eski_dogru_kacinma"] == 0.667  # eski ölçü teknik hatayı kaçınma sayar (korunur)
    assert s["teknik_hata_orani"] == 0.2 and s["yanlis_kabul_orani"] == 0.333
    assert s["cevap_inceleme_bekliyor"] == 1 and s["anlamsal_dogruluk"] is None and s["anlamsal_dogruluk_gerekce"]
    assert s["yanlis_ret_alt_nedenleri"] == {"kanit_yetersiz": 1}


# ---- Ortak kurulum ------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built():
    path = H.d_tmp()
    vectors = ChromaVectorStore(path)
    meta = FileMetadataStore(H.CORPUS, path, ledger_name="bos.jsonl")
    res = build_index(chunk_corpus(meta.corpus().documents), ChunkConfig(), FakeEmbedder(MODEL), vectors, meta.registry())
    yield path, vectors, res
    vectors.close()
    gc.collect()
    H.remove(path)


def make(built, config=RetrievalConfig()):
    path, vectors, _ = built
    meta = FileMetadataStore(H.CORPUS, path, ledger_name=f"izler/{uuid.uuid4().hex}.jsonl")
    recs = {d.record.uid: d.record for d in meta.corpus().documents}
    for uid in SOURCES:
        meta.ledger.append(APPROVE, recs[uid], "test-onayi", "test", T0, list(recs.values()))
    return Retriever(meta, vectors, FakeEmbedder(MODEL), MODEL, config=config, clock=lambda: T0)


def duplicating(monkeypatch):
    """Chroma'nın her sonucu iki kez döndürdüğü durum (özdeş şablon parçalarının etkisini taklit eder)."""
    real, calls = ChromaVectorStore.query, []

    def dup(col, vector, n, doc_uids):
        calls.append(n)
        hits = real(col, vector, (n + 1) // 2, doc_uids)
        return [h for pair in zip(hits, hits) for h in pair][:n]

    monkeypatch.setattr(ChromaVectorStore, "query", staticmethod(dup))
    return calls


# ---- D1: aday tamamlama -------------------------------------------------------------------------------------------


def test_refill_is_off_by_default(built, monkeypatch):
    calls = duplicating(monkeypatch)
    res = make(built).retrieve(Q, evaluation=True)
    assert RetrievalConfig().refill is False and calls == [10] and len(res.chunks) <= 5  # ayıklama sonrası eksik
    assert not any(w.startswith("aday_tamamlama") for w in res.warnings)


def test_refill_restores_unique_candidates_in_evaluation_mode(built, monkeypatch):
    calls = duplicating(monkeypatch)
    res = make(built, RetrievalConfig(refill=True)).retrieve(Q, evaluation=True)
    assert res.outcome is R.FOUND and calls[:2] == [10, 20]
    assert len(res.chunks) == 10 and len({c.chunk_id for c in res.chunks}) == 10
    assert [c.rank for c in res.chunks] == list(range(1, 11)) and any(w.startswith("aday_tamamlama:n=") for w in res.warnings)
    off = make(built).retrieve(Q, evaluation=True)
    assert [c.chunk_id for c in res.chunks[: len(off.chunks)]] == [c.chunk_id for c in off.chunks]  # ilk sıralar değişmez


def test_refill_never_applies_in_normal_mode(built, monkeypatch):
    calls = duplicating(monkeypatch)
    ret = make(built, RetrievalConfig(refill=True))
    monkeypatch.setattr(ret.metadata.registry(), "threshold", lambda key: 2.0)  # kayda yazılmaz
    res = ret.retrieve(Q)
    assert res.outcome is R.FOUND and calls == [10] and len(res.chunks) <= 5


def test_refill_stops_at_cap_and_when_exhausted(built, monkeypatch):
    calls = duplicating(monkeypatch)
    make(built, RetrievalConfig(refill=True, refill_max=12)).retrieve(Q, evaluation=True)
    assert calls == [10, 12]


def test_refilled_hits_pass_the_same_integrity_check(built, monkeypatch):
    """Tamamlama turunda gelen ve metni değiştirilmiş bir sonuç bütün sorguyu `unavailable` yapar."""
    path, vectors, res = built
    real = ChromaVectorStore.query

    def tampered(col, vector, n, doc_uids):
        hits = real(col, vector, n, doc_uids)
        if n <= 10:
            return [h for pair in zip(hits, hits) for h in pair][:n]
        bad = hits[-1]
        return hits[:-1] + [type(bad)(bad.vector_id, bad.distance, bad.document + " (değişti)", bad.metadata)]

    monkeypatch.setattr(ChromaVectorStore, "query", staticmethod(tampered))
    out = make(built, RetrievalConfig(refill=True)).retrieve(Q, evaluation=True)
    assert out.outcome is R.UNAVAILABLE and out.chunks == () and any("indeks_butunlugu_bozuk" in w for w in out.warnings)


def test_refill_keeps_approval_filter(built):
    ret = make(built, RetrievalConfig(refill=True))
    res = ret.retrieve(Q, evaluation=True)
    assert res.chunks and {f"{c.source_key}@{c.version}" for c in res.chunks} <= set(SOURCES)
    path, vectors, idx = built
    col = vectors.open(collection_name(idx.index_key), idx.index_key)
    assert vectors.count(col) == len(vectors.snapshot(col))


# ---- D0: ham karar kaydı ------------------------------------------------------------------------------------------


class FakeGenerator:
    def __init__(self, response):
        self.response = response

    def chat_json(self, messages, schema):
        return {"veri": self.response}


def test_raw_decision_recorded_for_answered_and_rejected(built):
    ret = make(built)
    retrieval = ret.retrieve(Q, evaluation=True)
    top = sorted(retrieval.chunks, key=lambda c: c.rank)[0]
    ok = EvidencePipeline(ret, FakeGenerator({"destekleniyor": True, "alintilar": [{"parca": "K1", "metin": top.text[:60]}]}),
                          GenerationMode.TEST).decide(Q, retrieval, POLICY_CANDIDATES)  # fmt: skip
    assert ok.outcome is RagOutcome.ANSWERED and ok.decision.supported and ok.decision.claims[0].chunk_id == top.chunk_id
    assert ok.decision.as_dict()["alinti_sayisi"] == 1 and ok.decision.problems == ()
    empty = EvidencePipeline(ret, FakeGenerator({"destekleniyor": True, "alintilar": []}), GenerationMode.TEST).decide(
        Q, retrieval, POLICY_CANDIDATES)
    assert empty.outcome is RagOutcome.NO_SOURCE and empty.decision.supported and empty.decision.problems == ("alinti_yok",)
    assert K.classify(True, empty.outcome.value, empty.warnings, True).reason == "bos_alinti"


def test_no_decision_when_model_not_called(built):
    path, vectors, _ = built
    meta = FileMetadataStore(H.CORPUS, path, ledger_name=f"izler/{uuid.uuid4().hex}.jsonl")  # onay yok
    ret = Retriever(meta, vectors, FakeEmbedder(MODEL), MODEL, clock=lambda: T0)
    out = EvidencePipeline(ret, FakeGenerator({}), GenerationMode.TEST).answer(Q, policy=POLICY_CANDIDATES)
    assert out.outcome is RagOutcome.NO_SOURCE and out.decision is None and out.model_calls == 0
