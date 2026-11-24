"""Kanıt kapsaması: getirilen parçalar sorunun AYIRT EDİCİ terimlerini gerçekten içeriyor mu? (Aşama 7, DENEYSEL)

Yalnız cosine benzerliği, alana yakın ama corpus'ta cevabı olmayan soruları ayıramaz (ör. "Random Forest kaç karar
ağacı kullanır?" → "Modeller" bölümüne yakın düşer). Bu modül deterministik ve modelden bağımsız bir ek denetimdir:

- Soru ve parça metinleri Türkçe küçük harfe çevrilir; sözcükler kaba kökle (ilk 5 karakter) eşlenir.
- İşlev sözcükleri ve soru sözcükleri atılır. Corpus parçalarının büyük bölümünde geçen GENEL terimler de atılır
  (belge sıklığı eşiği): böylece "sistem", "görev" gibi her yerde geçen sözcükler kapsamı şişirmez.
- Kapsam = sorunun ayırt edici terimlerinden ilk k parçada (metin + bölüm adı) geçenlerin oranı.
  Ayırt edici terimi olmayan soru için kapsam 0'dır (kanıt gösterilemez → fail-closed).

Bu modül normal sorgu akışına BAĞLI DEĞİLDİR. Etkinleştirmek proje sahibinin kararını gerektirir.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
STOP = frozenset("""
bir ve veya ile için mi mı mu mü midir mıdır mudur müdür nedir nelerdir neler nasıl hangi hangisi kaç ne nerede neden
niçin kim kimdir kime olarak olan olur olmalıdır olmalı gerekir gereken bu şu o da de ya yoksa hem göre kadar sonra önce
tarafından ise ki çok en daha her hiç gibi ama fakat var yok ben benim bana miyim mıyım yapılır yapmalıyım edilir
""".split())  # fmt: skip
STEM = 5


def fold(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def stems(text: str) -> list[str]:
    return [w[:STEM] for w in _WORD.findall(fold(text)) if len(w) >= 2 and w not in STOP]


@dataclass(frozen=True)
class TermStats:
    """Corpus parçalarındaki terim belge sıklığı (kök başına parça oranı)."""

    df: dict[str, float]

    @classmethod
    def from_chunks(cls, texts: Iterable[str]) -> TermStats:
        texts = list(texts)
        counts: dict[str, int] = {}
        for t in texts:
            for s in set(stems(t)):
                counts[s] = counts.get(s, 0) + 1
        n = max(len(texts), 1)
        return cls({s: c / n for s, c in counts.items()})


def content_terms(question: str, stats: TermStats, max_df: float) -> list[str]:
    """Sorunun ayırt edici kökleri: işlev sözcüğü değil ve corpus parçalarının en fazla `max_df` oranında geçiyor.
    Corpus'ta hiç geçmeyen kökler de ayırt edicidir (ve kapsamı düşürür: bu, kaynak dışılığın güçlü işaretidir)."""
    return list(dict.fromkeys(s for s in stems(question) if stats.df.get(s, 0.0) <= max_df))


def coverage(question: str, passages: Sequence[str], stats: TermStats, max_df: float = 0.3) -> float:
    terms = content_terms(question, stats, max_df)
    if not terms:
        return 0.0
    present = set()
    for p in passages:
        present.update(stems(p))
    return sum(t in present for t in terms) / len(terms)


# ---- Cevap türü uyumu: sayı soran soru, sayı içermeyen kanıtla cevaplanmaz (DENEYSEL) -------------------------

QUANTITY_WORDS = frozenset({"kaç", "kaçtır", "kaçta", "kaçıncı", "yüzde", "yüzdesi", "yüzdedir"})
NUMBER_WORDS = frozenset({"iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz", "on", "yirmi", "otuz", "kırk", "elli",
                          "altmış", "yetmiş", "seksen", "doksan", "yüz", "bin", "sıfır", "yarım", "çeyrek"})  # fmt: skip
_DIGIT = re.compile(r"\d")


def asks_quantity(question: str) -> bool:
    words = _WORD.findall(fold(question))
    return any(w in QUANTITY_WORDS for w in words) or "ne kadar" in fold(question)


def has_number(text: str) -> bool:
    return bool(_DIGIT.search(text)) or any(w in NUMBER_WORDS for w in _WORD.findall(fold(text)))


def answer_type_ok(question: str, passages: Sequence[str]) -> bool:
    """Sayı/miktar soran soruda, verilen parçalardan en az biri sayı içermeli. Diğer sorularda kısıt yok."""
    return not asks_quantity(question) or any(has_number(p) for p in passages)
