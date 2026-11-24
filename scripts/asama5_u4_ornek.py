"""Aşama 5 / U4: gerçek Aşama 4 / U1 modelleriyle Katman A çalıştırmaları (rapor için; veritabanısız).

    .\\.venv\\Scripts\\python.exe scripts\\asama5_u4_ornek.py

U3 örnek betiğindeki 'test_sentetik' kurallar, takvimler ve kullanım oranı aynen kullanılır; ek olarak
U4 için 'test_sentetik' destek yetkileri, servis-yetkinlik eşlemesi, öncelik ağırlıkları ve fazla
toleransı tanımlanır. Hiçbiri gerçek klinik standart değildir. Çıktı: docs/veri/asama5_u4_ornekler.json.
"""

import json
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.domain.planning import FloatRule, PlanningConfig, StaffingRule  # noqa: E402
from medakis.domain.service_calendar import always_open, weekly_calendar  # noqa: E402
from medakis.domain.time import Interval  # noqa: E402
from medakis.forecasting.calibration import load_calibration  # noqa: E402
from medakis.forecasting.registry import load_model  # noqa: E402
from medakis.optimization.layer_a import jsonable_result, optimize  # noqa: E402
from medakis.optimization.layer_a_contracts import (  # noqa: E402
    LayerAInputs,
    OptimizationConfig,
    UnitSkillRequirement,
    staff_skills_from_tables,
)
from medakis.optimization.u3_flow import run_comparison, units_of  # noqa: E402
from medakis.synthetic.v2.calendar import HALF_DAY_START, day_infos  # noqa: E402
from medakis.synthetic.v21 import generate_v21  # noqa: E402

RF_DIR = ROOT / "artifacts" / "forecasting" / "asama4_seed42" / "random_forest"
INFO_DIR = ROOT / "artifacts" / "forecasting" / "asama5_u1" / "baseline_bilgi"
CAL_FILE = ROOT / "artifacts" / "forecasting" / "asama5_u1" / "kalibrasyon.json"
OUT = ROOT / "docs" / "veri" / "asama5_u4_ornekler.json"
SRC = "test_sentetik: U4 örnek değeri; yetkili kaynak değildir"


def main() -> int:
    started = time.perf_counter()
    ds = generate_v21()
    base = ds.base
    tz = ZoneInfo(base.params.timezone)
    forest, rf_manifest = load_model(RF_DIR)
    info, info_manifest = load_model(INFO_DIR)
    cal = load_calibration(CAL_FILE.read_text(encoding="utf-8"))
    models = {"random_forest": forest, "baseline_bilgi": info}
    manifests = {"random_forest": rf_manifest, "baseline_bilgi": info_manifest}
    versions = {k: f"{k}@{m['model_sha256'][:12]}" for k, m in manifests.items()}
    known = base.scenario_start - timedelta(days=30)
    rules = [StaffingRule(u, role, (0, 24), n, SRC, "test", known, None, known)
             for u in (1, 2, 3, 4)
             for role, n in (("nurse", 2 if u in (1, 2, 3) else 1), ("supervisor", 1 if u in (1, 2, 3) else 0), ("support", 0))]  # fmt: skip
    floats = (FloatRule("nurse", None, (1, 2), ("MON", "MED"), 60, known, None, known, SRC + " (A-B)"),
              FloatRule("nurse", None, (3,), ("TRIAGE",), 60, known, None, known, SRC + " (acil)"))  # fmt: skip
    reqs = tuple(UnitSkillRequirement(u, "nurse", sk, SRC, "test", known) for u, sk in ((1, ("MON", "MED")), (2, ("MON", "MED")), (3, ("TRIAGE",))))
    plan = replace(ds.plan, staffing_rules=tuple(rules), float_rules=floats, config=PlanningConfig(0.85, 0.35, 1.0, 10.0, SRC))
    p = base.params
    days = [(d.day, d.holiday.kind if d.holiday else "yok") for d in day_infos(p.start_day, p.days + 1)]
    coverage = Interval(base.scenario_start, base.scenario_now + timedelta(days=1))
    calendars = [always_open(u, coverage, SRC, "test", known) for u in (1, 2, 3)]
    calendars.append(weekly_calendar(4, days, {wd: (dtime(8), dtime(17)) for wd in range(5)}, HALF_DAY_START, p.timezone, SRC, "test", known))
    config = OptimizationConfig({u: 1 for u in (1, 2, 3, 4)}, {u: 30 for u in (1, 2, 3, 4)}, 10.0, SRC, "test", known)
    skills, catalog = staff_skills_from_tables(base.tables)
    staff = ds.staff()
    units = units_of(base)
    runs = {}
    for day in range(11, 16):
        for hour in (10, 14, 22):
            at = datetime(2027, 1, day, hour, tzinfo=tz).astimezone(UTC)
            rows = run_comparison(base, plan, staff, at, models, cal, manifests, versions, calendars, tz)
            inp = LayerAInputs(at, plan, staff, units, calendars, rows, skills, catalog, reqs, (), config, tz)
            t0 = time.perf_counter()
            res = optimize(inp)
            again = optimize(inp)
            strip = lambda r: {k: v for k, v in r.items() if k != "cozucu_asamalari"}  # noqa: E731
            runs[at.astimezone(tz).strftime("%Y-%m-%d %H:%M")] = {
                "durum": res["durum"], "nedenler": res["nedenler"], "uyarilar": res["uyarilar"],
                "u3_durumlari": dict(Counter(r["durum"] for r in rows)),
                "asamalar": res["cozucu_asamalari"], "dogrulama": res["dogrulama"],
                "oneri_yayinlanabilir": res["oneri_yayinlanabilir"], "tasimalar": res["tasimalar"],
                "kilitli": res["kilitli_yerlesimler"], "tani": res["tani"],
                "servis_rol": [{k: v for k, v in r.items() if k != "atomlar"} for r in res["servis_rol"]],
                "deterministik": jsonable_result(strip(res)) == jsonable_result(strip(again)),
                "u4_sure_sn": round((time.perf_counter() - t0) / 2, 3),
            }  # fmt: skip
    summary = {
        "varsayimlar": {"kaynak": SRC, "destek_kurallari": [f.source for f in floats],
                        "yetkinlik_esleme": {q.unit_id: q.required_skills for q in reqs},
                        "agirliklar": dict(config.weights), "fazla_toleransi": dict(config.surplus_tolerance)},
        "durum_sayilari": dict(Counter(r["durum"] for r in runs.values())),
        "calistirmalar": runs,
        "sure_sn": round(time.perf_counter() - started, 1),
    }  # fmt: skip
    OUT.write_text(json.dumps(jsonable_result(summary), ensure_ascii=False, indent=1), encoding="utf-8")
    for k, r in runs.items():
        print(k, r["durum"], r["u3_durumlari"], r["nedenler"][:3], [(m["personel"], m["son_onayli_servis"], m["hedef_servis"]) for m in r["tasimalar"]],
              r["dogrulama"]["gecti"], r["deterministik"], r["u4_sure_sn"])
    print(summary["durum_sayilari"], summary["sure_sn"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
