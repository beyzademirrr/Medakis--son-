"""Aşama 4 tahmin modellerini eğitir ve değerlendirir (yalnız bellek ve yerel dosya; veritabanı yok).

    .\\.venv\\Scripts\\python.exe scripts\\asama4_egit.py
    .\\.venv\\Scripts\\python.exe scripts\\asama4_egit.py --cikti artifacts\\forecasting\\deneme

Model dosyaları artifacts/ altına yazılır (Git'e eklenmez). Küçük özet (metrikler, köken, model
özetleri) --ozet ile ayrıca yazılabilir (ör. docs/veri/asama4_sonuclar.json).
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.forecasting.training import ExperimentConfig, run_experiment  # noqa: E402


def main(argv) -> int:
    parser = argparse.ArgumentParser(description="Aşama 4 tahmin modelleri")
    parser.add_argument("--cikti", default=None, help="Model klasörü (varsayılan artifacts/forecasting/<zaman>)")
    parser.add_argument("--ozet", default=None, help="Küçük sonuç özeti JSON yolu")
    args = parser.parse_args(argv)
    out = Path(args.cikti) if args.cikti else ROOT / "artifacts" / "forecasting" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results = run_experiment(ExperimentConfig(), out)
    print(f"Çıktı: {out}")
    print(f"Doğrulama ortalama MAE: {results['dogrulama_secimi']['ortalama_mae']}")
    print(f"Seçilen RF: {results['dogrulama_secimi']['secilen_rf']}; süre {results['sure_sn']} sn")
    if args.ozet:
        summary = {k: v for k, v in results.items() if k != "modeller"}
        summary["modeller"] = {k: {f: m[f] for f in ("model", "parametreler", "model_sha256", "model_bayt", "surumler")}
                               for k, m in results["modeller"].items()}  # fmt: skip
        Path(args.ozet).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"Özet: {args.ozet}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
