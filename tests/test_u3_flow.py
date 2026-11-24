"""Aşama 5 / U3 akışı: karar anı girdileri, U1 güvenli tahmin, U2 plan ve karşılaştırma (veritabanısız).

Küçük veri kümesi (28 gün) ve küçük model; Aşama 4 modelleri yeniden eğitilmez, U1 kalibrasyonu
değiştirilmez. Kurallar, kullanım oranı ve takvim 'test_sentetik' etiketlidir.
"""

from dataclasses import replace
from datetime import timedelta
from datetime import time as dtime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from medakis.domain.planning import PlanningConfig, StaffingRule
from medakis.domain.service_calendar import always_open, weekly_calendar
from medakis.domain.time import Interval
from medakis.forecasting import calibration as C
from medakis.forecasting import u1_run as U
from medakis.forecasting.dataset import CONTEXT_COLUMNS, CONTRACT_VERSION, INPUT_COLUMNS, TARGET_COLUMNS, SplitPlan
from medakis.forecasting.dataset import build_rows, build_training_frame
from medakis.forecasting.dataset import known_view as tables_known_view
from medakis.forecasting.models import ForestModel
from medakis.optimization import comparison as X
from medakis.optimization.u3_flow import decision_inputs, run_comparison
from medakis.synthetic.v2 import ParamsV2
from medakis.synthetic.v2.calendar import HALF_DAY_START, day_infos
from medakis.synthetic.v21 import ParamsV21, generate_v21

TZ = ZoneInfo("Europe/Istanbul")
SRC = "test_sentetik"


@pytest.fixture(scope="module")
def ds():
    return generate_v21(ParamsV21(base=ParamsV2(days=28)))


@pytest.fixture(scope="module")
def tf(ds):
    return build_training_frame(ds.base, SplitPlan(2, 1, 1))


@pytest.fixture(scope="module")
def models(tf):
    forest = ForestModel({"n_estimators": 15, "min_samples_leaf": 5, "max_features": 0.5}).fit(U.part(tf, "egitim"))
    return U.build_models(tf, forest)


@pytest.fixture(scope="module")
def cal(tf, models):
    return U.calibrate(tf, models, C.CalibrationConfig(min_group_size=50), {"model_sha256": {}})


def manifests():
    good = {"sozlesme_surumu": CONTRACT_VERSION, "surumler": {"scikit_learn": C.sklearn.__version__},
            "girdiler": list(INPUT_COLUMNS), "model_sha256": "test"}  # fmt: skip
    return {"random_forest": good, "baseline_bilgi": good}


@pytest.fixture(scope="module")
def setup(ds):
    base = ds.base
    known = base.scenario_start - timedelta(days=30)
    rules = tuple(StaffingRule(u, r, (0, 24), 1 if r == "nurse" else 0, SRC, "test", known, None, known)
                  for u in (1, 2, 3, 4) for r in ("nurse", "supervisor", "support"))  # fmt: skip
    plan = replace(ds.plan, staffing_rules=rules, config=PlanningConfig(0.85, 0.35, 1.0, 10.0, SRC))
    p = base.params
    days = [(d.day, d.holiday.kind if d.holiday else "yok") for d in day_infos(p.start_day, p.days + 1)]
    cover = Interval(base.scenario_start, base.scenario_now + timedelta(days=1))
    cals = [always_open(u, cover, SRC, "test", known) for u in (1, 2, 3)]
    cals.append(weekly_calendar(4, days, {wd: (dtime(8), dtime(17)) for wd in range(5)}, HALF_DAY_START, p.timezone,
                                SRC, "test", known))  # fmt: skip
    return plan, cals


def _moment(tf, hour, weekday=1, part="test"):
    rows = U.part(tf, part)
    row = rows[(rows["yerel_saat"] == hour) & (rows["haftanin_gunu"] == weekday) & (rows["servis_id"] == 1)].iloc[0]
    return pd.Timestamp(row["an_utc"]).to_pydatetime()


def _run(ds, setup, models, cal, at, mans=None, base=None):
    plan, cals = setup
    mans = mans or manifests()
    versions = {k: f"{k}@test" for k in mans}
    return run_comparison(base or ds.base, plan, ds.staff(), at, models, cal, mans, versions, cals, TZ)


def test_four_units_three_roles_and_traceable_model(ds, tf, setup, models, cal):
    at = _moment(tf, 10)
    rows = _run(ds, setup, models, cal, at)
    assert {(r["servis_id"], r["rol"]) for r in rows} == {(u, r) for u in (1, 2, 3, 4) for r in ("nurse", "supervisor", "support")}
    assert {r["servis_turu"] for r in rows} == {"inpatient", "emergency", "outpatient"}
    for r in rows:
        assert r["karar_ani"] == at and r["model"] in ("random_forest", "baseline_bilgi") and r["model_sha256"] == "test"
        assert r["insan_onayi_gerekli"] is True and not set(r) & set(X.DECISION_FIELDS_FORBIDDEN)
    by = {(r["servis_id"], r["rol"]): r for r in rows}
    nurse = by[(1, "nurse")]
    assert nurse["durum"] in (X.UYGUN, X.INCELEME, X.IHLAL)
    assert nurse["etkin_kapasite_dk"] == pytest.approx(nurse["kullanilabilir_personel_dk"] * 0.85, abs=1e-5)


def test_decision_inputs_use_only_knowable_data(ds, tf):
    at = _moment(tf, 10)
    frame = decision_inputs(ds.base, at)
    assert list(frame.columns) == ["servis_id", "an_utc", *INPUT_COLUMNS]
    assert not set(frame.columns) & (set(TARGET_COLUMNS) | set(CONTEXT_COLUMNS))
    j = int((at - ds.base.scenario_start) / timedelta(minutes=15))
    full = build_rows(ds.base, origins=[j])[["servis_id", "an_utc", *INPUT_COLUMNS]]
    pd.testing.assert_frame_equal(frame, full)  # tam veri ve t görünümü aynı girdiyi verir (sızıntı yok)


def test_result_independent_of_information_after_t(ds, tf, setup, models, cal):
    at = _moment(tf, 10)
    truncated = SimpleNamespace(**vars(ds.base))
    truncated.tables = tables_known_view(ds.base.tables, at)  # t sonrası gerçekleşme ve kayıtlar yok
    assert _run(ds, setup, models, cal, at) == _run(ds, setup, models, cal, at, base=truncated)


def test_same_decision_time_same_result(ds, tf, setup, models, cal):
    at = _moment(tf, 14)
    assert _run(ds, setup, models, cal, at) == _run(ds, setup, models, cal, at)


def test_decision_time_must_be_on_grid_and_in_range(ds, tf):
    at = _moment(tf, 10)
    with pytest.raises(ValueError, match="ızgara"):
        decision_inputs(ds.base, at + timedelta(minutes=7))
    with pytest.raises(ValueError, match="geçerli tahmin anları"):
        decision_inputs(ds.base, ds.base.scenario_now)  # 60 dk ufuk dönem dışında


def test_incompatible_model_gives_no_numeric_comparison(ds, tf, setup, models, cal):
    at = _moment(tf, 10)
    bad = manifests()
    bad["random_forest"] = {**bad["random_forest"], "sozlesme_surumu": "9.9"}
    rows = _run(ds, setup, models, cal, at, mans=bad)
    for r in rows:
        assert r["tamponlu_talep_dk"] is None and r["acik_dk"] is None and r["fazla_dk"] is None
        assert r["durum"] != X.UYGUN and "gecerli_tahmin_yok" in r["nedenler"]


def test_outpatient_closed_at_night_never_silently_ok(ds, tf, setup, models, cal):
    at = _moment(tf, 2)
    by = {(r["servis_id"], r["rol"]): r for r in _run(ds, setup, models, cal, at)}
    clinic = by[(4, "nurse")]
    assert clinic["takvim_durumu"] == "kapali"
    assert clinic["durum"] == X.KAPALI or "kapali_serviste_talep" in clinic["nedenler"]
    plan, cals = setup
    no_cal = run_comparison(ds.base, plan, ds.staff(), at, models, cal, manifests(), {}, cals[:3], TZ)
    clinic = [r for r in no_cal if (r["servis_id"], r["rol"]) == (4, "nurse")][0]
    assert clinic["durum"] == X.VERI_EKSIK and "planli_vardiya_yok" in clinic["nedenler"]


def test_sudden_absence_report_boundary_in_generated_plan(ds, setup, models, cal):
    base = ds.base
    step = timedelta(minutes=15)
    absence = next(u for u in ds.plan.unavailability if u.reason == "devamsizlik" and u.recorded_at > u.starts_at + step
                   and u.starts_at > base.scenario_start + timedelta(days=2) and u.ends_at < base.scenario_now - timedelta(hours=2))  # fmt: skip
    shift = next(s for s in ds.plan.shifts if s.staff_id == absence.staff_id and s.starts_at == absence.starts_at)
    before = base.scenario_start + ((absence.recorded_at - base.scenario_start - timedelta(microseconds=1)) // step) * step  # kesin önce
    after = before + step
    plan, cals = setup
    without = (replace(plan, unavailability=tuple(u for u in plan.unavailability if u is not absence)), cals)
    key = (shift.unit_id, shift.role)

    def pick(rows):
        return next(r for r in rows if (r["servis_id"], r["rol"]) == key)

    # Bildirimden önce: sonuç, kayıt hiç yokmuş gibi (gelecek bilgisi sızmaz).
    assert _run(ds, setup, models, cal, before) == _run(ds, without, models, cal, before)
    # Bildirimden sonra: plan aynı, kullanılabilir dakika düşer.
    r_after, r_without = pick(_run(ds, setup, models, cal, after)), pick(_run(ds, without, models, cal, after))
    assert r_after["planli_personel_dk"] == r_without["planli_personel_dk"]
    assert r_after["kullanilabilir_personel_dk"] < r_without["kullanilabilir_personel_dk"]
