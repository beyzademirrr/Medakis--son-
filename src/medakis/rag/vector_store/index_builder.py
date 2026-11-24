"""İndeks oluşturma: building → doğrulama → ready (Aşama 7.2).

Kayıt (dosya; ileride PostgreSQL) ile Chroma arasında atomik işlem yoktur. Protokol:
1. Kimlik manifesti yazılır (değişmez), durum `building`.
2. Her parça TEK istekle gömülür (kesin token sayısı için); sayı `max_input_tokens`'ı aşarsa indeks `failed`.
   Vektör boyutu kimlikteki boyutla aynı olmalıdır. Vektörler deterministik kimliklerle upsert edilir.
3. Doğrulama: kimlik kümesi tam eşit, her vektörün metadata hash'i ve metninin SHA-256'sı parça eşlemesiyle aynı,
   boyutlar doğru. Geçerse `ready`, geçmezse `failed`.
4. Aynı modelin önceki `ready` indeksleri ancak yeni indeks `ready` olduktan sonra `retired` yapılır.
İdempotency: aynı girdi aynı kimliği ve koleksiyonu üretir; `ready` indeks yeniden kurulmaz, yarım kalan
`building` ya da `failed` indeks kaldığı yerden tamamlanır (doğru hash'li vektörler yeniden gömülmez).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from medakis.rag.chunking.chunker import Chunk, ChunkConfig, corpus_sha256, sha256_text
from medakis.rag.contracts.providers import MeasuredEmbedder
from medakis.rag.errors import ChunkingError, IndexError_, RagError
from medakis.rag.vector_store.chroma_store import ChromaVectorStore
from medakis.rag.vector_store.index_identity import collection_name, identity_payload, index_key
from medakis.rag.vector_store.index_registry import BUILDING, FAILED, READY, RETIRED, IndexRegistry

UPSERT_BATCH = 16  # gömme parça başına tek istektir; Chroma'ya yazım küçük gruplar hâlinde yapılır


@dataclass(frozen=True)
class BuildResult:
    index_key: str
    collection: str
    status: str
    vector_count: int
    embedded_now: int  # bu çalıştırmada gömülen parça sayısı (yeniden çalıştırmada 0 olmalı)
    retired: tuple[str, ...] = ()


def _metadata(c: Chunk, key: str, tokens: int) -> dict:
    return {"index_key": key, "chunk_id": c.chunk_id, "doc_uid": c.doc_uid, "source_key": c.source_key, "version": c.version,
            "ordinal": c.ordinal, "section": c.section, "char_start": c.char_start, "char_end": c.char_end,
            "chunk_sha256": c.sha256, "embed_sha256": c.embed_sha256, "token_count": tokens}  # fmt: skip


def verify_collection(store: ChromaVectorStore, col, mapping: list[dict], key: str, dims: int) -> list[str]:
    """Doğrulama sorunları (boş liste = geçti)."""
    snap = store.snapshot(col, with_vectors=True)
    expected = {m["chunk_id"]: m for m in mapping}
    problems = []
    if set(snap) != set(expected):
        problems.append(f"kimlik_kumesi_uyusmuyor:eksik={len(set(expected) - set(snap))},fazla={len(set(snap) - set(expected))}")
    for vid, item in snap.items():
        m, md = expected.get(vid), item["metadata"]
        if m is None:
            continue
        if md.get("chunk_sha256") != m["sha256"] or sha256_text(item["document"]) != m["sha256"]:
            problems.append(f"hash_uyusmuyor:{vid}")
        if md.get("index_key") != key or md.get("doc_uid") != m["doc_uid"] or md.get("char_start") != m["char_start"]:
            problems.append(f"metadata_uyusmuyor:{vid}")
        if item["dims"] != dims:
            problems.append(f"boyut_uyusmuyor:{vid}")
    return problems


def build_index(chunks: list[Chunk], config: ChunkConfig, embedder: MeasuredEmbedder, store: ChromaVectorStore,
                registry: IndexRegistry, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> BuildResult:  # fmt: skip
    if not chunks:
        raise IndexError_("parca_yok")
    info = embedder.describe()
    corpus = corpus_sha256(chunks)
    key = index_key(info, config, corpus)
    name = collection_name(key)
    mapping = [c.map_entry() for c in sorted(chunks, key=lambda c: (c.doc_uid, c.ordinal))]
    if registry.state(key) == READY:
        col = store.open(name, key)
        if verify_collection(store, col, mapping, key, info.dimensions):
            raise IndexError_("hazir_indeks_dogrulanamadi", key)  # sessizce yeniden kurulmaz; ayrı inceleme gerekir
        return BuildResult(key, name, READY, len(mapping), 0)
    if registry.state(key) == RETIRED:
        raise IndexError_("emekli_indeks_yeniden_kurulmaz", key)
    registry.write_manifest(key, {**identity_payload(info, config, corpus), "koleksiyon": name, "parcalar": mapping})
    registry.record(key, BUILDING, now())
    embedded = 0
    tokens: dict[str, int] = {}
    pending: list[tuple[str, list[float], str, dict]] = []
    col = None

    def flush() -> None:
        if pending and col is not None:
            ids, vecs, docs, metas = (list(x) for x in zip(*pending))
            store.upsert(col, ids, vecs, docs, metas)
            pending.clear()

    try:
        col = store.open(name, key, create=True)
        existing = store.snapshot(col)
        stale = sorted(set(existing) - {m["chunk_id"] for m in mapping})
        if stale:  # bu koleksiyon yalnız bu kimliğe aittir: eşlemede olmayan vektör geçersizdir ve silinir
            store.delete_ids(col, stale)
        for c in chunks:
            have = existing.get(c.chunk_id)
            if have and have["metadata"].get("chunk_sha256") == c.sha256 and sha256_text(have["document"]) == c.sha256:
                tokens[c.chunk_id] = int(have["metadata"].get("token_count", 0))
                continue
            vector, count = embedder.embed_one(info.doc_prefix + c.embed_text)
            if count > config.max_input_tokens:
                raise ChunkingError("parca_token_siniri_asildi", f"{c.chunk_id}:{count}>{config.max_input_tokens}")
            if len(vector) != info.dimensions:
                raise IndexError_("boyut_uyusmuyor", f"{len(vector)}!={info.dimensions}")
            pending.append((c.chunk_id, vector, c.text, _metadata(c, key, count)))
            tokens[c.chunk_id] = count
            embedded += 1
            if len(pending) >= UPSERT_BATCH:
                flush()
        flush()
        problems = verify_collection(store, col, mapping, key, info.dimensions)
        if problems:
            raise IndexError_("dogrulama_basarisiz", ";".join(problems[:5]))
    except RagError as exc:
        try:
            flush()  # gömülmüş geçerli vektörler korunur: yeniden çalıştırmada tekrar gömülmez
        except Exception:  # noqa: BLE001
            pass
        registry.record(key, FAILED, now(), hata=exc.code)
        raise
    registry.record(key, READY, now(), vektor_sayisi=len(mapping), token_en_fazla=max(tokens.values()),
                    token_toplam=sum(tokens.values()), model=info.model, digest=info.digest)  # fmt: skip
    retired = []
    for old in registry.ready_keys(info.model):
        if old != key:
            registry.record(old, RETIRED, now(), yerine=key)
            retired.append(old)
    return BuildResult(key, name, READY, len(mapping), embedded, tuple(retired))


def cleanup_retired(store: ChromaVectorStore, registry: IndexRegistry) -> list[str]:
    """Emekli indekslerin koleksiyonlarını siler (eski vektör temizliği). Hazır indekslere dokunmaz."""
    removed = []
    ready = {collection_name(k) for k in registry.ready_keys()}
    for key in registry.keys_in(RETIRED):
        name = collection_name(key)
        if name not in ready and name in store.names():
            store.delete(name)
            removed.append(name)
    return removed
