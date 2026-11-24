"""Aşama 5 / U1: rol bazında model seçimi, toplam–rol tutarlılığı, belirsizlik tamponu, dağılım dışı denetim.

Kurallar (Aşama 5 tasarımı §2–§3):
- Model seçimi ve bütün kalibrasyon parametreleri YALNIZ doğrulama bölümünden öğrenilir; test
  bölümü yalnız raporlanır (kapsama, uyarı oranı) ve hiçbir parametreyi değiştirmez.
- Karar tabanı rol tahminleridir; toplam tahmin yalnız tutarlılık denetimidir. Rol tahminleri asla
  azaltılmaz, pozitif fark rollere dağıtılmaz; büyük fark 'inceleme_gerekli' üretir.
- Tampon = doğrulama artıklarının (y − ŷ) niceliği. İstatistiksel GARANTİ değildir; sentetik veriye
  dayanır. Dağılım dışı girdide tahmin güvenli kabul edilmez.
Personel sayısı, vardiya veya atama önerisi üretilmez. Veritabanı bağımlılığı yoktur.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import sklearn

from medakis.forecasting.dataset import CONTRACT_VERSION, INPUT_COLUMNS, ROLES
from medakis.forecasting.models import DEMAND_TARGETS

CALIBRATION_FORMAT = "medakis-kalibrasyon/1"
HORIZONS = (30, 60)
UYGUN, INCELEME = "uygun", "inceleme_gerekli"
HOUR_BANDS = ((0, 8, "gece"), (8, 16, "gunduz"), (16, 24, "aksam"))
OOD_NUMERIC = ("hasta_sayisi", "bekleyen_dk", "bilinen_plan_dk_30", "bilinen_plan_dk_60")
CATEGORICAL = ("servis_turu", "servis_profili", "tatil_turu")
# Birikimli toplamların kayan nokta artığı (ör. −1e-12) negatif girdi sayılmaz.
NEGATIVE_TOLERANCE = 1e-6
# Geçerli kategori alanları (veri sözleşmesi ve şema): bunların dışı GEÇERSİZ girdidir ve modele verilmez.
# Geçerli ama eğitimde görülmemiş kombinasyon ayrı bir dağılım dışı uyarısıdır (gorulmemis_kategori).
CATEGORY_DOMAINS = {
    "servis_turu": ("inpatient", "emergency", "outpatient"),
    "tatil_turu": ("yok", "tam", "yarim_13"),
}


@dataclass(frozen=True)
class CalibrationConfig:
    """Bütün eşikler yapılandırılabilir ve rapora yazılır (tasarım varsayımları, K-3/K-5)."""

    min_relative_gain: float = 0.10  # seçim: en az %10 iyileşme
    min_absolute_gain: float = 0.25  # ve en az 0,25 dk
    quantiles: tuple[float, ...] = (0.80, 0.90)
    alpha: float = 0.90  # durum ve tamponlu talep için kullanılan nicelik
    relative_units: tuple[str, ...] = ("emergency",)  # göreli tampon kullanılan servis türleri
    relative_epsilon: float = 1.0  # göreli artıkta payda alt sınırı (dk)
    min_group_size: int = 200  # bant grubu için en az örnek; altı geri döner
    consistency_multiplier: float = 2.0  # |toplam − Σrol| > çarpan × τ → inceleme
    buffer_ratio_limit: dict[str, float] = field(default_factory=lambda: {"emergency": 0.5, "*": 0.35})
    buffer_ratio_min_minutes: float = 10.0  # oran denetimi yalnız tampon bu değeri aşarsa
    ood_quantile: float = 0.995  # eğitim aralığı: [1 − q, q] nicelikleri
    ood_margin: float = 0.0  # aralık genişletme payı (oran)


# RF paralel tahmininde ağaç toplamının sırası değişebilir (~1e-15 fark). Kalibrasyon kararları ve özeti
# bu gürültüden etkilenmesin diye tahminler 9 ondalığa yuvarlanarak kullanılır (Aşama 4 modeli değişmez).
STABLE_DECIMALS = 9


def stable(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.astype(float).round(STABLE_DECIMALS)


def _band(hour: int) -> str | None:
    """Saat bandı; geçersiz saatte None (tampon aranmaz)."""
    return next((name for lo, hi, name in HOUR_BANDS if lo <= hour < hi), None)


def _role_of(target: str) -> str:
    parts = target.split("_")
    return parts[3] if len(parts) == 4 else "toplam"


def _horizon_of(target: str) -> int:
    return int(target.split("_")[2])


# ---- Rol bazında model seçimi ------------------------------------------------------------------


def selection_table(val: pd.DataFrame, candidates: dict[str, pd.DataFrame], config: CalibrationConfig) -> pd.DataFrame:
    """Hedef × servis doğrulama MAE tablosu ve seçim. Varsayılan 'random_forest'; diğer aday ancak
    hem göreli hem mutlak eşiği aşarak daha iyiyse seçilir."""
    rows = []
    candidates = {name: stable(pred) for name, pred in candidates.items()}
    for target in DEMAND_TARGETS:
        for unit in sorted(val["servis_id"].unique()):
            mask = val["servis_id"] == unit
            y = val.loc[mask, target].to_numpy()
            maes = {name: round(float(np.abs(pred.loc[mask, target].to_numpy() - y).mean()), STABLE_DECIMALS)
                    for name, pred in candidates.items()}  # fmt: skip
            chosen, reason = "random_forest", "varsayılan (fark eşiği aşılmadı)"
            rf = maes["random_forest"]
            for name, mae in maes.items():
                if name == "random_forest":
                    continue
                gain = rf - mae
                if gain >= config.min_absolute_gain and rf > 0 and gain / rf >= config.min_relative_gain:
                    chosen = name
                    reason = f"doğrulama MAE {rf:.3f} → {mae:.3f} (−{gain:.3f} dk, %{100 * gain / rf:.1f})"
            rows.append({"hedef": target, "rol": _role_of(target), "ufuk": _horizon_of(target), "servis_id": int(unit),
                         "servis_turu": val.loc[mask, "servis_turu"].iloc[0], **{f"mae_{k}": v for k, v in maes.items()},
                         "secilen": chosen, "gerekce": reason, "n": int(mask.sum())})  # fmt: skip
    return pd.DataFrame(rows)


def combine(candidates: dict[str, pd.DataFrame], frame: pd.DataFrame, choice: dict[tuple[str, int], str]) -> pd.DataFrame:
    """Satırın servisine göre seçilen modelin tahminini alır; negatifleri sıfırlar."""
    out = pd.DataFrame(index=frame.index, columns=list(DEMAND_TARGETS), dtype=float)
    for target in DEMAND_TARGETS:
        values = np.full(len(frame), np.nan)
        for unit in frame["servis_id"].unique():
            if not valid_unit_id(unit):  # geçersiz kimlik: tahmin seçilemez, NaN kalır
                continue
            mask = (frame["servis_id"] == unit).to_numpy()
            name = choice.get((target, int(unit)), "random_forest")
            values[mask] = candidates[name].loc[frame.index[mask], target].to_numpy()
        out[target] = np.clip(np.round(values, STABLE_DECIMALS), 0.0, None)
    return out


# ---- Tutarlılık ---------------------------------------------------------------------------------


def discrepancy(pred: pd.DataFrame, horizon: int) -> pd.Series:
    """d = ŷ_toplam − Σ ŷ_rol (pozitif: toplam model rollerden fazla iş görüyor)."""
    roles = sum(pred[f"talep_dk_{horizon}_{r}"] for r in ROLES)
    return pred[f"talep_dk_{horizon}"] - roles


# ---- Tampon kalibrasyonu ------------------------------------------------------------------------


def _quantile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(values, q, method="higher")) if len(values) else float("nan")


@dataclass
class Calibration:
    config: CalibrationConfig
    selection: list[dict[str, Any]]
    tolerance: dict[str, float]  # "servis_id:ufuk" -> τ (toplam hedefin doğrulama MAE'si)
    buffers: dict[str, float]  # "servis_id|hedef|bant|q|yöntem" -> nicelik
    buffer_fallbacks: list[str]
    ood_ranges: dict[str, list[float]]  # "servis_id|sütun" -> [alt, üst]
    seen_combinations: list[list[str]]
    provenance: dict[str, Any]

    def choice(self) -> dict[tuple[str, int], str]:
        return {(r["hedef"], int(r["servis_id"])): r["secilen"] for r in self.selection}

    def to_json(self) -> str:
        payload = {"bicim": CALIBRATION_FORMAT, **asdict(self)}
        payload["config"] = asdict(self.config)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1, default=str)

    def sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


def load_calibration(text: str, expected_sha256: str | None = None) -> Calibration:
    """Kayıtlı kalibrasyonu YENİDEN AYARLAMADAN yükler. Gidiş-dönüş metni birebir aynı değilse veya özet
    beklenenden farklıysa reddeder (sessiz sürüm/biçim kayması olmaz)."""
    data = json.loads(text)
    if data.pop("bicim", None) != CALIBRATION_FORMAT:
        raise ValueError("Kalibrasyon biçimi tanınmıyor.")
    raw = data.pop("config")
    config = CalibrationConfig(**{k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()})
    cal = Calibration(config=config, **data)
    if cal.to_json() != text:
        raise ValueError("Kalibrasyon dosyası gidiş-dönüşte aynı çıkmadı; yüklenmedi.")
    if expected_sha256 is not None and cal.sha256() != expected_sha256:
        raise ValueError("Kalibrasyon özeti beklenenden farklı; yüklenmedi.")
    return cal


def _method(kind: str, config: CalibrationConfig) -> str:
    return "goreli" if kind in config.relative_units else "mutlak"


def fit_calibration(train: pd.DataFrame, val: pd.DataFrame, candidates_val: dict[str, pd.DataFrame],
                    config: CalibrationConfig, provenance: dict[str, Any]) -> Calibration:  # fmt: skip
    """Yalnız eğitim (OOD aralıkları, görülen kategoriler) ve doğrulama (seçim, τ, tampon) kullanır."""
    if set(train["bolum"]) != {"egitim"} or set(val["bolum"]) != {"dogrulama"}:
        raise ValueError("Kalibrasyon yalnız eğitim ve doğrulama bölümleriyle yapılır; test kullanılmaz.")
    table = selection_table(val, candidates_val, config)
    choice = {(r["hedef"], int(r["servis_id"])): r["secilen"] for r in table.to_dict("records")}
    pred = combine(candidates_val, val, choice)

    tolerance = {}
    for unit in sorted(val["servis_id"].unique()):
        mask = val["servis_id"] == unit
        for h in HORIZONS:
            t = f"talep_dk_{h}"
            tolerance[f"{int(unit)}:{h}"] = round(float(np.abs(pred.loc[mask, t] - val.loc[mask, t]).mean()), STABLE_DECIMALS)

    buffers: dict[str, float] = {}
    fallbacks: list[str] = []
    bands = val["yerel_saat"].map(_band)
    for target in DEMAND_TARGETS:
        resid_all = (val[target] - pred[target]).to_numpy()
        for unit in sorted(val["servis_id"].unique()):
            umask = (val["servis_id"] == unit).to_numpy()
            kind = val.loc[umask, "servis_turu"].iloc[0]
            for method in ("mutlak", "goreli"):
                denom = np.maximum(pred[target].to_numpy(), config.relative_epsilon)
                values = resid_all if method == "mutlak" else resid_all / denom
                for _lo, _hi, band in HOUR_BANDS:
                    bmask = umask & (bands == band).to_numpy()
                    source = values[bmask]
                    if len(source) < config.min_group_size:
                        source = values[umask]  # geri dönüş 1: servis düzeyi (bütün bantlar)
                        fallbacks.append(f"{int(unit)}|{target}|{band}|{method}: bant n={int(bmask.sum())} → servis")
                        if len(source) < config.min_group_size:
                            same_kind = (val["servis_turu"] == kind).to_numpy()
                            source = values[same_kind]  # geri dönüş 2: aynı servis türü
                            fallbacks.append(f"{int(unit)}|{target}|{band}|{method}: servis n={int(umask.sum())} → tür")
                    for q in config.quantiles:
                        buffers[f"{int(unit)}|{target}|{band}|{q}|{method}"] = round(max(0.0, _quantile(source, q)), STABLE_DECIMALS)
    ood: dict[str, list[float]] = {}
    for unit in sorted(train["servis_id"].unique()):
        rows = train[train["servis_id"] == unit]
        for column in OOD_NUMERIC:
            lo = float(np.quantile(rows[column], 1 - config.ood_quantile))
            hi = float(np.quantile(rows[column], config.ood_quantile))
            pad = (hi - lo) * config.ood_margin
            ood[f"{int(unit)}|{column}"] = [lo - pad, hi + pad]
    seen = sorted({(str(a), str(b)) for a, b in train[["servis_turu", "servis_profili"]].itertuples(index=False)})
    return Calibration(config, table.to_dict("records"), tolerance, buffers, sorted(set(fallbacks)), ood,
                       [list(s) for s in seen], provenance)  # fmt: skip


def buffer_for(cal: Calibration, frame: pd.DataFrame, pred: pd.DataFrame, target: str, q: float | None = None,
               method: str | None = None) -> np.ndarray:  # fmt: skip
    q = cal.config.alpha if q is None else q
    out = np.zeros(len(frame))
    bands = frame["yerel_saat"].map(_band).to_numpy()
    units = frame["servis_id"].to_numpy()
    kinds = frame["servis_turu"].to_numpy()
    yhat = pred[target].to_numpy()
    for i in range(len(frame)):
        if not valid_unit_id(units[i]) or bands[i] is None or not math.isfinite(yhat[i]):  # geçersiz satır: tampon yok
            out[i] = np.nan
            continue
        m = method or _method(kinds[i], cal.config)
        value = cal.buffers.get(f"{int(units[i])}|{target}|{bands[i]}|{q}|{m}")
        if value is None:  # eğitimde/doğrulamada bulunmayan servis: tampon bilinmiyor
            out[i] = np.nan
            continue
        out[i] = value * max(yhat[i], cal.config.relative_epsilon) if m == "goreli" else value
    return np.where(np.isnan(out), np.nan, np.clip(out, 0.0, None))


def coverage(frame: pd.DataFrame, pred: pd.DataFrame, buffers: np.ndarray, target: str) -> float:
    """Gözlenen kapsama: y ≤ ŷ + B olan satırların oranı (yalnız rapor)."""
    ok = frame[target].to_numpy() <= pred[target].to_numpy() + np.nan_to_num(buffers, nan=0.0) + 1e-9
    return float(ok.mean()) if len(ok) else float("nan")


# ---- Dağılım dışı ve uyumluluk denetimi ------------------------------------------------------


def input_problems(frame: pd.DataFrame) -> list[str]:
    """Çerçeve düzeyinde eksik/geçersiz girdi denetimi (sütun yoksa satır denetimi yapılamaz)."""
    missing = [c for c in ("servis_id", *INPUT_COLUMNS) if c not in frame.columns]
    return [f"eksik_girdi:{c}" for c in missing]


def valid_unit_id(value: Any) -> bool:
    """Servis kimliği pozitif tam sayı olmalı. 1, np.int64(1) ve 1.0 geçerli; 1.5, 0, negatif, bool, metin,
    NaN ve sonsuz geçersiz. Kesirli kimlik int() ile SESSİZCE başka servise dönüştürülmez."""
    if isinstance(value, (bool, np.bool_)):
        return False
    if isinstance(value, (int, np.integer)):
        return int(value) >= 1
    if isinstance(value, (float, np.floating)):
        return math.isfinite(float(value)) and float(value).is_integer() and float(value) >= 1
    return False


def compatibility_problems(manifests: dict[str, dict[str, Any]], cal: Calibration) -> list[str]:
    problems = []
    for name, manifest in manifests.items():
        if manifest.get("sozlesme_surumu") != CONTRACT_VERSION:
            problems.append(f"uyumsuz_sozlesme:{name}")
        if manifest.get("surumler", {}).get("scikit_learn") != sklearn.__version__:
            problems.append(f"uyumsuz_sklearn:{name}")
        if manifest.get("girdiler") not in (None, list(INPUT_COLUMNS)):
            problems.append(f"uyumsuz_girdiler:{name}")
        expected = cal.provenance.get("model_sha256", {}).get(name)
        if expected and manifest.get("model_sha256") != expected:
            problems.append(f"farkli_model:{name}")
    return problems


def _value_problem(column: str, value: Any) -> str | None:
    """Tek girdi değeri: eksik, NaN, sonsuz, sayısal olmayan, negatif veya alan dışı kategori."""
    if column in CATEGORICAL:
        if not isinstance(value, str) or not value.strip():
            return f"gecersiz_girdi:{column}"
        domain = CATEGORY_DOMAINS.get(column)
        return f"gecersiz_kategori:{column}" if domain is not None and value not in domain else None
    if value is None or isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)):
        return f"gecersiz_girdi:{column}"
    if not math.isfinite(float(value)):
        return f"gecersiz_girdi:{column}"
    if value < -NEGATIVE_TOLERANCE:
        return f"negatif_girdi:{column}"
    return None


def row_input_problems(frame: pd.DataFrame) -> list[list[str]]:
    """Satır başına girdi geçerliliği. Sorunlu satır modele VERİLMEZ (tahmin öncesi denetim)."""
    columns = [c for c in INPUT_COLUMNS if c in frame.columns]
    out: list[list[str]] = []
    for row in frame[columns].itertuples(index=False, name=None):
        out.append([p for c, v in zip(columns, row) if (p := _value_problem(c, v)) is not None])
    unit_ok = frame["servis_id"].map(valid_unit_id) if "servis_id" in frame else None
    if unit_ok is not None:
        for i, ok in enumerate(unit_ok.to_numpy()):
            if not ok:
                out[i].append("gecersiz_kimlik:servis_id")
    return out


def guarded_candidates(frame: pd.DataFrame, models: dict[str, Any], manifests: dict[str, dict[str, Any]] | None = None,
                       cal: Calibration | None = None) -> dict[str, pd.DataFrame]:  # fmt: skip
    """Model tahmini ÖNCESİ doğrulama: sütun eksikse veya model uyumsuzsa hiç tahmin yapılmaz; geçersiz
    satırlar modele verilmez. Tahmin edilmeyen hücreler NaN kalır (geçerli tahmin izlenimi verilmez)."""
    empty = pd.DataFrame(np.nan, index=frame.index, columns=list(DEMAND_TARGETS), dtype=float)
    out = {name: empty.copy() for name in models}
    if input_problems(frame):
        return out
    if manifests is not None and cal is not None and compatibility_problems(manifests, cal):
        return out
    valid = np.array([not p for p in row_input_problems(frame)], dtype=bool)
    if not valid.any():
        return out
    rows = frame.loc[valid]
    for name, model in models.items():
        out[name].loc[rows.index, list(DEMAND_TARGETS)] = model.predict(rows)[list(DEMAND_TARGETS)].to_numpy(dtype=float)
    return out


def row_warnings(frame: pd.DataFrame, cal: Calibration) -> list[list[str]]:
    seen = {tuple(s) for s in cal.seen_combinations}
    out: list[list[str]] = []
    problems = row_input_problems(frame)
    for i, (_, row) in enumerate(frame.iterrows()):
        warnings: list[str] = list(problems[i])
        if (str(row["servis_turu"]), str(row["servis_profili"])) not in seen:
            warnings.append(f"gorulmemis_kategori:{row['servis_turu']}/{row['servis_profili']}")
        if "gecersiz_kimlik:servis_id" in warnings:
            out.append(warnings)
            continue
        for column in OOD_NUMERIC:
            bounds = cal.ood_ranges.get(f"{int(row['servis_id'])}|{column}")
            if bounds is None:
                warnings.append(f"bilinmeyen_servis:{row['servis_id']}")
                break
            value = row[column]
            if isinstance(value, (int, float, np.number)) and math.isfinite(value) and not bounds[0] <= value <= bounds[1]:
                warnings.append(f"dagilim_disi:{column}")
        out.append(warnings)
    return out


# ---- Güvenli karar destek çıktısı ----------------------------------------------------------------


def safe_forecast(frame: pd.DataFrame, candidates: dict[str, pd.DataFrame], cal: Calibration,
                  manifests: dict[str, dict[str, Any]], model_versions: dict[str, str]) -> pd.DataFrame:  # fmt: skip
    """Satır = servis × an × ufuk × rol. Personel sayısı veya atama ÜRETMEZ."""
    if input_problems(frame):
        raise KeyError(f"Girdi sütunları eksik: {input_problems(frame)}")
    frame_problems = compatibility_problems(manifests, cal)
    warnings = row_warnings(frame, cal)
    invalid = [bool(p) for p in row_input_problems(frame)]
    # Uyumsuz model veya geçersiz girdi: aday tahminler kullanılmaz (dışarıdan verilmiş olsalar bile).
    usable = {name: c.copy() for name, c in candidates.items()}
    for c in usable.values():
        if frame_problems:
            c.loc[:, :] = np.nan
        else:
            c.loc[frame.index[np.array(invalid, dtype=bool)], :] = np.nan
    pred = combine(usable, frame, cal.choice())
    limit = cal.config.buffer_ratio_limit
    records = []
    for h in HORIZONS:
        d = discrepancy(pred, h).to_numpy()
        roles_sum = sum(pred[f"talep_dk_{h}_{r}"] for r in ROLES).to_numpy()
        for role in ROLES:
            target = f"talep_dk_{h}_{role}"
            buf = buffer_for(cal, frame, pred, target)
            for i, (index, row) in enumerate(frame.iterrows()):
                unit = int(row["servis_id"]) if "gecersiz_kimlik:servis_id" not in warnings[i] else None
                reasons = list(frame_problems) + list(warnings[i])
                yhat = float(pred.at[index, target])
                if math.isnan(yhat):  # tahmin yapılmadı: sayı yok, tampon yok, inceleme zorunlu
                    reasons.append("gecerli_tahmin_yok")
                    b = float("nan")
                else:
                    tau = cal.tolerance.get(f"{unit}:{h}")
                    if tau is None:
                        reasons.append("tolerans_yok")
                    elif abs(d[i]) > cal.config.consistency_multiplier * tau:
                        reasons.append("toplam_rol_tutarsiz")
                    b = float(buf[i])
                    if math.isnan(b):
                        reasons.append("tampon_yok")
                        b = 0.0
                    ratio = limit.get(row["servis_turu"], limit["*"])
                    if b > cal.config.buffer_ratio_min_minutes and b > ratio * max(yhat, cal.config.relative_epsilon):
                        reasons.append("yuksek_goreli_tampon")
                chosen = cal.choice().get((target, unit), "random_forest")
                records.append({
                    "servis_id": unit, "an_utc": row["an_utc"], "rol": role, "ufuk_dk": h,
                    "model": chosen, "model_surumu": model_versions.get(chosen, "?"),
                    "nokta_tahmin_dk": yhat, "tampon_dk": b, "tamponlu_talep_dk": yhat + b,
                    "rol_toplami_dk": float(roles_sum[i]), "toplam_tahmin_dk": float(pred.at[index, f"talep_dk_{h}"]),
                    "tutarsizlik_dk": float(d[i]), "uyarilar": sorted(set(warnings[i])),
                    "durum": INCELEME if reasons else UYGUN, "inceleme_nedenleri": sorted(set(reasons)),
                    "aciklama": "Tampon, doğrulama dönemindeki eksik tahminlerden türetilmiş sentetik bir paydır; "
                                "istatistiksel garanti değildir. Personel sayısı veya atama önerisi değildir.",
                })  # fmt: skip
    return pd.DataFrame(records)
