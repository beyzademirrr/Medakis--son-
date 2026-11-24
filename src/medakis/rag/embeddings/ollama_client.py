"""Yerel Ollama embedding istemcisi (Aşama 7.3).

- Yalnız loopback (127.0.0.1, localhost, ::1) üzerinden HTTP; ortam vekil (proxy) ayarları yok sayılır.
- Yalnız şu uç noktalar: /api/version, /api/tags, /api/ps (salt okunur), /api/show, /api/embed. Model indirme/oluşturma/silme uç noktası
  bu modülde YOKTUR; model kurulu değilse açık hata verilir (indirme proje sahibinin onayına bağlıdır).
- /api/embed: tek girdi (prompt_eval_count toplu istekte toplam döner), truncate=false (bağlam aşımı hata; sessiz
  kesme yok), keep_alive=0 (varsayılan), num_ctx açık. Zaman aşımı ve bellek hatasında en fazla bir yeniden deneme.
"""

from __future__ import annotations

import math
import time
from typing import Any

import httpx

from medakis.rag.contracts.providers import EmbedderInfo
from medakis.rag.embeddings.prefixes import prefixes_for
from medakis.rag.errors import EmbeddingUnavailable

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED = frozenset({("GET", "/api/version"), ("GET", "/api/tags"), ("GET", "/api/ps"), ("POST", "/api/show"), ("POST", "/api/embed")})


def check_base_url(url: str) -> httpx.URL:
    parsed = httpx.URL(url)
    if parsed.scheme != "http" or parsed.host not in LOOPBACK or parsed.userinfo or parsed.path not in ("", "/") or parsed.query:
        raise EmbeddingUnavailable("ollama_adresi_yerel_degil", str(url))
    return parsed


class OllamaEmbedder:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", num_ctx: int = 2048, timeout_s: float = 120.0,
                 transport: httpx.BaseTransport | None = None, keep_alive: int | str = 0) -> None:  # fmt: skip
        self.model = model
        # Varsayılan 0: sorgudan sonra model bellekten çıkar (üretici modele yer açılır). Çevrimdışı indeksleme ve
        # sıcak gecikme ölçümü yalnız açıkça daha uzun bir değer verir.
        self.keep_alive = keep_alive
        self.prefixes = prefixes_for(model)
        self.base = check_base_url(base_url)
        self.requested_ctx = num_ctx
        self.timeout_s = timeout_s  # KM31: yeniden deneme DAHİL tek çağrının toplam bütçesi
        self._client = httpx.Client(base_url=str(self.base), timeout=timeout_s, trust_env=False, transport=transport)
        self._info: EmbedderInfo | None = None

    def close(self) -> None:
        self._client.close()

    # ---- HTTP --------------------------------------------------------------------------------------------

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if (method, path) not in ALLOWED:
            raise EmbeddingUnavailable("izinsiz_uc_nokta", path)
        deadline = time.monotonic() + self.timeout_s
        last: EmbeddingUnavailable | None = None
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:  # yeniden deneme toplam bütçeyi aşamaz
                raise EmbeddingUnavailable("zaman_asimi", "toplam_butce_asildi")
            try:
                resp = self._client.request(method, path, json=body, timeout=min(self.timeout_s, remaining))
            except httpx.ConnectError:
                raise EmbeddingUnavailable("ollama_kapali") from None
            except httpx.TimeoutException:
                last = EmbeddingUnavailable("zaman_asimi")
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    raise EmbeddingUnavailable("yanit_gecersiz") from None
                if not isinstance(data, dict):
                    raise EmbeddingUnavailable("yanit_gecersiz")
                return data
            text = resp.text.lower()
            if "context length" in text:
                raise EmbeddingUnavailable("baglam_asildi")
            if resp.status_code == 404 or "not found" in text:
                raise EmbeddingUnavailable("model_yok", f"{self.model} kurulu değil; indirme proje sahibinin onayına bağlıdır")
            if "memory" in text:
                last = EmbeddingUnavailable("bellek_yetersiz")
                continue
            raise EmbeddingUnavailable("ollama_hatasi", str(resp.status_code))
        assert last is not None
        raise last

    # ---- Sağlayıcı arayüzü --------------------------------------------------------------------------------

    def unload(self) -> None:
        """Modeli bellekten çıkarır: boş girdi ve keep_alive=0 (0.40.17'de doğrulandı). Gömme üretmez."""
        self._call("POST", "/api/embed", {"model": self.model, "input": [], "keep_alive": 0})

    def loaded_models(self) -> list[dict[str, Any]]:
        """Bellekte yüklü modeller (salt okunur /api/ps): soğuk ölçümden önce boşaltmayı doğrulamak için."""
        return list(self._call("GET", "/api/ps").get("models", []))

    def version(self) -> str:
        return str(self._call("GET", "/api/version").get("version", ""))

    def describe(self) -> EmbedderInfo:
        tags = self._call("GET", "/api/tags").get("models", [])
        names = {self.model, f"{self.model}:latest"}
        entry = next((m for m in tags if m.get("name") in names or m.get("model") in names), None)
        if entry is None or not entry.get("digest"):
            raise EmbeddingUnavailable("model_yok", f"{self.model} kurulu değil; indirme proje sahibinin onayına bağlıdır")
        info = self._call("POST", "/api/show", {"model": self.model}).get("model_info", {})
        dims = next((v for k, v in info.items() if k.endswith(".embedding_length")), None)
        ctx = next((v for k, v in info.items() if k.endswith(".context_length")), None)
        if not isinstance(dims, int) or dims <= 0:
            raise EmbeddingUnavailable("boyut_bilinmiyor")
        num_ctx = min(self.requested_ctx, ctx) if isinstance(ctx, int) and ctx > 0 else self.requested_ctx
        self._info = EmbedderInfo(self.model, str(entry["digest"]), dims, ctx if isinstance(ctx, int) else None, num_ctx,
                                  self.prefixes.document, self.prefixes.query, self.prefixes.source)  # fmt: skip
        return self._info

    def embed_one(self, text: str) -> tuple[list[float], int]:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingUnavailable("bos_girdi")
        info = self._info or self.describe()
        data = self._call("POST", "/api/embed", {"model": self.model, "input": [text], "truncate": False, "keep_alive": self.keep_alive,
                                                 "options": {"num_ctx": info.num_ctx}})  # fmt: skip
        vectors, count = data.get("embeddings"), data.get("prompt_eval_count")
        if not isinstance(vectors, list) or len(vectors) != 1 or not isinstance(count, int) or count <= 0:
            raise EmbeddingUnavailable("yanit_gecersiz")
        vector = vectors[0]
        if len(vector) != info.dimensions or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector):
            raise EmbeddingUnavailable("vektor_gecersiz")
        return [float(x) for x in vector], count

    def embed_many(self, texts: list[str], known_counts: list[int]) -> list[list[float]]:
        """Toplu istek yalnız parça başına sayılar önceden biliniyorsa: toplam eşleşmezse hata (sessiz kesme yok)."""
        info = self._info or self.describe()
        if len(texts) != len(known_counts) or not texts:
            raise EmbeddingUnavailable("toplu_girdi_gecersiz")
        data = self._call("POST", "/api/embed", {"model": self.model, "input": list(texts), "truncate": False, "keep_alive": self.keep_alive,
                                                 "options": {"num_ctx": info.num_ctx}})  # fmt: skip
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingUnavailable("yanit_gecersiz")
        if data.get("prompt_eval_count") != sum(known_counts):
            raise EmbeddingUnavailable("toplu_sayim_uyusmuyor", f"{data.get('prompt_eval_count')}!={sum(known_counts)}")
        return [[float(x) for x in v] for v in vectors]
