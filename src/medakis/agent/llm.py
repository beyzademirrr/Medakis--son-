"""İsteğe bağlı yerel dil modeli katmanı (Ollama + Gemma 3 4B): yalnız araç çıktısından üretilmiş yanıtı yeniden ifade eder.

Güvenlik: yalnız yerel (loopback) Ollama adresi kullanılır. Model çıktısındaki her sayı, araçlardan üretilen deterministik
yanıtta bulunmalıdır; bulunmayan sayı (uydurma) varsa model çıktısı atılır ve deterministik yanıt gösterilir. Model yoksa
ya da kapalıysa agent deterministik kipte çalışır ve bu durum arayüzde açıkça yazılır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from medakis.rag.embeddings.ollama_client import check_base_url

BASE_URL = "http://127.0.0.1:11434"
MODEL = "gemma3:4b"
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
SYSTEM = ("Sen bir hastane operasyon asistanısın. Sana verilen ARAÇ YANITI dışındaki hiçbir bilgiyi ekleme. Yeni sayı, personel "
          "kodu ya da birim uydurma. Yanıtı Türkçe, kısa ve yöneticiye yönelik yeniden yaz. Onay verildiğini ya da atama "
          "yapıldığını asla söyleme.")  # fmt: skip


@dataclass(frozen=True)
class LlmStatus:
    available: bool
    detail: str


def status(timeout: float = 2.0) -> LlmStatus:
    try:
        url = check_base_url(BASE_URL)
        resp = httpx.get(str(url).rstrip("/") + "/api/tags", timeout=timeout)
        names = [m.get("name", "") for m in (resp.json() or {}).get("models", [])]
    except Exception as exc:  # noqa: BLE001 — Ollama kapalı/erişilemez
        return LlmStatus(False, f"Ollama erişilemiyor ({type(exc).__name__})")
    if MODEL not in names:
        return LlmStatus(False, f"Ollama açık, {MODEL} modeli yüklü değil")
    return LlmStatus(True, f"{MODEL} (yerel Ollama)")


def numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUM.findall(text)}


def grounded(candidate: str, source: str) -> bool:
    """Aday metindeki her sayı kaynak metinde de geçiyor mu?"""
    return numbers(candidate) <= numbers(source)


def rephrase(text: str, timeout: float = 60.0) -> tuple[str | None, str]:
    """(yeni metin | None, açıklama). None: model yok, hata ya da sayı denetimi başarısız."""
    try:
        url = check_base_url(BASE_URL)
        resp = httpx.post(str(url).rstrip("/") + "/api/chat", timeout=timeout,
                          json={"model": MODEL, "stream": False, "options": {"temperature": 0},
                                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "ARAÇ YANITI:\n" + text}]})  # fmt: skip
        out = (resp.json() or {}).get("message", {}).get("content", "").strip()
    except Exception as exc:  # noqa: BLE001
        return None, f"model_hatasi:{type(exc).__name__}"
    if not out:
        return None, "bos_yanit"
    if not grounded(out, text):
        return None, "sayi_denetimi_basarisiz: modelin eklediği sayı araç çıktısında yok"
    return out, "ok"
