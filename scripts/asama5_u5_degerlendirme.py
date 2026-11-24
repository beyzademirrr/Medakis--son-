"""Aşama 5 / U5 değerlendirme paketi (veritabanısız; sonuçlar JSON/CSV).

    .\\.venv\\Scripts\\python.exe scripts\\asama5_u5_degerlendirme.py --bolum matris
    .\\.venv\\Scripts\\python.exe scripts\\asama5_u5_degerlendirme.py --bolum gercek
    .\\.venv\\Scripts\\python.exe scripts\\asama5_u5_degerlendirme.py            (ikisi)

matris  : T1–T15, saldırılar, O1–O4 sözlük sırası, performans (tests/u5_scenarios.py; test_sentetik).
gercek  : Aşama 4 RF + U1 kalibrasyonu (yeniden eğitim/ayar YOK) ile üretici 2.1 verisinde katmanlı karar anı
          örneklemi; tahmin metrikleri (test bölümü); optimizasyon ve güvenli ret kırılımları; B/C seçenekleri
          yalnız sayımla (varsayımsal; kodda etkin değil).
Çıktılar: docs/veri/asama5_u5_*.json ve *.csv
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
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
OUT = ROOT / "docs" / "veri"
U1_CODES = ("yuksek_goreli_tampon", "dagilim_disi", "gorulmemis_kategori", "toplam_rol_tutarsiz", "tolerans_yok",
            "tampon_yok", "gecerli_tahmin_yok", "gecersiz_girdi", "negatif_girdi", "gecersiz_kategori", "bilinmeyen_servis")  # fmt: skip


CONFIG_CODES = ("optimizasyon_yapilandirmasi", "oncelik_agirligi", "fazla_toleransi", "cozum_suresi", "yetkinlik_kurali",
                "destek_kurali", "bilinmeyen_yetkinlik", "talep_veya_kullanim_orani_yok", "calisma_takvimi_dogrulanamadi",
                "asgari_kural_yok", "u3_veri_eksik")  # fmt: skip


def conv(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): conv(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [conv(x) for x in v]
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(conv(r.get(k)), ensure_ascii=False) if isinstance(r.get(k), (dict, list, tuple)) else r.get(k)) for k in fields})


# ---- Matris bölümü ---------------------------------------------------------------------------------------


def run_matrix(prefix: str = "asama5_u5") -> None:
    import u5_scenarios as S

    started = time.perf_counter()
    matrix = []
    for tid, fn in S.MATRIX.items():
        matrix += fn()
    experiment = []
    for tid, fn in S.FREEZE_EXPERIMENT.items():  # deneysel K1: varsayılan matristen ayrı
        experiment += fn()
    attacks = S.attack_suite()
    lex = S.lexicographic_suite()
    perf = {}
    for name, kw in S.PERF_PROFILES.items():
        perf[name] = S.measure(S.perf_inputs(**kw))
    summary = {
        "matris_sonuc": dict(Counter(r["sonuc"] for r in matrix)),
        "deneysel_dondurma_sonuc": dict(Counter(r["sonuc"] for r in experiment)),
        "deneysel_dondurma_yayin": sum(1 for r in experiment if r["oneri_yayinlanabilir"]),
        "saldiri_sonuc": dict(Counter(r["sonuc"] for r in attacks)),
        "sozluk_sonuc": dict(Counter(r["sonuc"] for r in lex)),
        "matris": matrix, "deneysel_dondurma": experiment, "saldirilar": attacks, "sozluk_sirasi": lex, "performans": perf,
        "sure_sn": round(time.perf_counter() - started, 1),
    }  # fmt: skip
    (OUT / f"{prefix}_matris.json").write_text(json.dumps(conv(summary), ensure_ascii=False, indent=1), encoding="utf-8")
    write_csv(OUT / f"{prefix}_matris.csv", matrix, ["kimlik", "alt", "amac", "beklenen", "durum", "dogrulama", "oneri_yayinlanabilir",
                                                      "acik_personel_dk", "fazla_personel_dk", "tasima_sayisi", "sure_sn", "sonuc",
                                                      "kontroller", "nedenler", "asamalar"])  # fmt: skip
    write_csv(OUT / f"{prefix}_saldirilar.csv", attacks, ["saldiri", "ozet_yeniden_hesaplandi", "reddedildi", "beklenen_kod", "kodlar", "sonuc"])
    write_csv(OUT / f"{prefix}_performans.csv", [{"profil": k, **v} for k, v in perf.items()],
              ["profil", "personel", "degisken", "kisit", "durum", "cozucu_sn", "toplam_sn", "dogrulayici_sn", "dogrulama",
               "python_bellek_tepe_mb", "tasima", "tutarli", "asamalar"])  # fmt: skip
    print("matris", summary["matris_sonuc"], "deneysel", summary["deneysel_dondurma_sonuc"], "saldiri", summary["saldiri_sonuc"], "sözlük", summary["sozluk_sonuc"], summary["sure_sn"], "sn")


# ---- Gerçek model bölümü ---------------------------------------------------------------------------------


def run_real() -> None:  # noqa: C901, PLR0915
    import numpy as np

    from medakis.domain.planning import FloatRule, PlanningConfig, StaffingRule
    from medakis.domain.service_calendar import always_open, weekly_calendar
    from medakis.domain.time import Interval
    from medakis.forecasting import calibration as C
    from medakis.forecasting import u1_run as U
    from medakis.forecasting.dataset import ROLES, build_training_frame
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
    models = {"random_forest": forest, "baseline_bilgi": info}
    mans = {"random_forest": rf_m, "baseline_bilgi": info_m}
    versions = {k: f"{k}@{m['model_sha256'][:12]}" for k, m in mans.items()}

    # -- Tahmin metrikleri (test bölümü; yeniden ayar YOK) --
    tf = build_training_frame(base)
    test = U.part(tf, "test")
    cands = U.candidates_for(test, models, mans, cal)
    pred = C.combine(cands, test, cal.choice())
    forecast = {}
    for u in sorted(test["servis_id"].unique()):
        mask = (test["servis_id"] == u).to_numpy()
        for role in ROLES:
            target = f"talep_dk_60_{role}"
            y, yh = test.loc[mask, target].to_numpy(), pred.loc[mask, target].to_numpy()
            buf = C.buffer_for(cal, test[mask], pred[mask], target)
            err = y - yh
            forecast[f"{int(u)}|{role}"] = {
                "n": int(mask.sum()), "MAE": round(float(np.abs(err).mean()), 3), "RMSE": round(float(np.sqrt((err ** 2).mean())), 3),
                "WAPE": round(float(np.abs(err).sum() / max(y.sum(), 1e-9)), 4) if y.sum() > 0 else None,
                "kapsama_q0_9": round(float((y <= yh + np.nan_to_num(buf, nan=0.0) + 1e-9).mean()), 4),
                "ortalama_talep": round(float(y.mean()), 3), "ortalama_tampon": round(float(np.nanmean(buf)), 3),
            }  # fmt: skip
    out = C.safe_forecast(test, cands, cal, mans, versions)
    o60 = out[out["ufuk_dk"] == 60]
    warn_dist = {}
    for (u, role), g in o60.groupby(["servis_id", "rol"]):
        codes = Counter(c.split(":")[0] for rs in g["inceleme_nedenleri"] for c in rs)
        warn_dist[f"{int(u)}|{role}"] = {"inceleme_orani": round(float((g["durum"] == "inceleme_gerekli").mean()), 4),
                                         "nedenler": dict(codes.most_common())}  # fmt: skip
    print("tahmin metrikleri bitti", round(time.perf_counter() - started, 1))

    # -- Fikstürler (test_sentetik; U4 örnek betiğiyle aynı) --
    src = "test_sentetik: U5 değerlendirme; yetkili kaynak değildir"
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

    # -- Katmanlı karar anı örneklemi --
    def loc(d, h, m=0):
        return datetime(d.year, d.month, d.day, h, m, tzinfo=tz).astimezone(UTC)

    sample: dict[datetime, set[str]] = defaultdict(set)
    first_test = datetime(2027, 1, 4).date()
    for k in range(21):
        d = first_test + timedelta(days=k)
        for h in (2, 8, 14, 20):
            sample[loc(d, h)].add("sistematik_test_donemi")
    for d, h, m in ((datetime(2026, 10, 28).date(), 12, 30), (datetime(2026, 10, 28).date(), 14, 0), (datetime(2026, 10, 29).date(), 10, 0),
                    (datetime(2026, 10, 29).date(), 22, 0), (datetime(2027, 1, 1).date(), 10, 0), (datetime(2027, 1, 1).date(), 22, 0),
                    (datetime(2027, 1, 2).date(), 10, 0)):  # fmt: skip
        sample[loc(d, h, m)].add("tatil_yarim_gun")
    totals = test.groupby("an_utc")["talep_dk_60"].sum().sort_values(ascending=False)
    for ts in list(totals.index[:10]):
        sample[ts.to_pydatetime()].add("yuksek_talep_gerceklesen_ust10")
    step = timedelta(minutes=15)
    absences = sorted((u for u in ds.plan.unavailability if u.reason == "devamsizlik" and u.recorded_at > u.starts_at
                       and u.starts_at >= loc(first_test, 0) and u.ends_at < base.scenario_now - timedelta(hours=2)),
                      key=lambda u: u.recorded_at)[:8]  # fmt: skip
    for u in absences:
        after = base.scenario_start + (-(-(u.recorded_at - base.scenario_start) // step)) * step
        sample[after].add("ani_devamsizlik_bildirim_sonrasi")
    runs = []
    for at in sorted(sample):
        try:
            rows = run_comparison(base, plan, staff, at, models, cal, mans, versions, calendars, tz)
        except ValueError as exc:
            runs.append({"an": at, "katman": sorted(sample[at]), "durum": "ornek_disi", "nedenler": [str(exc)]})
            continue
        inp = LayerAInputs(at, plan, staff, units, calendars, rows, skills, catalog, reqs, (), config, tz)
        t0 = time.perf_counter()
        res = L.optimize(inp)
        el = time.perf_counter() - t0
        local = at.astimezone(tz)
        pairs = []
        for r in rows:
            d, yh, util, cap = r["tamponlu_talep_dk"], r["nokta_tahmin_dk"], r["kullanim_orani"], r["kullanilabilir_personel_dk"]
            gb = gu = None
            if d is not None and yh is not None and util and cap is not None:
                gb = max(0, math.ceil(round(d / util, 9)) - math.floor(cap + 1e-9))
                gu = max(0, math.ceil(round(yh / util, 9)) - math.floor(cap + 1e-9))
            pairs.append({"servis_id": r["servis_id"], "rol": r["rol"], "u3_durum": r["durum"], "u3_nedenler": r["nedenler"],
                          "takvim": r["takvim_durumu"], "D": d, "yhat": yh, "C": cap, "plan_acik_tamponlu": gb, "plan_acik_tamponsuz": gu})  # fmt: skip
        violations = V.verify(inp, res["cozum"]).violations if res["cozum"] else None
        runs.append({
            "an": at, "yerel": local.strftime("%Y-%m-%d %H:%M"), "hafta_sonu": local.weekday() >= 5, "katman": sorted(sample[at]),
            "durum": res["durum"], "nedenler": res["nedenler"], "uyarilar": res["uyarilar"], "yayinlanabilir": res["oneri_yayinlanabilir"],
            "dogrulama": res["dogrulama"]["gecti"] if res["dogrulama"]["calisti"] else None, "dogrulayici_ihlal_sayisi": None if violations is None else len(violations),
            "tasima": len(res["tasimalar"]), "tasimalar": res["tasimalar"], "asamalar": [(s["asama"], s["durum"], s["amac_degeri"], s["sure_sn"]) for s in res["cozucu_asamalari"]],
            "tani": res["tani"], "u4_sn": round(el, 4), "ciftler": pairs,
            "onerilen": [{k: x[k] for k in ("servis_id", "rol", "ihtiyac_personel_dk_tam", "kapasite_personel_dk_tam", "acik_personel_dk_tam",
                                            "fazla_personel_dk_tam", "mevcut_acik_tam")} for x in res["servis_rol"]],
        })  # fmt: skip
        print(len(runs), local.strftime("%Y-%m-%d %H:%M"), res["durum"], len(res["tasimalar"]), round(el, 2))

    # -- Kırılımlar --
    valid = [r for r in runs if r["durum"] != "ornek_disi"]
    status = Counter(r["durum"] for r in valid)
    published = [r for r in valid if r["yayinlanabilir"]]
    review_pairs = Counter()
    single_blocker = 0
    clinic_only = 0
    closed_clinic_warn_runs = 0
    hypo_b = hypo_c = 0
    for r in valid:
        if r["durum"] != "inceleme_gerekli":
            continue
        blockers = {n.split(":", 1)[1] for n in r["nedenler"] if n.startswith("u3_inceleme_gerekli:")}
        other = [n for n in r["nedenler"] if not n.startswith("u3_inceleme_gerekli:")]
        for b in blockers:
            review_pairs[b] += 1
        if len(blockers) == 1 and not other:
            single_blocker += 1
        closed_zero = {f"{p['servis_id']}:{p['rol']}" for p in r["ciftler"] if p["takvim"] == "kapali" and (p["D"] or 0) == 0}
        if blockers & closed_zero:
            closed_clinic_warn_runs += 1
        if blockers and blockers <= closed_zero and not other:
            hypo_c += 1
            clinic_only += 1
        if blockers and not [n for n in other if not n.startswith("kismi_acik_pencere")]:
            hypo_b += 1  # yalnız inceleme türü nedenler: B'de dondurularak çözüm denenebilirdi
    u1_rates = defaultdict(Counter)
    for r in valid:
        for p in r["ciftler"]:
            for c in p["u3_nedenler"]:
                head = c.split(":")[0]
                if head in U1_CODES:
                    u1_rates[f"{p['servis_id']}|{p['rol']}"][head] += 1
    gaps_b = [sum(p["plan_acik_tamponlu"] or 0 for p in r["ciftler"]) for r in valid]
    gaps_u = [sum(p["plan_acik_tamponsuz"] or 0 for p in r["ciftler"]) for r in valid]
    role_gap = defaultdict(lambda: [0, 0])
    for r in valid:
        for p in r["ciftler"]:
            role_gap[p["rol"]][0] += p["plan_acik_tamponlu"] or 0
            role_gap[p["rol"]][1] += p["plan_acik_tamponsuz"] or 0
    summary = {
        "ornek": {"toplam_an": len(runs), "gecerli": len(valid), "katman_sayilari": dict(Counter(k for r in runs for k in r["katman"]))},
        "durumlar": dict(status),
        "yayinlanabilir_oran": round(len(published) / max(1, len(valid)), 4),
        "dogrulanmis_sifir_tasima_oran": round(sum(1 for r in published if r["tasima"] == 0) / max(1, len(valid)), 4),
        "dogrulanmis_en_az_bir_tasima_oran": round(sum(1 for r in published if r["tasima"] > 0) / max(1, len(valid)), 4),
        "dogrulayici_gecis": {"calisan": sum(1 for r in valid if r["dogrulama"] is not None), "gecen": sum(1 for r in valid if r["dogrulama"])},
        "yayinlanan_cozumlerde_H_ihlali": sum(r["dogrulayici_ihlal_sayisi"] or 0 for r in published),
        "inceleme_servis_rol_kirilimi": dict(review_pairs.most_common()),
        "tek_servis_rol_durdurdu": single_blocker,
        "kapali_poliklinik_uyarisi_olan_inceleme": closed_clinic_warn_runs,
        "yalniz_kapali_poliklinik_uyarisi_durdurdu": clinic_only,
        "varsayimsal_B_dondurma_ile_cozulebilecek": hypo_b,
        "varsayimsal_C_kapali_sifir_talep_istisnasi_ile_kapidan_gececek": hypo_c,
        "model_invalid_sayisi": sum(1 for r in valid if r["durum"] == "MODEL_INVALID"),
        "yapilandirma_eksikligi_ret": sum(1 for r in valid if any(n.startswith(CONFIG_CODES) for n in r["nedenler"])),
        "kapali_serviste_pozitif_talep_ret": sum(1 for r in valid if any(n.startswith("kapali_serviste_talep") for n in r["nedenler"])),
        "kismi_acik_pencere_ret": sum(1 for r in valid if any(n.startswith("kismi_acik_pencere") for n in r["nedenler"])),
        "model_invalid_nedenleri": dict(Counter(n.split(":")[0] for r in valid if r["durum"] == "MODEL_INVALID" for n in r["nedenler"])),
        "infeasible_tanilari": [{"an": r["yerel"], "tani": r["tani"]} for r in valid if r["durum"] == "INFEASIBLE"],
        "u1_uyari_sayilari": {k: dict(v) for k, v in sorted(u1_rates.items())},
        "plan_acigi_tamponlu_ortalama": round(float(np.mean(gaps_b)), 2), "plan_acigi_tamponsuz_ortalama": round(float(np.mean(gaps_u)), 2),
        "rol_bazinda_plan_acigi_toplam": {k: {"tamponlu": v[0], "tamponsuz": v[1]} for k, v in role_gap.items()},
        "u4_sure_sn": {"ortalama": round(float(np.mean([r["u4_sn"] for r in valid])), 4), "en_fazla": round(max(r["u4_sn"] for r in valid), 4)},
        "tahmin_metrikleri_test_60dk": forecast, "u1_uyari_dagilimi_test_60dk": warn_dist,
        "calistirmalar": runs, "sure_sn": round(time.perf_counter() - started, 1),
    }  # fmt: skip
    (OUT / "asama5_u5_gercek_model.json").write_text(json.dumps(conv(summary), ensure_ascii=False, indent=1), encoding="utf-8")
    write_csv(OUT / "asama5_u5_gercek_model_calistirmalar.csv", runs,
              ["yerel", "katman", "hafta_sonu", "durum", "yayinlanabilir", "dogrulama", "tasima", "u4_sn", "nedenler", "tani", "asamalar"])
    write_csv(OUT / "asama5_u5_tahmin_metrikleri.csv", [{"servis_rol": k, **v} for k, v in forecast.items()],
              ["servis_rol", "n", "MAE", "RMSE", "WAPE", "kapsama_q0_9", "ortalama_talep", "ortalama_tampon"])
    print({k: summary[k] for k in ("durumlar", "yayinlanabilir_oran", "tek_servis_rol_durdurdu", "varsayimsal_B_dondurma_ile_cozulebilecek",
                                   "varsayimsal_C_kapali_sifir_talep_istisnasi_ile_kapidan_gececek", "sure_sn")})  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bolum", choices=("matris", "gercek", "hepsi"), default="hepsi")
    ap.add_argument("--onek", default="asama5_u5", help="matris çıktı dosya öneki (U5 kanıtını ezmemek için)")
    args = ap.parse_args()
    if args.bolum in ("matris", "hepsi"):
        run_matrix(args.onek)
    if args.bolum in ("gercek", "hepsi"):
        run_real()
    return 0


if __name__ == "__main__":
    sys.exit(main())
