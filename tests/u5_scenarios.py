"""Aşama 5 / U5 değerlendirme senaryoları (veritabanısız; pytest matrisi ve değerlendirme betiği ortak kullanır).

Bütün kurallar, takvimler, ağırlıklar, toleranslar, destek yetkileri ve yetkinlik eşlemeleri AÇIKÇA
'test_sentetik' fikstürleridir; gerçek klinik standart değildir. Çalışma takvimindeki tatil günleri
2429 sayılı Kanun takvimindendir (28 Ekim yarım gün, 29 Ekim tam gün). Bu modül U4 kodunu DEĞİŞTİRMEZ;
yalnız çağırır ve kanıt kaydı üretir. Zaman aşımı senaryolarında çözücü çağrısı geçici olarak sarılır
ve hemen eski haline getirilir.
"""

from __future__ import annotations

import contextlib
import copy
import functools
import time
import tracemalloc
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from medakis.domain.models import Staff
from medakis.domain.planning import FloatRule, PlannedShift, PlannedUnavailability, PlanningConfig, PlanSet, StaffingRule
from medakis.domain.service_calendar import always_open, weekly_calendar
from medakis.domain.time import Interval
from medakis.optimization import comparison as X
from medakis.optimization import layer_a as L
from medakis.optimization import verifier as V
from medakis.optimization.layer_a_contracts import (
    LayerAInputs,
    MoveLock,
    OptimizationConfig,
    StaffSkill,
    UnitSkillRequirement,
)

TZ = ZoneInfo("Europe/Istanbul")
T0 = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)  # Salı yerel 08:00
H, M = timedelta(hours=1), timedelta(minutes=1)
T = T0 + 2 * H  # yerel 10:00
PUB = T0 - timedelta(days=14)
KNOWN = T0 - timedelta(days=30)
SRC = "test_sentetik"
A, B, ED, CL = 1, 2, 3, 4
ROLES = ("nurse", "supervisor", "support")
UNITS = {A: X.UnitInfo(A, "Yatakli Servis A", "inpatient"), B: X.UnitInfo(B, "Yatakli Servis B", "inpatient"),
         ED: X.UnitInfo(ED, "Acil Servis", "emergency"), CL: X.UnitInfo(CL, "Ayaktan Klinik", "outpatient")}  # fmt: skip
CATALOG = frozenset({"MON", "MED", "IV", "TRIAGE"})
COVER = Interval(T0 - timedelta(days=3), T0 + timedelta(days=6))
HOLIDAYS = {date(2026, 10, 28): "yarim_13", date(2026, 10, 29): "tam"}
CAL_DAYS = [(date(2026, 10, 26) + timedelta(days=i), HOLIDAYS.get(date(2026, 10, 26) + timedelta(days=i), "yok")) for i in range(6)]
CALS = tuple(always_open(u, COVER, SRC, "test", KNOWN) for u in (A, B, ED)) + (
    weekly_calendar(CL, CAL_DAYS, {wd: (dtime(8), dtime(17)) for wd in range(5)}, dtime(13), "Europe/Istanbul", SRC, "test", KNOWN),)  # fmt: skip
CONFIG = OptimizationConfig({u: 1 for u in UNITS}, {u: 30 for u in UNITS}, 10.0, SRC, "test", KNOWN)
MIN_NURSE = {A: 2, B: 2, ED: 2, CL: 1}
FLOAT_AB = FloatRule("nurse", None, (A, B), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":A-B")
FLOAT_ED = FloatRule("nurse", None, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil")
REQS = (UnitSkillRequirement(A, "nurse", ("MON", "MED"), SRC, "test", KNOWN),
        UnitSkillRequirement(B, "nurse", ("MON", "MED"), SRC, "test", KNOWN),
        UnitSkillRequirement(ED, "nurse", ("TRIAGE",), SRC, "test", KNOWN),
        UnitSkillRequirement(CL, "nurse", ("MED",), SRC, "test", KNOWN))  # fmt: skip


# ---- Personel profili ve senaryo kurucu ---------------------------------------------------------------


def profile(nurses=(3, 2, 2, 1), supervisors=(1, 1, 1, 0), support=(1, 1, 1, 0)) -> tuple[dict[int, Staff], dict[int, int]]:
    """Kimlik: servis·1000 + rol kaydırma + sıra (hemşire 1.., sorumlu 500.., destek 800..)."""
    staff, home = {}, {}
    for unit, counts in zip((A, B, ED, CL), zip(nurses, supervisors, support)):
        for role, offset, n in zip(ROLES, (0, 500, 800), counts):
            for i in range(1, n + 1):
                sid = unit * 1000 + offset + i
                staff[sid] = Staff(sid, f"{role[:1].upper()}{sid}", role, True)
                home[sid] = unit
    return staff, home


BASE_STAFF, BASE_HOME = profile()
N_A1, N_A2, N_A3 = 1001, 1002, 1003  # Yataklı A hemşireleri (N_A3 TRIAGE'lı)
N_B1, N_B2 = 2001, 2002


def shifts_for(home: dict[int, int], staff: dict[int, Staff], day: int = 0, clinic: tuple[int, int] | None = (8, 17)) -> list[PlannedShift]:
    out = []
    start = T0 + timedelta(days=day)
    for i, sid in enumerate(sorted(home), start=1):
        unit = home[sid]
        if unit == CL:
            if clinic is None:
                continue
            s, e = start + (clinic[0] - 8) * H, start + (clinic[1] - 8) * H
        else:
            s, e = start, start + 12 * H
        out.append(PlannedShift(i, sid, unit, staff[sid].role, 1, s, e, 0, PUB, "planned", None, SRC))
    return out


def rules(min_nurse=None, extra=()) -> tuple[StaffingRule, ...]:
    mn = {**MIN_NURSE, **(min_nurse or {})}
    out = []
    for u in UNITS:
        out.append(StaffingRule(u, "nurse", (0, 24), mn[u], SRC, "test", KNOWN, None, KNOWN))
        out.append(StaffingRule(u, "supervisor", (0, 24), 1 if u != CL else 0, SRC, "test", KNOWN, None, KNOWN))
        out.append(StaffingRule(u, "support", (0, 24), 0, SRC, "test", KNOWN, None, KNOWN))
    return tuple(out) + tuple(extra)


def skills(staff: dict[int, Staff], home: dict[int, int], triage_a3_until=None, drop=(), everyone=()) -> tuple[StaffSkill, ...]:
    out = []
    for sid, person in sorted(staff.items()):
        if person.role != "nurse":
            continue
        out += [StaffSkill(sid, k, KNOWN, None) for k in ("MON", "MED") if (sid, k) not in drop]
        if home.get(sid) == ED or sid in everyone:
            out.append(StaffSkill(sid, "TRIAGE", KNOWN, None))
    if N_A3 in staff and N_A3 not in everyone:
        out.append(StaffSkill(N_A3, "TRIAGE", KNOWN, triage_a3_until))
    return tuple(out)


def build(nurse=None, at=T, day=0, clinic=(8, 17), extra_shifts=(), unav=(), min_nurse=None, extra_rules=(),
          floats=(FLOAT_AB,), sk=None, locks=(), config=CONFIG, reqs=REQS, review=(), cals=CALS, staff=None,
          home=None, plan_rules=None, demands=None, util=0.5) -> LayerAInputs:  # fmt: skip
    """nurse = {servis: ŷ}; u = 0,5 → D = 2·ŷ personel-dk. Diğer roller talep 0 (demands verilirse o kullanılır)."""
    staff = staff or BASE_STAFF
    home = home or BASE_HOME
    plan = PlanSet(tuple(shifts_for(home, staff, day, clinic)) + tuple(extra_shifts), tuple(unav),
                   plan_rules or rules(min_nurse, extra_rules), tuple(floats), PlanningConfig(util, 0.35, 1.0, 10.0, SRC))  # fmt: skip
    if demands is None:
        demands = []
        for u in UNITS:
            for r in ROLES:
                point = (nurse or {}).get(u, 0.0) if r == "nurse" else 0.0
                status, why = ("inceleme_gerekli", ("yuksek_goreli_tampon",)) if (u, r) in review else ("uygun", ())
                demands.append(X.DemandEstimate(u, r, at, 60, point, 0.0, point, status, why, (), "random_forest", "rf@t", "t"))
    rows = X.compare(at, demands, plan, staff, UNITS, cals, TZ)
    return LayerAInputs(at, plan, staff, UNITS, cals, rows, sk if sk is not None else skills(staff, home), CATALOG, reqs,
                        tuple(locks), config, TZ)  # fmt: skip


# ---- Çalıştırma ve kanıt kaydı ---------------------------------------------------------------------------


EXP_FIELDS = ("cozum", "tasimalar", "kilitli_yerlesimler", "servis_rol")


def run(inp: LayerAInputs, overrides=None, experimental=False) -> tuple[dict[str, Any], float]:
    """experimental=True: iki yönlü dondurma açık deneysel modla (klinik onay yok; yayın kapalı)."""
    t0 = time.perf_counter()
    if experimental:
        raw = L.optimize(inp, solver_overrides=overrides, freeze_policy=L.FREEZE_TWO_WAY, experimental_mode="test")
        # Sızıntı denetimi: deneysel çalıştırmada normal çıktı alanları boş, yayın kapalı olmalı
        leak_free = (raw["cozum"] is None and raw["tasimalar"] == [] and raw["kilitli_yerlesimler"] == [] and raw["servis_rol"] == []
                     and not raw["oneri_yayinlanabilir"])  # fmt: skip
        inner = raw["deneysel_sonuc"] or {}
        res = {**raw, **{k: inner[k] for k in EXP_FIELDS if k in inner}, "deneysel_sizinti_yok": leak_free}
    else:
        res = L.optimize(inp, solver_overrides=overrides)
    return res, round(time.perf_counter() - t0, 4)


def moves(res) -> list[tuple[int, int, int]]:
    return [(m["personel"], m["son_onayli_servis"], m["hedef_servis"]) for m in res["tasimalar"]]


def stage_values(res) -> dict[str, Any]:
    return {s["asama"]: s["amac_degeri"] for s in res["cozucu_asamalari"]}


def evidence(tid, sub, purpose, data, expected, res, elapsed, checks: dict[str, bool], extra=None) -> dict[str, Any]:
    if "deneysel_sizinti_yok" in res:  # deneysel kayıtların hepsinde çıktı sızıntısı denetlenir
        checks = {**checks, "deneysel_sizinti_yok": bool(res["deneysel_sizinti_yok"])}
    sol = res.get("cozum") or {}
    rows = sol.get("servis_rol", [])
    return {
        "kimlik": tid, "alt": sub, "amac": purpose, "veri": data, "beklenen": expected,
        "durum": res["durum"], "asamalar": [(s["asama"], s["durum"], s["amac_degeri"]) for s in res["cozucu_asamalari"]],
        "dogrulama": res["dogrulama"]["gecti"] if res["dogrulama"]["calisti"] else None,
        "oneri_yayinlanabilir": res["oneri_yayinlanabilir"],
        "acik_personel_dk": sum(r["acik_tam"] for r in rows) if rows else None,
        "fazla_personel_dk": sum(r["fazla_tam"] for r in rows) if rows else None,
        "tasima_sayisi": len(res["tasimalar"]), "sure_sn": elapsed, "nedenler": res["nedenler"] + res["uyarilar"],
        "tani": res["tani"], "kontroller": checks, "ek": extra or {},
        "sonuc": "gecti" if all(checks.values()) else "basarisiz",
    }  # fmt: skip


def _published(res) -> bool:
    return bool(res["oneri_yayinlanabilir"] and res["dogrulama"]["gecti"] and res["insan_onayi_gerekli"] is True)


def _row(res, u, r="nurse"):
    return next(x for x in res["servis_rol"] if x["servis_id"] == u and x["rol"] == r)


def _frozen_set(res) -> set[tuple[int, str]]:
    return {(f["servis_id"], f["rol"]) for f in res.get("dondurulan_servis_roller", [])}


def _verified_experimental(res) -> bool:
    """Deneysel çözüm: doğrulayıcıdan geçti, ama yayımlanabilir değil ve deneysel etiketli."""
    return bool(res["cozum"] is not None and res["dogrulama"]["gecti"] and not res["oneri_yayinlanabilir"]
                and res["deneysel_mod"] == "test" and res["insan_onayi_gerekli"] is True and res.get("deneysel_sizinti_yok")
                and res["deneysel_sonuc"]["dogrulandi"] is True and res["deneysel_sonuc"]["yayinlanabilir"] is False)  # fmt: skip


def _freeze_safe(res, must_freeze) -> dict[str, bool]:
    """K1 (iki yönlü dondurma, yalnız deneysel mod) güvenlik kontrolleri: işaretli çiftler dondurulmuş ya da küresel
    ret; dondurulan çifte giriş/çıkış ve belirsiz servisten çıkış yok; yayın yok; çözüm varsa doğrulanmış ve 'kismi'."""
    frozen = _frozen_set(res)
    uncertain = {u for u, _ in frozen}
    refused = res["durum"] in (L.REVIEW, L.MODEL_INVALID, L.INFEASIBLE)
    return {
        "isaretliler_donduruldu_veya_ret": refused or set(must_freeze) <= frozen,
        "dondurulana_giris_cikis_yok": all((m["hedef_servis"], m["rol"]) not in frozen and m["son_onayli_servis"] not in uncertain
                                           for m in res["tasimalar"]),
        "deneysel_yayin_yok": not res["oneri_yayinlanabilir"],
        "deneysel_sizinti_yok": bool(res.get("deneysel_sizinti_yok")),
        "cozum_dogrulanmis_kismi": res["cozum"] is None or (_verified_experimental(res) and res["oneri_kapsami"] == "kismi"),
    }  # fmt: skip


def _no_publication(res) -> bool:
    return not res["oneri_yayinlanabilir"] and res["tasimalar"] == [] and res["cozum"] is None


# ---- U1 zinciri (küçük model; T3, T11, T12) ---------------------------------------------------------------


@functools.cache
def small_u1():
    from medakis.forecasting import calibration as C
    from medakis.forecasting import training as TR
    from medakis.forecasting import u1_run as U
    from medakis.forecasting.dataset import CONTRACT_VERSION, INPUT_COLUMNS, SplitPlan
    from medakis.forecasting.models import ForestModel

    cfg = TR.ExperimentConfig(days=28, plan=SplitPlan(2, 1, 1))
    tf = TR.frame_for(cfg)
    forest = ForestModel({"n_estimators": 15, "min_samples_leaf": 5, "max_features": 0.5}).fit(U.part(tf, "egitim"))
    models = U.build_models(tf, forest)
    cal = U.calibrate(tf, models, C.CalibrationConfig(min_group_size=50), {"model_sha256": {}})
    good = {"sozlesme_surumu": CONTRACT_VERSION, "surumler": {"scikit_learn": C.sklearn.__version__},
            "girdiler": list(INPUT_COLUMNS), "model_sha256": "kucuk-test"}  # fmt: skip
    return cfg, tf, models, cal, {"random_forest": good, "baseline_bilgi": dict(good)}


def u1_at(frame: pd.DataFrame, at=T, mutate=None, candidates_hook=None):
    from medakis.forecasting import calibration as C
    from medakis.forecasting import u1_run as U

    _, _, models, cal, mans = small_u1()
    rows = frame[frame["an_utc"] == pd.Timestamp(at)].copy()
    if mutate is not None:
        rows = mutate(rows)
    cands = U.candidates_for(rows, models, mans, cal)
    if candidates_hook is not None:
        cands = candidates_hook(cands)
    out = C.safe_forecast(rows, cands, cal, mans, {k: f"{k}@kucuk" for k in mans})
    return rows, cands, out, X.demand_from_u1(out, mans)


@contextlib.contextmanager
def patched_solver(fn):
    real = L._run_solver
    L._run_solver = fn(real)
    try:
        yield
    finally:
        L._run_solver = real


# ---- T1–T15 ------------------------------------------------------------------------------------------------


def t1():
    res, el = run(build({A: 60, B: 30, ED: 50}))
    sv = stage_values(res)
    return [evidence("T1", "a", "Normal doluluk: yeterli kadro, gereksiz taşıma yok", "ŷ A60 B30 Acil50 (D=2ŷ), kadro 3/2/2/1",
                     "OPTIMAL, doğrulanmış, 0 taşıma, O1=0", res, el,
                     {"optimal": res["durum"] == L.OPTIMAL, "yayinlanabilir": _published(res), "tasima_yok": moves(res) == [],
                      "acik_sifir": sv.get("O1_karsilanmayan_talep") == 0})]  # fmt: skip


def t2():
    out = []
    res, el = run(build({A: 30, B: 120, ED: 50}))
    a, b = (_row(res, A), _row(res, B)) if res["servis_rol"] else ({}, {})
    out.append(evidence("T2", "a", "Yataklı B yüksek talep; A'dan yetkili destek; A asgarisi korunur",
                        "ŷ B120 (D240, kapasite 120), A 3 hemşire asgari 2, A↔B destek (MON)",
                        "1 taşıma A→B; B açığı 120→60; A her atomda ≥2", res, el,
                        {"yayinlanabilir": _published(res), "tek_tasima_A_B": [m[1:] for m in moves(res)] == [(A, B)],
                         "B_acik_azaldi": (b.get("mevcut_acik_tam"), b.get("acik_personel_dk_tam")) == (120, 60),
                         "A_asgari": bool(a) and all(x["kisi"] >= x["asgari_kisi"] for x in a["atomlar"])},
                        {"B": {k: b.get(k) for k in ("mevcut_kapasite_tam", "kapasite_personel_dk_tam", "mevcut_acik_tam", "acik_personel_dk_tam")}}))  # fmt: skip
    res, el = run(build({A: 30, B: 120, ED: 50}, min_nurse={A: 3}))
    out.append(evidence("T2", "b", "A asgarisi 3 iken taşıma yapılamaz; açık raporlanır", "aynı, A asgari 3",
                        "OPTIMAL, 0 taşıma, B açığı 120 raporlanır", res, el,
                        {"yayinlanabilir": _published(res), "tasima_yok": moves(res) == [],
                         "acik_raporlandi": bool(res["servis_rol"]) and _row(res, B)["acik_personel_dk_tam"] == 120}))  # fmt: skip
    return out


def t3(experimental=False):
    cfg, _, models, cal, mans = small_u1()
    from medakis.forecasting import calibration as C
    from medakis.forecasting import training as TR
    from medakis.forecasting import u1_run as U

    hv = TR.frame_for(cfg, ed_volume_profile="yuksek").frame
    rows, _, out, demands = u1_at(hv)
    ed = out[(out["servis_id"] == ED) & (out["ufuk_dk"] == 60)]
    limit, floor_min, eps = cal.config.buffer_ratio_limit["emergency"], cal.config.buffer_ratio_min_minutes, cal.config.relative_epsilon
    rule = {r.rol: (r.tampon_dk > floor_min and r.tampon_dk > limit * max(r.nokta_tahmin_dk, eps)) for r in ed.itertuples()}
    flagged = {r.rol: "yuksek_goreli_tampon" in r.inceleme_nedenleri for r in ed.itertuples()}
    ratio = {r.rol: (round(r.tampon_dk / r.nokta_tahmin_dk, 3) if r.nokta_tahmin_dk else None) for r in ed.itertuples()}
    res, el = run(build(demands=demands), experimental=experimental)
    test = hv[hv["bolum"] == "test"]
    full = C.safe_forecast(test, U.candidates_for(test, models, mans, cal), cal, mans, {})
    e = full[(full["servis_id"] == ED) & (full["ufuk_dk"] == 60)]
    by_role = {role: {"inceleme_orani": round(float((g["durum"] == "inceleme_gerekli").mean()), 3),
                      "yuksek_goreli_tampon_orani": round(float(g["inceleme_nedenleri"].apply(lambda r: "yuksek_goreli_tampon" in r).mean()), 3),
                      "B_bolu_yhat_medyan": round(float((g["tampon_dk"] / g["nokta_tahmin_dk"].clip(lower=1.0)).median()), 3)}
               for role, g in e.groupby("rol")}  # fmt: skip
    base = {"esik_kurali_birebir": rule == flagged, "en_az_bir_inceleme": any(flagged.values())}
    if experimental:
        expect = ("Eşiği aşan Acil satırı inceleme_gerekli; DENEYSEL K1: işaretli çift dondurulur, Acil'e giriş/çıkış yok; "
                  "çözüm varsa doğrulanmış kısmi, yayın yok")
        checks = {**base, **_freeze_safe(res, [(ED, role) for role, f in flagged.items() if f])}
    else:
        expect = "Eşiği aşan Acil satırı inceleme_gerekli (kural birebir); herhangi bir inceleme varsa U4 öneri vermez"
        checks = {**base, "u4_inceleme": res["durum"] == L.REVIEW, "oneri_yok": _no_publication(res)}
    return [evidence("D-T3" if experimental else "T3", "a", "Acil yüksek hacim: göreli tampon eşiği aşılırsa inceleme; tehlikeli öneri yok",
                     "ed_volume_profile=yuksek (28 gün) karar anı satırları; küçük RF + U1 kalibrasyonu (normal veride); eşik B/ŷ > 0,5 ve B > 10 dk",
                     expect, res, el, checks,
                     {"acil_B_bolu_yhat": ratio, "esik_asildi": rule, "test_donemi_acil": by_role,
                      "not": "Acil profili yüksek hacimde de 'temel' kalır; görülmemiş kategori uyarısı oluşmaz."})]  # fmt: skip


def t4(experimental=False):
    out = []
    solved = _verified_experimental if experimental else _published
    to_cl = FloatRule("nurse", None, (A, CL), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":poliklinik")
    cases = [("a", "29 Ekim tam gün tatil 10:00", dict(day=2, at=T + 2 * timedelta(days=1), clinic=None), "kapali"),
             ("b", "28 Ekim yarım gün 14:00 (13:00 sonrası)", dict(day=1, at=T0 + timedelta(days=1) + 6 * H, clinic=(8, 13)), "kapali"),
             ("c", "Hafta içi 18:00 (kapanış sonrası)", dict(at=T0 + 10 * H), "kapali"),
             ("d", "28 Ekim yarım gün 12:30 (kısmi açık)", dict(day=1, at=T0 + timedelta(days=1) + 4 * H + 30 * M, clinic=(8, 13)), "kismi")]  # fmt: skip
    for sub, label, kw, kind in cases:
        res, el = run(build({A: 10}, floats=(to_cl,), **kw), experimental=experimental)
        if kind == "kapali":
            checks = {"cozum": solved(res), "polikliniğe_tasima_yok": all(m[2] != CL for m in moves(res)),
                      "poliklinik_kisi_0": bool(res["servis_rol"]) and _row(res, CL)["onerilen_kisi"] == 0}  # fmt: skip
            expect = "Doğrulanmış kapalı: talep 0, polikliniğe taşıma yok"
        elif experimental:
            checks = {"poliklinik_donduruldu": _frozen_set(res) == {(CL, r) for r in ROLES}, **_freeze_safe(res, [(CL, r) for r in ROLES]),
                      "polikliniğe_tasima_yok": all(CL not in (m[1], m[2]) for m in moves(res)),
                      "dogrulanmis_kismi_cozum": _verified_experimental(res) and res["oneri_kapsami"] == "kismi"}  # fmt: skip
            expect = ("Kısmi açık, DENEYSEL K1: poliklinik (bütün roller) dondurulur; polikliniğe/poliklinikten taşıma yok; "
                      "doğrulanmış kısmi çözüm, yayın yok")
        else:
            checks = {"inceleme": res["durum"] == L.REVIEW, "kismi_neden": "kismi_acik_pencere:4" in res["nedenler"],
                      "oneri_yok": _no_publication(res)}  # fmt: skip
            expect = "Kısmi açık: inceleme_gerekli, öneri yok"
        out.append(evidence("D-T4" if experimental else "T4", sub, "Poliklinik saatleri ve tatil", label + "; A→poliklinik destek kuralı tanımlı", expect, res, el, checks))
    return out


def t5():
    res, el = run(build({B: 40}, min_nurse={B: 4}))
    return [evidence("T5", "a", "Personel yetersizliği: asgari sağlanamıyor", "B asgari 4; B 2 + A'dan en fazla 1",
                     "INFEASIBLE, gevşetme yok, eksik kişi atom bazında", res, el,
                     {"infeasible": res["durum"] == L.INFEASIBLE, "oneri_yok": _no_publication(res),
                      "atom_tanisi": any(t.startswith("H7_karsilanamaz:2:nurse:") and t.endswith("3<4") for t in res["tani"])})]  # fmt: skip


def t6():
    absent = lambda rec: PlannedUnavailability(1, N_B2, T0, T0 + 12 * H, "devamsizlik", rec, "ani")  # noqa: E731
    base, _ = run(build({A: 30, B: 60}))
    known, el1 = run(build({A: 30, B: 60}, unav=[absent(T - 10 * M)]))
    unknown, el2 = run(build({A: 30, B: 60}, unav=[absent(T + 10 * M)]))
    b_known = _row(known, B) if known["servis_rol"] else {}
    return [evidence("T6", "a", "Ani devamsızlık bildirimden önce/sonra", "B hemşiresi bütün vardiya devamsız; bildirim t−10 / t+10",
                     "Bilinen: kapasite düşer, A'dan destek; bilinmeyen: çözüm devamsızlık hiç yokmuş gibi (özet aynı)",
                     known, el1 + el2,
                     {"bilinen_kapasite_dustu": b_known.get("mevcut_kapasite_tam") == 60, "bilinen_destek": len(moves(known)) == 1,
                      "bilinmeyen_ayni_ozet": unknown["cozum"] is not None and base["cozum"] is not None
                      and unknown["cozum"]["ozet_sha256"] == base["cozum"]["ozet_sha256"]},
                     {"ozet_bilinmeyen": (unknown.get("cozum") or {}).get("ozet_sha256"), "ozet_temel": (base.get("cozum") or {}).get("ozet_sha256")})]  # fmt: skip


def t7():
    both = FloatRule("nurse", N_A3, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":A3")
    res, el = run(build({A: 10, B: 120, ED: 240}, floats=(both,)))
    sol = res.get("cozum") or {"atamalar": []}
    ids = [a["personel"] for a in sol["atamalar"]]
    sv = stage_values(res)
    return [evidence("T7", "a", "Eşzamanlı açık: Acil ve B; tek kişi iki yerde değil; O2 dengesi",
                     "B D240 (kapasite 120), Acil D480 (120); tek destek hemşiresi (A3: TRIAGE+MON) iki servise de yetkili",
                     "1 taşıma Acil'e (göreli açık max %62,5 < %75); her kişi tek atama", res, el,
                     {"yayinlanabilir": _published(res), "tek_atama": len(ids) == len(set(ids)), "acile": moves(res) == [(N_A3, A, ED)],
                      "O1": sv.get("O1_karsilanmayan_talep") == 420, "O2": sv.get("O2_denge") == 625},
                     {"alternatif_B": "B'ye gitseydi O2 = 750 (max %25, %75)"})]  # fmt: skip


def t8():
    ed_only = FloatRule("nurse", None, (ED,), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil")
    res, el = run(build({A: 10, ED: 240}, floats=(ed_only,), sk=skills(BASE_STAFF, BASE_HOME, triage_a3_until=T + 30 * M)))
    ok, el2 = run(build({A: 10, ED: 240}, floats=(ed_only,)))
    return [evidence("T8", "a", "Yetkinlik yetersizliği: TRIAGE yok veya pencere içinde bitiyor", "A hemşireleri: 2'sinde TRIAGE yok, A3'te 10:30'da bitiyor",
                     "Acil'e taşıma yok; açık raporlanır. Kontrol: geçerli TRIAGE ile yalnız A3 taşınır.", res, el + el2,
                     {"yayinlanabilir": _published(res), "tasima_yok": moves(res) == [],
                      "acik_raporlandi": bool(res["servis_rol"]) and _row(res, ED)["acik_personel_dk_tam"] > 0,
                      "kontrol_A3": moves(ok) == [(N_A3, A, ED)]})]  # fmt: skip


def t9():
    out = []
    sol_inp = build({A: 30, B: 120, ED: 50})
    good, _ = run(sol_inp)
    broken = {
        "a": ("Örtüşen vardiya (A1 aynı anda B'de)", dict(extra_shifts=[PlannedShift(99, N_A1, B, "nurse", 1, T0, T0 + 4 * H, 0, PUB, "planned", None, SRC)]), "u3_gecersiz_plan"),
        "b": ("Yanlış rol (hemşireye sorumlu vardiyası)", dict(extra_shifts=[PlannedShift(98, N_B1, B, "supervisor", 1, T0 + 12 * H, T0 + 14 * H, 0, PUB, "planned", None, SRC)]), "u3_gecersiz_plan"),
        "c": ("Eksik asgari kural (Acil hemşire)", dict(plan_rules=tuple(r for r in rules() if not (r.unit_id == ED and r.role == "nurse"))), "u3_veri_eksik:3:nurse"),
        "d": ("Eksik çalışma takvimi (poliklinik)", dict(cals=CALS[:3]), "u3_veri_eksik:4"),
        "e": ("Eksik alan: ters aralıklı vardiya", dict(extra_shifts=[PlannedShift(97, N_B1, B, "nurse", 1, T0 + 13 * H, T0 + 13 * H, 0, PUB, "planned", None, SRC)]), "u3_gecersiz_plan"),
    }  # fmt: skip
    for sub, (label, kw, code) in broken.items():
        inp = build({A: 30, B: 120, ED: 50}, **kw)
        res, el = run(inp)
        listed = V.verify(inp, good["cozum"]).violations if good["cozum"] else []  # istisna testi düşürür
        out.append(evidence("T9", sub, "Eksik/tutarsız plan → güvenli ret", label, "MODEL_INVALID; doğrulayıcı aynı çözümü bu girdiyle reddeder",
                            res, el,
                            {"model_invalid": res["durum"] == L.MODEL_INVALID, "neden": any(n.startswith(code) for n in res["nedenler"]),
                             "oneri_yok": _no_publication(res), "dogrulayici_listeler": bool(listed),
                             **({"h5_kodu": any(x.startswith("H5_gecersiz_vardiya_araligi:2001:97") for x in listed)} if sub == "e" else {})},
                            {"dogrulayici_ihlalleri": listed[:6]}))  # fmt: skip
    return out


def t10():
    one = FloatRule("nurse", N_A3, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":A3")
    res, el = run(build({B: 40, ED: 40}, min_nurse={B: 3, ED: 3}, floats=(one,)))
    return [evidence("T10", "a", "Birlikte sağlanamayan asgari kurallar", "B ve Acil asgari 3 (her biri 2 kişi); tek destek hemşiresi ikisine de yetkili",
                     "INFEASIBLE; birinde 2<3 atom tanısı", res, el,
                     {"infeasible": res["durum"] == L.INFEASIBLE, "oneri_yok": _no_publication(res),
                      "somut_atom": any(t.endswith(":2<3") for t in res["tani"]) and len({t.split(":")[1] for t in res["tani"]}) == 1},
                     {"tani": res["tani"]})]  # fmt: skip


def t11(experimental=False):
    _, tf, _, _, _ = small_u1()
    frame = tf.frame

    def ood(rows):
        rows = rows.copy()
        idx = rows.index[rows["servis_id"] == ED][0]
        rows.loc[idx, "hasta_sayisi"] = float(rows["hasta_sayisi"].max() * 20 + 100)  # eğitim aralığı dışı
        rows.loc[rows.index[rows["servis_id"] == A][0], "servis_profili"] = "yuksek_bilinmeyen"  # görülmemiş kategori
        return rows

    def invalid(rows):
        rows = rows.copy()
        rows.loc[rows.index[rows["servis_id"] == B][0], "bekleyen_dk"] = float("nan")
        return rows

    out = []
    for sub, label, mutate in (("a", "Eğitim dışı girdi (Acil hasta sayısı) + görülmemiş kategori (A profili)", ood),
                               ("b", "Geçersiz girdi (B bekleyen_dk = NaN)", invalid)):  # fmt: skip
        _, _, u1, demands = u1_at(frame, mutate=mutate)
        flagged = u1[u1["durum"] == "inceleme_gerekli"]
        res, el = run(build(demands=demands), experimental=experimental)
        inp_rows = build(demands=demands).u3_rows
        flagged_pairs = [(r["servis_id"], r["rol"]) for r in inp_rows if r["durum"] == "inceleme_gerekli"]
        if sub == "a" and experimental:  # dağılım dışı / görülmemiş kategori: dondurulabilir neden
            expect = "U1 inceleme; DENEYSEL K1: işaretli çiftler dondurulur (giriş/çıkış yok) ya da küresel ret; yayın yok"
            checks = {"u1_isaretledi": len(flagged) > 0, **_freeze_safe(res, flagged_pairs)}
        elif sub == "a":
            expect = "U1 inceleme; U4 inceleme_gerekli (küresel ret), öneri yok"
            checks = {"u1_isaretledi": len(flagged) > 0, "u4_guvenli_ret": res["durum"] == L.REVIEW,
                      "u1_nedeni_raporda": any(n.startswith("u3_inceleme_gerekli") for n in res["nedenler"]),
                      "oneri_yok": _no_publication(res)}  # fmt: skip
        else:  # geçersiz girdi (NaN): talep yok → dondurulamaz, MODEL_INVALID
            expect = "U1 inceleme; U4 MODEL_INVALID (talep yok, dondurulamaz), öneri yok"
            checks = {"u1_isaretledi": len(flagged) > 0, "u4_guvenli_ret": res["durum"] == L.MODEL_INVALID,
                      "u1_nedeni_raporda": any(n.startswith("u3_inceleme_gerekli") for n in res["nedenler"]),
                      "oneri_yok": _no_publication(res)}  # fmt: skip
        out.append(evidence("D-T11" if experimental else "T11", sub, "Yüksek tahmin hatası riski → güvenli ret", label, expect, res, el, checks,
                            {"u1_nedenler": sorted({c for rs in flagged["inceleme_nedenleri"] for c in rs})}))  # fmt: skip
    return out


def t12(experimental=False):
    _, tf, _, _, _ = small_u1()
    _, base_c, base_out, _ = u1_at(tf.frame)

    def inflate(cands):
        cands = {k: v.copy() for k, v in cands.items()}
        for v in cands.values():
            v["talep_dk_60"] = v["talep_dk_60"] + 10_000  # toplam ≫ rol toplamı
        return cands

    _, _, out, demands = u1_at(tf.frame, candidates_hook=inflate)
    rows60 = out[out["ufuk_dk"] == 60]
    same_roles = (rows60.sort_values(["servis_id", "rol"])["nokta_tahmin_dk"].to_numpy()
                  == base_out[base_out["ufuk_dk"] == 60].sort_values(["servis_id", "rol"])["nokta_tahmin_dk"].to_numpy()).all()  # fmt: skip
    res, el = run(build(demands=demands), experimental=experimental)
    checks = {"u1_tutarsiz": all("toplam_rol_tutarsiz" in r for r in rows60["inceleme_nedenleri"]),
              "roller_ayni": bool(same_roles), "u4_inceleme": res["durum"] == L.REVIEW, "oneri_yok": _no_publication(res)}
    if experimental:
        checks["tum_ciftler_donduruldu"] = res["nedenler"] == ["tum_servis_roller_donduruldu"]
    return [evidence("D-T12" if experimental else "T12", "a", "Uzlaştırma farkı > 2τ", "Toplam 60 dk tahmini +10 000 dk; rol tahminleri değişmedi",
                     "U1 toplam_rol_tutarsiz; roller düşürülmez; U4 inceleme, öneri yok"
                     + ("; DENEYSEL K1: bütün çiftler donar" if experimental else ""), res, el, checks)]  # fmt: skip


def t13():
    base_inp = build({A: 30, B: 120, ED: 50})
    base, el = run(base_inp)
    h0 = (base.get("cozum") or {}).get("ozet_sha256")
    future = T + 5 * M
    variants = {
        "a": ("t'den sonra yayımlanan ek B vardiyası", dict(extra_shifts=[PlannedShift(90, 2003, B, "nurse", 1, T0, T0 + 12 * H, 0, future, "planned", None, SRC)],
                                                              staff={**BASE_STAFF, 2003: Staff(2003, "N2003", "nurse", True)})),
        "b": ("t'den sonra iptal edilen B vardiyası", "iptal"),
        "c": ("t'den sonra bildirilen B devamsızlığı", dict(unav=[PlannedUnavailability(5, N_B1, T0, T0 + 12 * H, "devamsizlik", future, "ani")])),
        "d": ("t'den sonra bilinen daha katı A kuralı", dict(extra_rules=(StaffingRule(A, "nurse", (0, 24), 3, SRC, "test", KNOWN, None, future),))),
        "e": ("t'den sonra onaylanan kilit", dict(locks=[MoveLock(N_A1, B, T - H, T + 2 * H, "onay-gelecek", future)])),
        "f": ("t'den sonra bilinen destek kuralı", dict(floats=(FLOAT_AB, replace(FLOAT_ED, known_at=future)))),
    }  # fmt: skip
    out = []
    for sub, (label, kw) in variants.items():
        if kw == "iptal":
            plan = base_inp.plan
            shifts = tuple(replace(s, status="cancelled", cancelled_at=future) if s.staff_id == N_B1 else s for s in plan.shifts)
            p2 = replace(plan, shifts=shifts)
            rows = X.compare(T, [X.DemandEstimate(u, r, T, 60, ({A: 30, B: 120, ED: 50}.get(u, 0.0) if r == "nurse" else 0.0), 0.0,
                                                  ({A: 30, B: 120, ED: 50}.get(u, 0.0) if r == "nurse" else 0.0), "uygun", (), (),
                                                  "random_forest", "rf@t", "t") for u in UNITS for r in ROLES], p2, BASE_STAFF, UNITS, CALS, TZ)  # fmt: skip
            inp = replace(base_inp, plan=p2, u3_rows=rows)
        else:
            inp = build({A: 30, B: 120, ED: 50}, **kw)
        res, el2 = run(inp)
        h = (res.get("cozum") or {}).get("ozet_sha256")
        out.append(evidence("T13", sub, "Bilgi sızıntısı yok", label, "Çözüm içeriği ve özeti t görünümüyle birebir aynı", res, el2,
                            {"ayni_ozet": h is not None and h == h0, "yayinlanabilir": _published(res)}, {"ozet": h, "temel_ozet": h0}))  # fmt: skip
    return out


def t14():
    out = []
    res, el = run(build({A: 30, B: 120}), overrides={"max_time_in_seconds": 0.0})
    out.append(evidence("T14", "a", "Zaman aşımı, çözüm yok", "max_time_in_seconds = 0", "UNKNOWN; öneri yok", res, el,
                        {"unknown": res["durum"] == L.UNKNOWN, "oneri_yok": _no_publication(res)}))  # fmt: skip

    def first_feasible(real):
        calls = []

        def wrapped(model, limit, overrides):
            run_ = real(model, limit, overrides)
            calls.append(1)
            if len(calls) == 1:
                run_.status = L.FEASIBLE
            return run_
        return wrapped

    with patched_solver(first_feasible):
        res, el = run(build({A: 30, B: 120}))
    out.append(evidence("T14", "b", "O1 aşaması FEASIBLE bitti", "O1 durumu FEASIBLE olarak bildirildi (çözüm gerçek)",
                        "FEASIBLE; O1 optimal değil; sözlük sırası tam değil; yalnız doğrulanmışsa yayın", res, el,
                        {"feasible": res["durum"] == L.FEASIBLE, "etiket": res["cozucu_asamalari"][0]["optimal"] is False,
                         "tam_degil": res["sozluk_sirali_tam"] is False, "dogrulandi": _published(res)}))  # fmt: skip

    def stop_third(real):
        calls = []

        def wrapped(model, limit, overrides):
            calls.append(1)
            run_ = real(model, limit, overrides)
            if len(calls) == 3:
                run_.status, run_.objective, run_.bound = L.UNKNOWN, None, None
            return run_
        return wrapped

    with patched_solver(stop_third):
        res, el = run(build({A: 30, B: 120}))
    out.append(evidence("T14", "c", "O3 aşamasında çözüm yok", "O3 UNKNOWN", "FEASIBLE; O2 sonrası doğrulanmış çözüm korunur; tam değil",
                        res, el,
                        {"feasible": res["durum"] == L.FEASIBLE, "uc_asama": len(res["cozucu_asamalari"]) == 3,
                         "uyari": "sozluk_sirasi_tamamlanamadi:O3_hareket:UNKNOWN" in res["uyarilar"], "dogrulandi": _published(res)}))  # fmt: skip
    real_verify = L.verifier.verify
    L.verifier.verify = lambda inp, sol: V.VerificationResult(False, ["deneme"])
    try:
        res, el = run(build({A: 30, B: 120}))
    finally:
        L.verifier.verify = real_verify
    out.append(evidence("T14", "d", "Doğrulayıcı geçmezse yayın yok", "OPTIMAL çözüm, doğrulayıcı reddediyor",
                        "OPTIMAL ama oneri_yayinlanabilir = false, taşıma listesi boş", res, el,
                        {"optimal": res["durum"] == L.OPTIMAL, "oneri_yok": _no_publication(res)}))  # fmt: skip
    return out


def t15():
    import subprocess
    import sys
    from pathlib import Path

    inp = build({A: 30, B: 120, ED: 240}, floats=(FLOAT_AB, FLOAT_ED))
    r1, el = run(inp)
    r2, _ = run(inp)
    root = Path(__file__).resolve().parents[1]
    code = ("import sys; sys.path[:0]=['src','tests']; import u5_scenarios as S; from medakis.optimization import layer_a as L;"
            "r=L.optimize(S.build({S.A:30,S.B:120,S.ED:240}, floats=(S.FLOAT_AB,S.FLOAT_ED))); print(r['cozum']['ozet_sha256'])")  # fmt: skip
    fresh = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300).stdout.strip()
    h1 = (r1.get("cozum") or {}).get("ozet_sha256")
    strip = lambda r: [(s["asama"], s["durum"], s["amac_degeri"]) for s in r["cozucu_asamalari"]]  # noqa: E731
    return [evidence("T15", "a", "Tekrarlanabilirlik", "Aynı girdi, tek işçi, tohum 0; aynı süreçte iki kez ve yeni süreçte bir kez",
                     "Aynı çözüm içeriği, aynı SHA-256 ve aynı aşama değerleri", r1, el,
                     {"ayni_icerik": r1["cozum"] == r2["cozum"], "ayni_asamalar": strip(r1) == strip(r2), "yeni_surec_ozet": fresh == h1},
                     {"ozet": h1, "yeni_surec_ozet": fresh})]  # fmt: skip


MATRIX = {"T1": t1, "T2": t2, "T3": t3, "T4": t4, "T5": t5, "T6": t6, "T7": t7, "T8": t8, "T9": t9, "T10": t10,
          "T11": t11, "T12": t12, "T13": t13, "T14": t14, "T15": t15}  # fmt: skip
# Deneysel K1 (iki yönlü dondurma, açık deneysel mod; klinik onay yok, yayın yok): varsayılan matristen ayrı raporlanır
FREEZE_EXPERIMENT = {"D-T3": lambda: t3(True), "D-T4": lambda: t4(True), "D-T11": lambda: t11(True), "D-T12": lambda: t12(True)}


# ---- Saldırı paketi (bağımsız doğrulayıcı) -------------------------------------------------------------------


def _rehash(sol, fn):
    bad = copy.deepcopy(sol)
    fn(bad)
    bad["ozet_sha256"] = L.solution_hash(bad)
    return bad


def attack_suite() -> list[dict[str, Any]]:
    inp = build({A: 30, B: 120, ED: 50})
    sol = L.optimize(inp)["cozum"]
    mover = sol["tasimalar"][0]["personel"]
    lock_inp = build({A: 10, ED: 100}, floats=(FLOAT_AB, FLOAT_ED), locks=[MoveLock(N_A3, ED, T - 30 * M, T + 60 * M, "onay-a3", T - 30 * M)])
    lock_sol = L.optimize(lock_inp)["cozum"]
    brk_inp = build({B: 60}, unav=[PlannedUnavailability(1, N_B1, T, T + 30 * M, "mola", PUB, "planli"),
                                    PlannedUnavailability(2, N_B1, T + 15 * M, T + 45 * M, "izin", PUB, "planli")])  # fmt: skip
    brk_sol = L.optimize(brk_inp)["cozum"]
    early_inp = build({B: 60}, extra_shifts=[PlannedShift(91, 2003, B, "nurse", 1, T0, T + 20 * M, 0, PUB, "planned", None, SRC)],
                      staff={**BASE_STAFF, 2003: Staff(2003, "N2003", "nurse", True)})  # fmt: skip
    early_sol = L.optimize(early_inp)["cozum"]

    def at(s, sid):
        return next(a for a in s["atamalar"] if a["personel"] == sid)

    def sr(s, u, r="nurse"):
        return next(x for x in s["servis_rol"] if (x["servis_id"], x["rol"]) == (u, r))

    def ex(s, u, r="nurse"):
        return next(x for x in s["servis_rol_aciklama"] if (x["servis_id"], x["rol"]) == (u, r))

    attacks = [
        ("Çift servis ataması", inp, sol, lambda s: s["atamalar"].append({**at(s, mover), "servis": A}), "H1_cift_atama"),
        ("Yanlış rol", inp, sol, lambda s: at(s, N_B1).update(rol="support"), "H2_rol_degisti"),
        ("Yetkisiz taşıma (Acil'e)", inp, sol, lambda s: at(s, N_A1).update(servis=ED), "H6_tasima_yetkisi_yok"),
        ("Onay kilidini bozma (kilitli kişiyi başka servise)", lock_inp, lock_sol, lambda s: at(s, N_A3).update(servis=A), "H8_kilit_ihlali"),
        ("Kilit bitişini yanlış gösterme", lock_inp, lock_sol, lambda s: s["kilitli_yerlesimler"][0].update(kilit_bitis=T + 5 * H), "yayin_kilitli_yerlesimler_tutarsiz"),
        ("Vardiya dışı kapasite (10:20'de çıkan kişiyi tam sayma)", early_inp, early_sol, lambda s: sr(s, B).update(kapasite_tam=sr(s, B)["kapasite_tam"] + 40), "H4_kapasite_tutarsiz"),
        ("Mola/izni kapasiteye geri ekleme", brk_inp, brk_sol, lambda s: sr(s, B).update(kapasite_tam=sr(s, B)["kapasite_tam"] + 45), "H4_kapasite_tutarsiz"),
        ("Asgari personel ihlali (A'dan ikinci hemşireyi alma)", inp, sol,
         lambda s: at(s, next(a["personel"] for a in s["atamalar"] if a["rol"] == "nurse" and a["servis"] == A)).update(servis=B), "H7_asgari_alti"),
        ("Talep manipülasyonu", inp, sol, lambda s: sr(s, B).update(talep_tam=60), "H10_talep_tutarsiz"),
        ("Açık manipülasyonu", inp, sol, lambda s: sr(s, B).update(acik_tam=0), "H10_acik_tutarsiz"),
        ("Fazla manipülasyonu", inp, sol, lambda s: sr(s, A).update(fazla_tam=0), "H10_fazla_tutarsiz"),
        ("Taşıma hedefini değiştirme", inp, sol, lambda s: s["tasimalar"][0].update(hedef_servis=ED), "yayin_tasimalar_tutarsiz"),
        ("Taşıma personelini değiştirme", inp, sol, lambda s: s["tasimalar"][0].update(personel=N_A1 if mover != N_A1 else N_A2), "yayin_tasimalar_tutarsiz"),
        ("Açıklama atomunu değiştirme", inp, sol, lambda s: ex(s, A)["atomlar"][0].update(kisi=7), "yayin_aciklama_tutarsiz"),
        ("Amaç değerini küçük gösterme", inp, sol, lambda s: s["asama_degerleri"].update(O1_karsilanmayan_talep=0), "amac_degeri_tutarsiz:O1"),
        ("Amaç değerini büyük gösterme", inp, sol, lambda s: s["asama_degerleri"].update(O4_fazla_kapasite=99_999), "amac_degeri_tutarsiz:O4"),
    ]  # fmt: skip
    out = []
    for name, i, s, fn, code in attacks:
        bad = _rehash(s, fn)
        res = V.verify(i, bad)
        out.append({"saldiri": name, "ozet_yeniden_hesaplandi": bad["ozet_sha256"] == L.solution_hash(bad),
                    "reddedildi": not res.passed, "beklenen_kod": code, "kodlar": res.violations[:6],
                    "sonuc": "gecti" if (not res.passed and any(v.startswith(code) for v in res.violations)) else "basarisiz"})  # fmt: skip
    # K1 dondurma saldırıları (özet yeniden hesaplanır)
    fz_inp = build({A: 30, B: 180}, review=[(B, "nurse")])
    fz_sol = L.optimize(fz_inp, freeze_policy=L.FREEZE_TWO_WAY, experimental_mode="test")["deneysel_sonuc"]["cozum"]
    out_inp = build({B: 120}, min_nurse={A: 3, ED: 1}, review=[(ED, "support")],
                    floats=(FloatRule("nurse", None, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil-B"),))  # fmt: skip
    out_sol = L.optimize(out_inp, freeze_policy=L.FREEZE_TWO_WAY, experimental_mode="test")["deneysel_sonuc"]["cozum"]
    for name, i, s_, fn, code in (
        ("Dondurulan çifte giriş", fz_inp, fz_sol, lambda s: at(s, N_A3).update(servis=B), "dondurma_ihlali_giris"),
        ("Belirsiz servisten çıkış (başka rol)", out_inp, out_sol, lambda s: at(s, 3001).update(servis=B), "dondurma_ihlali_cikis"),
        ("Dondurma kümesini daraltma", fz_inp, fz_sol, lambda s: s.update(dondurulan_servis_roller=[]), "dondurma_kumesi_tutarsiz"),
        ("Politikayı küresel gösterme", fz_inp, fz_sol, lambda s: s.update(dondurma_politikasi="kapali"), "on_kosul_u3_durumu"),
        ("Kısmi öneriyi tam gösterme", fz_inp, fz_sol, lambda s: s.update(oneri_kapsami="tam"), "oneri_kapsami_tutarsiz"),
        ("Deneysel etiketi silme", fz_inp, fz_sol, lambda s: s.pop("deneysel_mod"), "dondurma_deneysel_mod_eksik"),
    ):  # fmt: skip
        bad = _rehash(s_, fn)
        res = V.verify(i, bad)
        out.append({"saldiri": name, "ozet_yeniden_hesaplandi": bad["ozet_sha256"] == L.solution_hash(bad),
                    "reddedildi": not res.passed, "beklenen_kod": code, "kodlar": res.violations[:6],
                    "sonuc": "gecti" if (not res.passed and any(v.startswith(code) for v in res.violations)) else "basarisiz"})  # fmt: skip
    # Girdi tarafı saldırıları (çözüm aynı, ham girdi değişmiş)
    no_skill = replace(inp, skills=tuple(k for k in inp.skills if not (k.staff_id == mover and k.skill == "MON")))
    short_lock = replace(lock_inp, locks=(MoveLock(N_A3, ED, T - 10 * M, T + 20 * M, "onay-a3", T - 30 * M),))
    for name, i, s, code in (("Gerekli yetkinliği kaldırma (girdi)", no_skill, sol, "H3_yetkinlik_eksik"),
                             ("Kilit süresini kısaltma (girdi)", short_lock, lock_sol, "H8_asgari_kalis_saglanmiyor")):  # fmt: skip
        res = V.verify(i, s)
        out.append({"saldiri": name, "ozet_yeniden_hesaplandi": None, "reddedildi": not res.passed, "beklenen_kod": code,
                    "kodlar": res.violations[:6], "sonuc": "gecti" if any(v.startswith(code) for v in res.violations) else "basarisiz"})  # fmt: skip
    only_hash = copy.deepcopy(sol)
    only_hash["tasimalar"] = []
    res = V.verify(inp, only_hash)
    out.append({"saldiri": "Özeti güncellemeden değişiklik (yalnız bütünlük)", "ozet_yeniden_hesaplandi": False, "reddedildi": not res.passed,
                "beklenen_kod": "ozet_uyusmuyor", "kodlar": res.violations[:6],
                "sonuc": "gecti" if "ozet_uyusmuyor" in res.violations and len(res.violations) > 1 else "basarisiz"})  # fmt: skip
    return out


# ---- O1–O4 sözlük sırası --------------------------------------------------------------------------------------


def lexicographic_suite() -> list[dict[str, Any]]:
    out = []
    # L1: O1 daha iyi → daha fazla taşıma kabul edilir
    res, el = run(build({A: 10, B: 180}, min_nurse={A: 1}))
    sv = stage_values(res)
    out.append({"kimlik": "L1", "varsayim": "B D360, kapasite 120; A 3 hemşire asgari 1",
                "beklenen": "2 taşıma (O1 360→… en küçük), O3 = 2", "gercek": {"tasima": len(moves(res)), **sv, "durum": res["durum"]},
                "sonuc": "gecti" if (len(moves(res)) == 2 and sv.get("O3_hareket") == 2 and sv.get("O1_karsilanmayan_talep") == 120) else "basarisiz"})  # fmt: skip
    # L2: O1 eşit → O2 denge
    both = FloatRule("nurse", N_A3, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":A3")
    res, el = run(build({A: 10, B: 120, ED: 240}, floats=(both,)))
    sv = stage_values(res)
    out.append({"kimlik": "L2", "varsayim": "Tek destek hemşiresi; B D240, Acil D480; iki seçenekte O1 = 420",
                "beklenen": "Acil (O2 625 < 750)", "gercek": {"tasimalar": moves(res), **sv},
                "sonuc": "gecti" if moves(res) == [(N_A3, A, ED)] and sv.get("O2_denge") == 625 else "basarisiz"})  # fmt: skip
    # L3: O1 ve O2 eşit → O3 daha az taşıma
    halves = [PlannedUnavailability(1, N_A2, T, T + 30 * M, "izin", PUB, "planli"),
              PlannedUnavailability(2, N_A3, T + 30 * M, T + H, "izin", PUB, "planli")]  # fmt: skip
    res, el = run(build({A: 10, B: 90}, min_nurse={A: 1}, unav=halves))
    sv = stage_values(res)
    out.append({"kimlik": "L3", "varsayim": "B açığı 60; A1 tam (60 dk), A2 ve A3 yarım (30+30); A asgari 1",
                "beklenen": "1 taşıma (A1), 2 yarım kişi değil; O1 = 0", "gercek": {"tasimalar": moves(res), **sv},
                "sonuc": "gecti" if moves(res) == [(N_A1, A, B)] and sv.get("O1_karsilanmayan_talep") == 0 and sv.get("O3_hareket") == 1 else "basarisiz"})  # fmt: skip
    # L4: ilk üçü eşit → O4 tolerans üstü fazla
    cfg = replace(CONFIG, surplus_tolerance={A: 0, B: 1000, ED: 1000, CL: 1000})
    to_b = FloatRule("nurse", None, (A, B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":B")
    reqs = tuple(q for q in REQS if q.unit_id != ED) + (UnitSkillRequirement(ED, "nurse", ("MON",), SRC, "test", KNOWN),)
    staff, home = profile(nurses=(3, 2, 3, 1))
    res, el = run(build({A: 10, B: 90, ED: 10}, floats=(to_b,), config=cfg, reqs=reqs, staff=staff, home=home))
    sv = stage_values(res)
    src = [m[1] for m in moves(res)]
    out.append({"kimlik": "L4", "varsayim": "B açığı 60; A ve Acil'de fazla; tolerans A 0, Acil 1000",
                "beklenen": "Taşıma A'dan (A'nın tolerans üstü fazlası azalır)", "gercek": {"tasimalar": moves(res), **sv},
                "sonuc": "gecti" if src == [A] and sv.get("O3_hareket") == 1 else "basarisiz"})  # fmt: skip
    # L5: O3, O4'ten önce: açık yokken fazla için taşıma yapılmaz
    cfg = replace(CONFIG, surplus_tolerance={A: 0, B: 1000, ED: 1000, CL: 1000})
    res, el = run(build({A: 10, B: 60}, config=cfg))
    sv = stage_values(res)
    out.append({"kimlik": "L5", "varsayim": "B'de açık yok; A'da tolerans üstü fazla", "beklenen": "0 taşıma; O4 > 0",
                "gercek": {"tasimalar": moves(res), **sv},
                "sonuc": "gecti" if moves(res) == [] and (sv.get("O4_fazla_kapasite") or 0) > 0 else "basarisiz"})  # fmt: skip
    t14_ev = {e["alt"]: e for e in t14()}
    out.append({"kimlik": "L6", "varsayim": "O1 FEASIBLE bildirildi", "beklenen": "FEASIBLE, optimal=false, sözlük tam değil",
                "gercek": {"durum": t14_ev["b"]["durum"], "asamalar": t14_ev["b"]["asamalar"]}, "sonuc": t14_ev["b"]["sonuc"]})
    out.append({"kimlik": "L7", "varsayim": "O3 UNKNOWN", "beklenen": "Doğrulanmış O2 çözümü korunur; tam değil",
                "gercek": {"durum": t14_ev["c"]["durum"], "asamalar": t14_ev["c"]["asamalar"]}, "sonuc": t14_ev["c"]["sonuc"]})
    return out


# ---- Performans ve ölçek -------------------------------------------------------------------------------------


def perf_inputs(scale: int, many_options: bool = False, many_atoms: bool = False, limit: float = 10.0) -> LayerAInputs:
    """Sentetik ölçek profili. Klinik kurallar genişletilmez; 'çok seçenek' profilinde rol düzeyi destek
    yetkisi A/B/Acil arasında tanımlıdır (test_sentetik) ve hemşirelere TRIAGE verilir."""
    staff, home = profile(nurses=(8 * scale, 8 * scale, 6 * scale, 3 * scale), supervisors=(2 * scale, 2 * scale, 2 * scale, 0),
                          support=(3 * scale, 3 * scale, 2 * scale, 0))  # fmt: skip
    nurses = [s for s, p in staff.items() if p.role == "nurse"]
    floats = (FLOAT_AB,)
    reqs = REQS
    sk = skills(staff, home)
    if many_options:
        floats = (FloatRule("nurse", None, (A, B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":genis"),)
        sk = skills(staff, home, everyone=tuple(nurses))
    unav, extra = [], ()
    if many_atoms:
        unav = [PlannedUnavailability(i, sid, T + ((i * 5) % 45) * M, T + ((i * 5) % 45 + 15) * M, "mola", PUB, "planli")
                for i, sid in enumerate(nurses, start=1)]  # fmt: skip
        chain = []
        for u in (A, B, ED):  # 10 dk'lık ardışık geçerlilik dilimleri (aynı değer; yalnız sınır sayısını artırır)
            for k in range(6):
                start = T + k * 10 * M
                chain.append(StaffingRule(u, "nurse", (0, 24), 2, SRC + ":dilim", "test", start, start + 10 * M, KNOWN))
        extra = tuple(chain)
    cfg = replace(CONFIG, time_limit_s=limit)
    demand = {A: 40 * scale, B: 300 * scale, ED: 260 * scale, CL: 60 * scale}
    return build(demand, staff=staff, home=home, floats=floats, sk=sk, unav=unav, extra_rules=extra, config=cfg)


def measure(inp: LayerAInputs) -> dict[str, Any]:
    sizes = {}

    def capture(real):
        def wrapped(model, limit, overrides):
            if not sizes:
                proto = model.Proto()
                sizes.update(degisken=len(proto.variables), kisit=len(proto.constraints))
            return real(model, limit, overrides)
        return wrapped

    tracemalloc.start()
    with patched_solver(capture):
        t0 = time.perf_counter()
        res = L.optimize(inp)
        total = time.perf_counter() - t0
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    vt = None
    if res["cozum"] is not None:
        t1 = time.perf_counter()
        ok = V.verify(inp, res["cozum"]).passed
        vt = round(time.perf_counter() - t1, 4)
    else:
        ok = None
    again = L.optimize(inp)
    return {"personel": len(inp.staff), "durum": res["durum"], "asamalar": [(s["asama"], s["durum"], s["sure_sn"]) for s in res["cozucu_asamalari"]],
            "cozucu_sn": round(sum(s["sure_sn"] for s in res["cozucu_asamalari"]), 4), "toplam_sn": round(total, 4),
            "dogrulayici_sn": vt, "dogrulama": ok, "python_bellek_tepe_mb": round(peak / 2**20, 2), **sizes,
            "tasima": len(res["tasimalar"]), "tutarli": (res.get("cozum") or {}).get("ozet_sha256") == (again.get("cozum") or {}).get("ozet_sha256"),
            "nedenler": (res["nedenler"] + res["uyarilar"])[:4]}  # fmt: skip


PERF_PROFILES = {
    "P1_mevcut_yaklasik_40": dict(scale=1),
    "P2_x2": dict(scale=2),
    "P3_x5": dict(scale=5),
    "P4_x5_cok_secenek": dict(scale=5, many_options=True),
    "P5_x5_cok_atom": dict(scale=5, many_atoms=True),
    "P6_x10_cok_secenek_cok_atom": dict(scale=10, many_options=True, many_atoms=True),
    "P7_x10_kisa_sure_0_05sn": dict(scale=10, many_options=True, many_atoms=True, limit=0.05),
}
