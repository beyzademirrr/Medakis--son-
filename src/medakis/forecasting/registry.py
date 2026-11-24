"""Model dosyaları ve tahmin arayüzü (yerel dosya; veritabanı ve Streamlit yok).

Her model bir klasördür: `model.joblib` + `manifest.json`. Manifest; model biçim sürümü, sözleşme
sürümü, girdi ve hedef sütunları, parametreler, paket sürümleri, veri kökeni (üretici sürümü, seed,
içerik/çerçeve özetleri), metrikler ve model dosyasının SHA-256 özetini taşır. Yükleme, sözleşme,
sütun, scikit-learn sürümü ve dosya özeti tutmazsa reddedilir.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn

from medakis.forecasting.dataset import CONTRACT_VERSION, INPUT_COLUMNS

MODEL_FORMAT = "medakis-tahmin-modeli/1"
MODEL_FILE = "model.joblib"
MANIFEST_FILE = "manifest.json"


class ModelCompatibilityError(RuntimeError):
    """Kayıtlı model bu kodla güvenle kullanılamaz."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_versions() -> dict[str, str]:
    return {"python": platform.python_version(), "scikit_learn": sklearn.__version__, "numpy": np.__version__,
            "pandas": pd.__version__, "joblib": joblib.__version__}  # fmt: skip


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def save_model(model: Any, directory: Path, provenance: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Modeli ve manifestini yazar; manifesti döndürür. Klasör varsa üzerine yazılmaz."""
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"Model klasörü boş değil: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MODEL_FILE
    joblib.dump(model, path, compress=3)
    manifest = {
        "bicim": MODEL_FORMAT,
        "model": model.name,
        "parametreler": getattr(model, "params", None),
        "hedefler": list(model.targets),
        "girdiler": list(INPUT_COLUMNS),
        "sozlesme_surumu": CONTRACT_VERSION,
        "surumler": runtime_versions(),
        "veri_kokeni": provenance,
        "model_dosyasi": MODEL_FILE,
        "model_sha256": file_sha256(path),
        "model_bayt": path.stat().st_size,
        **(extra or {}),
    }
    (directory / MANIFEST_FILE).write_text(json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_model(directory: Path) -> tuple[Any, dict[str, Any]]:
    directory = Path(directory)
    manifest = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
    problems = []
    if manifest.get("bicim") != MODEL_FORMAT:
        problems.append(f"biçim {manifest.get('bicim')}")
    if manifest.get("sozlesme_surumu") != CONTRACT_VERSION:
        problems.append(f"sözleşme {manifest.get('sozlesme_surumu')} (beklenen {CONTRACT_VERSION})")
    if manifest.get("girdiler") != list(INPUT_COLUMNS):
        problems.append("girdi sütunları farklı")
    if manifest.get("surumler", {}).get("scikit_learn") != sklearn.__version__:
        problems.append(f"scikit-learn {manifest.get('surumler', {}).get('scikit_learn')} (kurulu {sklearn.__version__})")
    path = directory / manifest.get("model_dosyasi", MODEL_FILE)
    if not path.is_file() or file_sha256(path) != manifest.get("model_sha256"):
        problems.append("model dosyası eksik veya özeti tutmuyor")
    if problems:
        raise ModelCompatibilityError("Model yüklenemez: " + "; ".join(problems))
    model = joblib.load(path)
    if list(model.targets) != manifest["hedefler"]:
        raise ModelCompatibilityError("Model yüklenemez: hedefler manifestle uyuşmuyor")
    return model, manifest


@dataclass
class WorkloadForecaster:
    """Aşama 3 paneline ileride bağlanabilecek tahmin arayüzü (yalnız INPUT_COLUMNS ile çalışır)."""

    model: Any
    manifest: dict[str, Any]

    @classmethod
    def load(cls, directory: Path) -> WorkloadForecaster:
        return cls(*load_model(directory))

    def predict(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Satır başına hedef tahminleri (dakika, negatif değil). `rows` en az INPUT_COLUMNS içermeli."""
        return self.model.predict(rows)

    def forecast(self, rows: pd.DataFrame) -> list[dict[str, Any]]:
        """Kimlik sütunları varsa onlarla birlikte sözlük listesi (panel/servis katmanı için)."""
        predictions = self.predict(rows)
        out = []
        for index, values in predictions.iterrows():
            item = {k: rows.at[index, k] for k in ("servis_id", "an_utc") if k in rows.columns}
            item.update({k: float(v) for k, v in values.items()})
            item["model"] = self.manifest["model"]
            item["model_sha256"] = self.manifest["model_sha256"]
            out.append(item)
        return out
