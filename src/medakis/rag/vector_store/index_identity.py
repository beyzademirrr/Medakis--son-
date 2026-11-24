"""İndeks kimliği: model, digest, boyut, uzaklık, önekler, parçalama yapılandırması ve corpus özeti.

Aynı boyutta olsalar bile farklı modellerin vektörleri aynı koleksiyona hiçbir zaman karışmaz: koleksiyon adı
bu kimliğin SHA-256'sından türetilir.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from medakis.rag.chunking.chunker import ChunkConfig
from medakis.rag.contracts.providers import EmbedderInfo

DISTANCE = "cosine"


def identity_payload(info: EmbedderInfo, chunk_config: ChunkConfig, corpus_sha: str) -> dict:
    return {"embedding": asdict(info), "uzaklik": DISTANCE, "parcalama": chunk_config.as_dict(), "corpus_sha256": corpus_sha}


def index_key(info: EmbedderInfo, chunk_config: ChunkConfig, corpus_sha: str) -> str:
    payload = identity_payload(info, chunk_config, corpus_sha)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def collection_name(key: str) -> str:
    return f"medakis-{key[:40]}"
