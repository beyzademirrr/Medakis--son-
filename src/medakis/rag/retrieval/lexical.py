"""D5 modelsiz karşılaştırma yöntemleri: Türkçe BM25, hibrit sıralama (RRF) ve yapılandırılmış kaynak erişimi.

YALNIZ değerlendirme içindir; normal sorgu akışına bağlanmaz. Yeni paket gerektirmez (standart kütüphane).
- Güvenlik: bu modül aday KÜMESİNİ kendisi seçmez. Çağıran, retrieval ile aynı `eligible_documents` sonucundaki izinli
  belgelerin, manifestle hash'i doğrulanmış parçalarını verir; onaysız, taslak, emekli, geçerlilik dışı ya da içeriği
  değişmiş belgeler hiç dizine girmez.
- Parametreler önceden sabittir (k1=1,2, b=0,75; RRF k=60); dev verisinde ayarlanmaz.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

K1, B, RRF_K = 1.2, 0.75, 60
_TOKEN = re.compile(r"[0-9a-zçğıöşüâîû]+")


def normalize_tr(text: str) -> str:
    """Türkçe küçük harf: İ→i, I→ı (Python'un varsayılan lower() 'I'yı 'i' yapar)."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def tokenize(text: str, prefix: int | None = None) -> list[str]:
    toks = _TOKEN.findall(normalize_tr(text))
    return [t[:prefix] for t in toks] if prefix else toks


@dataclass(frozen=True)
class LexDoc:
    key: str  # parça ya da bölüm kimliği
    text: str


class BM25:
    def __init__(self, docs: Sequence[LexDoc], prefix: int | None = None, k1: float = K1, b: float = B) -> None:
        self.docs, self.prefix, self.k1, self.b = list(docs), prefix, k1, b
        self.tf = [Counter(tokenize(d.text, prefix)) for d in self.docs]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        df = Counter(t for c in self.tf for t in c)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[tuple[str, float]]:
        q = tokenize(query, self.prefix)
        out = []
        for d, tf, ln in zip(self.docs, self.tf, self.len):
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / (self.avg or 1.0)))
            out.append((d.key, s))
        return sorted(out, key=lambda x: (-x[1], x[0]))  # eşitlikte deterministik


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: her listede 1/(k + sıra). Eşitlikte kimliğe göre deterministik."""
    acc: dict[str, float] = {}
    for ranking in rankings:
        for r, key in enumerate(ranking, 1):
            acc[key] = acc.get(key, 0.0) + 1.0 / (k + r)
    return sorted(acc.items(), key=lambda x: (-x[1], x[0]))


@dataclass(frozen=True)
class LexicalGate:
    outcome: str  # "found" | "conflicting_sources" | "no_source"
    allowed_parts: tuple[dict, ...]
    reason: str = ""


def gate(parts: Sequence[dict], allowed_docs: Sequence[str], conflicts: Sequence[str]) -> LexicalGate:
    """Modelsiz yöntemlerin aday kümesi: retrieval ile AYNI izinli belge kümesi (`eligible_documents`) ve aynı çatışma
    kuralı. Sürüm/onay izi çatışması varsa hiçbir şey kabul edilmez. Parçalar çağıranda manifest hash'iyle doğrulanmıştır."""
    if conflicts:
        return LexicalGate("conflicting_sources", (), "belge_catismasi")
    allowed = set(allowed_docs)
    kept = tuple(p for p in parts if p["belge"] in allowed)
    if not kept:
        return LexicalGate("no_source", (), "izinli_belge_yok")
    return LexicalGate("found", kept)


def rule_conflict_in(ranked_keys: Sequence[str], parts_by_key: dict[str, dict]) -> bool:
    """Retrieval ile aynı kural: sonuç listesindeki bir belgede kural çatışması işareti varsa `conflicting_sources`."""
    return any(parts_by_key[k].get("kural_catismasi") for k in ranked_keys if k in parts_by_key)


def first_sentence(text: str) -> str:
    m = re.search(r"(?<=[.!?…])\s", text)
    return text[: m.start()] if m else text
