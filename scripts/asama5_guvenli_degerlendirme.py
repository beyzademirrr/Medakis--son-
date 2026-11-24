"""Aşama 5 güvenli iyileştirmeler: 104 karar anı, iki dondurma politikası yan yana (veritabanısız, salt okunur).

    .\\.venv\\Scripts\\python.exe scripts\\asama5_guvenli_degerlendirme.py --asama golge
    .\\.venv\\Scripts\\python.exe scripts\\asama5_guvenli_degerlendirme.py --asama son
    .\\.venv\\Scripts\\python.exe scripts\\asama5_guvenli_degerlendirme.py --asama duzeltme

Karar anı örneklemi, fikstürler ve modeller U5 değerlendirmesiyle (scripts/asama5_u5_degerlendirme.py) aynıdır;
Aşama 4 RF + U1 kalibrasyonu yeniden eğitilmez veya ayarlanmaz. Her anda U3 bir kez, U4 iki politikayla
("kapali" = küresel ret, varsayılan; "iki_yonlu" = servis-rol dondurma, yalnız açık deneysel modla
"golge_degerlendirme" ve yayın kapalı) çalıştırılır. Üretim yayını ile deneysel doğrulanmış çözüm ayrı sayılır. Bağımsız güvenlik denetimleri:
yayımlanan her çözüm doğrulayıcıdan yeniden geçirilir; dondurulan çiftlerin ve belirsiz servislerin kadrosu
son onaylı yerleşimle karşılaştırılır. Çıktı: docs/veri/asama5_guvenli_{golge|son}.json ve .csv
"""

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "docs" / "veri"
POLICIES = ("kapali", "iki_yonlu")


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


def main() -> int:  # noqa: C901, PLR0915
    ap = argparse.ArgumentParser()
    ap.add_argument("--asama", choices=("golge", "son", "duzeltme", "duzeltme2"), required=True)
    args = ap.parse_args()

    from medakis.domain.planning import FloatRule, PlanningConfig, StaffingRule
    from medakis.domain.service_calendar import always_open, weekly_calendar
    from medakis.domain.time import Interval
    from medakis.forecasting import calibration as C
    from medakis.forecasting import u1_run as U
    from medakis.forecasting.dataset import build_training_frame
    from medakis.forecasting.registry import load_model
    from medakis.optimization import layer_a as L
    from medakis.optimization import verifier as V
    from medakis.optimization.layer_a_contracts import LayerAInputs, OptimizationConfig, UnitSkillRequirement, staff_skills_from_tables
    from medakis.optimization.u3_flow import run_comparison, units_of
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
    src = "test_sentetik: U5 değerlendirme; yetkili kaynak değildir"  # U5 ile birebir aynı fikstürler
    known = base.scenario_start - timedelta(days=30)
    rules = [StaffingRule(u, role, (0, 24), n, src, "test", known, None, known) for u in (1, 2, 3, 4)
             for role, n in (("nurse", 2 if u < 4 else 1), ("supervisor", 1 if u < 4 else 0), ("support", 0))]  # fmt: skip
    floats = (FloatRule("nurse", None, (1, 2), ("MON", "MED"), 60, known, None, known, src + " (A-B)"),
              FloatRule("nurse", None, (3,), ("TRIAGE",), 60, known, None, known, src + " (acil)"))  # fmt: skip
    reqs = tuple(UnitSkillRequirement(u, "nurse", sk, src, "test", known) for u, sk in ((1, ("MON", "MED")), (2, ("MON", "MED")), (3, ("TRIAGE",))))
    plan = replace(ds.plan, staffing_rules=tuple(rules), float_rules=floats, config=PlanningConfig(0.85, 0.35, 1.0, 10.0, src))
    p = base.params
    days = [(d.day, d.holiday.kind if d.holiday else "yok") for d in day_infos(p.start_day, p.days + 1)]
    cover = Interval(base.scenario_start, base.scenario_now + timedelta(days=1))
    calendars = [always_open(u, cover, src, "test", known) for u in (1, 2, 3)]
    calendars.append(weekly_calendar(4, days, {wd: (dtime(8), dtime(17)) for wd in range(5)}, HALF_DAY_START, p.timezone, src, "test", known))
    config = OptimizationConfig({u: 1 for u in (1, 2, 3, 4)}, {u: 30 for u in (1, 2, 3, 4)}, 10.0, src, "test", known)
    skills, catalog = staff_skills_from_tables(base.tables)
    staff, units = ds.staff(), units_of(base)

    # U5 ile aynı katmanlı örneklem
    tf = build_training_frame(base)
    test = U.part(tf, "test")

    def loc(d, h, m=0):
        return datetime(d.year, d.month, d.day, h, m, tzinfo=tz).astimezone(UTC)

    sample: dict[datetime, set[str]] = defaultdict(set)
    first_test = datetime(2027, 1, 4).date()
    for k in range(21):
        for h in (2, 8, 14, 20):
            sample[loc(first_test + timedelta(days=k), h)].add("sistematik_test_donemi")
    for d, h, m in ((datetime(2026, 10, 28).date(), 12, 30), (datetime(2026, 10, 28).date(), 14, 0), (datetime(2026, 10, 29).date(), 10, 0),
                    (datetime(2026, 10, 29).date(), 22, 0), (datetime(2027, 1, 1).date(), 10, 0), (datetime(2027, 1, 1).date(), 22, 0),
                    (datetime(2027, 1, 2).date(), 10, 0)):  # fmt: skip
        sample[loc(d, h, m)].add("tatil_yarim_gun")
    for ts in list(test.groupby("an_utc")["talep_dk_60"].sum().sort_values(ascending=False).index[:10]):
        sample[ts.to_pydatetime()].add("yuksek_talep_gerceklesen_ust10")
    step = timedelta(minutes=15)
    absences = sorted((u for u in ds.plan.unavailability if u.reason == "devamsizlik" and u.recorded_at > u.starts_at
                       and u.starts_at >= loc(first_test, 0) and u.ends_at < base.scenario_now - timedelta(hours=2)),
                      key=lambda u: u.recorded_at)[:8]  # fmt: skip
    for u in absences:
        sample[base.scenario_start + (-(-(u.recorded_at - base.scenario_start) // step)) * step].add("ani_devamsizlik_bildirim_sonrasi")

    runs = []
    for at in sorted(sample):
        rows = run_comparison(base, plan, staff, at, models, cal, mans, versions, calendars, tz)
        inp = LayerAInputs(at, plan, staff, units, calendars, rows, skills, catalog, reqs, (), config, tz)
        rec = {"an": at, "yerel": at.astimezone(tz).strftime("%Y-%m-%d %H:%M"), "hafta_sonu": at.astimezone(tz).weekday() >= 5,
               "katman": sorted(sample[at]), "u3": {f"{r['servis_id']}:{r['rol']}": {"durum": r["durum"], "nedenler": r["nedenler"],
                                                                                   "takvim": r["takvim_durumu"]} for r in rows}}  # fmt: skip
        for policy in POLICIES:
            t0 = time.perf_counter()
            mode = "golge_degerlendirme" if policy == "iki_yonlu" else None  # dondurma yalnız açık deneysel modla
            res = L.optimize(inp, freeze_policy=policy, experimental_mode=mode)
            el = time.perf_counter() - t0
            # Deneysel sızıntı denetimi: normal çıktı alanları boş ve yayın kapalı; içerik yalnız deneysel_sonuc'ta
            leak_free = mode is None or (res["cozum"] is None and res["tasimalar"] == [] and res["kilitli_yerlesimler"] == []
                                         and res["servis_rol"] == [] and not res["oneri_yayinlanabilir"])  # fmt: skip
            if mode is not None:
                res = {**res, **{k: v for k, v in (res["deneysel_sonuc"] or {}).items()
                                 if k in ("cozum", "tasimalar", "kilitli_yerlesimler", "servis_rol")}}  # fmt: skip
            safety = {}
            if res["cozum"] is not None:
                again = V.verify(inp, res["cozum"])
                frozen = {(f["servis_id"], f["rol"]) for f in res["dondurulan_servis_roller"]}
                uncertain = {u for u, _ in frozen}
                expl = {(x["servis_id"], x["rol"]): x for x in res["servis_rol"]}
                safety = {
                    "yeniden_dogrulama": again.passed, "ihlal_yok": not again.violations,
                    "dondurulan_kadro_degismedi": all(expl[k]["onerilen_kisi"] == expl[k]["mevcut_kisi"] for k in frozen),
                    "belirsiz_servis_personel_kaybetmedi": all(expl[(u, r)]["onerilen_kisi"] >= expl[(u, r)]["mevcut_kisi"]
                                                               for u in uncertain for r in ("nurse", "supervisor", "support")),
                    "dondurulana_giris_cikis_yok": all(not ((m["hedef_servis"], m["rol"]) in frozen or m["son_onayli_servis"] in uncertain)
                                                       for m in res["tasimalar"]),
                }  # fmt: skip
            rec[policy] = {"durum": res["durum"], "yayin": res["oneri_yayinlanabilir"], "kapsam": res["oneri_kapsami"],
                           "deneysel_mod": res.get("deneysel_mod"), "deneysel_sizinti_yok": leak_free,
                           "dogrulanmis_cozum": bool(res["cozum"] is not None and res["dogrulama"]["gecti"]),
                           "nedenler": res["nedenler"], "uyarilar": res["uyarilar"], "cozumsuzluk_turu": res["cozumsuzluk_turu"],
                           "dondurulan": [f"{f['servis_id']}:{f['rol']}" for f in res["dondurulan_servis_roller"]],
                           "tasimalar": [(m["personel"], m["son_onayli_servis"], m["hedef_servis"]) for m in res["tasimalar"]],
                           "asamalar": [(s["asama"], s["durum"], s["amac_degeri"]) for s in res["cozucu_asamalari"]],
                           "tani": res["tani"], "guvenlik": safety, "sure_sn": round(el, 4)}  # fmt: skip
        runs.append(rec)
        print(len(runs), rec["yerel"], rec["kapali"]["durum"], "→", rec["iki_yonlu"]["durum"], rec["iki_yonlu"]["kapsam"],
              len(rec["iki_yonlu"]["tasimalar"]), rec["iki_yonlu"]["guvenlik"])  # fmt: skip

    def summary(policy):
        rs = [r[policy] for r in runs]
        prod = [r for r in rs if r["yayin"]]
        pub = [r for r in rs if r.get("dogrulanmis_cozum", r["yayin"])]  # doğrulanmış çözüm (deneyselde yayın değil)
        return {
            "durumlar": dict(Counter(r["durum"] for r in rs)),
            "uretim_yayini": len(prod), "uretim_yayini_oran": round(len(prod) / len(rs), 4),
            "deneysel_sizinti": sum(1 for r in rs if not r.get("deneysel_sizinti_yok", True)),
            "dogrulanmis_cozum": len(pub), "dogrulanmis_cozum_oran": round(len(pub) / len(rs), 4),
            "kismi_oneri": sum(1 for r in pub if r["kapsam"] == "kismi"), "tam_oneri": sum(1 for r in pub if r["kapsam"] == "tam"),
            "en_az_bir_tasima": sum(1 for r in pub if r["tasimalar"]), "tasima_toplam": sum(len(r["tasimalar"]) for r in pub),
            "yayinlananlarda_ihlal": sum(0 if r["guvenlik"].get("ihlal_yok") else 1 for r in pub),
            "guvenlik_kontrolleri_hepsi_dogru": all(all(r["guvenlik"].values()) for r in pub),
            "cozumsuzluk_turleri": dict(Counter(r["cozumsuzluk_turu"] for r in rs if r["durum"] == "INFEASIBLE")),
            "ret_nedenleri": dict(Counter(n.split(":")[0] for r in rs if not r.get("dogrulanmis_cozum", r["yayin"]) for n in r["nedenler"])),
            "u4_sure_ortalama_sn": round(sum(r["sure_sn"] for r in rs) / len(rs), 4),
        }  # fmt: skip

    frozen_roles = Counter(f.split(":")[1] for r in runs for f in r["iki_yonlu"]["dondurulan"])
    frozen_pairs = Counter(f for r in runs for f in r["iki_yonlu"]["dondurulan"])
    support_only = sum(1 for r in runs if r["iki_yonlu"]["dondurulan"] and all(f.endswith(":support") for f in r["iki_yonlu"]["dondurulan"]))
    support_unlocked = sum(1 for r in runs if r["kapali"]["durum"] == "inceleme_gerekli" and r["iki_yonlu"]["dogrulanmis_cozum"]
                           and any(f.endswith(":support") for f in r["iki_yonlu"]["dondurulan"]))  # fmt: skip
    still_blocked_support = sum(1 for r in runs if r["iki_yonlu"]["durum"] == "inceleme_gerekli"
                                and any(n.endswith(":support") for n in r["iki_yonlu"]["nedenler"]))  # fmt: skip
    baseline_u5 = json.loads((OUT / "asama5_u5_gercek_model.json").read_text(encoding="utf-8"))
    result = {
        "asama": args.asama, "varsayilan_politika": L.DEFAULT_FREEZE_POLICY, "an_sayisi": len(runs),
        "not": ("iki_yonlu yalnız açık deneysel modla (golge_degerlendirme) çalıştı; klinik onay yok, üretim yayını 0. "
                "dogrulanmis_cozum deneysel ölçümdür, varsayılan sistem başarısı değildir."),
        "u5_onceki": {"durumlar": baseline_u5["durumlar"], "yayinlanabilir_oran": baseline_u5["yayinlanabilir_oran"]},
        "politika_kapali": summary("kapali"), "politika_iki_yonlu": summary("iki_yonlu"),
        "k4_destek": {"dondurulan_rol_sayilari": dict(frozen_roles), "dondurulan_ciftler": dict(frozen_pairs.most_common()),
                      "yalniz_destek_dondurulan_an": support_only, "destek_dondurmasiyla_acilan_an": support_unlocked,
                      "dondurmaya_ragmen_destek_nedeniyle_duran_an": still_blocked_support},
        "calistirmalar": runs, "sure_sn": round(time.perf_counter() - started, 1),
    }  # fmt: skip
    (OUT / f"asama5_guvenli_{args.asama}.json").write_text(json.dumps(conv(result), ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / f"asama5_guvenli_{args.asama}.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["yerel", "katman", "kapali_durum", "kapali_yayin", "iki_yonlu_durum", "iki_yonlu_yayin", "iki_yonlu_dogrulanmis", "kapsam", "dondurulan",
                    "tasima", "guvenlik_tamam", "iki_yonlu_nedenler"])  # fmt: skip
        for r in runs:
            b = r["iki_yonlu"]
            w.writerow([r["yerel"], "|".join(r["katman"]), r["kapali"]["durum"], r["kapali"]["yayin"], b["durum"], b["yayin"],
                        b["dogrulanmis_cozum"], b["kapsam"],
                        "|".join(b["dondurulan"]), len(b["tasimalar"]), all(b["guvenlik"].values()) if b["guvenlik"] else "",
                        "|".join(b["nedenler"][:6])])  # fmt: skip
    print({k: result[k] for k in ("politika_kapali", "politika_iki_yonlu", "k4_destek")}, result["sure_sn"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
