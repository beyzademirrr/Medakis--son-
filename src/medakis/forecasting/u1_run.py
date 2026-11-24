"""Aşama 5 / U1 akışı: seçim ve kalibrasyon (eğitim + doğrulama) → test ve sağlamlıkta yalnız rapor.

Aşama 4 Random Forest'ı yeniden eğitilmez; kayıtlı dosya özet denetimiyle yüklenir. Bilgi amaçlı güçlü
baseline, Aşama 4'teki tanımla (INFO_BASELINE_KEYS) yalnız eğitim bölümünden kurulur.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from medakis.forecasting.calibration import (
    HORIZONS,
    INCELEME,
    Calibration,
    CalibrationConfig,
    buffer_for,
    combine,
    coverage,
    discrepancy,
    fit_calibration,
    guarded_candidates,
    safe_forecast,
)
from medakis.forecasting.dataset import ROLES, TrainingFrame
from medakis.forecasting.models import DEMAND_TARGETS, INFO_BASELINE_KEYS, SeasonalBaseline

Model = Any


def candidates_for(frame: pd.DataFrame, models: dict[str, Model], manifests: dict[str, dict] | None = None,
                   cal: Calibration | None = None) -> dict[str, pd.DataFrame]:  # fmt: skip
    """Tahmin öncesi doğrulamalı aday tahminler (geçersiz satır ve uyumsuz model: NaN, model çağrılmaz)."""
    return guarded_candidates(frame, models, manifests, cal)


def part(tf: TrainingFrame, name: str) -> pd.DataFrame:
    return tf.frame[tf.frame["bolum"] == name]


def build_models(tf: TrainingFrame, forest: Model) -> dict[str, Model]:
    info = SeasonalBaseline(keys=INFO_BASELINE_KEYS, name="baseline_bilgi").fit(part(tf, "egitim"))
    return {"random_forest": forest, "baseline_bilgi": info}


def calibrate(tf: TrainingFrame, models: dict[str, Model], config: CalibrationConfig, provenance: dict[str, Any]) -> Calibration:
    train, val = part(tf, "egitim"), part(tf, "dogrulama")
    return fit_calibration(train, val, candidates_for(val, models), config, provenance)


def coverage_table(frame: pd.DataFrame, pred: pd.DataFrame, cal: Calibration) -> list[dict[str, Any]]:
    rows = []
    targets = [f"talep_dk_{h}_{r}" for h in HORIZONS for r in ROLES] + [f"talep_dk_{h}" for h in HORIZONS]
    for target in targets:
        for unit in sorted(frame["servis_id"].unique()):
            mask = (frame["servis_id"] == unit).to_numpy()
            sub, sub_pred = frame[mask], pred[mask]
            kind = sub["servis_turu"].iloc[0]
            for q in cal.config.quantiles:
                for method in ("mutlak", "goreli"):
                    b = buffer_for(cal, sub, sub_pred, target, q=q, method=method)
                    rows.append({"hedef": target, "servis_id": int(unit), "tur": kind, "q": q, "yontem": method,
                                 "kapsama": coverage(sub, sub_pred, b, target), "ortalama_tampon": float(np.nanmean(b)),
                                 "ortalama_talep": float(sub[target].mean()), "n": int(mask.sum())})  # fmt: skip
    return rows


def consistency_table(frame: pd.DataFrame, pred: pd.DataFrame, cal: Calibration) -> list[dict[str, Any]]:
    rows = []
    for h in HORIZONS:
        d = discrepancy(pred, h)
        for unit in sorted(frame["servis_id"].unique()):
            mask = frame["servis_id"] == unit
            tau = cal.tolerance[f"{int(unit)}:{h}"]
            dd = d[mask]
            rows.append({"ufuk": h, "servis_id": int(unit), "tau": tau, "ortalama_d": float(dd.mean()),
                         "mutlak_d_p50": float(dd.abs().median()), "mutlak_d_p95": float(dd.abs().quantile(0.95)),
                         "pozitif_oran": float((dd > 0).mean()),
                         "buyuk_fark_orani": float((dd.abs() > cal.config.consistency_multiplier * tau).mean())})  # fmt: skip
    return rows


def status_summary(out: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {"satir": len(out), "inceleme_orani": float((out["durum"] == INCELEME).mean())}
    by = out.groupby(["servis_id", "rol", "ufuk_dk"])["durum"].apply(lambda s: float((s == INCELEME).mean()))
    summary["inceleme_orani_kirilim"] = {f"{u}|{r}|{h}": v for (u, r, h), v in by.items()}
    reasons = Counter(reason.split(":")[0] for items in out["inceleme_nedenleri"] for reason in items)
    summary["neden_sayilari"] = dict(sorted(reasons.items()))
    per_unit: dict[str, dict[str, int]] = {}
    for unit, items in zip(out["servis_id"], out["inceleme_nedenleri"]):
        counts = per_unit.setdefault(str(unit), {})
        for reason in items:
            key = reason.split(":")[0] if not reason.startswith("dagilim_disi") else reason
            counts[key] = counts.get(key, 0) + 1
    summary["neden_kirilim"] = {u: dict(sorted(c.items())) for u, c in sorted(per_unit.items())}
    return summary


def evaluate_frame(frame: pd.DataFrame, models: dict[str, Model], cal: Calibration, manifests: dict[str, dict],
                   versions: dict[str, str]) -> dict[str, Any]:  # fmt: skip
    """Kalibrasyonu DEĞİŞTİRMEZ: yalnız uygular ve ölçer."""
    cands = candidates_for(frame, models, manifests, cal)
    pred = combine(cands, frame, cal.choice())
    mae = {t: {"birlesik": float(np.abs(pred[t] - frame[t]).mean()),
               "random_forest": float(np.abs(cands["random_forest"][t] - frame[t]).mean())} for t in DEMAND_TARGETS}  # fmt: skip
    out = safe_forecast(frame, cands, cal, manifests, versions)
    return {"mae": mae, "kapsama": coverage_table(frame, pred, cal), "tutarlilik": consistency_table(frame, pred, cal),
            "durum": status_summary(out)}, out  # type: ignore[return-value]


def save_info_baseline(models: dict[str, Model], directory: Path, provenance: dict[str, Any]) -> dict[str, Any]:
    from medakis.forecasting.registry import save_model

    return save_model(models["baseline_bilgi"], directory, provenance)
