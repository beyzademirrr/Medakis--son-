"""Aşama 7.2: güvenli parçalama, SHA-256, Chroma indeksleme ve idempotency (ağsız; sahte vektör, gerçek Chroma)."""

import gc

import pytest
import rag_helpers as H

from medakis.rag.chunking.chunker import ChunkConfig, chunk_corpus, chunk_document, corpus_sha256
from medakis.rag.chunking.markdown_sections import parse_sections
from medakis.rag.chunking.token_counter import ApproxTokenCounter
from medakis.rag.embeddings.fake_provider import FakeEmbedder
from medakis.rag.errors import ChunkingError, IndexError_
from medakis.rag.ingestion.corpus_loader import LoadedDocument, load_corpus
from medakis.rag.vector_store.chroma_store import ChromaVectorStore
from medakis.rag.vector_store.index_builder import build_index, cleanup_retired
from medakis.rag.vector_store.index_identity import collection_name
from medakis.rag.vector_store.index_registry import FAILED, READY, RETIRED, IndexRegistry

CORPUS = load_corpus(H.CORPUS)
CHUNKS = chunk_corpus(CORPUS.documents)
CFG = ChunkConfig()


@pytest.fixture()
def env():
    path = H.d_tmp()
    store = ChromaVectorStore(path)
    yield store, IndexRegistry(path), path
    store.close()
    del store
    gc.collect()
    H.remove(path)


def _doc(text, key="SYN-X"):
    e, _ = H.entry(key, text=text)
    from medakis.rag.ingestion.manifest import parse_entry

    return LoadedDocument(parse_entry(e)[0], text)


# ---- Bölümler ve parçalama ------------------------------------------------------------------------------------


def test_sections_keep_boundaries_and_exact_offsets():
    text = "# Sentetik başlık\n\nGiriş metni.\n\n## Bir\n\nBirinci bölüm.\n\n## İki\n\n### Alt\n\nAlt bölüm metni.\n"
    title, secs = parse_sections(text)
    assert title == "Sentetik başlık" and [s.name for s in secs] == ["Giriş", "Bir", "İki > Alt"]
    assert [text[s.start:s.end] for s in secs] == ["Giriş metni.", "Birinci bölüm.", "Alt bölüm metni."]


@pytest.mark.parametrize("text, code", [
    ("Başlıksız metin\n", "belge_basligi_yok"),
    ("# Sentetik a\n\n## Boş\n\n## Dolu\n\nMetin.\n", "bos_bolum"),
    ("# Sentetik a\n\n### Alt\n\nMetin.\n", "alt_bolum_ust_bolumsuz"),
    ("# Sentetik a\n\n# İkinci başlık\n\nMetin.\n", "birden_cok_belge_basligi"),
])  # fmt: skip
def test_malformed_documents_raise(text, code):
    with pytest.raises(ChunkingError, match=code):
        parse_sections(text)


def test_real_corpus_chunks_are_exact_substrings_within_sections():
    docs = CORPUS.by_uid()
    assert len(CHUNKS) >= 50
    for c in CHUNKS:
        text = docs[c.doc_uid].text
        assert text[c.char_start:c.char_end] == c.text and c.est_tokens <= CFG.target_tokens
        assert c.embed_text.endswith(c.text) and c.section in c.embed_text  # başlık ve bölüm embed metnine dahil
        sec = next(s for s in parse_sections(text)[1] if s.name == c.section)
        assert sec.start <= c.char_start and c.char_end <= sec.end  # bölüm sınırı aşılmaz


def test_chunking_is_deterministic_and_hash_tracked():
    again = chunk_corpus(load_corpus(H.CORPUS).documents)
    assert [c.chunk_id for c in again] == [c.chunk_id for c in CHUNKS]
    assert corpus_sha256(again) == corpus_sha256(CHUNKS) and len({c.chunk_id for c in CHUNKS}) == len(CHUNKS)


def test_long_section_split_with_bounded_overlap_and_full_coverage():
    sentences = [f"Cümle {i} sentetik devir ve görev açıklaması içerir." for i in range(80)]
    text = "# Sentetik uzun belge\n\n## Uzun bölüm\n\n" + " ".join(sentences) + "\n"
    cfg = ChunkConfig(target_tokens=120, overlap_tokens=40, max_input_tokens=200)
    chunks = chunk_document(_doc(text), cfg)
    counter = ApproxTokenCounter()
    assert len(chunks) > 3 and all(c.est_tokens <= 120 for c in chunks)
    for a, b in zip(chunks, chunks[1:]):
        assert b.char_start > a.char_start and b.char_end > a.char_end  # ilerleme var, yinelenen parça yok
        if b.char_start < a.char_end:
            assert counter.count(text[b.char_start:a.char_end]) <= 40  # örtüşme sınırı
    covered = set()
    for c in chunks:
        covered.update(range(c.char_start, c.char_end))
    assert all(text.index(s) in covered for s in sentences)  # hiçbir cümle kaybolmaz


def test_overlong_word_is_an_error_not_silent_truncation():
    text = "# Sentetik a\n\n## Bölüm\n\n" + "x" * 2000 + "\n"
    with pytest.raises(ChunkingError, match="sozcuk_cok_uzun"):
        chunk_document(_doc(text), ChunkConfig(target_tokens=100, overlap_tokens=10, max_input_tokens=200))


# ---- İndeks: building → ready ve idempotency --------------------------------------------------------------------


def test_build_ready_then_idempotent_rerun(env):
    store, registry, _ = env
    emb = FakeEmbedder()
    first = build_index(CHUNKS, CFG, emb, store, registry)
    assert first.status == READY and first.vector_count == len(CHUNKS) == first.embedded_now
    assert len(emb.calls) == len(CHUNKS)  # parça başına TEK istek (kesin token sayımı için)
    second = build_index(CHUNKS, CFG, emb, store, registry)
    assert (second.index_key, second.collection, second.embedded_now) == (first.index_key, first.collection, 0)
    assert store.names() == [first.collection] and registry.ready_keys() == [first.index_key]
    col = store.open(first.collection, first.index_key)
    assert col.count() == len(CHUNKS)  # yinelenen vektör yok


def test_different_model_never_shares_collection(env):
    store, registry, _ = env
    a = build_index(CHUNKS, CFG, FakeEmbedder("model-a"), store, registry)
    b = build_index(CHUNKS, CFG, FakeEmbedder("model-b"), store, registry)  # aynı boyut, farklı model
    c = build_index(CHUNKS, CFG, FakeEmbedder("model-a", digest="1" * 64), store, registry)  # aynı ad, farklı digest
    assert len({a.collection, b.collection, c.collection}) == 3
    assert registry.state(a.index_key) == RETIRED and registry.state(b.index_key) == READY  # a'nın yerine c geçti


def test_dimension_mismatch_fails_index(env):
    store, registry, _ = env

    class Wrong(FakeEmbedder):
        def embed_one(self, text):
            v, n = super().embed_one(text)
            return v[:-1], n

    with pytest.raises(IndexError_, match="boyut_uyusmuyor"):
        build_index(CHUNKS, CFG, Wrong(), store, registry)
    key = registry.events()[-1]["index_key"]
    assert registry.state(key) == FAILED and registry.ready_keys() == []


def test_measured_token_overflow_fails_without_truncation(env):
    store, registry, _ = env
    with pytest.raises(ChunkingError, match="parca_token_siniri_asildi"):
        build_index(CHUNKS, CFG, FakeEmbedder(token_scale=50.0), store, registry)
    assert registry.ready_keys() == []


def test_half_write_recovers_and_only_embeds_missing(env):
    store, registry, _ = env

    class Crash(FakeEmbedder):
        def embed_one(self, text):
            if len(self.calls) >= 10:
                raise IndexError_("yapay_kesinti")
            return super().embed_one(text)

    with pytest.raises(IndexError_):
        build_index(CHUNKS, CFG, Crash(), store, registry)
    key = registry.events()[-1]["index_key"]
    assert registry.state(key) == FAILED and registry.ready_keys() == []  # yarım indeks sorgulanamaz
    emb = FakeEmbedder()
    done = build_index(CHUNKS, CFG, emb, store, registry)
    assert done.index_key == key and done.status == READY and done.embedded_now == len(CHUNKS) - 10


def test_stale_vector_removed_on_rebuild(env):
    store, registry, _ = env
    emb = FakeEmbedder()
    with pytest.raises(ChunkingError):
        build_index(CHUNKS, CFG, FakeEmbedder(token_scale=50.0), store, registry)
    key = registry.events()[-1]["index_key"]
    col = store.open(collection_name(key), key)
    store.upsert(col, ["sahte-eski"], [emb.vector("eski")], ["eski metin"], [{"index_key": key, "doc_uid": "SYN-ESKI@1.0"}])
    done = build_index(CHUNKS, CFG, emb, store, registry)
    assert done.status == READY and "sahte-eski" not in store.snapshot(store.open(done.collection, key))


def test_tampered_ready_index_is_not_silently_rebuilt(env):
    store, registry, _ = env
    done = build_index(CHUNKS, CFG, FakeEmbedder(), store, registry)
    col = store.open(done.collection, done.index_key)
    first = CHUNKS[0]
    store.upsert(col, [first.chunk_id], [FakeEmbedder().vector("x")], ["değiştirilmiş metin"], [col.get(ids=[first.chunk_id])["metadatas"][0]])
    with pytest.raises(IndexError_, match="hazir_indeks_dogrulanamadi"):
        build_index(CHUNKS, CFG, FakeEmbedder(), store, registry)


def test_manifest_is_immutable_and_failed_building_not_queryable(env):
    store, registry, _ = env
    done = build_index(CHUNKS, CFG, FakeEmbedder(), store, registry)
    with pytest.raises(IndexError_, match="degismez"):
        registry.write_manifest(done.index_key, {"oynanmis": True})
    with pytest.raises(IndexError_, match="gecersiz_indeks_gecisi"):
        registry.record(done.index_key, "building", H.T0)  # hazır indeks yeniden building yapılamaz


def test_retired_collections_cleaned_only_after_new_ready(env):
    store, registry, _ = env
    old = build_index(CHUNKS, CFG, FakeEmbedder(digest="a" * 64), store, registry)
    assert cleanup_retired(store, registry) == []  # hazır indeks silinmez
    new = build_index(CHUNKS, CFG, FakeEmbedder(digest="b" * 64), store, registry)
    assert registry.state(old.index_key) == RETIRED and old.collection in store.names()  # önce yeni hazır, eski duruyor
    assert cleanup_retired(store, registry) == [old.collection] and store.names() == [new.collection]


def test_threshold_only_from_dev_on_ready_index(env):
    store, registry, _ = env
    done = build_index(CHUNKS, CFG, FakeEmbedder(), store, registry)
    with pytest.raises(IndexError_, match="yalniz_dev"):
        registry.set_threshold(done.index_key, 0.5, H.T0, {"kume": "test"})
    registry.set_threshold(done.index_key, 0.42, H.T0, {"kume": "dev", "set_sha256": "x"})
    assert registry.threshold(done.index_key) == 0.42 and registry.state(done.index_key) == READY


# ---- Chroma güvenlik ayarları ---------------------------------------------------------------------------------


def test_chroma_never_runs_default_embedding_and_telemetry_off(env, monkeypatch):
    import chromadb.utils.embedding_functions as ef

    def boom(self, input):  # noqa: A002 - Chroma arayüz imzası
        raise AssertionError("varsayılan embedding çağrıldı")

    for name in ("DefaultEmbeddingFunction", "ONNXMiniLM_L6_V2"):
        if hasattr(ef, name):
            monkeypatch.setattr(getattr(ef, name), "__call__", boom)
    store, registry, _ = env
    done = build_index(CHUNKS, CFG, FakeEmbedder(), store, registry)
    col = store.open(done.collection, done.index_key)
    assert store.query(col, FakeEmbedder().vector("devir"), 3, [CHUNKS[0].doc_uid])
    assert store._client.get_settings().anonymized_telemetry is False
    assert col.configuration["hnsw"]["space"] == "cosine" and col.configuration["embedding_function"] is None
    with pytest.raises(ValueError):
        col.add(ids=["z"], documents=["metin"])  # vektörsüz ekleme reddedilir: gizli model çağrısı yok
