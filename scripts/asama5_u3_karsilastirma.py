"""Aşama 5 / U3: örnek ihtiyaç–kapasite karşılaştırmaları (rapor için; veritabanısız, salt okunur).

    .\\.venv\\Scripts\\python.exe scripts\\asama5_u3_karsilastirma.py

Aşama 4 RF modeli ve U1 bilgi baseline'ı özet denetimiyle yüklenir; U1 kalibrasyonu yeniden ayarlanmadan
dosyadan okunur. Asgari personel kuralları, kullanım oranı ve çalışma takvimi AÇIKÇA 'test_sentetik'
etiketli örnek değerlerdir; gerçek hastane standardı veya çalışma saati değildir. Çıktı:
docs/veri/asama5_u3_ornekler.json.
"""

import json
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.domain.planning import PlanningConfig, StaffingRule  # noqa: E402
from medakis.domain.service_calendar import always_open, weekly_calendar  # noqa: E402
from medakis.domain.time import Interval  # noqa: E402
from medakis.forecasting.calibration import load_calibration  # noqa: E402
from medakis.forecasting.registry import file_sha256, load_model  # noqa: E402
from medakis.optimization.comparison import jsonable  # noqa: E402
from medakis.optimization.u3_flow import run_comparison  # noqa: E402
from medakis.synthetic.v2.calendar import HALF_DAY_START, day_infos  # noqa: E402
from medakis.synthetic.v21 import generate_v21  # noqa: E402

RF_DIR = ROOT / "artifacts" / "forecasting" / "asama4_seed42" / "random_forest"
INFO_DIR = ROOT / "artifacts" / "forecasting" / "asama5_u1" / "baseline_bilgi"
CAL_FILE = ROOT / "artifacts" / "forecasting" / "asama5_u1" / "kalibrasyon.json"
CAL_SHA = "02fbfbe371f5d64e"  # U1 raporundaki özetin başı
OUT = ROOT / "docs" / "veri" / "asama5_u3_ornekler.json"
TEST_SOURCE = "test_sentetik: U3 örnek değeri; yetkili kaynak değildir"


def local(tz, y, m, d, hh, mm=0):  # noqa: ANN001, ANN201
    return datetime(y, m, d, hh, mm, tzinfo=tz).astimezone(UTC)


def main() -> int:
    started = time.perf_counter()
    ds = generate_v21()
    base = ds.base
    tz = ZoneInfo(base.params.timezone)
    forest, rf_manifest = load_model(RF_DIR)
    info, info_manifest = load_model(INFO_DIR)
    cal = load_calibration(CAL_FILE.read_text(encoding="utf-8"))
    if not cal.sha256().startswith(CAL_SHA):
        raise SystemExit("Kalibrasyon özeti U1 raporuyla aynı değil; durduruldu.")
    models = {"random_forest": forest, "baseline_bilgi": info}
    manifests = {"random_forest": rf_manifest, "baseline_bilgi": info_manifest}
    versions = {k: f"{k}@{m['model_sha256'][:12]}" for k, m in manifests.items()}

    known = base.scenario_start - timedelta(days=30)
    rules = []
    for unit in (1, 2, 3, 4):  # 7/24 bant; örnek değerler (test_sentetik)
        for role, n in (("nurse", 2 if unit in (1, 2, 3) else 1), ("supervisor", 1 if unit in (1, 2, 3) else 0), ("support", 0)):
            rules.append(StaffingRule(unit, role, (0, 24), n, TEST_SOURCE, "test", known, None, known))
    config = PlanningConfig(0.85, 0.35, 1.0, 10.0, TEST_SOURCE)
    plan = replace(ds.plan, staffing_rules=tuple(rules), config=config)
    p = base.params
    days = [(d.day, d.holiday.kind if d.holiday else "yok") for d in day_infos(p.start_day, p.days + 1)]
    coverage = Interval(base.scenario_start, base.scenario_now + timedelta(days=1))
    calendars = [always_open(u, coverage, TEST_SOURCE, "test", known) for u in (1, 2, 3)]
    calendars.append(weekly_calendar(4, days, {wd: (dtime(8), dtime(17)) for wd in range(5)}, HALF_DAY_START,
                                     p.timezone, TEST_SOURCE + " (üretici poliklinik şablonu 08:00-17:00)", "test", known))  # fmt: skip
    staff = ds.staff()

    # Ani devamsızlık örneği: bildirim öncesi ve sonrası (15 dk ızgarası)
    step = timedelta(minutes=15)
    absence = next(u for u in plan.unavailability if u.reason == "devamsizlik" and u.recorded_at > u.starts_at + step
                   and u.starts_at > base.scenario_start + timedelta(days=60))  # fmt: skip
    before = base.scenario_start + ((absence.recorded_at - base.scenario_start - timedelta(microseconds=1)) // step) * step
    after = before + step
    moments = {
        "hafta_ici_10": local(tz, 2027, 1, 12, 10),
        "gece_02_poliklinik_kapali": local(tz, 2027, 1, 12, 2),
        "yarim_gun_12_30_kismi": local(tz, 2026, 10, 28, 12, 30),
        "yarim_gun_13_00_kapali": local(tz, 2026, 10, 28, 13),
        "tam_gun_tatil_10": local(tz, 2026, 10, 29, 10),
        "devamsizlik_bildirim_oncesi": before,
        "devamsizlik_bildirim_sonrasi": after,
    }
    results = {}
    for name, at in moments.items():
        rows = run_comparison(base, plan, staff, at, models, cal, manifests, versions, calendars, tz)
        results[name] = jsonable(rows)
    no_calendar = run_comparison(base, plan, staff, moments["gece_02_poliklinik_kapali"], models, cal, manifests, versions,
                                 calendars[:3], tz)  # fmt: skip
    results["gece_02_takvimsiz"] = jsonable([r for r in no_calendar if r["servis_id"] == 4])
    again = run_comparison(base, plan, staff, moments["hafta_ici_10"], models, cal, manifests, versions, calendars, tz)
    summary = {
        "kalibrasyon_sha256": cal.sha256(), "kalibrasyon_dosyasi_sha256": file_sha256(CAL_FILE),
        "model_sha256": {k: m["model_sha256"] for k, m in manifests.items()},
        "uretici": {"taban_icerik_sha256": ds.base_content_sha256(), "plan_sha256": ds.plan_sha256()},
        "varsayimlar": {"kaynak": TEST_SOURCE, "kullanim_orani": config.utilization_target,
                        "asgari_personel": [{"servis_id": r.unit_id, "rol": r.role, "bant": list(r.band), "asgari": r.min_staff} for r in rules],
                        "poliklinik_takvimi": "Pzt-Cum 08:00-17:00; yarım gün 13:00; tam gün tatil ve hafta sonu kapalı"},
        "devamsizlik_ornegi": {"personel": absence.staff_id, "baslangic": absence.starts_at.isoformat(),
                               "bildirim": absence.recorded_at.isoformat()},
        "deterministik": jsonable(again) == results["hafta_ici_10"],
        "anlar": {k: v.isoformat() for k, v in moments.items()},
        "sonuclar": results,
        "sure_sn": round(time.perf_counter() - started, 1),
    }  # fmt: skip
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, rows in results.items():
        print(name, [(r["servis_id"], r["rol"], r["durum"]) for r in rows if r["rol"] == "nurse"])
    print("deterministik", summary["deterministik"], "süre", summary["sure_sn"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
