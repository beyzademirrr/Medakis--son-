"""Dar sağlayıcı arayüzleri (taslak).

Bilerek dar tutulmuştur: sağlayıcılar yalnız metin alır ve vektör/metin döndürür.
Araç çağrısı, SQL, dosya veya shell parametresi yoktur. Gerçek Ollama
uygulamaları Aşama 7-8'de eklenecektir; birim testleri sahte sağlayıcı kullanır.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Metinleri dense vektöre çevirir (ör. Ollama /api/embed, truncate=false)."""

    @property
    def model_name(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class GenerationProvider(Protocol):
    """Yalnız verilen sistem mesajı ve kaynaklı istemden metin üretir."""

    @property
    def model_name(self) -> str: ...

    def generate(self, system: str, prompt: str) -> str: ...


@dataclass(frozen=True)
class EmbedderInfo:
    """İndeks kimliğine giren model bilgisi (Ollama: /api/tags digest, /api/show boyut ve bağlam)."""

    model: str
    digest: str
    dimensions: int
    context_length: int | None
    num_ctx: int
    doc_prefix: str
    query_prefix: str
    prefix_source: str


@runtime_checkable
class MeasuredEmbedder(Protocol):
    """Aşama 7: tek metin → (vektör, modelin KESİN token sayısı). Toplu istekte sayı toplam döndüğü için
    parça başına tek istek yapılır; truncate=false; bağlam aşımı hatadır (sessiz kesme yok)."""

    def describe(self) -> EmbedderInfo: ...

    def embed_one(self, text: str) -> tuple[list[float], int]: ...
