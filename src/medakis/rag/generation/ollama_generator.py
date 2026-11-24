"""Yerel Ollama üretim istemcisi (Aşama 8.4). YALNIZ geliştirme/test kipinde oluşturulabilir.

- Kip kapısı yapıcıdadır: `kapali` kipte istemci oluşturulamaz, dolayısıyla model hiçbir koşulda çağrılmaz.
- Yalnız loopback HTTP; vekil ayarları yok sayılır. İzinli uç noktalar: /api/tags, /api/ps (salt okunur), /api/show, /api/chat.
  İndirme/oluşturma/silme ya da araç çağrısı yoktur; istekte `tools` alanı gönderilmez.
- Model digest'i sabitlenebilir (`expected_digest`); uyuşmazsa çağrı yapılmaz.
- İstek: `stream=false`, JSON şema (`format`), `temperature=0`, `seed=0`, `num_ctx=2048`, `num_predict=256`.
- Fail-closed: kapalı sunucu, zaman aşımı, bellek yetersizliği, çıktı sınırına takılma (done_reason=length),
  giriş bütçesinin aşılması (`prompt_eval_count` > 1536), bozuk ya da şema dışı JSON → GenerationUnavailable.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

import httpx

from medakis.rag.contracts.generation import QuoteClaim
from medakis.rag.embeddings.ollama_client import check_base_url
from medakis.rag.errors import RagError
from medakis.rag.generation.mode import GenerationMode, require_enabled
from medakis.rag.generation.prompt import INPUT_BUDGET, OUTPUT_TOKENS

ALLOWED = frozenset({("GET", "/api/tags"), ("GET", "/api/ps"), ("POST", "/api/show"), ("POST", "/api/chat")})
DEFAULT_MODEL = "gemma3:4b"
GEMMA3_4B_DIGEST = "68ceab7e468a31c988b44d5a4984a0e21f58c4f7158c6ce29c1804babac38df7"  # 2026-11-24 yerelde doğrulandı


class GenerationUnavailable(RagError):
    """Üretici kullanılamıyor ya da çıktısı güvenle kullanılamaz."""


class OllamaGenerator:
    def __init__(self, mode: GenerationMode, model: str = DEFAULT_MODEL, base_url: str = "http://127.0.0.1:11434",
                 expected_digest: str | None = GEMMA3_4B_DIGEST, num_ctx: int = 2048, num_predict: int = OUTPUT_TOKENS,
                 timeout_s: float = 120.0, keep_alive: int | str = 0, transport: httpx.BaseTransport | None = None) -> None:  # fmt: skip
        self.mode = require_enabled(mode)  # kapalı kipte burada durur: ağ isteği yok
        self.model, self.expected_digest = model, expected_digest
        self.num_ctx, self.num_predict, self.keep_alive = num_ctx, num_predict, keep_alive
        self.timeout_s = timeout_s  # KM31: yeniden deneme DAHİL toplam işlem bütçesi (varsayılan 120 sn)
        self._client = httpx.Client(base_url=str(check_base_url(base_url)), timeout=timeout_s, trust_env=False, transport=transport)
        self._digest: str | None = None
        self._deadline: float | None = None

    supports_budget = True  # hat kalan bütçeyi `chat_json(..., budget_s=…)` ile iletebilir

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if (method, path) not in ALLOWED:
            raise GenerationUnavailable("izinsiz_uc_nokta", path)
        deadline = self._deadline if self._deadline is not None else time.monotonic() + self.timeout_s
        last: GenerationUnavailable | None = None
        for _ in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:  # yeniden deneme toplam bütçeyi aşamaz
                raise GenerationUnavailable("zaman_asimi", "toplam_butce_asildi")
            try:
                resp = self._client.request(method, path, json=body, timeout=min(self.timeout_s, remaining))
            except httpx.ConnectError:
                raise GenerationUnavailable("ollama_kapali") from None
            except httpx.TimeoutException:
                last = GenerationUnavailable("zaman_asimi")
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    raise GenerationUnavailable("yanit_gecersiz") from None
                if not isinstance(data, dict):
                    raise GenerationUnavailable("yanit_gecersiz")
                return data
            text = resp.text.lower()
            if "context length" in text:
                raise GenerationUnavailable("baglam_asildi")
            if resp.status_code == 404 or "not found" in text:
                raise GenerationUnavailable("model_yok", f"{self.model} kurulu değil; indirme proje sahibinin onayına bağlıdır")
            if "memory" in text:
                last = GenerationUnavailable("bellek_yetersiz")
                continue
            raise GenerationUnavailable("ollama_hatasi", str(resp.status_code))
        assert last is not None
        raise last

    def loaded_models(self) -> list[dict[str, Any]]:
        """Bellekte yüklü modeller (salt okunur /api/ps; Aşama 8.6 yaşam döngüsü denetimi)."""
        return list(self._call("GET", "/api/ps").get("models", []))

    def unload(self) -> None:
        """Üreticiyi bellekten çıkarır: boş mesaj ve keep_alive=0 (yanıt done_reason=unload; üretim yapılmaz)."""
        self._call("POST", "/api/chat", {"model": self.model, "messages": [], "keep_alive": 0})

    def describe(self) -> str:
        tags = self._call("GET", "/api/tags").get("models", [])
        names = {self.model, f"{self.model}:latest"}
        entry = next((m for m in tags if m.get("name") in names or m.get("model") in names), None)
        if entry is None or not entry.get("digest"):
            raise GenerationUnavailable("model_yok", f"{self.model} kurulu değil; indirme proje sahibinin onayına bağlıdır")
        if self.expected_digest and entry["digest"] != self.expected_digest:
            raise GenerationUnavailable("model_digest_uyusmuyor", str(entry["digest"])[:12])
        self._digest = str(entry["digest"])
        return self._digest

    def chat_json(self, messages: tuple[dict, ...] | list[dict], schema: dict, budget_s: float | None = None) -> dict[str, Any]:
        """Şemalı JSON yanıt: {'veri': dict, 'prompt_eval_count', 'eval_count', 'sure_ms'}.

        Sürüm denetimi, istek ve yeniden deneme TEK bütçeyi paylaşır: `budget_s` (hattın kalan bütçesi) ya da
        `timeout_s` (120 sn). Bütçe bitince `zaman_asimi`."""
        self._deadline = time.monotonic() + min(self.timeout_s, budget_s if budget_s is not None else self.timeout_s)
        try:
            if self._digest is None:
                self.describe()
            body = {"model": self.model, "messages": list(messages), "stream": False, "format": schema, "keep_alive": self.keep_alive,
                    "options": {"temperature": 0, "seed": 0, "num_ctx": self.num_ctx, "num_predict": self.num_predict}}  # fmt: skip
            data = self._call("POST", "/api/chat", body)
        finally:
            self._deadline = None
        if data.get("done_reason") == "length":
            raise GenerationUnavailable("cikti_kesildi")  # çıktı sınırına takılan yanıt kullanılmaz
        prompt_tokens = data.get("prompt_eval_count")
        if isinstance(prompt_tokens, int) and prompt_tokens > INPUT_BUDGET:
            raise GenerationUnavailable("baglam_butcesi_asildi", f"{prompt_tokens}>{INPUT_BUDGET}")
        content = (data.get("message") or {}).get("content")
        try:
            parsed = json.loads(content) if isinstance(content, str) else None
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            raise GenerationUnavailable("sema_disi_cikti")
        total = data.get("total_duration")
        return {"veri": parsed, "prompt_eval_count": prompt_tokens, "eval_count": data.get("eval_count"),
                "sure_ms": round(total / 1e6, 1) if isinstance(total, (int, float)) and math.isfinite(total) else None}  # fmt: skip


def parse_evidence(obj: dict[str, Any]) -> tuple[bool, tuple[QuoteClaim, ...], str]:
    """Kanıt denetimi JSON'unu katı biçimde ayrıştırır; şemaya uymayan her şey hatadır (fail-closed)."""
    if not isinstance(obj, dict) or not isinstance(obj.get("destekleniyor"), bool) or not isinstance(obj.get("alintilar"), list):
        raise GenerationUnavailable("sema_disi_cikti")
    claims = []
    for q in obj["alintilar"]:
        if not isinstance(q, dict) or not isinstance(q.get("parca"), str) or not isinstance(q.get("metin"), str):
            raise GenerationUnavailable("sema_disi_cikti")
        claims.append(QuoteClaim(q["parca"].strip(), q["metin"]))
    missing = obj.get("eksik") if isinstance(obj.get("eksik"), str) else ""
    return obj["destekleniyor"], tuple(claims), missing
