"""Aşama 5 / U1: rol bazında model seçimi, tutarlılık, tampon kalibrasyonu, dağılım dışı denetim.

    .\\.venv\\Scripts\\python.exe scripts\\asama5_u1_kalibrasyon.py

Aşama 4 RF modeli artifacts/forecasting/asama4_seed42/random_forest'tan özet denetimiyle yüklenir
(yeniden eğitilmez). Veritabanı kullanılmaz. Kalibrasyon ve bilgi baseline dosyaları artifacts/ altına,
küçük özet docs/veri/asama5_u1_kalibrasyon.json'a yazılır.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.forecasting import u1_run as U  # noqa: E402
from medakis.forecasting.calibration import CalibrationConfig  # noqa: E402
from medakis.forecasting.dataset import build_training_frame  # noqa: E402
from medakis.forecasting.registry import file_sha256, load_model  # noqa: E402
from medakis.forecasting.training import ROBUSTNESS  # noqa: E402
from medakis.synthetic.v2 import ParamsV2, generate_v2  # noqa: E402

RF_DIR = ROOT / "artifacts" / "forecasting" / "asama4_seed42" / "random_forest"
OUT_DIR = ROOT / "artifacts" / "forecasting" / "asama5_u1"
SUMMARY = ROOT / "docs" / "veri" / "asama5_u1_kalibrasyon.json"


def main() -> int:
    started = time.perf_counter()
    forest, rf_manifest = load_model(RF_DIR)
    tf = build_training_frame(generate_v2(ParamsV2()))
    if tf.metadata["cerceve_sha256"] != rf_manifest["veri_kokeni"]["cerceve_sha256"]:
        raise SystemExit("RF modelinin eğitildiği çerçeve ile bu çerçeve aynı değil; durduruldu.")
    models = U.build_models(tf, forest)
    provenance = {k: tf.metadata[k] for k in ("uretici_surumu", "seed", "icerik_sha256", "cerceve_sha256", "sozlesme_surumu", "bolumler")}
    info_manifest = U.save_info_baseline(models, OUT_DIR / "baseline_bilgi", provenance)
    provenance["model_sha256"] = {"random_forest": rf_manifest["model_sha256"], "baseline_bilgi": info_manifest["model_sha256"]}
    config = CalibrationConfig()
    cal = U.calibrate(tf, models, config, provenance)
    (OUT_DIR / "kalibrasyon.json").write_text(cal.to_json(), encoding="utf-8")
    manifests = {"random_forest": rf_manifest, "baseline_bilgi": info_manifest}
    versions = {k: f"{k}@{m['model_sha256'][:12]}" for k, m in manifests.items()}

    val_eval, _ = U.evaluate_frame(U.part(tf, "dogrulama"), models, cal, manifests, versions)
    test_eval, test_out = U.evaluate_frame(U.part(tf, "test"), models, cal, manifests, versions)
    robust = {}
    examples = {"test": test_out.head(0)}
    for name, overrides in ROBUSTNESS.items():
        other = build_training_frame(generate_v2(ParamsV2(**overrides)))
        robust[name], out = U.evaluate_frame(U.part(other, "test"), models, cal, manifests, versions)
        robust[name]["parametre_farki"] = overrides
        examples[name] = out[(out["servis_id"] == 3) & (out["ufuk_dk"] == 60)].head(6)
    examples["test"] = test_out[(test_out["ufuk_dk"] == 60)].groupby(["servis_id", "rol"]).head(1)
    summary = {
        "kalibrasyon_sha256": cal.sha256(), "config": json.loads(cal.to_json())["config"], "secim": cal.selection,
        "tolerans": cal.tolerance, "tampon_geri_donusleri": cal.buffer_fallbacks, "ood_araliklari": cal.ood_ranges,
        "gorulen_kombinasyonlar": cal.seen_combinations, "koken": provenance,
        "dogrulama": val_eval, "test": test_eval, "saglamlik": robust,
        "ornekler": {k: json.loads(v.to_json(orient="records", date_format="iso")) for k, v in examples.items()},
        "dosyalar": {"kalibrasyon.json": file_sha256(OUT_DIR / "kalibrasyon.json")},
        "sure_sn": round(time.perf_counter() - started, 1),
    }  # fmt: skip
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Kalibrasyon {cal.sha256()[:16]}; özet {SUMMARY}; süre {summary['sure_sn']} sn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
