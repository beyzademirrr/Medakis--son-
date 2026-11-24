"""Model başına belge/sorgu önekleri (indeks kimliğine girer; değişirse yeni indeks gerekir).

Bilinmeyen model için varsayılan önek UYDURULMAZ: yapılandırma hatası verilir.
- qwen3-embedding: model kartındaki `get_detailed_instruct` biçimi ("Instruct: {görev}\\nQuery:{sorgu}"); belgeler
  öneksiz. Kart sürümü 7.6'da model digest'iyle birlikte yeniden doğrulanacak.
- bge-m3: yalnız dense kullanım; talimat öneki yok. Sparse/çok vektörlü arama uygulanmış sayılmaz.
- paraphrase-multilingual: önek yok (hafif yedek; kısa bağlam).
"""

from __future__ import annotations

from dataclasses import dataclass

from medakis.rag.errors import EmbeddingUnavailable

QWEN_TASK = "Given a web search query, retrieve relevant passages that answer the query"


@dataclass(frozen=True)
class Prefixes:
    document: str
    query: str
    source: str


PREFIXES: dict[str, Prefixes] = {
    "qwen3-embedding:0.6b": Prefixes("", f"Instruct: {QWEN_TASK}\nQuery:", "Qwen3-Embedding model kartı, get_detailed_instruct (7.6'da yeniden doğrulanacak)"),
    "bge-m3": Prefixes("", "", "BGE-M3 dense; talimat öneki yok"),
    "paraphrase-multilingual:278m": Prefixes("", "", "paraphrase-multilingual; önek yok"),
}


def prefixes_for(model: str) -> Prefixes:
    if model not in PREFIXES:
        raise EmbeddingUnavailable("onek_yapilandirmasi_yok", model)
    return PREFIXES[model]
