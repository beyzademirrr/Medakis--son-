"""Aşama 4 tamamlayıcı: şartnamedeki son dört pencere baseline'ını KARŞILAŞTIRMA REFERANSI olarak ölçer.

    .\\.venv\\Scripts\\python.exe scripts\\asama4_son_dort_baseline.py

- Aşama 4 ile aynı veri (üretici 2.0.0, seed 42, 112 gün) ve aynı kronolojik bölümler (10/3/3 hafta, ambargo).
- Mevcut model dosyaları YALNIZ OKUNUR: mevsimsel baseline, Random Forest, gerçekleşen iş RF'si, bilgi baseline'ı.
  Hiçbir model eğitilmez; model seçimi ve U1 kalibrasyonu DEĞİŞMEZ (bu betik onlara hiçbir şey yazmaz).
- Son dört pencere referansı öğrenme gerektirmez; yalnız toplam hedeflerde tanımlıdır (rol gecikme girdisi yok).
- Veritabanı yok. Çıktı: docs/veri/asama4_son_dort_baseline.json
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.forecasting.models import LAST_FOUR_TARGETS, LastFourBaseline, metrics  # noqa: E402
from medakis.forecasting.registry import load_model  # noqa: E402
from medakis.forecasting.training import ExperimentConfig, frame_for, part  # noqa: E402

ART = ROOT / "artifacts" / "forecasting"
MODELS = {
    "baseline": ART / "asama4_seed42" / "baseline",
    "baseline_bilgi": ART / "asama5_u1" / "baseline_bilgi",
    "random_forest": ART / "asama4_seed42" / "random_forest",
    "random_forest_gerceklesen": ART / "asama4_seed42" / "random_forest_gerceklesen",
}
OUT = ROOT / "docs" / "veri" / "asama4_son_dort_baseline.json"


def scores(frame: pd.DataFrame, pred: pd.DataFrame, target: str) -> list[dict]:
    rows = []
    groups = [("tum", frame.index)] + [(str(u), frame.index[frame["servis_id"] == u]) for u in sorted(frame["servis_id"].unique())]
    for unit, idx in groups:
        p = pred.loc[idx, target]
        mask = p.notna().to_numpy()
        m = metrics(frame.loc[idx, target].to_numpy()[mask], p.to_numpy()[mask])
        rows.append({"servis": unit, "kapsama": round(float(mask.mean()), 4), **m})
    return rows


def main() -> int:
    started = time.perf_counter()
    missing = [str(p) for p in MODELS.values() if not p.exists()]
    if missing:
        print("Model dosyaları yok:", missing)
        return 2
    tf = frame_for(ExperimentConfig())
    models = {name: load_model(path) for name, path in MODELS.items()}
    sha = {name: man.get("model_sha256") for name, (_, man) in models.items()}
    last_four = LastFourBaseline()
    out = {
        "tanim": "Son dört erişilebilir 15 dk pencerenin ortalama gerçekleşen iş hızı × (ufuk / 15). Yalnız karşılaştırma "
                 "referansı; model seçimine ve U1 kalibrasyonuna girmez. Rol hedeflerinde tanımsız (rol gecikme girdisi yok).",
        "veri": tf.metadata, "model_sha256": sha, "hedefler": list(LAST_FOUR_TARGETS),
        "secime_etkisi": "yok", "kalibrasyona_etkisi": "yok",
    }  # fmt: skip
    for split in ("dogrulama", "test"):
        frame = part(tf, split)
        preds = {
            "son_dort_pencere": last_four.predict(frame),
            "baseline": models["baseline"][0].predict(frame),
            "baseline_bilgi": models["baseline_bilgi"][0].predict(frame),
            "random_forest": models["random_forest"][0].predict(frame),
            "random_forest_gerceklesen": models["random_forest_gerceklesen"][0].predict(frame),
        }
        block = {}
        for target in LAST_FOUR_TARGETS:
            block[target] = {name: scores(frame, p, target) for name, p in preds.items() if target in p.columns}
        out[split] = block
        out[f"{split}_satir"] = int(len(frame))
    out["sure_sn"] = round(time.perf_counter() - started, 1)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=lambda v: None if isinstance(v, float) and np.isnan(v) else str(v)),
                   encoding="utf-8")  # fmt: skip
    for target in LAST_FOUR_TARGETS:
        line = {name: next(r for r in rows if r["servis"] == "tum")["mae"] for name, rows in out["test"][target].items()}
        print(target, {k: round(v, 2) for k, v in line.items()})
    print("Çıktı:", OUT, out["sure_sn"], "sn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
