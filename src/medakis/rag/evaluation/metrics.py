"""Retrieval ölçüleri (şartname §10 tanımlarıyla birebir).

- Recall@5(q) = ilk 5 TEKRARSIZ sonuçta bulunan gold parça sayısı / bütün gold parça sayısı.
- MRR@10(q): ilk ilgili parçanın sırası r ≤ 10 ise 1/r, yoksa 0.
- Makro ortalama yalnız cevaplanabilir ve gold kümesi boş olmayan sorularda; kaynaksız sorular bu ölçülere 0 olarak
  eklenmez, ayrı kaçınma ölçülerine girer. Geçersiz/filtrelenmiş parçalar zaten sonuçta yoktur (başarı sayılmaz).
- Kaçınma recall'u = doğru kaçınılan kaynak dışı soru / bütün kaynak dışı sorular;
  yanlış kaçınma oranı = kaçınılan cevaplanabilir soru / bütün cevaplanabilir sorular.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


def _unique(ranked: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(ranked))


def recall_at_k(ranked: Sequence[str], gold: Sequence[str], k: int = 5) -> float | None:
    gold_set = set(gold)
    if not gold_set:
        return None
    return len(set(_unique(ranked)[:k]) & gold_set) / len(gold_set)


def reciprocal_rank(ranked: Sequence[str], gold: Sequence[str], k: int = 10) -> float | None:
    gold_set = set(gold)
    if not gold_set:
        return None
    return next((1.0 / r for r, x in enumerate(_unique(ranked)[:k], 1) if x in gold_set), 0.0)


@dataclass(frozen=True)
class CaseOutcome:
    case_key: str
    answerable: bool
    ranked: tuple[str, ...]  # değerlendirme kipinde doğrulanmış sıralı parça kimlikleri
    gold: tuple[str, ...]
    abstained: bool  # eşik uygulandığında kaçınıldı mı


@dataclass(frozen=True)
class Summary:
    recall_at_5: float | None
    mrr_at_10: float | None
    abstention_recall: float | None
    false_abstention: float | None
    n_answerable: int
    n_unanswerable: int


def summarize(cases: Sequence[CaseOutcome]) -> Summary:
    ans = [c for c in cases if c.answerable and c.gold]
    unans = [c for c in cases if not c.answerable]
    all_ans = [c for c in cases if c.answerable]
    rec = [recall_at_k(c.ranked, c.gold) for c in ans]
    rr = [reciprocal_rank(c.ranked, c.gold) for c in ans]
    return Summary(
        sum(rec) / len(rec) if rec else None,  # type: ignore[arg-type]
        sum(rr) / len(rr) if rr else None,  # type: ignore[arg-type]
        sum(c.abstained for c in unans) / len(unans) if unans else None,
        sum(c.abstained for c in all_ans) / len(all_ans) if all_ans else None,
        len(ans), len(unans),
    )  # fmt: skip
