"""Aşama 5 / U6: Katman B görev ataması uygulanabilirlik denetimi — 104 karar anı (veritabanısız, salt okunur).

    .\\.venv\\Scripts\\python.exe scripts\\asama5_u6_degerlendirme.py

Karar anları Aşama 5 güvenli iyileştirme değerlendirmesiyle aynıdır (docs/veri/asama5_guvenli_duzeltme2.json).
Görevler üretici 2.0.0 tablolarından t anında bilinen görünümle (tasks_known_at) alınır; plan üretici 2.1'in
planlı vardiya ve uygunsuzluklarıdır (known_view). Rezerv için ŷ, Aşama 4 RF + U1 kalibrasyonu (yeniden eğitim veya
ayar YOK) ile U3 akışından alınır. Gerçek atama yapılmaz; hiçbir tabloya yazılmaz. Her sonuç bağımsız doğrulayıcıdan
yeniden geçirilir. Çıktı: docs/veri/asama5_u6_katman_b.json ve .csv
"""

import csv
import json
import math
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "docs" / "veri"


def conv(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): conv(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [conv(x) for x in v]
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def main() -> int:  # noqa: PLR0915
    from medakis.domain.service_calendar import always_open, weekly_calendar
    from medakis.domain.time import Interval
    from medakis.forecasting import calibration as C
    from medakis.forecasting.registry import load_model
    from medakis.optimization import layer_b as B
    from medakis.optimization import layer_b_verifier as BV
    from medakis.optimization.layer_a_contracts import staff_skills_from_tables
    from medakis.optimization.layer_b_contracts import GAP_REASONS, LayerBConfig, LayerBInputs, tasks_known_at
    from medakis.optimization.u3_flow import run_comparison
    from medakis.synthetic.v2.calendar import HALF_DAY_START, day_infos
    from medakis.synthetic.v21 import generate_v21

    started = time.perf_counter()
    ds = generate_v21()
    base = ds.base
    tz = ZoneInfo(base.params.timezone)
    forest, rf_m = load_model(ROOT / "artifacts/forecasting/asama4_seed42/random_forest")
    info, info_m = load_model(ROOT / "artifacts/forecasting/asama5_u1/baseline_bilgi")
    cal = C.load_calibration((ROOT / "artifacts/forecasting/asama5_u1/kalibrasyon.json").read_text(encoding="utf-8"))
    assert cal.sha256().startswith("02fbfbe371f5d64e")
    assert ds.base_content_sha256().startswith("2a1fdfb0") and ds.plan_sha256().startswith("09c0315d")
    models = {"random_forest": forest, "baseline_bilgi": info}
    mans = {"random_forest": rf_m, "baseline_bilgi": info_m}
    versions = {k: f"{k}@{m['model_sha256'][:12]}" for k, m in mans.items()}
    src = "test_sentetik: U6 değerlendirme; yetkili kaynak değildir"
    known = base.scenario_start - timedelta(days=30)
    p = base.params
    days = [(d.day, d.holiday.kind if d.holiday else "yok") for d in day_infos(p.start_day, p.days + 1)]
    cover = Interval(base.scenario_start, base.scenario_now + timedelta(days=1))
    calendars = [always_open(u, cover, src, "test", known) for u in (1, 2, 3)]
    calendars.append(weekly_calendar(4, days, {wd: (dtime(8), dtime(17)) for wd in range(5)}, HALF_DAY_START, p.timezone, src, "test", known))
    skills, catalog = staff_skills_from_tables(base.tables)
    staff = ds.staff()
    units = (1, 2, 3, 4)
    config = LayerBConfig(10.0, src, "test", known)
    moments = [datetime.fromisoformat(r["an"]) for r in
               json.loads((OUT / "asama5_guvenli_duzeltme2.json").read_text(encoding="utf-8"))["calistirmalar"]]  # fmt: skip
    assert len(moments) == 104

    runs = []
    for at in moments:
        rows = run_comparison(base, ds.plan, staff, at, models, cal, mans, versions, calendars, tz)
        forecasts = {(r["servis_id"], r["rol"]): float(r["nokta_tahmin_dk"]) for r in rows
                     if r.get("nokta_tahmin_dk") is not None and math.isfinite(float(r["nokta_tahmin_dk"]))}  # fmt: skip
        tasks, uncertain = tasks_known_at(base.tables, at, units)
        inp = LayerBInputs(at, ds.plan, staff, units, tasks, skills, catalog, config, forecasts, uncertain)
        t0 = time.perf_counter()
        res = B.check_tasks(inp)
        el = time.perf_counter() - t0
        sol = res["cozum"]
        rec = {"an": at, "yerel": at.astimezone(tz).strftime("%Y-%m-%d %H:%M"), "durum": res["durum"],
               "gosterilebilir": res["denetim_gosterilebilir"], "nedenler": res["nedenler"], "uyarilar": res["uyarilar"],
               "asamalar": [(s["asama"], s["durum"], s["amac_degeri"]) for s in res["cozucu_asamalari"]], "sure_sn": round(el, 4),
               "gercek_atama_yok": res["gercek_atama_yok"], "kayit_yok": res["kayit_yok"]}  # fmt: skip
        if sol is not None:
            again = BV.verify_tasks(inp, sol)
            rec.update({
                "yeniden_dogrulama": again.passed, "ihlaller": again.violations, "kapsam": sol["kapsam"],
                "atanan_gorev": len(sol["atamalar"]), "atanmayan_gorev": len(sol["atanmayanlar"]),
                "atanmayan_nedenleri": dict(Counter(g["neden"] for g in sol["atanmayanlar"])),
                "acik_dk_nedenleri": {r: sum(g["acik_dk"] for g in sol["atanmayanlar"] if g["neden"] == r) for r in GAP_REASONS},
                "amac": sol["amac_degerleri"],
                "rol_bazinda": {r: {"atanan": sum(1 for a in sol["atamalar"] if a["rol"] == r),
                                    "atanmayan": sum(1 for g in sol["atanmayanlar"] if g["rol"] == r)} for r in ("nurse", "supervisor", "support")},
                "rezerv_toplam_dk": round(sum(x["rezerv_dk"] or 0.0 for x in sol["rezerv"]), 3),
                "tahminsiz_rezerv_satiri": sum(1 for x in sol["rezerv"] if x["rezerv_dk"] is None),
            })  # fmt: skip
        runs.append(rec)
        print(len(runs), rec["yerel"], rec["durum"], rec.get("kapsam", {}).get("pencerede_son_tarihli_gorev"),
              rec.get("atanan_gorev"), rec.get("atanmayan_nedenleri"), rec.get("yeniden_dogrulama"), rec["sure_sn"])  # fmt: skip

    shown = [r for r in runs if r["gosterilebilir"]]
    summary = {
        "an_sayisi": len(runs), "durumlar": dict(Counter(r["durum"] for r in runs)),
        "gosterilebilir": len(shown), "yeniden_dogrulama_hepsi": all(r["yeniden_dogrulama"] for r in shown),
        "ihlal_toplam": sum(len(r["ihlaller"]) for r in shown),
        "gercek_atama_veya_kayit": sum(1 for r in runs if not (r["gercek_atama_yok"] and r["kayit_yok"])),
        "kapsamdaki_gorev": sum(r["kapsam"]["pencerede_son_tarihli_gorev"] for r in shown),
        "sonraki_pencere_gorev": sum(r["kapsam"]["sonraki_pencere_gorev"] for r in shown),
        "iptal_zamani_belirsiz_gorev_ust": max((r["kapsam"]["iptal_zamani_belirsiz_gorev"] for r in shown), default=0),
        "atanan_gorev": sum(r["atanan_gorev"] for r in shown), "atanmayan_gorev": sum(r["atanmayan_gorev"] for r in shown),
        "atanmayan_nedenleri": dict(sum((Counter(r["atanmayan_nedenleri"]) for r in shown), Counter())),
        "acik_dk_nedenleri": dict(sum((Counter(r["acik_dk_nedenleri"]) for r in shown), Counter())),
        "somut_gorev_dk": sum(r["kapsam"]["somut_gorev_dk"] for r in shown), "atanan_dk": sum(r["kapsam"]["atanan_dk"] for r in shown),
        "rol_bazinda": {role: {k: sum(r["rol_bazinda"][role][k] for r in shown) for k in ("atanan", "atanmayan")}
                        for role in ("nurse", "supervisor", "support")},
        "rezerv_toplam_dk": round(sum(r["rezerv_toplam_dk"] for r in shown), 3),
        "sozluk_sirali_tam": sum(1 for r in runs if all(s[1] == "OPTIMAL" for s in r["asamalar"]) and r["asamalar"]),
        "sure_ortalama_sn": round(sum(r["sure_sn"] for r in runs) / len(runs), 4), "sure_en_buyuk_sn": max(r["sure_sn"] for r in runs),
    }  # fmt: skip
    result = {"ozet": summary, "calistirmalar": runs, "sure_sn": round(time.perf_counter() - started, 1),
              "not": "Uygulanabilirlik denetimi; gerçek atama yok, kayıt yok, sentetik veri. Rezerv kimliksizdir ve atanmaz."}  # fmt: skip
    (OUT / "asama5_u6_katman_b.json").write_text(json.dumps(conv(result), ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "asama5_u6_katman_b.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["yerel", "durum", "gosterilebilir", "kapsam", "sonraki", "atanan", "atanmayan", "nedenler", "O1", "O2", "rezerv_dk",
                    "dogrulama", "sure_sn"])  # fmt: skip
        for r in runs:
            k = r.get("kapsam", {})
            w.writerow([r["yerel"], r["durum"], r["gosterilebilir"], k.get("pencerede_son_tarihli_gorev"), k.get("sonraki_pencere_gorev"),
                        r.get("atanan_gorev"), r.get("atanmayan_gorev"), "|".join(f"{a}={b}" for a, b in (r.get("atanmayan_nedenleri") or {}).items()),
                        (r.get("amac") or {}).get("O1_atanmayan_gorev_dk"), (r.get("amac") or {}).get("O2_yuk_farki_dk"),
                        r.get("rezerv_toplam_dk"), r.get("yeniden_dogrulama"), r["sure_sn"]])  # fmt: skip
    print(json.dumps(summary, ensure_ascii=False), result["sure_sn"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
