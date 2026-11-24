"""D5: cevaplanabilirlik (kabul/ret) değerlendirmesi — skor YÖNÜNDEN bağımsız, grup bazlı CV.

- Cosine UZAKLIĞI düşük = iyi; reranker/BM25 SKORU yüksek = iyi. Mevcut `calibrate()` uzaklık bekler (ret: d > τ).
  Yüksek-iyi skorlar d = −s dönüşümüyle verilir; ret koşulu s < −τ olur. Bu eşdeğerlik birim testlerle doğrulanır.
- Eşik YALNIZ eğitim katlarında seçilir (`calibrate`: yanlış kaçınma ≤ 0,15 altında en yüksek kaçınma) ve test katına
  uygulanır. Kat ataması sha256(grup)[:8] mod 5 (önceki deneylerle aynı); paraphrase grubu bölünmez.
- Skor olasılık ya da anlamsal doğruluk garantisi DEĞİLDİR; yalnız bir kabul/ret sinyalidir.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from medakis.rag.retrieval.thresholds import DevObservation, calibrate

FOLDS = 5


def fold_of(group: str, folds: int = FOLDS) -> int:
    return int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % folds


def to_distance(score: float | None, higher_is_better: bool) -> float | None:
    if score is None:
        return None
    return -score if higher_is_better else score


def accepts(score: float | None, tau: float | None, higher_is_better: bool) -> bool:
    """`calibrate` kuralının skor uzayındaki karşılığı: kabul ⇔ uzaklık ≤ τ. Eşik yoksa ya da skor yoksa ret."""
    d = to_distance(score, higher_is_better)
    return d is not None and tau is not None and d <= tau


def auroc(pos: Sequence[float], neg: Sequence[float]) -> float | None:
    """Cevaplanabilir (pos) skorlarının kaynak dışı (neg) skorlarından yüksek olma olasılığı; eşitlik 0,5.
    Skorlar YÜKSEK-İYİ yönünde verilmelidir."""
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


@dataclass(frozen=True)
class CvCase:
    case_key: str
    group: str
    answerable: bool
    score: float | None  # None: aday yok / karar verilemedi


def group_cv(cases: Sequence[CvCase], higher_is_better: bool, folds: int = FOLDS) -> tuple[dict[str, bool], list[float | None]]:
    """Her vaka için {vaka: kabul edildi mi}; eşik yalnız o vakanın DIŞINDAKİ katlarda seçilir."""
    accepted: dict[str, bool] = {}
    taus: list[float | None] = []
    for f in range(folds):
        train = [DevObservation(c.case_key, "dev", c.answerable, to_distance(c.score, higher_is_better))
                 for c in cases if fold_of(c.group, folds) != f]  # fmt: skip
        tau = calibrate(train).max_distance
        taus.append(tau)
        for c in cases:
            if fold_of(c.group, folds) == f:
                accepted[c.case_key] = accepts(c.score, tau, higher_is_better)
    return accepted, taus
