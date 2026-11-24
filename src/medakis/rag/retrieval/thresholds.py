"""Benzerlik eşiği kalibrasyonu: YALNIZ dev kümesi, model/indeks başına (KM29).

Eşik evrensel sabit değildir. Aday eşikler dev sorularındaki en iyi (en küçük) uzaklıklardır. Kural:
yanlış kaçınma ≤ `max_false_abstention` (varsayılan 0,15) koşulunu sağlayan eşikler içinde kaçınma recall'u en
yüksek olan; eşitlikte yanlış kaçınması düşük, sonra daha küçük (daha temkinli) eşik. Uygun eşik yoksa None.
Seçilen eşik indeks kaydına kaynağıyla (dev set özeti) yazılır; test kümesi hiçbir adımda okunmaz.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DevObservation:
    case_key: str
    split: str
    answerable: bool
    best_distance: float | None  # değerlendirme kipinde ilk doğrulanmış sonucun uzaklığı; sonuç yoksa None


@dataclass(frozen=True)
class Calibration:
    max_distance: float | None
    abstention_recall: float | None
    false_abstention: float | None
    n_dev: int
    reason: str


def _rates(obs: Sequence[DevObservation], tau: float) -> tuple[float | None, float | None]:
    abst = [o.best_distance is None or o.best_distance > tau for o in obs]
    unans = [a for a, o in zip(abst, obs) if not o.answerable]
    ans = [a for a, o in zip(abst, obs) if o.answerable]
    return (sum(unans) / len(unans) if unans else None, sum(ans) / len(ans) if ans else None)


def calibrate(obs: Sequence[DevObservation], max_false_abstention: float = 0.15) -> Calibration:
    if any(o.split != "dev" for o in obs):
        raise ValueError("Kalibrasyon yalnız dev kümesiyle yapılır.")
    if not obs or not any(o.answerable for o in obs) or all(o.answerable for o in obs):
        return Calibration(None, None, None, len(obs), "dev_kumesi_yetersiz")
    candidates = sorted({o.best_distance for o in obs if o.best_distance is not None})
    best = None
    for tau in candidates:
        rec, fa = _rates(obs, tau)
        if fa is None or fa > max_false_abstention:
            continue
        key = (-(rec or 0.0), fa, tau)
        if best is None or key < best[0]:
            best = (key, tau, rec, fa)
    if best is None:
        return Calibration(None, None, None, len(obs), "uygun_esik_yok")
    return Calibration(best[1], best[2], best[3], len(obs), "ok")
