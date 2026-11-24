"""Aşama 5 / U3 akışı: karar anı t → tahmin girdileri → U1 güvenli tahmin → ihtiyaç–kapasite karşılaştırması.

Zaman güvenliği:
- Tahmin girdileri yalnız `forecasting.dataset.known_view(tables, t)` görünümünden kurulur (Aşama 4
  sözleşmesi); hedef ve bağlam sütunları (gelecek gerçekleşme, gelecek kapasite) atılır.
- Kapasite yalnız `unit_capacity_at(plan, t, ...)` ile; gerçekleşen vardiya tabloları kullanılmaz.
- t, veri kümesinin 15 dakikalık tahmin ızgarasında ve geçerli tahmin anları içinde olmalıdır.
Model yeniden eğitilmez, kalibrasyon değişmez; üretici modülleri içe aktarılmaz (veri kümesi parametredir).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, tzinfo
from typing import Any

import pandas as pd

from medakis.domain.models import Staff
from medakis.domain.planning import PlanSet
from medakis.domain.service_calendar import ServiceCalendar
from medakis.domain.time import ensure_utc
from medakis.domain.workload import WINDOW_MINUTES
from medakis.forecasting.calibration import Calibration, guarded_candidates, safe_forecast
from medakis.forecasting.dataset import INPUT_COLUMNS, _Axis, build_rows, known_view, origin_indices
from medakis.optimization.comparison import HORIZON_MINUTES, UnitInfo, compare, demand_from_u1


def decision_inputs(base_ds: Any, at: datetime) -> pd.DataFrame:
    """t anında bilinen tablolardan servis başına bir girdi satırı (yalnız kimlik + INPUT_COLUMNS)."""
    at = ensure_utc(at, "Karar anı")
    axis = _Axis(base_ds.scenario_start, base_ds.scenario_now)
    offset = at - axis.start
    step = timedelta(minutes=WINDOW_MINUTES)
    if offset % step != timedelta(0):
        raise ValueError(f"Karar anı 15 dakikalık tahmin ızgarasında değil: {at.isoformat()}")
    j = offset // step
    if j not in set(origin_indices(axis)):
        raise ValueError(f"Karar anı geçerli tahmin anları dışında (geçmiş 1 saat ve 60 dk ufuk gerekir): {at.isoformat()}")
    rows = build_rows(base_ds, tables=known_view(base_ds.tables, at), origins=[j])
    return rows[["servis_id", "an_utc", *INPUT_COLUMNS]].reset_index(drop=True)


def forecast_at(frame: pd.DataFrame, models: Mapping[str, Any], cal: Calibration, manifests: Mapping[str, dict],
                versions: Mapping[str, str]) -> pd.DataFrame:  # fmt: skip
    """U1 güvenli tahmin (tahmin öncesi doğrulamalı)."""
    cands = guarded_candidates(frame, dict(models), dict(manifests), cal)
    return safe_forecast(frame, cands, cal, dict(manifests), dict(versions))


def units_of(base_ds: Any) -> dict[int, UnitInfo]:
    return {u["id"]: UnitInfo(u["id"], u["name"], u["kind"]) for u in base_ds.tables["units"]}


def run_comparison(base_ds: Any, plan: PlanSet, staff: Mapping[int, Staff], at: datetime, models: Mapping[str, Any],
                   cal: Calibration, manifests: Mapping[str, dict], versions: Mapping[str, str],
                   calendars: Sequence[ServiceCalendar], local_tz: tzinfo) -> list[dict[str, Any]]:  # fmt: skip
    """Tek karar anı için bütün servis × rol karşılaştırmaları (60 dk ufuk)."""
    frame = decision_inputs(base_ds, at)
    out = forecast_at(frame, models, cal, manifests, versions)
    demands = demand_from_u1(out, manifests, HORIZON_MINUTES)
    return compare(at, demands, plan, staff, units_of(base_ds), calendars, local_tz, HORIZON_MINUTES)
