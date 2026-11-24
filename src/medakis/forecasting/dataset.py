"""Aşama 4 eğitim veri sözleşmesi ve dönüşümü (model eğitimi YOK).

Kaynak: üretici 2.0.0'ın bellekte ürettiği veri kümesi (`generate_v2`). Veritabanına bağlanılmaz,
seed çalıştırılmaz; `medakis_test` içeriğine bağımlılık yoktur.

Satır = (servis, tahmin anı t). t, 15 dakikalık UTC ızgarasının bir sınırıdır. Sütunlar dört gruptadır:
- kimlik: servis ve t; bölüm: zaman temelli eğitim/doğrulama/test/ambargo etiketi.
- girdi: YALNIZ t anında bilinebilen bilgi (takvim, servis türü, t anındaki bekleyen iş ve hasta,
  t'ye kadar tamamlanmış görevlerden geçmiş iş, t'de bilinen planlı iş, son bir saatteki iptaller).
  Sonradan gerçekleşen tamamlanma/iptal/taburculuk bilgisi kullanılmaz (sızıntı testiyle korunur).
- hedef: [t, t+h) içindeki talep (tahmini dk, durumdan bağımsız) ve gerçekleşen iş (Aşama 3 tanımı).
- bağlam: [t, t+h) kapasitesi ve iptaller. Gelecek bilgisidir; girdi olarak KULLANILMAZ.
Roller yalnız nurse, supervisor, support (hekim yok).
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from medakis.domain.capacity import Unavailability, staff_capacity
from medakis.domain.models import CareTask, Staff, StaffShift
from medakis.domain.time import Interval
from medakis.domain.workload import WINDOW_MINUTES, distribute, task_shares, window_grid

CONTRACT_VERSION = "1.0"
ROLES = ("nurse", "supervisor", "support")
HORIZONS = (30, 60)
LAGS = 4  # son 1 saat, 15 dakikalık pencereler
ID, SPLIT, INPUT, TARGET, CONTEXT = "kimlik", "bolum", "girdi", "hedef", "baglam"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    group: str
    unit: str
    known_at: str
    source: str


def _fields() -> tuple[FieldSpec, ...]:
    f: list[FieldSpec] = [
        FieldSpec("servis_id", ID, "-", "sabit", "units.id"),
        FieldSpec("an_utc", ID, "UTC", "t", "15 dk ızgara sınırı"),
        FieldSpec("bolum", SPLIT, "-", "tasarım", "zaman temelli ayrım + ambargo"),
        FieldSpec("servis_turu", INPUT, "kategori", "sabit", "units.kind"),
        FieldSpec("servis_profili", INPUT, "kategori", "sabit", "üretici profili (normal/yuksek/temel/randevu)"),
        FieldSpec("yerel_saat", INPUT, "saat", "t", "takvim"),
        FieldSpec("gunun_dakikasi", INPUT, "dk", "t", "takvim"),
        FieldSpec("haftanin_gunu", INPUT, "0-6", "t", "takvim"),
        FieldSpec("hafta_sonu", INPUT, "0/1", "t", "takvim"),
        FieldSpec("tatil_turu", INPUT, "yok/tam/yarim_13", "önceden bilinir", "2429 sayılı Kanun takvimi"),
        FieldSpec("tatil_ertesi", INPUT, "0/1", "önceden bilinir", "takvim"),
        FieldSpec("hasta_sayisi", INPUT, "kişi", "t", "encounters: admitted <= t < discharged"),
        FieldSpec("bekleyen_dk", INPUT, "tahmini dk", "t", "pending_at: created <= t < kapanış (tamamlanma/iptal)"),
        *[FieldSpec(f"bekleyen_dk_{r}", INPUT, "tahmini dk", "t", "pending_at, rol bazında") for r in ROLES],
        *[FieldSpec(f"gecmis_is_dk_l{k}", INPUT, "gerçek dk", "t",
                    f"[t-{15 * k}, t-{15 * (k - 1)}) penceresinde, t'ye kadar TAMAMLANMIŞ görevlerin payı") for k in range(1, LAGS + 1)],  # fmt: skip
        FieldSpec("son_15dk_kapasite_dk", INPUT, "personel-dk", "t", "[t-15, t) kullanılabilir zaman (Aşama 3 kapasite tanımı)"),
        FieldSpec("son_1sa_iptal_dk", INPUT, "tahmini dk", "t", "cancelled_at ∈ (t-60, t]"),
        *[FieldSpec(f"bilinen_plan_dk_{h}", INPUT, "tahmini dk", "t",
                    f"t'de bilinen ve açık, vadesi [t, t+{h}) olan görevler") for h in HORIZONS],  # fmt: skip
        *[FieldSpec(f"talep_dk_{h}", TARGET, "tahmini dk", f"t+{h} sonrası", f"vadesi [t, t+{h}) olan bütün görevler") for h in HORIZONS],
        *[FieldSpec(f"talep_dk_{h}_{r}", TARGET, "tahmini dk", f"t+{h} sonrası", "talep, rol bazında") for h in HORIZONS for r in ROLES],
        *[FieldSpec(f"gerceklesen_dk_{h}", TARGET, "gerçek dk", f"t+{h} sonrası (pencere available_at)",
                    f"[t, t+{h}) gerçekleşen iş (Aşama 3 dağıtımı)") for h in HORIZONS],  # fmt: skip
        *[FieldSpec(f"kapasite_dk_{h}_{r}", CONTEXT, "personel-dk", "gelecek", f"[t, t+{h}) kullanılabilir zaman") for h in HORIZONS for r in ROLES],
        *[FieldSpec(f"iptal_dk_{h}_{reason}", CONTEXT, "tahmini dk", "gelecek", f"cancelled_at ∈ [t, t+{h}), neden {reason}")
          for h in HORIZONS for reason in ("clinical", "discharged", "capacity")],  # fmt: skip
    ]
    return tuple(f)


FIELDS = _fields()
INPUT_COLUMNS = tuple(f.name for f in FIELDS if f.group == INPUT)
TARGET_COLUMNS = tuple(f.name for f in FIELDS if f.group == TARGET)
CONTEXT_COLUMNS = tuple(f.name for f in FIELDS if f.group == CONTEXT)


@dataclass(frozen=True)
class SplitPlan:
    """Kronolojik ayrım (hafta). Etiket ufku bir sonraki bölüme taşan anlar 'ambargo' olur."""

    train_weeks: int = 10
    validation_weeks: int = 3
    test_weeks: int = 3


@dataclass
class TrainingFrame:
    frame: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)

    def inputs(self) -> pd.DataFrame:
        return self.frame[list(INPUT_COLUMNS)]

    def frame_sha256(self) -> str:
        return frame_digest(self.frame)


def frame_digest(frame: pd.DataFrame) -> str:
    rounded = frame.copy()
    for column in rounded.columns:
        if rounded[column].dtype.kind == "f":
            rounded[column] = rounded[column].round(6)
    raw = rounded.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ---- Yardımcılar: dakika ekseni ve fark dizileri ----------------------------------------------


class _Axis:
    def __init__(self, start: datetime, end: datetime):
        self.start = start
        self.n = int((end - start).total_seconds() // 60 // WINDOW_MINUTES)  # pencere sayısı; anlar 0..n

    def m(self, value: datetime | None) -> float:
        return math.inf if value is None else (value - self.start).total_seconds() / 60

    def at(self, j: int) -> datetime:
        return self.start + timedelta(minutes=WINDOW_MINUTES * j)


def _range_add(diff: np.ndarray, lo: int, hi: int, value: float) -> None:
    """diff üzerinde [lo, hi] (kapalı) aralığına değer ekler; sınır dışı kırpılır."""
    lo, hi = max(lo, 0), min(hi, len(diff) - 2)
    if lo <= hi:
        diff[lo] += value
        diff[hi + 1] -= value


def _open_count(axis: _Axis, opens: list[float], closes: list[float], weights: list[float]) -> np.ndarray:
    """Her an j için: opens <= t_j < closes olan kayıtların ağırlık toplamı."""
    diff = np.zeros(axis.n + 2)
    for o, c, w in zip(opens, closes, weights):
        lo = math.ceil(o / WINDOW_MINUTES - 1e-9)
        hi = axis.n if math.isinf(c) else math.ceil(c / WINDOW_MINUTES - 1e-9) - 1
        _range_add(diff, lo, hi, w)
    return np.cumsum(diff)[: axis.n + 1]


# ---- Tablolardan alan modeli -------------------------------------------------------------------


def care_task(row: dict) -> CareTask:
    return CareTask(row["id"], row["encounter_id"], row["unit_id"], row["task_type_id"], row["created_at"], row["due_at"],
                    row["estimated_minutes"], row["actual_minutes"], row["status"], row["completed_at"],
                    row.get("cancelled_at"))  # fmt: skip


def known_view(tables: dict[str, list[dict]], at: datetime) -> dict[str, list[dict]]:
    """`at` anında bilinen tablolar: sonradan oluşan kayıtlar çıkar, sonradan olan kapanışlar silinir.

    Sızıntı testinin referansıdır: girdiler tam veriden ve bu görünümden aynı çıkmalıdır.
    """
    out = dict(tables)
    out["encounters"] = [
        {**e, "discharged_at": e["discharged_at"] if e["discharged_at"] is not None and e["discharged_at"] <= at else None}
        for e in tables["encounters"] if e["admitted_at"] <= at
    ]  # fmt: skip
    tasks = []
    for r in tables["care_tasks"]:
        if r["created_at"] > at:
            continue
        r = dict(r)
        if r["status"] == "completed" and r["completed_at"] > at:
            r.update(status="pending", completed_at=None, actual_minutes=None)
        if r["status"] == "cancelled" and r.get("cancelled_at") is not None and r["cancelled_at"] > at:
            r.update(status="pending", cancelled_at=None, cancel_reason=None)
        tasks.append(r)
    out["care_tasks"] = tasks
    out["staff_shifts"] = [s for s in tables["staff_shifts"] if s["starts_at"] <= at]
    out["staff_unavailability"] = [u for u in tables["staff_unavailability"] if u["starts_at"] <= at]
    return out


# ---- Dönüşüm -----------------------------------------------------------------------------------


def _unit_columns(tables: dict[str, list[dict]], unit_id: int, axis: _Axis, roles_of: dict[int, str],
                  origins: list[int]) -> dict[str, np.ndarray]:  # fmt: skip
    tasks = [r for r in tables["care_tasks"] if r["unit_id"] == unit_id]
    cols: dict[str, np.ndarray] = {}
    if any(r["status"] == "cancelled" and r.get("cancelled_at") is None for r in tasks):
        raise ValueError("İptal zamanı olmayan görev: sözleşme yalnız üretici 2.0.0 verisini kabul eder (K3).")

    def close_of(r: dict) -> float:
        if r["status"] == "completed":
            return axis.m(r["completed_at"])
        if r["status"] == "cancelled":
            return axis.m(r["cancelled_at"])
        return math.inf

    created = [axis.m(r["created_at"]) for r in tasks]
    closes = [close_of(r) for r in tasks]
    est = [r["estimated_minutes"] for r in tasks]
    cols["bekleyen_dk"] = _open_count(axis, created, closes, est)
    for role in ROLES:
        mask = [roles_of[r["task_type_id"]] == role for r in tasks]
        cols[f"bekleyen_dk_{role}"] = _open_count(axis, [c for c, k in zip(created, mask) if k],
                                                   [c for c, k in zip(closes, mask) if k], [e for e, k in zip(est, mask) if k])  # fmt: skip

    encs = [e for e in tables["encounters"] if e["unit_id"] == unit_id]
    cols["hasta_sayisi"] = _open_count(axis, [axis.m(e["admitted_at"]) for e in encs],
                                       [axis.m(e["discharged_at"]) for e in encs], [1.0] * len(encs))  # fmt: skip

    W = WINDOW_MINUTES
    for h in HORIZONS:
        known = np.zeros(axis.n + 2)
        demand = np.zeros(axis.n + 2)
        by_role = {role: np.zeros(axis.n + 2) for role in ROLES}
        cancels = {reason: np.zeros(axis.n + 2) for reason in ("clinical", "discharged", "capacity")}
        for r, c, x, e in zip(tasks, created, closes, est):
            d = axis.m(r["due_at"])
            first_after = math.floor((d - h) / W) + 1  # t > due - h
            last_upto = math.floor(d / W + 1e-9)  # t <= due
            _range_add(demand, first_after, last_upto, e)
            _range_add(by_role[roles_of[r["task_type_id"]]], first_after, last_upto, e)
            lo = max(math.ceil(c / W - 1e-9), first_after)
            hi = last_upto if math.isinf(x) else min(last_upto, math.ceil(x / W - 1e-9) - 1)
            _range_add(known, lo, hi, e)
            if r["status"] == "cancelled":
                q = axis.m(r["cancelled_at"])  # q ∈ [t, t+h)
                _range_add(cancels[r["cancel_reason"]], math.floor((q - h) / W) + 1, math.floor(q / W + 1e-9), e)
        cols[f"bilinen_plan_dk_{h}"] = np.cumsum(known)[: axis.n + 1]
        cols[f"talep_dk_{h}"] = np.cumsum(demand)[: axis.n + 1]
        for role in ROLES:
            cols[f"talep_dk_{h}_{role}"] = np.cumsum(by_role[role])[: axis.n + 1]
        for reason, arr in cancels.items():
            cols[f"iptal_dk_{h}_{reason}"] = np.cumsum(arr)[: axis.n + 1]

    recent = np.zeros(axis.n + 2)
    for r, e in zip(tasks, est):
        if r["status"] == "cancelled":
            q = axis.m(r["cancelled_at"])  # q ∈ (t-60, t]
            _range_add(recent, math.ceil(q / W - 1e-9), math.ceil((q + 60) / W - 1e-9) - 1, e)
    cols["son_1sa_iptal_dk"] = np.cumsum(recent)[: axis.n + 1]

    # Gerçekleşen iş (hedef) ve t'ye kadar tamamlanmış geçmiş iş (girdi)
    grid = window_grid(Interval(axis.start, axis.at(axis.n)))
    domain_tasks = [care_task(r) for r in tasks]
    observed = np.array([o.observed_minutes for o in distribute(domain_tasks, grid)] + [0.0] * 4)
    for h in HORIZONS:
        k = h // W
        csum = np.concatenate([[0.0], np.cumsum(observed)])
        cols[f"gerceklesen_dk_{h}"] = np.array([csum[min(j + k, len(observed))] - csum[j] for j in range(axis.n + 1)])
    lags = {k: np.zeros(axis.n + 1) for k in range(1, LAGS + 1)}
    for t in domain_tasks:
        if t.status != "completed":
            continue
        done = axis.m(t.completed_at)
        for w, share in task_shares(t, grid).items():
            for k in range(1, LAGS + 1):
                j = w + k
                if j <= axis.n and done <= W * j + 1e-9:
                    lags[k][j] += share
    for k, arr in lags.items():
        cols[f"gecmis_is_dk_l{k}"] = arr
    return {name: arr[origins] for name, arr in cols.items()}


def _capacity_columns(tables: dict[str, list[dict]], unit_id: int, axis: _Axis, origins: list[int]) -> dict[str, np.ndarray]:
    shifts = [s for s in tables["staff_shifts"] if s["unit_id"] == unit_id]
    staff_ids = {s["staff_id"] for s in shifts}
    plan = Interval(axis.start, axis.at(axis.n) + timedelta(hours=2))
    by_role = {role: np.zeros(axis.n + 8) for role in ROLES}
    unav: dict[int, list[Unavailability]] = defaultdict(list)
    for u in tables["staff_unavailability"]:
        if u["staff_id"] in staff_ids:
            unav[u["staff_id"]].append(Unavailability(u["staff_id"], Interval(u["starts_at"], u["ends_at"]), u["reason"]))
    for person in tables["staff"]:
        if person["id"] not in staff_ids:
            continue
        own = [StaffShift(s["id"], s["staff_id"], s["unit_id"], s["starts_at"], s["ends_at"], s["break_minutes"])
               for s in shifts if s["staff_id"] == person["id"]]  # fmt: skip
        cap = staff_capacity(Staff(person["id"], person["synthetic_code"], person["role"], person["active"]), own,
                             unav[person["id"]], plan)  # fmt: skip
        arr = by_role[person["role"]]
        for interval in cap.available:
            a, b = axis.m(interval.start), axis.m(interval.end)
            w = max(0, math.floor(a / WINDOW_MINUTES))
            while w < len(arr) and WINDOW_MINUTES * w < b:
                lo, hi = max(a, WINDOW_MINUTES * w), min(b, WINDOW_MINUTES * (w + 1))
                if hi > lo:
                    arr[w] += hi - lo
                w += 1
    total = sum(by_role.values())
    cols = {"son_15dk_kapasite_dk": np.array([total[j - 1] if j >= 1 else 0.0 for j in range(axis.n + 1)])}
    for h in HORIZONS:
        k = h // WINDOW_MINUTES
        for role, arr in by_role.items():
            cols[f"kapasite_dk_{h}_{role}"] = np.array([arr[j:j + k].sum() for j in range(axis.n + 1)])
    return {name: arr[origins] for name, arr in cols.items()}


def _calendar_columns(ds, axis: _Axis, origins: list[int]) -> dict[str, list]:  # noqa: ANN001
    tz = ZoneInfo(ds.params.timezone)
    days = {d.day: d for d in ds.calendar}
    out: dict[str, list] = defaultdict(list)
    for j in origins:
        local = axis.at(j).astimezone(tz)
        info = days.get(local.date())
        out["yerel_saat"].append(local.hour)
        out["gunun_dakikasi"].append(local.hour * 60 + local.minute)
        out["haftanin_gunu"].append(local.weekday())
        out["hafta_sonu"].append(int(local.weekday() >= 5))
        out["tatil_turu"].append(info.holiday.kind if info is not None and info.holiday is not None else "yok")
        out["tatil_ertesi"].append(int(info.after_holiday) if info is not None else 0)
    return out


def origin_indices(axis: _Axis) -> list[int]:
    """Tahmin anları: geçmiş 1 saat ve en uzun ufuk dönem içinde kalacak biçimde."""
    return list(range(LAGS, axis.n - max(HORIZONS) // WINDOW_MINUTES + 1))


def build_rows(ds, tables: dict[str, list[dict]] | None = None, origins: list[int] | None = None) -> pd.DataFrame:  # noqa: ANN001
    """Sözleşme sütunlarıyla satırlar (bölüm etiketi olmadan). `tables` verilirse onlar kullanılır."""
    tables = tables if tables is not None else ds.tables
    axis = _Axis(ds.scenario_start, ds.scenario_now)
    origins = origins if origins is not None else origin_indices(axis)
    roles_of = {t["id"]: t["required_role"] for t in tables["task_types"]}
    if set(roles_of.values()) - set(ROLES) or {s["role"] for s in tables["staff"]} - set(ROLES):
        raise ValueError("Yalnız nurse, supervisor, support rolleri modellenir.")
    calendar = _calendar_columns(ds, axis, origins)
    parts = []
    for unit in sorted(tables["units"], key=lambda u: u["id"]):
        kind, profile, _ = ds.unit_profiles[unit["id"]]
        data: dict[str, Any] = {
            "servis_id": [unit["id"]] * len(origins),
            "an_utc": [axis.at(j) for j in origins],
            "servis_turu": [kind] * len(origins),
            "servis_profili": [profile] * len(origins),
            **calendar,
        }
        data.update(_unit_columns(tables, unit["id"], axis, roles_of, origins))
        data.update(_capacity_columns(tables, unit["id"], axis, origins))
        parts.append(pd.DataFrame(data))
    return pd.concat(parts, ignore_index=True)


def assign_splits(frame: pd.DataFrame, start: datetime, plan: SplitPlan = SplitPlan()) -> tuple[pd.Series, dict[str, str]]:
    """Kronolojik bölüm ve ambargo: etiket ufku (t + en uzun ufuk) bölüm sınırını aşan anlar 'ambargo'."""
    b1 = start + timedelta(weeks=plan.train_weeks)
    b2 = b1 + timedelta(weeks=plan.validation_weeks)
    b3 = b2 + timedelta(weeks=plan.test_weeks)
    reach = timedelta(minutes=max(HORIZONS))

    def label(t: datetime) -> str:
        for name, lo, hi in (("egitim", start, b1), ("dogrulama", b1, b2), ("test", b2, b3)):
            if lo <= t < hi:
                return name if t + reach <= hi else "ambargo"
        return "ambargo"

    bounds = {"egitim": f"{start.isoformat()} / {b1.isoformat()}", "dogrulama": f"{b1.isoformat()} / {b2.isoformat()}",
              "test": f"{b2.isoformat()} / {b3.isoformat()}"}  # fmt: skip
    return frame["an_utc"].map(label), bounds


def build_training_frame(ds, plan: SplitPlan = SplitPlan()) -> TrainingFrame:  # noqa: ANN001
    """Üretici 2.0.0 veri kümesinden sözleşmeye uygun eğitim çerçevesi ve köken bilgisi."""
    from medakis.synthetic.v2 import GENERATOR_VERSION

    rows = build_rows(ds)
    split, bounds = assign_splits(rows, ds.scenario_start, plan)
    rows.insert(2, "bolum", split)
    frame = rows[[f.name for f in FIELDS]]
    metadata = {
        "sozlesme_surumu": CONTRACT_VERSION,
        "kaynak": "üretici 2.0.0, bellek içi (veritabanı kullanılmadı)",
        "uretici_surumu": GENERATOR_VERSION,
        "seed": ds.params.seed,
        "parametreler": ds.params.as_dict(),
        "parametre_parmak_izi": ds.params.fingerprint(),
        "donem": f"{ds.scenario_start.isoformat()} / {ds.scenario_now.isoformat()}",
        "icerik_sha256": ds.content_sha256(),
        "talep_sha256": ds.demand_sha256(),
        "ek_kayit_sha256": ds.extras_sha256(),
        "pencere_dk": WINDOW_MINUTES,
        "ufuklar_dk": list(HORIZONS),
        "bolumler": bounds,
        "bolum_sayilari": {k: int(v) for k, v in frame["bolum"].value_counts().sort_index().items()},
        "satir": len(frame),
    }
    result = TrainingFrame(frame, metadata)
    result.metadata["cerceve_sha256"] = result.frame_sha256()
    return result


def from_generator(params=None, plan: SplitPlan = SplitPlan()) -> TrainingFrame:  # noqa: ANN001
    """Kısayol: parametrelerden üret ve dönüştür (yalnız bellek)."""
    from medakis.synthetic.v2 import generate_v2

    return build_training_frame(generate_v2(params), plan)


__all__ = [
    "CONTRACT_VERSION", "FIELDS", "INPUT_COLUMNS", "TARGET_COLUMNS", "CONTEXT_COLUMNS", "SplitPlan", "TrainingFrame",
    "build_rows", "build_training_frame", "from_generator", "known_view", "assign_splits",
]  # fmt: skip
