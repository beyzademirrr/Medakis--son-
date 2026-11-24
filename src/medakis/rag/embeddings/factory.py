"""Profil → embedding istemcisi. Değerler `config/rag_profiles.json` ortak ayarlarından okunur.

Bu fonksiyon ağ çağrısı yapmaz ve model indirmez; istemci ancak kullanıldığında yerel Ollama'ya bağlanır.
"""

from __future__ import annotations

from typing import Any

import httpx

from medakis.rag.embeddings.ollama_client import OllamaEmbedder
from medakis.rag.errors import EmbeddingUnavailable

EMBED_NUM_CTX = 2048  # gömme bağlamı; modelin bağlamından büyükse modelinkiyle sınırlanır


def embedder_for_profile(profiles: dict[str, Any], profile: str, transport: httpx.BaseTransport | None = None) -> OllamaEmbedder:
    prof = profiles.get("profiles", {}).get(profile)
    if not prof or not prof.get("embed"):
        raise EmbeddingUnavailable("profil_yok", profile)
    common = profiles.get("common", {})
    if common.get("distance") != "cosine":
        raise EmbeddingUnavailable("uzaklik_cosine_degil")
    return OllamaEmbedder(prof["embed"], common.get("ollama_base_url", "http://127.0.0.1:11434"), EMBED_NUM_CTX,
                          float(common.get("timeout_seconds", 120)), transport)  # fmt: skip
