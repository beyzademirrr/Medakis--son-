"""Aşama 4 tahmin modelleri: mevsimsel ortalama baseline ve Random Forest; metrikler.

Hedef: gelecek 30/60 dakikalık TALEP (tahmini dakika), toplam ve rol bazında (nurse, supervisor,
support). Gerçekleşen iş yalnız ikincil analizdir. Girdi olarak yalnız sözleşmenin INPUT_COLUMNS
alanları kullanılır; hedef, bağlam, bölüm ve kimlik sütunları modele hiçbir zaman verilmez.
Veritabanı ve Streamlit bağımlılığı yoktur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from medakis.forecasting.dataset import INPUT_COLUMNS, ROLES

DEMAND_TARGETS = ("talep_dk_30", "talep_dk_60", *[f"talep_dk_{h}_{r}" for h in (30, 60) for r in ROLES])
REALIZED_TARGETS = ("gerceklesen_dk_30", "gerceklesen_dk_60")
CATEGORICAL_INPUTS = ("servis_turu", "servis_profili", "tatil_turu")
NUMERIC_INPUTS = tuple(c for c in INPUT_COLUMNS if c not in CATEGORICAL_INPUTS)
RANDOM_STATE = 42

# Baseline anahtarı (görev tanımı): servis türü × haftanın günü × yerel saat. Görülmeyen kombinasyon
# için eğitim verisinden geri dönüş sırası: servis türü × saat → servis türü → genel ortalama.
BASELINE_KEYS = (("servis_turu", "haftanin_gunu", "yerel_saat"), ("servis_turu", "yerel_saat"), ("servis_turu",), ())

# Bilgi amaçlı daha güçlü varyant (seçimde KULLANILMAZ): servis türü+profil × gün × günün dakikası.
INFO_BASELINE_KEYS = (
    ("servis_turu", "servis_profili", "haftanin_gunu", "gunun_dakikasi"),
    ("servis_turu", "servis_profili", "gunun_dakikasi"),
    ("servis_turu", "servis_profili"),
    ("servis_turu",),
    (),
)

# Doğrulamada karşılaştırılan az sayıda açık aday (arama yok).
RF_CANDIDATES: dict[str, dict[str, Any]] = {
    "rf_a_derin": {"n_estimators": 200, "max_depth": None, "min_samples_leaf": 5, "max_features": 1.0},
    "rf_b_yaprak10": {"n_estimators": 200, "max_depth": None, "min_samples_leaf": 10, "max_features": 0.5},
    "rf_c_sinirli": {"n_estimators": 200, "max_depth": 12, "min_samples_leaf": 5, "max_features": 0.5},
}


def model_inputs(frame: pd.DataFrame) -> pd.DataFrame:
    """Modelin göreceği tek tablo: yalnız INPUT_COLUMNS, sözleşmedeki sırayla."""
    missing = [c for c in INPUT_COLUMNS if c not in frame.columns]
    if missing:
        raise KeyError(f"Girdi sütunları eksik: {missing}")
    return frame.loc[:, list(INPUT_COLUMNS)]


@dataclass
class SeasonalBaseline:
    """Mevsimsel ortalama: yalnız eğitim bölümünden öğrenir; tahminde hedef sütunu okumaz."""

    targets: tuple[str, ...] = DEMAND_TARGETS
    tables: list[tuple[tuple[str, ...], pd.DataFrame]] = field(default_factory=list)
    global_mean: dict[str, float] = field(default_factory=dict)
    name: str = "baseline"
    keys: tuple[tuple[str, ...], ...] = BASELINE_KEYS

    def fit(self, train: pd.DataFrame) -> SeasonalBaseline:
        if "bolum" in train.columns and set(train["bolum"]) - {"egitim"}:
            raise ValueError("Baseline yalnız eğitim bölümünden öğrenir.")
        self.tables = []
        for keys in self.keys:
            if keys:
                self.tables.append((keys, train.groupby(list(keys))[list(self.targets)].mean()))
        self.global_mean = {t: float(train[t].mean()) for t in self.targets}
        return self

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        x = model_inputs(frame)
        out = pd.DataFrame(np.nan, index=x.index, columns=list(self.targets))
        for keys, table in self.tables:
            todo = out[self.targets[0]].isna()
            if not todo.any():
                break
            joined = x.loc[todo, list(keys)].join(table, on=list(keys))
            out.loc[todo, list(self.targets)] = joined[list(self.targets)].to_numpy()
        for t in self.targets:
            out[t] = out[t].fillna(self.global_mean[t])
        return out.clip(lower=0.0)


LAG_COLUMNS = tuple(f"gecmis_is_dk_l{k}" for k in range(1, 5))
LAST_FOUR_TARGETS = ("talep_dk_30", "talep_dk_60", "gerceklesen_dk_30", "gerceklesen_dk_60")


@dataclass
class LastFourBaseline:
    """Şartname §5 referansı: son dört erişilebilir 15 dk pencerenin ortalama iş hızı, ufka ölçeklenir.

    YALNIZ KARŞILAŞTIRMA REFERANSIDIR: model seçimine, U1 kalibrasyonuna ve optimizasyona girmez; öğrenme yoktur.
    Girdi `gecmis_is_dk_l1..l4`: t'ye kadar TAMAMLANMIŞ görevlerin geçmiş dört penceredeki payı (gerçekleşen iş).
    Rol kırılımlı gecikme girdisi olmadığından yalnız toplam hedefler için tanımlıdır. Talep hedefinde büyüklük
    farkı vardır (geçmiş gerçekleşen iş → gelecek talep); gerçekleşen hedeflerinde aynı büyüklük karşılaştırılır.
    Geçmiş eksik ya da sonlu değilse tahmin üretilmez (NaN; şartname: yetersiz geçmişte tahmin yok).
    """

    targets: tuple[str, ...] = LAST_FOUR_TARGETS
    name: str = "son_dort_pencere"

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        lags = frame.loc[:, list(LAG_COLUMNS)].astype(float)
        finite = np.isfinite(lags.to_numpy()).all(axis=1)
        rate = lags.mean(axis=1, skipna=False).where(finite)  # dk / 15 dk pencere
        out = {t: (rate * (int(t.split("_")[2]) / 15)).clip(lower=0.0) for t in self.targets}
        return pd.DataFrame(out, index=frame.index)


def make_forest(params: dict[str, Any]) -> Pipeline:
    encoder = ColumnTransformer(
        [("kategori", OneHotEncoder(handle_unknown="ignore", sparse_output=False), list(CATEGORICAL_INPUTS)),
         ("sayi", "passthrough", list(NUMERIC_INPUTS))],  # fmt: skip
        verbose_feature_names_out=False,
    )
    forest = RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1, **params)
    return Pipeline([("donusum", encoder), ("orman", forest)])


@dataclass
class ForestModel:
    """Çok çıktılı Random Forest (her hedef ayrı çıktı); negatif tahminler sıfırda kesilir."""

    params: dict[str, Any]
    targets: tuple[str, ...] = DEMAND_TARGETS
    pipeline: Pipeline | None = None
    name: str = "random_forest"

    def fit(self, train: pd.DataFrame) -> ForestModel:
        if "bolum" in train.columns and set(train["bolum"]) - {"egitim"}:
            raise ValueError("Model yalnız eğitim bölümünden öğrenir.")
        self.pipeline = make_forest(self.params)
        self.pipeline.fit(model_inputs(train), train[list(self.targets)].to_numpy())
        return self

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.pipeline is None:
            raise RuntimeError("Model eğitilmedi.")
        values = np.asarray(self.pipeline.predict(model_inputs(frame)))
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        return pd.DataFrame(np.clip(values, 0.0, None), index=frame.index, columns=list(self.targets))

    def feature_names(self) -> list[str]:
        return list(self.pipeline.named_steps["donusum"].get_feature_names_out()) if self.pipeline else []


# ---- Metrikler -------------------------------------------------------------------------------


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float | int | None]:
    y_true, y_pred = np.asarray(y_true, float), np.asarray(y_pred, float)
    err = y_pred - y_true
    total = float(np.abs(y_true).sum())
    zero = y_true == 0
    var = float(((y_true - y_true.mean()) ** 2).sum())
    return {
        "n": int(len(y_true)),
        "mae": float(np.abs(err).mean()) if len(err) else None,
        "rmse": float(np.sqrt((err**2).mean())) if len(err) else None,
        "wape": float(np.abs(err).sum() / total) if total > 0 else None,
        "r2": float(1 - (err**2).sum() / var) if var > 0 else None,
        "sifir_talep_n": int(zero.sum()),
        "sifir_talep_mae": float(np.abs(err[zero]).mean()) if zero.any() else None,
        "ortalama_talep": float(y_true.mean()) if len(y_true) else None,
    }


def breakdown(frame: pd.DataFrame, predictions: pd.DataFrame, targets: tuple[str, ...]) -> pd.DataFrame:
    """Hedef (ufuk × rol) × servis kırılımı ve 'tum' satırı."""
    rows = []
    for target in targets:
        groups = [("tum", frame.index)] + [(str(u), frame.index[frame["servis_id"] == u]) for u in sorted(frame["servis_id"].unique())]
        for unit, idx in groups:
            m = metrics(frame.loc[idx, target].to_numpy(), predictions.loc[idx, target].to_numpy())
            kind = "tum" if unit == "tum" else frame.loc[idx, "servis_turu"].iloc[0]
            profile = "tum" if unit == "tum" else frame.loc[idx, "servis_profili"].iloc[0]
            rows.append({"hedef": target, "servis": unit, "tur": kind, "profil": profile, **m})
    return pd.DataFrame(rows)


def mean_mae(frame: pd.DataFrame, predictions: pd.DataFrame, targets: tuple[str, ...]) -> float:
    """Seçim ölçütü: hedeflerin MAE ortalaması (tüm servisler, eşit ağırlık)."""
    return float(np.mean([np.abs(predictions[t] - frame[t]).mean() for t in targets]))
