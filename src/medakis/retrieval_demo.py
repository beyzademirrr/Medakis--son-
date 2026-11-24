"""Kaynaklı bilgi asistanı — anahtar kelime (BM25) tabanlı sınırlı demo arama.

Bu arama embedding ya da dil modeli KULLANMAZ. Yalnız kontrollü corpus'taki "kaynak" amaçlı belgelerde (güvenlik
fikstürleri hariç) Türkçe BM25 ile bölüm bulur ve bölüm metnini birebir alıntılar. Skor eşiğin altındaysa kaynak
bulunamadı der. BGE-M3 + reranker + Gemma hattı ayrı geliştirme kipindedir ve normal kullanıcıya kapalıdır.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from medakis.rag.chunking.chunker import chunk_corpus
from medakis.rag.ingestion.corpus_loader import load_corpus
from medakis.rag.retrieval.lexical import BM25, LexDoc

CORPUS = Path(__file__).resolve().parents[2] / "corpus" / "kontrollu"
MIN_SCORE = 4.0  # sabit; altında "kaynak bulunamadı"


@lru_cache(maxsize=1)
def _index():
    corpus = load_corpus(CORPUS)
    docs = [d for d in corpus.documents if d.record.purpose == "kaynak" and not d.record.source_key.startswith("SYN-FKS")]
    chunks = chunk_corpus(docs)
    meta = {c.chunk_id: c for c in chunks}
    return BM25([LexDoc(c.chunk_id, c.text) for c in chunks], prefix=5), meta, {d.record.uid: d.record for d in docs}


def search_sources(question: str, k: int = 3) -> dict:
    bm, meta, recs = _index()
    hits = [(cid, s) for cid, s in bm.scores(question)[:k] if s >= MIN_SCORE]
    if not hits:
        return {"bulundu": False, "mesaj": "Bu soruya kaynak belgelerde dayanak bulunamadı; cevap üretilmedi.", "kaynaklar": []}
    out = []
    for cid, s in hits:
        c = meta[cid]
        r = recs[c.doc_uid]
        out.append({"belge": r.source_name, "kimlik": r.source_key, "surum": r.version, "bolum": c.section, "skor": round(s, 2), "alinti": c.text})
    return {"bulundu": True, "yontem": "BM25 anahtar kelime araması (embedding/LLM yok)", "kaynaklar": out}
