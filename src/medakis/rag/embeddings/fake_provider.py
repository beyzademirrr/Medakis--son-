"""Ağsız, deterministik sahte embedding sağlayıcısı (yalnız testler ve geliştirme için).

Sözcük özetlerinden oluşan normalleştirilmiş bir torba vektörü üretir. Model kalitesi hakkında HİÇBİR kanıt
sağlamaz; yalnız boru hattının (parçalama, indeks, filtre, doğrulama, kaçınma) davranışını sınar.
"""

from __future__ import annotations

import hashlib
import math
import re

from medakis.rag.chunking.token_counter import ApproxTokenCounter
from medakis.rag.contracts.providers import EmbedderInfo

_WORD = re.compile(r"\w+", re.UNICODE)


def _stem(word: str) -> str:
    return word.casefold()[:5]  # kaba Türkçe kök yaklaşımı: ek farklılıklarını azaltır


class FakeEmbedder:
    def __init__(self, model: str = "sahte-embed", digest: str = "0" * 64, dims: int = 64, token_scale: float = 1.0,
                 query_prefix: str = "", doc_prefix: str = "") -> None:  # fmt: skip
        self.info = EmbedderInfo(model, digest, dims, 2048, 2048, doc_prefix, query_prefix, "sahte")
        self.token_scale = token_scale
        self.calls: list[str] = []
        self._counter = ApproxTokenCounter()

    def describe(self) -> EmbedderInfo:
        return self.info

    def vector(self, text: str) -> list[float]:
        v = [0.0] * self.info.dimensions
        for w in _WORD.findall(text):
            h = hashlib.sha256(_stem(w).encode()).digest()
            v[h[0] % self.info.dimensions] += 1.0 if h[1] % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_one(self, text: str) -> tuple[list[float], int]:
        self.calls.append(text)
        return self.vector(text), math.ceil(self._counter.count(text) * self.token_scale)
