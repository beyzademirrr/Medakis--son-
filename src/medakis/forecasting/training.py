"""Aşama 4 deney akışı: eğit (yalnız eğitim bölümü) → doğrulamada seç → testte BİR KEZ değerlendir.

Sıra sabittir ve testlerle korunur:
1. Veri: üretici 2.0.0 (bellek) → eğitim sözleşmesi çerçevesi (kronolojik bölüm + ambargo).
2. Baseline ve RF adayları yalnız 'egitim' satırlarıyla eğitilir.
3. Seçim yalnız 'dogrulama' MAE ortalamasıyla yapılır.
4. Seçilen model yeniden eğitilmeden 'test' bölümünde ve sağlamlık veri kümelerinde değerlendirilir.
Veritabanına yazılmaz; sonuçlar yerel klasöre (model dosyaları + manifest + sonuç JSON) yazılır.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from medakis.forecasting.dataset import SplitPlan, TrainingFrame, build_training_frame
from medakis.forecasting.models import (
    DEMAND_TARGETS,
    INFO_BASELINE_KEYS,
    REALIZED_TARGETS,
    RF_CANDIDATES,
    ForestModel,
    SeasonalBaseline,
    breakdown,
    mean_mae,
)
from medakis.forecasting.registry import runtime_versions, save_model
from medakis.synthetic.v2 import ParamsV2, generate_v2

ROBUSTNESS: dict[str, dict[str, Any]] = {
    "seed_43": {"seed": 43},
    "acil_yuksek_hacim": {"ed_volume_profile": "yuksek"},
    "acil_yuksek_kadro": {"ed_staffing_profile": "yuksek"},
    "triaj_yesil_yogun": {"ed_triage_profile": "yesil_yogun"},
}


@dataclass
class ExperimentConfig:
    seed: int = 42
    days: int = 112
    plan: SplitPlan = field(default_factory=SplitPlan)
    candidates: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(RF_CANDIDATES))
    robustness: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(ROBUSTNESS))


def frame_for(config: ExperimentConfig, **overrides: Any) -> TrainingFrame:
    params = ParamsV2(**{"seed": config.seed, "days": config.days, **overrides})
    return build_training_frame(generate_v2(params), config.plan)


def part(tf: TrainingFrame, name: str) -> pd.DataFrame:
    return tf.frame[tf.frame["bolum"] == name]


def evaluate(model: Any, frame: pd.DataFrame, targets: tuple[str, ...]) -> pd.DataFrame:
    """Eğitilmiş modeli değerlendirir; ASLA fit çağırmaz."""
    return breakdown(frame, model.predict(frame), targets)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(df.to_json(orient="records"))


def run_experiment(config: ExperimentConfig, output_dir: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    tf = frame_for(config)
    train, val, test = part(tf, "egitim"), part(tf, "dogrulama"), part(tf, "test")
    results: dict[str, Any] = {
        "veri": tf.metadata, "surumler": runtime_versions(), "hedefler": list(DEMAND_TARGETS),
        "ikincil_hedefler": list(REALIZED_TARGETS), "adaylar": config.candidates,
    }  # fmt: skip

    # 1-2) Eğitim (yalnız eğitim bölümü) ve doğrulamada seçim
    baseline = SeasonalBaseline().fit(train)
    selection = {"baseline": mean_mae(val, baseline.predict(val), DEMAND_TARGETS)}
    forests: dict[str, ForestModel] = {}
    for name, params in config.candidates.items():
        forests[name] = ForestModel(params).fit(train)
        selection[name] = mean_mae(val, forests[name].predict(val), DEMAND_TARGETS)
    chosen = min(config.candidates, key=lambda n: selection[n])
    forest = forests[chosen]
    results["dogrulama_secimi"] = {"ortalama_mae": selection, "secilen_rf": chosen,
                                   "rf_baselineden_iyi_mi": selection[chosen] < selection["baseline"]}  # fmt: skip
    results["dogrulama"] = {"baseline": _records(evaluate(baseline, val, DEMAND_TARGETS)),
                            "random_forest": _records(evaluate(forest, val, DEMAND_TARGETS))}  # fmt: skip

    # 3) Test: modeller sabit, yeniden eğitim yok
    results["test"] = {"baseline": _records(evaluate(baseline, test, DEMAND_TARGETS)),
                       "random_forest": _records(evaluate(forest, test, DEMAND_TARGETS))}  # fmt: skip
    # Bilgi amaçlı güçlü baseline (seçimde kullanılmadı; RF'yi haksız yere iyi göstermemek için)
    info = SeasonalBaseline(keys=INFO_BASELINE_KEYS, name="baseline_bilgi").fit(train)
    results["bilgi_baseline"] = {"dogrulama_ortalama_mae": mean_mae(val, info.predict(val), DEMAND_TARGETS),
                                 "test": _records(evaluate(info, test, DEMAND_TARGETS))}  # fmt: skip

    # 4) İkincil analiz: gerçekleşen iş (ayrı modeller, aynı seçilmiş parametre; ana hedef değil)
    base_real = SeasonalBaseline(targets=REALIZED_TARGETS, name="baseline_gerceklesen").fit(train)
    forest_real = ForestModel(config.candidates[chosen], targets=REALIZED_TARGETS, name="random_forest_gerceklesen").fit(train)
    results["ikincil_gerceklesen_test"] = {"baseline": _records(evaluate(base_real, test, REALIZED_TARGETS)),
                                           "random_forest": _records(evaluate(forest_real, test, REALIZED_TARGETS))}  # fmt: skip

    # 5) Sağlamlık: model seçimi değişmez; her veri kümesinin test bölümü
    robust: dict[str, Any] = {}
    for name, overrides in config.robustness.items():
        other = frame_for(config, **overrides)
        other_test = part(other, "test")
        robust[name] = {
            "parametre_farki": overrides, "icerik_sha256": other.metadata["icerik_sha256"],
            "talep_sha256": other.metadata["talep_sha256"], "cerceve_sha256": other.metadata["cerceve_sha256"],
            "baseline": _records(evaluate(baseline, other_test, DEMAND_TARGETS)),
            "random_forest": _records(evaluate(forest, other_test, DEMAND_TARGETS)),
            "gerceklesen_baseline": _records(evaluate(base_real, other_test, REALIZED_TARGETS)),
            "gerceklesen_random_forest": _records(evaluate(forest_real, other_test, REALIZED_TARGETS)),
        }  # fmt: skip
    results["saglamlik"] = robust

    importances = forest.pipeline.named_steps["orman"].feature_importances_
    results["onem"] = dict(sorted(zip(forest.feature_names(), map(float, importances)), key=lambda kv: -kv[1]))
    results["sure_sn"] = round(time.perf_counter() - started, 1)

    if output_dir is not None:
        output_dir = Path(output_dir)
        provenance = {k: tf.metadata[k] for k in ("uretici_surumu", "seed", "parametre_parmak_izi", "donem", "icerik_sha256",
                                                    "talep_sha256", "cerceve_sha256", "sozlesme_surumu", "bolumler")}  # fmt: skip
        results["modeller"] = {
            "baseline": save_model(baseline, output_dir / "baseline", provenance),
            "random_forest": save_model(forest, output_dir / "random_forest", provenance, {"secim": results["dogrulama_secimi"]}),
            "random_forest_gerceklesen": save_model(forest_real, output_dir / "random_forest_gerceklesen", provenance),
        }
        (output_dir / "sonuclar.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return results
