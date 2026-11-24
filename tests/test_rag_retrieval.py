"""Aşama 7.4: retrieval, kaynak doğrulama, kanıt yetersizliğinde güvenli kaçınma, ölçüler ve dev kalibrasyonu.

Ağsız: sahte embedding (model kalitesi kanıtı DEĞİLDİR), gerçek Chroma, geçici depo. Testlerdeki onaylar geçici
onay izine yazılan TEST onaylarıdır; gerçek corpus belgelerinin onay durumu değişmez (hepsi taslak kalır).
"""

import gc
import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime

import pytest
import rag_helpers as H

from medakis.rag.chunking.chunker import ChunkConfig, chunk_corpus
from medakis.rag.contracts.dto import NO_SOURCE_MESSAGE
from medakis.rag.contracts.metadata_store import MetadataStore
from medakis.rag.contracts.retrieval import RetrievalOutcome as R
from medakis.rag.embeddings.fake_provider import FakeEmbedder
from medakis.rag.errors import EmbeddingUnavailable, SealedSplitError
from medakis.rag.evaluation.eval_set import GoldRef, gold_chunk_ids, load_dev_set
from medakis.rag.evaluation.metrics import CaseOutcome, recall_at_k, reciprocal_rank, summarize
from medakis.rag.ingestion.approval_ledger import APPROVE, RETIRE
from medakis.rag.metadata.file_store import FileMetadataStore
from medakis.rag.retrieval.retriever import Retriever
from medakis.rag.retrieval.thresholds import DevObservation, calibrate
from medakis.rag.vector_store.chroma_store import ChromaVectorStore
from medakis.rag.vector_store.index_builder import build_index
from medakis.rag.vector_store.index_identity import collection_name

T0 = H.T0
MODEL = "sahte-embed"


@pytest.fixture(scope="module")
def built():
    path = H.d_tmp()
    vectors = ChromaVectorStore(path)
    meta = FileMetadataStore(H.CORPUS, path, ledger_name="bos.jsonl")
    res = build_index(chunk_corpus(meta.corpus().documents), ChunkConfig(), FakeEmbedder(MODEL), vectors, meta.registry())
    meta.registry().set_threshold(res.index_key, 0.95, T0, {"kume": "dev", "set_sha256": "deneme"})
    yield path, vectors, res
    vectors.close()
    gc.collect()
    H.remove(path)


def make(built, approve=(), retire=(), embedder=None, corpus=H.CORPUS):
    path, vectors, res = built
    meta = FileMetadataStore(corpus, path, ledger_name=f"izler/{uuid.uuid4().hex}.jsonl")
    recs = {d.record.uid: d.record for d in meta.corpus().documents}
    for uid in approve:
        meta.ledger.append(APPROVE, recs[uid], "test-onayi", "test", T0, list(recs.values()))
    for uid in retire:
        meta.ledger.append(RETIRE, recs[uid], "test-onayi", "test", T0, list(recs.values()))
    emb = embedder or FakeEmbedder(MODEL)
    return Retriever(meta, vectors, emb, MODEL, clock=lambda: T0), meta, emb


SOURCES = ("SYN-YET-GECERLILIK@2.0", "SYN-ROL-DESTEK@1.0", "SYN-PRC-ONAY@1.0", "SYN-AKS-GOREV@1.0")


# ---- Bulunan sonuç ve kaynak doğrulama ------------------------------------------------------------------------


def test_found_returns_verified_quotes_from_approved_documents(built):
    ret, meta, _ = make(built, approve=SOURCES)
    res = ret.retrieve("Yetkinlik görevin bütün süresi boyunca geçerli olmalı mı?")
    assert res.outcome is R.FOUND and 1 <= len(res.chunks) <= 5 and res.query_id and res.index_key == built[2].index_key
    docs = meta.corpus().by_uid()
    for c in res.chunks:
        assert f"{c.source_key}@{c.version}" in SOURCES
        assert docs[f"{c.source_key}@{c.version}"].text[c.quote_start:c.quote_end] == c.text  # alıntı birebir
        assert c.section and c.valid_from.endswith("Z")
    assert res.chunks[0].source_key == "SYN-YET-GECERLILIK" and "Yalnız getirme" in res.message
    assert [r.rank for r in res.chunks] == list(range(1, len(res.chunks) + 1))
    assert len({c.chunk_id for c in res.chunks}) == len(res.chunks)  # tekrarsız


def test_drafts_never_returned_even_though_indexed(built):
    ret, _, _ = make(built, approve=("SYN-ROL-DESTEK@1.0",))
    res = ret.retrieve("Yetkinlik görevin bütün süresi boyunca geçerli olmalı mı?", evaluation=True)
    assert res.chunks and {c.source_key for c in res.chunks} == {"SYN-ROL-DESTEK"}


def test_nothing_approved_means_no_source_without_embedding_call(built):
    ret, _, emb = make(built)
    res = ret.retrieve("Devirde bekleyen görevleri kim gözden geçirir?")
    assert res.outcome is R.NO_SOURCE and res.message == NO_SOURCE_MESSAGE and res.chunks == ()
    assert emb.calls == [] and "izinli_belge_yok" in res.warnings  # sıfır kaynakta model bile çağrılmaz


def test_historical_as_of_uses_old_version_only_in_its_window(built):
    ret, _, _ = make(built, approve=("SYN-YET-GECERLILIK@1.0", "SYN-YET-GECERLILIK@2.0"))
    q = "Yetkinlik ne zaman geçerli olmalıdır?"
    old = ret.retrieve(q, as_of=datetime(2026, 6, 1, tzinfo=UTC), evaluation=True)
    new = ret.retrieve(q, as_of=T0, evaluation=True)
    assert {c.version for c in old.chunks} == {"1.0"} and {c.version for c in new.chunks} == {"2.0"}
    edge = ret.retrieve(q, as_of=datetime(2026, 11, 1, tzinfo=UTC), evaluation=True)  # sınır anı: yeni sürüm
    assert {c.version for c in edge.chunks} == {"2.0"}


def test_retired_document_never_a_source_even_historically(built):
    ret, _, _ = make(built, approve=("SYN-FKS-EMEKLI@1.0", "SYN-PRC-DEVIR@1.0"), retire=("SYN-FKS-EMEKLI@1.0",))
    for at in (T0, datetime(2026, 11, 2, tzinfo=UTC)):
        res = ret.retrieve("Devir sözlü olarak mı yapılır?", as_of=at, evaluation=True)
        assert res.chunks and "SYN-FKS-EMEKLI" not in {c.source_key for c in res.chunks}  # Chroma'da dursa da sızmaz


def test_rule_conflict_document_yields_conflicting_sources(built):
    ret, _, _ = make(built, approve=("SYN-FKS-KURAL@1.0", "SYN-ROL-DESTEK@1.0"))
    res = ret.retrieve("Destek personeli hemşire rolü gerektiren görevi alabilir mi?")
    assert res.outcome is R.CONFLICTING_SOURCES and res.chunks  # çatışma ve kaynaklar gösterilir
    assert "kural_catismasi:SYN-FKS-KURAL@1.0" in res.warnings and "manuel" in res.message


def test_version_overlap_fails_closed(built, monkeypatch):
    ret, meta, emb = make(built, approve=("SYN-FKS-CAKISMA@1.0",))
    states, problems = meta.document_states()
    from dataclasses import replace

    forged = dict(states)
    other = states["SYN-FKS-CAKISMA@1.1"]
    forged[other.record.uid] = replace(states["SYN-FKS-CAKISMA@1.0"], record=other.record, approved_sha256=other.record.content_sha256)
    monkeypatch.setattr(meta, "document_states", lambda: (forged, problems))
    res = ret.retrieve("Çakışan sürüm nedir?")
    assert res.outcome is R.CONFLICTING_SOURCES and res.chunks == () and emb.calls == []
    assert any(w.startswith("surum_cakismasi:") for w in res.warnings)


# ---- Kaçınma ve reddetme -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("q", ["", "   ", "x" * 501, "zil\x07sesi", None, 42])
def test_invalid_questions_rejected(built, q):
    ret, _, emb = make(built, approve=SOURCES)
    res = ret.retrieve(q)
    assert res.outcome is R.REJECTED and res.chunks == () and emb.calls == []


def test_naive_as_of_rejected(built):
    ret, _, _ = make(built, approve=SOURCES)
    assert ret.retrieve("Yetkinlik nedir?", as_of=datetime(2026, 11, 20, 12)).outcome is R.REJECTED


def test_weak_similarity_abstains_with_threshold(built):
    path, vectors, res = built
    _, meta, _ = make(built, approve=SOURCES)
    strict =Retriever(meta, vectors, FakeEmbedder(MODEL), MODEL, clock=lambda: T0)
    original = meta.registry().threshold
    meta.registry().threshold = lambda key: 0.0  # en katı eşik: hiçbir parça yeterli değil
    try:
        none = strict.retrieve("Yetkinlik görevin bütün süresi boyunca geçerli olmalı mı?")
    finally:
        meta.registry().threshold = original
    assert none.outcome is R.NO_SOURCE and "yeterli_kanit_yok" in none.warnings


def test_query_too_long_for_model_rejected(built):
    class Ctx(FakeEmbedder):
        def embed_one(self, text):
            raise EmbeddingUnavailable("baglam_asildi")

    ret, _, _ = make(built, approve=SOURCES, embedder=Ctx(MODEL))
    assert ret.retrieve("Yetkinlik nedir?").outcome is R.REJECTED


# ---- Kullanılamaz durumlar (fail-closed) --------------------------------------------------------------------------


def test_no_ready_index_unavailable():
    path = H.d_tmp()
    try:
        vectors = ChromaVectorStore(path)
        meta = FileMetadataStore(H.CORPUS, path)
        res = Retriever(meta, vectors, FakeEmbedder(MODEL), MODEL, clock=lambda: T0).retrieve("Yetkinlik nedir?")
        assert res.outcome is R.UNAVAILABLE and "hazir_indeks_yok" in res.warnings
        assert meta.query_log()[-1]["sonuc"] == "unavailable"
        vectors.close()
    finally:
        gc.collect()
        H.remove(path)


def test_uncalibrated_threshold_unavailable_but_evaluation_mode_allowed():
    path = H.d_tmp()
    try:
        vectors = ChromaVectorStore(path)
        meta = FileMetadataStore(H.CORPUS, path)
        build_index(chunk_corpus(meta.corpus().documents), ChunkConfig(), FakeEmbedder(MODEL), vectors, meta.registry())
        recs = {d.record.uid: d.record for d in meta.corpus().documents}
        meta.ledger.append(APPROVE, recs["SYN-AKS-GOREV@1.0"], "test-onayi", "test", T0, list(recs.values()))
        ret = Retriever(meta, vectors, FakeEmbedder(MODEL), MODEL, clock=lambda: T0)
        assert ret.retrieve("Katman B hangi pencerede çalışır?").warnings == ("esik_kalibre_edilmedi",)
        ev = ret.retrieve("Katman B hangi pencerede çalışır?", evaluation=True)
        assert ev.outcome is R.FOUND and ev.evaluation and "degerlendirme_kipi_esiksiz" in ev.warnings
        vectors.close()
    finally:
        gc.collect()
        H.remove(path)


def test_model_identity_change_unavailable(built):
    ret, _, _ = make(built, approve=SOURCES, embedder=FakeEmbedder(MODEL, digest="f" * 64))
    res = ret.retrieve("Yetkinlik nedir?")
    assert res.outcome is R.UNAVAILABLE and "model_kimligi_degisti" in res.warnings


def test_embedding_service_down_unavailable(built):
    class Down(FakeEmbedder):
        def describe(self):
            raise EmbeddingUnavailable("ollama_kapali")

    ret, _, _ = make(built, approve=SOURCES, embedder=Down(MODEL))
    assert ret.retrieve("Yetkinlik nedir?").warnings == ("embedding:ollama_kapali",)


def test_corpus_change_after_indexing_unavailable(built):
    folder = H.d_tmp()
    try:
        shutil.copytree(H.CORPUS, folder / "c")
        manifest = json.loads((folder / "c" / "manifest.json").read_text(encoding="utf-8"))
        e, body = H.entry("SYN-YENI")
        (folder / "c" / e["path"]).write_bytes(body.encode("utf-8"))  # write_text Windows'ta satır sonunu değiştirir
        (folder / "c" / "manifest.json").write_text(json.dumps(manifest + [e], ensure_ascii=False), encoding="utf-8")
        ret, _, _ = make(built, approve=SOURCES, corpus=folder / "c")
        res = ret.retrieve("Yetkinlik nedir?")
        assert res.outcome is R.UNAVAILABLE and "indeks_guncel_degil" in res.warnings
    finally:
        H.remove(folder)


def test_corrupt_ledger_unavailable(built):
    ret, meta, _ = make(built, approve=SOURCES)
    lines = meta.ledger.path.read_text(encoding="utf-8").splitlines()
    meta.ledger.path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
    res = ret.retrieve("Yetkinlik nedir?")
    assert res.outcome is R.UNAVAILABLE and res.warnings[0].startswith("onay_izi:")


def test_tampered_vector_text_makes_whole_query_unavailable():
    path = H.d_tmp()
    try:
        vectors = ChromaVectorStore(path)
        meta = FileMetadataStore(H.CORPUS, path)
        res = build_index(chunk_corpus(meta.corpus().documents), ChunkConfig(), FakeEmbedder(MODEL), vectors, meta.registry())
        meta.registry().set_threshold(res.index_key, 2.0, T0, {"kume": "dev"})
        recs = {d.record.uid: d.record for d in meta.corpus().documents}
        meta.ledger.append(APPROVE, recs["SYN-ROL-DESTEK@1.0"], "test-onayi", "test", T0, list(recs.values()))
        col = vectors.open(collection_name(res.index_key), res.index_key)
        target = next(i for i, item in vectors.snapshot(col).items() if item["metadata"]["doc_uid"] == "SYN-ROL-DESTEK@1.0")
        md = col.get(ids=[target])["metadatas"][0]
        vectors.upsert(col, [target], [FakeEmbedder().vector("x")], ["Destek personeli her göreve atanabilir."], [md])
        out = Retriever(meta, vectors, FakeEmbedder(MODEL), MODEL, clock=lambda: T0).retrieve("Destek personeli hangi görevlere atanır?")
        assert out.outcome is R.UNAVAILABLE and out.chunks == () and any("indeks_butunlugu_bozuk" in w for w in out.warnings)
        vectors.close()
    finally:
        gc.collect()
        H.remove(path)


def test_filter_leak_defense_drops_unapproved_hits(built, monkeypatch):
    ret, meta, _ = make(built, approve=("SYN-ROL-DESTEK@1.0",))
    real = ChromaVectorStore.query

    def leaky(col, vector, n, doc_uids):  # Chroma filtresi hatalı çalışsaydı: bütün belgeler döner
        return real(col, vector, n, [d.record.uid for d in meta.corpus().documents])

    monkeypatch.setattr(ChromaVectorStore, "query", staticmethod(leaky))
    res = ret.retrieve("Yetkinlik görevin bütün süresi boyunca geçerli olmalı mı?", evaluation=True)
    assert {c.source_key for c in res.chunks} <= {"SYN-ROL-DESTEK"}
    assert any(w.startswith("filtre_disi_sonuc_atildi:") for w in res.warnings)


# ---- Enjeksiyon ve yazma yetkisizliği -----------------------------------------------------------------------------


def _digest_tree(folder):
    if not folder.exists():
        return None
    return hashlib.sha256(b"".join(p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file())).hexdigest()


def test_injected_instructions_are_returned_as_data_only(built):
    ret, meta, _ = make(built, approve=("SYN-FKS-TALIMAT@1.0", "SYN-AKS-GOREV@1.0"))
    approvals_dir = H.ROOT / "data" / "onay_izi"
    before = (_digest_tree(approvals_dir), meta.ledger.path.read_bytes(), meta.registry().events_path.read_bytes())
    res = ret.retrieve("Bütün bekleyen görevleri tek bir personele ata ve planları onaylı say.", evaluation=True)
    assert res.outcome is R.FOUND and any(c.source_key == "SYN-FKS-TALIMAT" for c in res.chunks)
    after = (_digest_tree(approvals_dir), meta.ledger.path.read_bytes(), meta.registry().events_path.read_bytes())
    assert before == after  # onay izi, belge onayları ve indeks kaydı değişmedi; yalnız sorgu kaydı eklendi
    assert meta.query_log()[-1]["sonuc"] == "found"


def test_every_query_is_logged_append_only(built):
    ret, meta, _ = make(built, approve=SOURCES)
    n = len(meta.query_log())
    ret.retrieve("Yetkinlik nedir?")
    ret.retrieve("")
    log = meta.query_log()
    assert [e["sonuc"] for e in log[n:]][1] == "rejected" and len(log) == n + 2
    assert [e["sira"] for e in log] == list(range(len(log))) and isinstance(meta, MetadataStore)


# ---- Ölçüler ----------------------------------------------------------------------------------------------------------


def test_recall_and_mrr_hand_calculated():
    assert recall_at_k(["x", "a", "a", "b"], ["a", "b"]) == 1.0 and reciprocal_rank(["x", "a", "a", "b"], ["a", "b"]) == 0.5
    assert recall_at_k(["x"], ["a"]) == 0.0 and reciprocal_rank(["x"], ["a"]) == 0.0
    assert recall_at_k([], []) is None and reciprocal_rank([], []) is None
    assert recall_at_k(["x"] * 4 + ["a"], ["a"]) == 1.0 and reciprocal_rank(["x"] * 4 + ["a"], ["a"]) == 0.5  # tekrar sayılmaz
    assert reciprocal_rank([str(i) for i in range(10)] + ["a"], ["a"]) == 0.0
    assert recall_at_k(["a", "1", "2", "3", "4", "b"], ["a", "b"]) == 0.5


def test_summary_excludes_unanswerable_from_recall_and_reports_abstention():
    cases = [CaseOutcome("q1", True, ("a", "b"), ("a",), False), CaseOutcome("q2", True, ("x", "c"), ("c",), True),
             CaseOutcome("q3", False, ("x",), (), True), CaseOutcome("q4", False, (), (), False)]  # fmt: skip
    s = summarize(cases)
    assert (s.recall_at_5, s.mrr_at_10, s.abstention_recall, s.false_abstention) == (1.0, 0.75, 0.5, 0.5)
    assert (s.n_answerable, s.n_unanswerable) == (2, 2)


# ---- Dev kalibrasyonu ve mühürlü test kümesi -----------------------------------------------------------------------


def test_calibration_uses_dev_only_and_respects_false_abstention_cap():
    obs = [DevObservation(f"a{i}", "dev", True, d) for i, d in enumerate((0.10, 0.20, 0.30, 0.40, 0.60, 0.70, 0.50))]
    obs += [DevObservation(f"u{i}", "dev", False, d) for i, d in enumerate((0.55, 0.65, 0.80, 0.90, None))]
    cal = calibrate(obs)
    assert cal.reason == "ok" and cal.max_distance == 0.60 and cal.false_abstention <= 0.15
    assert cal.abstention_recall == 0.8
    with pytest.raises(ValueError):
        calibrate(obs + [DevObservation("t1", "test", True, 0.1)])
    assert calibrate([DevObservation("a", "dev", True, 0.1)]).reason == "dev_kumesi_yetersiz"


def test_dev_loader_refuses_test_split_and_maps_gold_by_span(tmp_path):
    dev = tmp_path / "dev.jsonl"
    case = {"case_key": "D1", "split": "dev", "category": "answerable", "question": "Yetkinlik nedir?",
            "as_of": "2026-11-20T12:00:00Z", "answerable": True,
            "gold": [{"doc_uid": "SYN-YET-GECERLILIK@2.0", "char_start": 0, "char_end": 400}]}  # fmt: skip
    dev.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    ds = load_dev_set(dev)
    assert len(ds.cases) == 1 and len(ds.sha256) == 64
    leak = tmp_path / "karisik.jsonl"
    leak.write_text(json.dumps({**case, "split": "test"}) + "\n", encoding="utf-8")
    with pytest.raises(SealedSplitError):
        load_dev_set(leak)
    with pytest.raises(SealedSplitError):
        load_dev_set(tmp_path / "test.jsonl")
    from medakis.rag.ingestion.corpus_loader import load_corpus

    parts = [c.map_entry() for c in chunk_corpus(load_corpus(H.CORPUS).documents)]
    ids = gold_chunk_ids([GoldRef("SYN-YET-GECERLILIK@2.0", 0, 400)], parts)
    assert ids and all(p["doc_uid"] == "SYN-YET-GECERLILIK@2.0" for p in parts if p["chunk_id"] in ids)


def test_no_test_split_unsealing_code_in_rag():
    text = "\n".join(p.read_text(encoding="utf-8") for p in (H.ROOT / "src" / "medakis" / "rag").rglob("*.py"))
    assert "load_test_set" not in text and "muhur_ac" not in text  # test kümesini açan kod Aşama 9'a kadar yok
