"""Aşama 5 / U4: Katman A CP-SAT optimizasyonu ve bağımsız doğrulayıcı (veritabanısız, hedefli).

Bütün kurallar, takvimler, ağırlıklar, toleranslar, destek yetkileri ve yetkinlik eşlemeleri AÇIKÇA
'test_sentetik' fikstürleridir; gerçek klinik standart değildir.
"""

import copy
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

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
    demand_staff_minutes,
)

TZ = ZoneInfo("Europe/Istanbul")
T0 = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)  # Salı, yerel 08:00
H, M = timedelta(hours=1), timedelta(minutes=1)
T = T0 + 2 * H  # karar anı: yerel 10:00
PUB = T0 - timedelta(days=14)
KNOWN = T0 - timedelta(days=30)
SRC = "test_sentetik"
A, B, ED, CL = 1, 2, 3, 4
UNITS = {A: X.UnitInfo(A, "Yatakli Servis A", "inpatient"), B: X.UnitInfo(B, "Yatakli Servis B", "inpatient"),
         ED: X.UnitInfo(ED, "Acil Servis", "emergency"), CL: X.UnitInfo(CL, "Ayaktan Klinik", "outpatient")}  # fmt: skip
NURSES = {11: A, 12: A, 13: A, 21: B, 22: B, 31: ED, 32: ED, 41: CL}
SUPERVISORS = {15: A, 25: B, 35: ED}
SUPPORT = {16: A, 26: B, 36: ED}
STAFF = {**{s: Staff(s, f"H{s}", "nurse", True) for s in NURSES}, **{s: Staff(s, f"S{s}", "supervisor", True) for s in SUPERVISORS},
         **{s: Staff(s, f"D{s}", "support", True) for s in SUPPORT}}  # fmt: skip
HOME = {**NURSES, **SUPERVISORS, **SUPPORT}
CATALOG = frozenset({"MON", "MED", "IV", "TRIAGE"})
COVER = Interval(T0 - timedelta(days=3), T0 + timedelta(days=5))
CALS = tuple(always_open(u, COVER, SRC, "test", KNOWN) for u in (A, B, ED)) + (
    weekly_calendar(CL, [(date(2026, 10, 26) + timedelta(days=i), "yok") for i in range(5)],
                    {wd: (time(8), time(17)) for wd in range(5)}, time(13), "Europe/Istanbul", SRC, "test", KNOWN),)  # fmt: skip
MIN_NURSE = {A: 2, B: 2, ED: 2, CL: 1}


def shifts(extra=()):
    out = []
    for i, (sid, unit) in enumerate(sorted(HOME.items()), start=1):
        hours = 9 if unit == CL else 12
        out.append(PlannedShift(i, sid, unit, STAFF[sid].role, 1, T0, T0 + hours * H, 0, PUB, "planned", None, SRC))
    return out + list(extra)


def rules(min_nurse=None):
    mn = {**MIN_NURSE, **(min_nurse or {})}
    out = []
    for u in UNITS:
        out.append(StaffingRule(u, "nurse", (0, 24), mn[u], SRC, "test", KNOWN, None, KNOWN))
        out.append(StaffingRule(u, "supervisor", (0, 24), 1 if u != CL else 0, SRC, "test", KNOWN, None, KNOWN))
        out.append(StaffingRule(u, "support", (0, 24), 0, SRC, "test", KNOWN, None, KNOWN))
    return tuple(out)


FLOAT_AB = FloatRule("nurse", None, (A, B), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":A-B")
FLOAT_ED = FloatRule("nurse", None, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil")
REQS = (UnitSkillRequirement(A, "nurse", ("MON", "MED"), SRC, "test", KNOWN),
        UnitSkillRequirement(B, "nurse", ("MON", "MED"), SRC, "test", KNOWN),
        UnitSkillRequirement(ED, "nurse", ("TRIAGE",), SRC, "test", KNOWN),
        UnitSkillRequirement(CL, "nurse", ("MED",), SRC, "test", KNOWN))  # fmt: skip


def skills(triage_13_until=None, drop=()):
    out = [StaffSkill(s, k, KNOWN, None) for s in NURSES for k in ("MON", "MED") if (s, k) not in drop]
    out.append(StaffSkill(13, "TRIAGE", KNOWN, triage_13_until))
    out += [StaffSkill(s, "TRIAGE", KNOWN, None) for s in (31, 32)]
    return tuple(out)


CONFIG = OptimizationConfig({u: 1 for u in UNITS}, {u: 30 for u in UNITS}, 10.0, SRC, "test", KNOWN)


def build(nurse=None, at=T, extra_shifts=(), unav=(), min_nurse=None, floats=(FLOAT_AB,), sk=None, locks=(), config=CONFIG,
          reqs=REQS, review=(), plan_rules=None, cals=CALS, staff=STAFF):  # fmt: skip
    """Senaryo: nurse = {servis: ŷ (iş-dk)}; u = 0,5 olduğundan D = 2·ŷ personel-dk. Diğer roller talep 0."""
    plan = PlanSet(tuple(shifts(extra_shifts)), tuple(unav), plan_rules or rules(min_nurse), tuple(floats),
                   PlanningConfig(0.5, 0.35, 1.0, 10.0, SRC))  # fmt: skip
    demand = []
    for u in UNITS:
        for r in ("nurse", "supervisor", "support"):
            point = (nurse or {}).get(u, 0.0) if r == "nurse" else 0.0
            status, why = ("inceleme_gerekli", ("yuksek_goreli_tampon",)) if (u, r) in review else ("uygun", ())
            demand.append(X.DemandEstimate(u, r, at, 60, point, 0.0, point, status, why, (), "random_forest", "rf@t", "t"))
    rows = X.compare(at, demand, plan, staff, UNITS, cals, TZ)
    return LayerAInputs(at, plan, staff, UNITS, cals, rows, sk if sk is not None else skills(), CATALOG, reqs, tuple(locks),
                        config, TZ)  # fmt: skip


def moves(res):
    return [(m["personel"], m["son_onayli_servis"], m["hedef_servis"]) for m in res["tasimalar"]]


def row(res, u, r="nurse"):
    return next(x for x in res["servis_rol"] if x["servis_id"] == u and x["rol"] == r)


def published(res):
    assert res["oneri_yayinlanabilir"] and res["dogrulama"]["gecti"] and res["insan_onayi_gerekli"] is True
    return res


# ---- 1, 2: yeterli personel ve yetkili destek ---------------------------------------------------------


def test_enough_staff_no_needless_move():
    res = published(L.optimize(build({A: 60, B: 30, ED: 50})))
    assert res["durum"] == L.OPTIMAL and res["sozluk_sirali_tam"] and moves(res) == []
    assert res["cozucu_asamalari"][0]["amac_degeri"] == 0 and len(res["cozucu_asamalari"]) == 4


def test_high_need_in_b_supported_from_a_keeping_a_minimum():
    res = published(L.optimize(build({A: 30, B: 120, ED: 50})))
    assert len(moves(res)) == 1 and moves(res)[0][1:] == (A, B)
    b, a = row(res, B), row(res, A)
    assert (b["ihtiyac_personel_dk_tam"], b["mevcut_kapasite_tam"], b["kapasite_personel_dk_tam"]) == (240, 120, 180)
    assert (b["mevcut_acik_tam"], b["acik_personel_dk_tam"]) == (120, 60)
    assert a["onerilen_kisi"] == 2 and all(x["kisi"] >= x["asgari_kisi"] for x in a["atomlar"])  # H9
    m = res["tasimalar"][0]
    assert m["asgari_kalis_dk"] == 60 and m["destek_kurali_kaynagi"].startswith(SRC) and m["sure_dk"] == 60
    assert set(m["gerekli_yetkinlikler"]) == {"MON", "MED"}
    # A'nın asgarisi 3 olsaydı taşıma yapılamazdı.
    strict = published(L.optimize(build({A: 30, B: 120, ED: 50}, min_nurse={A: 3})))
    assert moves(strict) == [] and row(strict, B)["acik_personel_dk_tam"] == 120


def test_min_staff_violation_fixed_by_valid_move():
    inp = build({A: 30, B: 40, ED: 50}, min_nurse={B: 3})
    assert next(r for r in inp.u3_rows if (r["servis_id"], r["rol"]) == (B, "nurse"))["durum"] == X.IHLAL
    res = published(L.optimize(inp))
    assert moves(res) and moves(res)[0][2] == B and row(res, B)["onerilen_kisi"] == 3


# ---- 3, 4: belirsizlik, kapalı ve kısmi pencere --------------------------------------------------------


def test_high_uncertainty_gives_no_proposal():
    res = L.optimize(build({A: 30, B: 120, ED: 400}, review=[(ED, "nurse")]), freeze_policy=L.FREEZE_OFF)  # küresel ret politikası
    assert res["durum"] == L.REVIEW and not res["oneri_yayinlanabilir"] and res["tasimalar"] == []
    assert "u3_inceleme_gerekli:3:nurse" in res["nedenler"] and res["cozucu_asamalari"] == []


def test_closed_clinic_gets_no_staff_and_partial_window_goes_to_review():
    to_clinic = FloatRule("nurse", None, (A, CL), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":poliklinik")
    late = T0 + 10 * H  # yerel 18:00, poliklinik kapalı
    res = published(L.optimize(build({A: 10}, at=late, floats=(to_clinic,))))
    assert all(m[2] != CL for m in moves(res)) and row(res, CL)["onerilen_kisi"] == 0
    partial = L.optimize(build({A: 10}, at=T0 + 8 * H + 30 * M, floats=(to_clinic,)), freeze_policy=L.FREEZE_OFF)  # 16:30-17:30
    assert partial["durum"] == L.REVIEW and "kismi_acik_pencere:4" in partial["nedenler"] and partial["tasimalar"] == []


# ---- 5, 6, 7, 8 -------------------------------------------------------------------------------------------


def test_impossible_minimum_is_infeasible_without_relaxation():
    res = L.optimize(build({B: 40}, min_nurse={B: 4}))
    assert res["durum"] == L.INFEASIBLE and not res["oneri_yayinlanabilir"] and res["tasimalar"] == []
    assert any(t.startswith("H7_karsilanamaz:2:nurse:") and t.endswith("3<4") for t in res["tani"])


def test_known_absence_reduces_capacity_unknown_does_not_leak():
    absent = lambda rec: PlannedUnavailability(1, 22, T0, T0 + 12 * H, "devamsizlik", rec, "ani")  # noqa: E731
    known = published(L.optimize(build({A: 30, B: 60}, unav=[absent(T - 10 * M)])))
    unknown = published(L.optimize(build({A: 30, B: 60}, unav=[absent(T + 10 * M)])))
    assert unknown["cozum"]["servis_rol"][3]["kapasite_tam"] == 120 and moves(unknown) == []
    b = row(known, B)
    assert b["mevcut_kapasite_tam"] == 60 and b["mevcut_kisi"] == 2  # kişi planda, dakikası düştü
    assert len(moves(known)) == 1 and b["kapasite_personel_dk_tam"] == 120


def test_one_person_never_in_two_units():
    both = FloatRule("nurse", 13, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":13")
    res = published(L.optimize(build({A: 10, B: 120, ED: 240}, floats=(both,))))
    ids = [a["personel"] for a in res["cozum"]["atamalar"]]
    assert len(ids) == len(set(ids)) == len(HOME) and len(moves(res)) == 1 and moves(res)[0][0] == 13


def test_nurse_without_valid_triage_not_moved_to_ed():
    ed_only = FloatRule("nurse", None, (ED,), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil")
    expiring = L.optimize(build({A: 10, ED: 240}, floats=(ed_only,), sk=skills(triage_13_until=T + 30 * M)))
    assert published(expiring) and moves(expiring) == []  # TRIAGE pencerenin ortasında bitiyor
    valid = published(L.optimize(build({A: 10, ED: 240}, floats=(ed_only,))))
    assert moves(valid) == [(13, A, ED)]  # yalnız geçerli TRIAGE'ı olan hemşire


# ---- 9: bozuk veya eksik girdi → MODEL_INVALID -----------------------------------------------------------


@pytest.mark.parametrize("make,code", [
    (lambda: build({B: 60}, extra_shifts=[PlannedShift(99, 11, B, "nurse", 1, T0, T0 + 4 * H, 0, PUB, "planned", None, SRC)]),
     "u3_gecersiz_plan"),
    (lambda: build({B: 60}, config=None), "optimizasyon_yapilandirmasi_yok"),
    (lambda: build({B: 60}, config=replace(CONFIG, weights={A: 1, B: 1, ED: 1})), "oncelik_agirligi_gecersiz:4"),
    (lambda: build({B: 60}, config=replace(CONFIG, source=" ")), "optimizasyon_yapilandirmasi_kaynaksiz"),
    (lambda: build({B: 60}, reqs=REQS[:1]), "yetkinlik_kurali_yok:2:nurse"),
    (lambda: build({B: 60}, floats=(replace(FLOAT_AB, source=""),)), "destek_kurali_kaynaksiz"),
    (lambda: build({B: 60}, floats=(replace(FLOAT_AB, required_skills=("UCMAK",)),)), "bilinmeyen_yetkinlik"),
    (lambda: build({B: 60}, cals=CALS[:3]), "u3_veri_eksik:4"),
])  # fmt: skip
def test_invalid_or_missing_inputs_are_model_invalid(make, code):
    res = L.optimize(make())
    assert res["durum"] == L.MODEL_INVALID and not res["oneri_yayinlanabilir"] and res["cozucu_asamalari"] == []
    assert any(n.startswith(code) for n in res["nedenler"]), res["nedenler"]


def test_unknown_unit_or_role_in_u3_rows_is_model_invalid():
    inp = build({B: 60})
    rows = list(inp.u3_rows) + [{**inp.u3_rows[0], "rol": "physician"}]
    res = L.optimize(replace(inp, u3_rows=rows))
    assert res["durum"] == L.MODEL_INVALID and any(n.startswith("bilinmeyen_servis_rol") for n in res["nedenler"])


# ---- 10: mola çakışması ve vardiya geçişinde H7 ----------------------------------------------------------


def test_staggered_breaks_block_move_that_breaks_atom_minimum():
    brk = [PlannedUnavailability(1, 11, T, T + 30 * M, "mola", PUB, "planli"),
           PlannedUnavailability(2, 12, T + 30 * M, T + H, "mola", PUB, "planli")]  # fmt: skip
    res = published(L.optimize(build({A: 10, B: 120}, unav=brk)))
    assert moves(res) == []  # dakika toplamı fazla görünse de her atomda A'da 2 hemşire gerekiyor
    assert all(x["kisi"] >= x["asgari_kisi"] for x in row(res, A)["atomlar"])
    no_breaks = published(L.optimize(build({A: 10, B: 120})))
    assert len(moves(no_breaks)) == 1


def test_shift_handover_counts_only_present_atoms():
    early = PlannedShift(90, 14, A, "nurse", 1, T0, T + 30 * M, 0, PUB, "planned", None, SRC)  # 10:30'da çıkar
    staff = {**STAFF, 14: Staff(14, "H14", "nurse", True)}
    extra_sk = skills() + (StaffSkill(14, "MON", KNOWN, None), StaffSkill(14, "MED", KNOWN, None))
    res = published(L.optimize(build({A: 10, B: 120}, extra_shifts=[early], staff=staff, sk=extra_sk)))
    assert row(res, A)["mevcut_kapasite_tam"] == 3 * 60 + 30  # yarım pencere katkı
    assert len(moves(res)) == 1 and all(x["kisi"] >= 2 for x in row(res, A)["atomlar"])


# ---- 11, 12: kilitler ve asgari kalış ---------------------------------------------------------------------


def _lock(staff=11, target=B, approved=T - 30 * M, revoked=None, until=T + 90 * M):
    return MoveLock(staff, target, T - 30 * M, until, "onay-1", approved, revoked)


def test_known_and_unknown_locks():
    held = published(L.optimize(build({A: 10, B: 10}, locks=[_lock()])))
    assert row(held, B)["onerilen_kisi"] == 3 and held["kilitli_yerlesimler"][0]["personel"] == 11 and moves(held) == []
    for lk in (_lock(approved=T + 5 * M), _lock(revoked=T - 5 * M)):
        free = published(L.optimize(build({A: 10, B: 10}, locks=[lk])))
        assert row(free, B)["onerilen_kisi"] == 2 and free["kilitli_yerlesimler"] == []
    later_revoke = published(L.optimize(build({A: 10, B: 10}, locks=[_lock(revoked=T + 5 * M)])))
    assert row(later_revoke, B)["onerilen_kisi"] == 3  # iptal t'de bilinmiyor: kilit geçerli


def test_conflicting_or_unusable_locks_refused():
    clash = L.optimize(build({A: 10}, locks=[_lock(), _lock(target=ED)]))
    assert clash["durum"] == L.MODEL_INVALID and "cakisan_kilit:11" in clash["nedenler"]
    absent = PlannedUnavailability(1, 11, T0, T0 + 12 * H, "devamsizlik", T - 5 * M, "ani")
    unusable = L.optimize(build({A: 10}, unav=[absent], locks=[_lock()]))
    assert unusable["durum"] == L.REVIEW and "kilitli_personel_kullanilamiyor:11" in unusable["nedenler"]


def test_min_stay_preserved_across_runs():
    first = published(L.optimize(build({A: 30, B: 120})))
    (sid, src, dst), = moves(first)
    t2 = T + 15 * M
    # Talep tersine döndü: A kısıtlı, B rahat. Onaysız öneri kilit değildir → kişi geri döner.
    unapproved = published(L.optimize(build({A: 120, B: 10}, at=t2)))
    assert not any(m[0] == sid and m[2] == dst for m in moves(unapproved))
    approved = MoveLock(sid, dst, T, T + 60 * M, "onay-7", T + 5 * M)  # asgari kalış 60 dk
    kept = published(L.optimize(build({A: 120, B: 10}, at=t2, locks=[approved])))
    assignment = {a["personel"]: a["servis"] for a in kept["cozum"]["atamalar"]}
    assert assignment[sid] == dst and kept["kilitli_yerlesimler"][0]["onay_kimligi"] == "onay-7"


# ---- 13: sahte veya değiştirilmiş çözüm ------------------------------------------------------------------


@pytest.fixture(scope="module")
def solved():
    inp = build({A: 30, B: 120, ED: 50})
    res = published(L.optimize(inp))
    return inp, res["cozum"]


def _tamper(sol, fn, rehash=True):
    bad = copy.deepcopy(sol)
    fn(bad)
    if rehash:
        bad["ozet_sha256"] = L.solution_hash(bad)
    return bad


def _mover(sol):
    return sol["tasimalar"][0]["personel"]


def test_verifier_accepts_original(solved):
    inp, sol = solved
    assert V.verify(inp, sol).passed


@pytest.mark.parametrize("change,code", [
    (lambda s: next(a for a in s["atamalar"] if a["personel"] == 12).update(servis=ED), "H6_tasima_yetkisi_yok:12:3"),
    (lambda s: s["atamalar"].append({"personel": 999, "rol": "nurse", "servis": B, "sabit": False}), "H6_plan_disi_personel:999"),
    (lambda s: s["atamalar"].pop(0), "H1_atamasiz_personel"),
    (lambda s: next(a for a in s["atamalar"] if a["personel"] == 21).update(rol="supervisor"), "H2_rol_degisti:21"),
    (lambda s: next(r for r in s["servis_rol"] if (r["servis_id"], r["rol"]) == (B, "nurse")).update(kapasite_tam=240),
     "H4_kapasite_tutarsiz:2:nurse"),
    (lambda s: next(r for r in s["servis_rol"] if (r["servis_id"], r["rol"]) == (B, "nurse")).update(acik_tam=0),
     "H10_acik_tutarsiz:2:nurse"),
    (lambda s: s["asama_degerleri"].update(O1_karsilanmayan_talep=0), "amac_degeri_tutarsiz:O1"),
])  # fmt: skip
def test_verifier_catches_tampered_content(solved, change, code):
    inp, sol = solved
    result = V.verify(inp, _tamper(sol, change))
    assert not result.passed and any(x.startswith(code) for x in result.violations), result.violations


def test_verifier_catches_min_staff_and_home_violation(solved):
    inp, sol = solved
    mover = _mover(sol)
    second = next(a["personel"] for a in sol["atamalar"] if a["rol"] == "nurse" and a["servis"] == A)
    bad = _tamper(sol, lambda s: next(a for a in s["atamalar"] if a["personel"] == second).update(servis=B))
    result = V.verify(inp, bad)
    assert any(x.startswith("H7_asgari_alti:1:nurse") for x in result.violations)
    assert f"H9_ev_servisi_asgari_alti:{mover}:1" in result.violations


def test_verifier_catches_changed_skill_lock_and_hash(solved):
    inp, sol = solved
    mover = _mover(sol)
    no_skill = replace(inp, skills=tuple(k for k in inp.skills if not (k.staff_id == mover and k.skill == "MON")))
    assert f"H3_yetkinlik_eksik:{mover}:2" in V.verify(no_skill, sol).violations
    locked_elsewhere = replace(inp, locks=(MoveLock(mover, ED, T - H, T + H, "onay-9", T - H),))
    assert f"H8_kilit_ihlali:{mover}" in V.verify(locked_elsewhere, sol).violations
    unhashed = _tamper(sol, lambda s: s["tasimalar"].clear(), rehash=False)
    assert "ozet_uyusmuyor" in V.verify(inp, unhashed).violations


def test_solver_output_rejected_by_verifier_is_not_published(monkeypatch):
    monkeypatch.setattr(L.verifier, "verify", lambda inp, sol: V.VerificationResult(False, ["deneme_ihlali"]))
    res = L.optimize(build({A: 30, B: 120}))
    assert res["durum"] == L.OPTIMAL and not res["oneri_yayinlanabilir"] and res["tasimalar"] == [] and res["cozum"] is None
    assert res["dogrulama"]["ihlaller"] == ["deneme_ihlali"]


# ---- 14: sözlük sıralı öncelikler ------------------------------------------------------------------------


def test_o1_before_o3_and_o3_before_o4():
    res = published(L.optimize(build({A: 30, B: 120})))  # açığı kapatmak hareketten önce gelir
    stages = {s["asama"]: s["amac_degeri"] for s in res["cozucu_asamalari"]}
    assert stages["O1_karsilanmayan_talep"] == 60 and stages["O3_hareket"] == 1
    cfg = replace(CONFIG, surplus_tolerance={A: 0, B: 1000, ED: 1000, CL: 1000})
    calm = published(L.optimize(build({A: 10, B: 60}, config=cfg)))  # B'de açık yok; A'daki fazla için taşıma YOK
    stages = {s["asama"]: s["amac_degeri"] for s in calm["cozucu_asamalari"]}
    assert moves(calm) == [] and stages["O3_hareket"] == 0 and stages["O4_fazla_kapasite"] > 0


def test_o2_balances_relative_gap_when_o1_ties():
    both = FloatRule("nurse", 13, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":13")
    res = published(L.optimize(build({A: 10, B: 120, ED: 240}, floats=(both,))))  # D_B 240 (g %50), D_ED 480 (g %75)
    assert moves(res) == [(13, A, ED)]
    stages = {s["asama"]: s["amac_degeri"] for s in res["cozucu_asamalari"]}
    assert stages["O1_karsilanmayan_talep"] == 120 + 300 and stages["O2_denge"] == 625  # 300/480 = %62,5


# ---- 15, 16: zaman sınırı ve belirlenimcilik --------------------------------------------------------------


def test_timeout_without_solution_is_unknown():
    res = L.optimize(build({A: 30, B: 120}), solver_overrides={"max_time_in_seconds": 0.0})
    assert res["durum"] == L.UNKNOWN and not res["oneri_yayinlanabilir"] and res["tasimalar"] == []


def test_feasible_stage_reported_honestly(monkeypatch):
    real = L._run_solver
    calls = []

    def downgrade(model, limit, overrides):
        run = real(model, limit, overrides)
        calls.append(run.status)
        if len(calls) == 1:
            run.status = L.FEASIBLE  # O1 zaman sınırına takıldı varsayımı
        return run

    monkeypatch.setattr(L, "_run_solver", downgrade)
    res = published(L.optimize(build({A: 30, B: 120})))
    assert res["durum"] == L.FEASIBLE and not res["sozluk_sirali_tam"]
    assert res["cozucu_asamalari"][0]["optimal"] is False and "asama_optimal_degil:O1_karsilanmayan_talep" in res["uyarilar"]


def test_later_stage_without_solution_keeps_earlier_verified_solution(monkeypatch):
    real = L._run_solver
    count = {"n": 0}

    def stop_at_o3(model, limit, overrides):
        count["n"] += 1
        run = real(model, limit, overrides)
        if count["n"] == 3:
            run.status, run.objective, run.bound = L.UNKNOWN, None, None
        return run

    monkeypatch.setattr(L, "_run_solver", stop_at_o3)
    res = published(L.optimize(build({A: 30, B: 120})))
    assert res["durum"] == L.FEASIBLE and len(res["cozucu_asamalari"]) == 3
    assert "sozluk_sirasi_tamamlanamadi:O3_hareket:UNKNOWN" in res["uyarilar"]


def _strip_time(res):
    out = copy.deepcopy(res)
    for s in out["cozucu_asamalari"]:
        s.pop("sure_sn")
    return out


def test_same_inputs_same_solution():
    inp = build({A: 30, B: 120, ED: 240}, floats=(FLOAT_AB, FLOAT_ED))
    assert _strip_time(L.optimize(inp)) == _strip_time(L.optimize(inp))


# ---- 17: sıfır talep, sıfır kapasite, yuvarlama -----------------------------------------------------------


def test_rounding_and_zero_boundaries():
    assert demand_staff_minutes(100.0, 0.5) == 200 and demand_staff_minutes(0.0, 0.8) == 0
    assert demand_staff_minutes(1e-6, 0.5) == 1  # talep yukarı
    assert demand_staff_minutes(0.3, 0.1) == 3  # 0,3/0,1 kayan noktada 2,9999…; ondalıkla kesin 3
    with pytest.raises(ValueError):
        demand_staff_minutes(-1.0, 0.5)
    brk = PlannedUnavailability(1, 21, T, T + 30 * M + timedelta(seconds=30), "mola", PUB, "planli")  # 29,5 dk kalır
    res = published(L.optimize(build({B: 60}, unav=[brk])))
    b = row(res, B)
    assert b["mevcut_kapasite_tam"] == 29 + 60  # 29,5 dk aşağı → 29 (kapasite aşağı yuvarlanır)
    assert b["kapasite_personel_dk_ham"] - b["kapasite_personel_dk_tam"] == pytest.approx(0.5)  # ham ve tam ayrı
    zero = published(L.optimize(build({})))  # her yerde talep 0
    assert all(r["acik_personel_dk_tam"] == 0 for r in zero["servis_rol"]) and moves(zero) == []
    assert {s["asama"]: s["amac_degeri"] for s in zero["cozucu_asamalari"]}["O2_denge"] == 0


# ---- 18: yazma yok ----------------------------------------------------------------------------------------


def test_no_writes_and_inputs_unchanged():
    inp = build({A: 30, B: 120})
    before = copy.deepcopy((inp.plan, dict(inp.staff), list(inp.u3_rows), inp.locks))
    res = published(L.optimize(inp))
    assert (inp.plan, dict(inp.staff), list(inp.u3_rows), inp.locks) == before
    assert not any(k in res for k in ("atanan_personel", "vardiya_degisikligi"))
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "medakis" / "optimization"
    for name in ("layer_a.py", "layer_a_contracts.py", "verifier.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}  # fmt: skip
        assert not {m for m in mods if m and m.startswith(("medakis.repositories", "sqlalchemy", "psycopg", "medakis.synthetic"))}
    verifier_mods = {n.module for n in ast.walk(ast.parse((root / "verifier.py").read_text(encoding="utf-8")))
                     if isinstance(n, ast.ImportFrom)}  # fmt: skip
    assert "medakis.optimization.layer_a" not in verifier_mods and "ortools.sat.python" not in verifier_mods


# ---- İnceleme düzeltmesi 1: kilit güvenlik muafiyeti sağlamaz ---------------------------------------------


def _ed_lock(staff, until=T + 30 * M, start=T - 30 * M):
    return MoveLock(staff, ED, start, until, "onay-acil", T - 30 * M)


@pytest.mark.parametrize("staff,floats,sk,until,why", [
    (12, (FLOAT_AB,), None, T + 60 * M, "yetki_yok"),  # acil destek yetkisi yok
    (12, (FLOAT_AB, FLOAT_ED), None, T + 60 * M, "yetkinlik_eksik"),  # yetki var, TRIAGE yok
    (13, (FLOAT_AB, FLOAT_ED), "expiring", T + 60 * M, "yetkinlik_eksik"),  # TRIAGE pencere içinde bitiyor
    (13, (FLOAT_AB, FLOAT_ED), None, (T - 10 * M, T + 20 * M), "asgari_kalis_saglanmiyor"),  # etkin, 30 dk < kural 60 dk
    (13, (FLOAT_AB, FLOAT_ED), None, T0 + 13 * H, "vardiya_disina_tasiyor"),  # vardiya 20:00'de biter
])  # fmt: skip
def test_lock_does_not_bypass_safety(staff, floats, sk, until, why):
    skill_set = skills(triage_13_until=T + 30 * M) if sk == "expiring" else None
    start, until = until if isinstance(until, tuple) else (T - 30 * M, until)
    res = L.optimize(build({A: 10, ED: 100}, floats=floats, sk=skill_set, locks=[_ed_lock(staff, until=until, start=start)]))
    assert res["durum"] == L.REVIEW and not res["oneri_yayinlanabilir"] and res["tasimalar"] == []
    assert f"kilit_guvenlik_kosulu_saglanmiyor:{staff}:{why}" in res["nedenler"], res["nedenler"]


def test_valid_lock_still_held_and_verifier_applies_same_checks():
    inp = build({A: 10, ED: 100}, floats=(FLOAT_AB, FLOAT_ED), locks=[_ed_lock(13, until=T + 60 * M)])
    res = published(L.optimize(inp))
    assert res["kilitli_yerlesimler"][0]["servis"] == ED and moves(res) == []
    sol = res["cozum"]
    expired = replace(inp, skills=skills(triage_13_until=T + 30 * M))
    assert "H3_yetkinlik_eksik:13:3" in V.verify(expired, sol).violations
    no_rule = replace(inp, plan=replace(inp.plan, float_rules=(FLOAT_AB,)))
    assert "H6_tasima_yetkisi_yok:13:3" in V.verify(no_rule, sol).violations
    short = replace(inp, locks=(_ed_lock(13, start=T - 10 * M, until=T + 20 * M),))  # etkin ama 30 dk
    assert "H8_asgari_kalis_saglanmiyor:13:3" in V.verify(short, sol).violations
    beyond = replace(inp, locks=(_ed_lock(13, until=T0 + 13 * H),))
    assert "H5_kilit_vardiya_disina_tasiyor:13" in V.verify(beyond, sol).violations


# ---- İnceleme düzeltmesi 2: ham çalışma takvimi bağımsız denetlenir --------------------------------------


def test_open_u3_rows_without_raw_calendar_refused():
    res = L.optimize(replace(build({A: 30, B: 120, ED: 240}), calendars=()))
    assert res["durum"] == L.MODEL_INVALID and not res["oneri_yayinlanabilir"] and res["cozucu_asamalari"] == []
    assert "calisma_takvimi_dogrulanamadi:2:bilinmiyor" in res["nedenler"]
    assert "takvim_u3_uyusmuyor:2:acik!=bilinmiyor" in res["nedenler"]


def test_u3_closed_but_raw_calendar_open_refused():
    inp = build({A: 10}, at=T0 + 10 * H)  # yerel 18:00: U3 poliklinik kapalı, talep 0
    assert next(r for r in inp.u3_rows if r["servis_id"] == CL)["takvim_durumu"] == "kapali"
    open_cl = CALS[:3] + (always_open(CL, COVER, SRC, "test", KNOWN),)
    res = L.optimize(replace(inp, calendars=open_cl))
    assert res["durum"] == L.MODEL_INVALID and "takvim_u3_uyusmuyor:4:kapali!=acik" in res["nedenler"]


def test_closed_with_positive_demand_never_zeroed():
    inp = build({A: 10}, at=T0 + 10 * H)
    rows = [dict(r, tamponlu_talep_dk=12.0) if (r["servis_id"], r["rol"]) == (CL, "nurse") else r for r in inp.u3_rows]
    res = L.optimize(replace(inp, u3_rows=rows))
    assert res["durum"] == L.MODEL_INVALID and "kapali_serviste_talep:4:nurse" in res["nedenler"]


@pytest.mark.parametrize("cals,code,at", [
    (CALS[:3] + (replace(CALS[3], known_at=T + M),), "calisma_takvimi_dogrulanamadi:4:bilinmiyor", T),  # t'den sonra bilinen
    (CALS + (always_open(CL, COVER, SRC, "b", KNOWN),), "calisma_takvimi_dogrulanamadi:4:celiskili", T0 + 10 * H),  # 18:00: biri kapalı, biri açık
    (CALS[:3] + (replace(CALS[3], source=" "),), "calisma_takvimi_dogrulanamadi:4:bilinmiyor", T),  # kaynaksız
])  # fmt: skip
def test_future_conflicting_or_invalid_raw_calendar_refused(cals, code, at):
    res = L.optimize(replace(build({B: 60}, at=at), calendars=cals))
    assert res["durum"] == L.MODEL_INVALID and code in res["nedenler"], res["nedenler"]


def test_partial_raw_calendar_goes_to_review():
    res = L.optimize(build({A: 10}, at=T0 + 8 * H + 30 * M), freeze_policy=L.FREEZE_OFF)
    assert res["durum"] == L.REVIEW and "kismi_acik_pencere:4" in res["nedenler"]


def test_verifier_rechecks_raw_calendar(solved):
    inp, sol = solved
    violations = V.verify(replace(inp, calendars=()), sol).violations
    assert "on_kosul_takvim_dogrulanamadi:2:bilinmiyor" in violations and "on_kosul_takvim_u3_uyusmuyor:2" in violations


# ---- İnceleme düzeltmesi 3: yayın alanları bağımsız yeniden kurulur ---------------------------------------


def _explain(s, u, r="nurse"):
    return next(x for x in s["servis_rol_aciklama"] if (x["servis_id"], x["rol"]) == (u, r))


def _swap_person(s):
    m = s["tasimalar"][0]
    m["personel"] = 12 if m["personel"] != 12 else 11


@pytest.mark.parametrize("change,code", [
    (lambda s: s["tasimalar"][0].update(hedef_servis=ED), "yayin_tasimalar_tutarsiz"),
    (_swap_person, "yayin_tasimalar_tutarsiz"),
    (lambda s: s["tasimalar"].clear(), "yayin_tasimalar_tutarsiz"),
    (lambda s: s["tasimalar"][0].update(bitis=T + 2 * H, sure_dk=120), "yayin_tasimalar_tutarsiz"),
    (lambda s: s["tasimalar"][0].update(gerekli_yetkinlikler=[]), "yayin_tasimalar_tutarsiz"),
    (lambda s: s["tasimalar"][0].update(destek_kurali_kaynagi="sahte"), "yayin_tasimalar_tutarsiz"),
    (lambda s: s["kilitli_yerlesimler"].append({"personel": 21, "rol": "nurse", "servis": B, "onay_kimligi": "x",
                                                "kilit_bitis": T}), "yayin_kilitli_yerlesimler_tutarsiz"),  # fmt: skip
    (lambda s: _explain(s, B).update(kapasite_personel_dk_tam=999), "yayin_aciklama_tutarsiz:2:nurse"),
    (lambda s: _explain(s, B).update(onerilen_kisi=5), "yayin_aciklama_tutarsiz:2:nurse"),
    (lambda s: _explain(s, A)["atomlar"][0].update(kisi=9), "yayin_aciklama_tutarsiz:1:nurse"),
    (lambda s: _explain(s, A)["atomlar"].pop(), "yayin_aciklama_tutarsiz:1:nurse"),
    (lambda s: s["asama_degerleri"].update(O1_karsilanmayan_talep=10_000), "amac_degeri_tutarsiz:O1"),  # büyük
    (lambda s: s["asama_degerleri"].update(O3_hareket=0), "amac_degeri_tutarsiz:O3"),  # küçük
    (lambda s: s["asama_degerleri"].update(O4_fazla_kapasite=10_000), "amac_degeri_tutarsiz:O4"),
])  # fmt: skip
def test_rehashed_tampered_publication_rejected(solved, change, code):
    inp, sol = solved
    bad = _tamper(sol, change)  # özet yeniden hesaplanır: tek başına doğrulama değildir
    assert bad["ozet_sha256"] == L.solution_hash(bad)
    result = V.verify(inp, bad)
    assert not result.passed and any(x.startswith(code) for x in result.violations), result.violations


def test_declared_objectives_equal_final_solution(solved):
    inp, sol = solved
    values = sol["asama_degerleri"]
    assert (values["O1_karsilanmayan_talep"], values["O2_denge"], values["O3_hareket"]) == (60, 250, 1)
    assert V.verify(inp, sol).passed


def test_failed_verification_leaves_publication_empty(monkeypatch):
    monkeypatch.setattr(L.verifier, "verify", lambda inp, sol: V.VerificationResult(False, ["yayin_tasimalar_tutarsiz"]))
    res = L.optimize(build({A: 30, B: 120}))
    assert not res["oneri_yayinlanabilir"] and res["tasimalar"] == [] and res["kilitli_yerlesimler"] == []
    assert res["servis_rol"] == [] and res["cozum"] is None


# ---- U5 T9-e düzeltmesi: doğrulayıcı geçersiz aralıklarda istisna değil kodlu ret döndürür ---------------


def _shift(i, staff, start, end, unit=B, role="nurse", planned_at=PUB):
    return PlannedShift(i, staff, unit, role, 1, start, end, 0, planned_at, "planned", None, SRC)


@pytest.mark.parametrize("label,start,end", [
    ("sifir_sureli", T0 + 13 * H, T0 + 13 * H),  # T9-e
    ("baslangic_bitisten_sonra", T0 + 14 * H, T0 + 13 * H),
])  # fmt: skip
def test_verifier_rejects_invalid_shift_interval_without_exception(solved, label, start, end):
    _, sol = solved
    inp = build({A: 30, B: 120, ED: 50}, extra_shifts=[_shift(97, 21, start, end)])
    result = V.verify(inp, sol)  # istisna fırlatmamalı
    assert isinstance(result, V.VerificationResult) and result.passed is False
    assert "H5_gecersiz_vardiya_araligi:21:97" in result.violations
    assert "girdi_gecersiz_plan:ters_aralik:vardiya:97" in result.violations
    assert L.optimize(inp)["durum"] == L.MODEL_INVALID  # çözücü davranışı değişmedi


def test_one_bad_shift_among_valid_ones_rejects_whole_solution(solved):
    good_inp, sol = solved
    assert V.verify(good_inp, sol).passed
    bad = build({A: 30, B: 120, ED: 50}, extra_shifts=[_shift(97, 21, T0 + 13 * H, T0 + 13 * H)])
    result = V.verify(bad, sol)
    assert not result.passed and result.violations
    assert len(bad.plan.shifts) == len(good_inp.plan.shifts) + 1  # bozuk kayıt sessizce çıkarılmadı


def test_valid_plan_and_solution_still_pass(solved):
    inp, sol = solved
    result = V.verify(inp, sol)
    assert result.passed and result.violations == []


def test_wrong_role_and_overlap_still_rejected(solved):
    _, sol = solved
    wrong_role = build({A: 30, B: 120, ED: 50}, extra_shifts=[_shift(98, 21, T0 + 12 * H, T0 + 14 * H, role="supervisor")])
    overlap = build({A: 30, B: 120, ED: 50}, extra_shifts=[_shift(99, 11, T0, T0 + 4 * H)])
    for inp, code in ((wrong_role, "girdi_gecersiz_plan:rol_uyusmuyor:vardiya:98"),
                      (overlap, "girdi_gecersiz_plan:cakisan_vardiya:personel:11")):  # fmt: skip
        result = V.verify(inp, sol)
        assert not result.passed and any(x.startswith(code) for x in result.violations), result.violations
        assert L.optimize(inp)["durum"] == L.MODEL_INVALID


def test_rehashed_solution_with_invalid_input_rejected(solved):
    _, sol = solved
    bad_inp = build({A: 30, B: 120, ED: 50}, extra_shifts=[_shift(97, 21, T0 + 13 * H, T0 + 13 * H)])
    copy_sol = _tamper(sol, lambda s: None)  # özet doğru
    assert copy_sol["ozet_sha256"] == L.solution_hash(copy_sol)
    result = V.verify(bad_inp, copy_sol)
    assert not result.passed and "ozet_uyusmuyor" not in result.violations


def test_invalid_unavailability_and_skill_interval_rejected(solved):
    good_inp, sol = solved
    rev_unav = build({A: 30, B: 120, ED: 50}, unav=[PlannedUnavailability(9, 21, T + H, T, "izin", PUB, "planli")])
    result = V.verify(rev_unav, sol)
    assert not result.passed and "H4_gecersiz_uygunsuzluk_araligi:21:9" in result.violations
    rev_skill = replace(good_inp, skills=good_inp.skills + (StaffSkill(11, "MON", T + H, T),))
    result = V.verify(rev_skill, sol)  # önceden passed=True dönüyordu
    assert not result.passed and "H3_gecersiz_yetkinlik_araligi:11:MON" in result.violations


def test_invalid_shift_unknown_at_t_does_not_leak(solved):
    good_inp, sol = solved
    future = build({A: 30, B: 120, ED: 50}, extra_shifts=[_shift(97, 21, T0 + 13 * H, T0 + 13 * H, planned_at=T + 5 * M)])
    assert V.verify(future, sol).passed  # t'de yayımlanmamış kayıt kararı etkilemez (U2 bilinme kuralı)


def test_invalid_cancelled_shift_still_rejected(solved):
    _, sol = solved
    cancelled = replace(_shift(97, 21, T0 + 14 * H, T0 + 13 * H), status="cancelled", cancelled_at=T - H)
    result = V.verify(build({A: 30, B: 120, ED: 50}, extra_shifts=[cancelled]), sol)
    assert not result.passed and "H5_gecersiz_vardiya_araligi:21:97" in result.violations


# ---- U5 son düzeltme: geçersiz yetkinlik aralığı çözücüde istisna değil MODEL_INVALID ----------------------


@pytest.mark.parametrize("label,start,end", [
    ("ters", T + H, T),  # valid_until < valid_from
    ("sifir", T, T),  # valid_until == valid_from
])  # fmt: skip
def test_invalid_skill_interval_is_model_invalid_not_exception(label, start, end):
    inp = build({A: 30, B: 120, ED: 50}, sk=skills() + (StaffSkill(11, "MON", start, end),))
    res = L.optimize(inp)  # istisna fırlatmamalı
    assert res["durum"] == L.MODEL_INVALID and not res["oneri_yayinlanabilir"] and res["tasimalar"] == []
    assert "gecersiz_yetkinlik_araligi:11:MON" in res["nedenler"] and res["cozucu_asamalari"] == []


def test_skill_ok_ignores_invalid_interval_and_never_accepts_it():
    window = Interval(T, T + H)
    only_bad = build({}, sk=(StaffSkill(11, "TRIAGE", T + H, T),))
    assert L._skill_ok(only_bad, 11, "TRIAGE", window) is False  # istisna yok, geçerli sayılmaz
    zero = build({}, sk=(StaffSkill(11, "TRIAGE", T, T),))
    assert L._skill_ok(zero, 11, "TRIAGE", window) is False
    mixed = build({}, sk=(StaffSkill(11, "TRIAGE", T + H, T), StaffSkill(11, "TRIAGE", KNOWN, None)))
    assert L._skill_ok(mixed, 11, "TRIAGE", window) is True  # yalnız geçerli kayıt sayılır


def test_valid_skill_records_still_allow_authorized_move():
    ed_only = FloatRule("nurse", None, (ED,), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil")
    bounded = skills(triage_13_until=T + 2 * H)  # sınırlı ama pencereyi kapsıyor
    res = published(L.optimize(build({A: 10, ED: 240}, floats=(ed_only,), sk=bounded)))
    assert moves(res) == [(13, A, ED)]
    assert published(L.optimize(build({A: 10, ED: 240}, floats=(ed_only,))))["tasimalar"][0]["personel"] == 13  # süresiz


def test_skill_ending_mid_window_blocks_move():
    ed_only = FloatRule("nurse", None, (ED,), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil")
    res = published(L.optimize(build({A: 10, ED: 240}, floats=(ed_only,), sk=skills(triage_13_until=T + 30 * M))))
    assert moves(res) == []
    assert L._skill_ok(build({}, sk=skills(triage_13_until=T + 30 * M)), 13, "TRIAGE", Interval(T, T + H)) is False


def test_skill_valid_only_after_decision_time_not_used():
    ed_only = FloatRule("nurse", None, (ED,), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil")
    base = tuple(k for k in skills() if not (k.staff_id == 13 and k.skill == "TRIAGE"))
    later = base + (StaffSkill(13, "TRIAGE", T + M, None),)  # t'den sonra başlıyor
    res = published(L.optimize(build({A: 10, ED: 240}, floats=(ed_only,), sk=later)))
    assert moves(res) == []
    exact = base + (StaffSkill(13, "TRIAGE", T, None),)  # tam t'de başlıyor: pencerenin tamamında geçerli
    assert moves(published(L.optimize(build({A: 10, ED: 240}, floats=(ed_only,), sk=exact)))) == [(13, A, ED)]


def test_solver_and_verifier_agree_on_invalid_skill_interval(solved):
    inp, sol = solved
    bad = replace(inp, skills=inp.skills + (StaffSkill(11, "MON", T + H, T),))
    assert L.optimize(bad)["durum"] == L.MODEL_INVALID
    assert "H3_gecersiz_yetkinlik_araligi:11:MON" in V.verify(bad, sol).violations


# ---- K2: U4 ön koşulu ve doğrulayıcı kapalı serviste ŷ = 0 ve D = 0 ister --------------------------------


def test_k2_closed_row_with_positive_point_refused_by_gate_and_verifier(solved):
    late = T0 + 10 * H  # yerel 18:00 poliklinik kapalı
    inp = build({A: 10}, at=late)
    rows = [dict(r, nokta_tahmin_dk=0.3) if (r["servis_id"], r["rol"]) == (CL, "nurse") else r for r in inp.u3_rows]
    res = L.optimize(replace(inp, u3_rows=rows))
    assert res["durum"] == L.MODEL_INVALID and "kapali_serviste_talep:4:nurse" in res["nedenler"]
    ok = published(L.optimize(inp))
    tampered = replace(inp, u3_rows=rows)
    assert "on_kosul_kapali_serviste_talep:4:nurse" in V.verify(tampered, ok["cozum"]).violations


def test_k2_closed_zero_rows_pass_gate():
    res = published(L.optimize(build({A: 10}, at=T0 + 10 * H)))
    clinic = [r for r in build({A: 10}, at=T0 + 10 * H).u3_rows if r["servis_id"] == CL]
    assert all(r["durum"] == X.KAPALI and r["tamponlu_talep_dk"] == 0.0 for r in clinic) and res["durum"] == L.OPTIMAL


# ---- K3: yapısal asgari personel eksiği vardiya planı sorunu olarak raporlanır ----------------------------


def test_k3_structural_shortfall_reported_as_plan_problem():
    inp = build({A: 10, B: 10}, plan_rules=tuple(
        replace(r, min_staff=2) if (r.unit_id, r.role) == (ED, "supervisor") else r for r in rules()))
    res = L.optimize(inp)
    assert res["durum"] == L.INFEASIBLE and res["cozumsuzluk_turu"] == "yapisal_plan_eksigi"
    assert res["cozucu_asamalari"] == [] and not res["oneri_yayinlanabilir"] and res["tasimalar"] == []
    assert res["tani"] and all(t.startswith("plan_asgari_karsilanamaz:3:supervisor:") and t.endswith("1<2") for t in res["tani"])
    assert "vardiya_plani_geri_bildirimi" in res["nedenler"]
    assert "yetkili_klinik_karar_gerekli:asgari_personel:3:supervisor" in res["nedenler"]
    assert next(r for r in inp.plan.staffing_rules if (r.unit_id, r.role) == (ED, "supervisor")).min_staff == 2  # kural aynen


def test_k3_combinational_infeasibility_kept_separate():
    res = L.optimize(build({B: 40}, min_nurse={B: 4}))
    assert res["durum"] == L.INFEASIBLE and res["cozumsuzluk_turu"] == "kisit_birlesimi"
    assert any(t.startswith("H7_karsilanamaz:2:nurse:") for t in res["tani"])


# ---- K1: iki yönlü servis-rol dondurma (yalnız açık deneysel mod; varsayılan küresel ret) --------------------

TWO = L.FREEZE_TWO_WAY


def exp(inp, **kw):
    """İki yönlü dondurma yalnız açık deneysel modla (klinik onay yok; yayın kapalı)."""
    return L.optimize(inp, freeze_policy=TWO, experimental_mode="test", **kw)


EXP_FIELDS = ("cozum", "tasimalar", "kilitli_yerlesimler", "servis_rol")


def verified_exp(res):
    """Deneysel sonuç: doğrulandı, yayın kapalı, normal çıktı alanları BOŞ; içerik yalnız `deneysel_sonuc` içinde.
    İnceleme kolaylığı için deneysel alanlarla birleştirilmiş bir görünüm döndürür (asıl sonuç değişmez)."""
    assert res["dogrulama"]["gecti"] and res["insan_onayi_gerekli"] is True
    assert not res["oneri_yayinlanabilir"] and res["deneysel_mod"] == "test"
    assert res["cozum"] is None and res["tasimalar"] == [] and res["kilitli_yerlesimler"] == [] and res["servis_rol"] == []
    out = res["deneysel_sonuc"]
    assert out["dogrulandi"] is True and out["yayinlanabilir"] is False and out["cozum"] is not None
    assert "deneysel_mod:test:klinik_onay_yok:yayin_kapali" in res["uyarilar"]
    return {**res, **{k: out[k] for k in EXP_FIELDS}}


def _frozen(res):
    return {(f["servis_id"], f["rol"]) for f in res["dondurulan_servis_roller"]}


def test_k1_uncertain_pair_frozen_others_solved():
    inp = build({A: 30, B: 120, ED: 400}, review=[(ED, "nurse")])
    assert L.optimize(inp, freeze_policy=L.FREEZE_OFF)["durum"] == L.REVIEW  # küresel ret politikası
    res = verified_exp(exp(inp))
    assert res["durum"] == L.OPTIMAL and res["oneri_kapsami"] == "kismi" and _frozen(res) == {(ED, "nurse")}
    assert [m[1:] for m in moves(res)] == [(A, B)] and all(ED not in m[1:] for m in moves(res))
    assert row(res, ED)["donduruldu"] is True and row(res, B)["donduruldu"] is False
    stages = {s["asama"]: s["amac_degeri"] for s in res["cozucu_asamalari"]}
    assert stages["O1_karsilanmayan_talep"] == 60  # Acil'in belirsiz açığı (800−120) amaca girmedi
    assert "kismi_oneri:dondurulan_servis_rol=1" in res["uyarilar"] and res["insan_onayi_gerekli"] is True


def test_k1_uncertain_unit_loses_no_staff_in_any_role():
    floats = (FloatRule("nurse", None, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil-B"),)
    base = build({B: 120}, min_nurse={A: 3, ED: 1}, floats=floats)
    assert [m[1:] for m in moves(verified_exp(exp(base)))] == [(ED, B)]  # kontrol: Acil verebilir
    res = verified_exp(exp(build({B: 120}, min_nurse={A: 3, ED: 1}, floats=floats, review=[(ED, "support")])))  # fmt: skip
    assert _frozen(res) == {(ED, "support")} and moves(res) == []  # Acil'de destek belirsiz → Acil hiçbir rolde vermez


def test_k1_no_inflow_into_frozen_pair():
    res = verified_exp(exp(build({A: 30, B: 180}, review=[(B, "nurse")])))
    assert _frozen(res) == {(B, "nurse")} and moves(res) == []


def test_k1_known_violation_not_frozen_global_refusal():
    res = exp(build({A: 30, B: 40}, min_nurse={B: 3}, review=[(B, "nurse")]))
    assert res["durum"] == L.REVIEW and "dondurma_guvenli_degil:bilinen_asgari_ihlal:2:nurse" in res["nedenler"]


def test_k1_non_freezable_reason_keeps_global_refusal():
    inp = build({A: 30, B: 120})
    rows = [dict(r, durum="inceleme_gerekli", durumlar=["inceleme_gerekli"], nedenler=["bilinmeyen_kod"])
            if (r["servis_id"], r["rol"]) == (ED, "nurse") else r for r in inp.u3_rows]  # fmt: skip
    res = exp(replace(inp, u3_rows=rows))
    assert res["durum"] == L.REVIEW and "u3_inceleme_gerekli:3:nurse" in res["nedenler"]


def test_k1_partial_window_unit_frozen_others_solved():
    to_cl = FloatRule("nurse", None, (A, CL), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":poliklinik")
    at = T0 + 8 * H + 30 * M  # 16:30-17:30, poliklinik kısmi açık
    assert L.optimize(build({A: 10}, at=at, floats=(to_cl,)), freeze_policy=L.FREEZE_OFF)["durum"] == L.REVIEW
    res = verified_exp(exp(build({A: 10, B: 150}, at=at, floats=(to_cl, FLOAT_AB))))
    assert _frozen(res) == {(CL, r) for r in ("nurse", "supervisor", "support")}
    assert all(m[2] != CL and m[1] != CL for m in moves(res)) and [m[1:] for m in moves(res)] == [(A, B)]


def test_k1_everything_frozen_is_review():
    every = [(u, r) for u in (A, B, ED, CL) for r in ("nurse", "supervisor", "support")]  # 10:00: poliklinik de açık
    res = exp(build({A: 30, B: 120}, review=every))
    assert res["durum"] == L.REVIEW and res["nedenler"] == ["tum_servis_roller_donduruldu"]


def test_k1_lock_into_frozen_pair_kept():
    lock = MoveLock(11, B, T - 30 * M, T + 90 * M, "onay-1", T - 30 * M)
    res = verified_exp(exp(build({A: 10, B: 60}, review=[(B, "nurse")], locks=[lock])))
    assert row(res, B)["onerilen_kisi"] == 3 and res["kilitli_yerlesimler"][0]["personel"] == 11


def test_k1_invalid_policy_is_model_invalid():
    res = L.optimize(build({A: 30}), freeze_policy="tek_yonlu")
    assert res["durum"] == L.MODEL_INVALID and "dondurma_politikasi_gecersiz:tek_yonlu" in res["nedenler"]


@pytest.fixture(scope="module")
def frozen_solutions():
    a = build({A: 30, B: 180}, review=[(B, "nurse")])
    floats = (FloatRule("nurse", None, (B, ED), ("MON",), 60, KNOWN, None, KNOWN, SRC + ":acil-B"),)
    b = build({B: 120}, min_nurse={A: 3, ED: 1}, floats=floats, review=[(ED, "support")])
    return (a, exp(a)["deneysel_sonuc"]["cozum"]), (b, exp(b)["deneysel_sonuc"]["cozum"])


def test_k1_verifier_accepts_frozen_solutions(frozen_solutions):
    for inp, sol in frozen_solutions:
        assert V.verify(inp, sol).passed


@pytest.mark.parametrize("which,change,code", [
    (0, lambda s: next(a for a in s["atamalar"] if a["personel"] == 13).update(servis=B), "dondurma_ihlali_giris:13:2"),
    (1, lambda s: next(a for a in s["atamalar"] if a["personel"] == 31).update(servis=B), "dondurma_ihlali_cikis:31"),
    (0, lambda s: s.update(dondurulan_servis_roller=[]), "dondurma_kumesi_tutarsiz"),
    (0, lambda s: s.update(dondurma_politikasi="kapali"), "on_kosul_u3_durumu:2:nurse"),
    (0, lambda s: s.update(oneri_kapsami="tam"), "oneri_kapsami_tutarsiz"),
    (0, lambda s: s.pop("deneysel_mod"), "dondurma_deneysel_mod_eksik"),
    (1, lambda s: s.update(deneysel_mod="uretim"), "deneysel_mod_gecersiz:uretim"),
    (0, lambda s: s["asama_degerleri"].update(O1_karsilanmayan_talep=s["asama_degerleri"]["O1_karsilanmayan_talep"] + 240),
     "amac_degeri_tutarsiz:O1"),
])  # fmt: skip
def test_k1_verifier_rejects_freeze_tampering(frozen_solutions, which, change, code):
    inp, sol = frozen_solutions[which]
    bad = _tamper(sol, change)
    result = V.verify(inp, bad)
    assert not result.passed and any(x.startswith(code) for x in result.violations), result.violations


def test_k1_default_policy_is_global_refusal():
    inp = build({A: 30, B: 120, ED: 400}, review=[(ED, "nurse")])
    assert L.DEFAULT_FREEZE_POLICY == L.FREEZE_OFF
    res = L.optimize(inp)
    assert res["durum"] == L.REVIEW and res["dondurma_politikasi"] == L.FREEZE_OFF and res["deneysel_mod"] is None
    assert "u3_inceleme_gerekli:3:nurse" in res["nedenler"] and not res["oneri_yayinlanabilir"] and res["tasimalar"] == []


def test_k1_two_way_requires_explicit_experimental_mode():
    inp = build({A: 30, B: 120, ED: 400}, review=[(ED, "nurse")])
    res = L.optimize(inp, freeze_policy=TWO)
    assert res["durum"] == L.MODEL_INVALID and res["nedenler"] == ["deneysel_dondurma_acik_mod_gerekli:klinik_onay_yok"]
    assert res["cozucu_asamalari"] == [] and res["cozum"] is None
    bad = L.optimize(inp, freeze_policy=TWO, experimental_mode="uretim")
    assert bad["durum"] == L.MODEL_INVALID and bad["nedenler"] == ["deneysel_mod_gecersiz:uretim"]


def test_k1_experimental_mode_never_publishable_even_if_verified():
    res = verified_exp(exp(build({A: 30, B: 120, ED: 400}, review=[(ED, "nurse")])))
    assert res["oneri_kapsami"] == "kismi" and res["cozum"]["deneysel_mod"] == "test"
    full = verified_exp(exp(build({A: 30, B: 120})))  # dondurulacak çift yok: yine de deneysel → yayın yok
    assert full["oneri_kapsami"] == "tam" and full["dondurulan_servis_roller"] == []


def test_default_solution_has_no_experimental_field():
    res = published(L.optimize(build({A: 30, B: 120})))
    assert "deneysel_mod" not in res["cozum"] and res["deneysel_mod"] is None


@pytest.fixture(scope="module")
def frozen_lock_solution():
    lock = MoveLock(11, B, T - 30 * M, T + 90 * M, "onay-1", T - 30 * M)
    inp = build({A: 10, B: 60}, review=[(B, "nurse")], locks=[lock])
    return inp, verified_exp(exp(inp))["cozum"]


@pytest.mark.parametrize("which,change,code", [
    (0, lambda s: s["atamalar"].pop(0), "H1_atamasiz_personel"),
    (0, lambda s: next(a for a in s["atamalar"] if a["personel"] == 21).update(rol="supervisor"), "H2_rol_degisti:21"),
    (0, lambda s: next(a for a in s["atamalar"] if a["personel"] == 13).update(servis=ED), "H6_tasima_yetkisi_yok:13:3"),
    (0, lambda s: next(r for r in s["servis_rol"] if (r["servis_id"], r["rol"]) == (B, "nurse")).update(kapasite_tam=240),
     "H4_kapasite_tutarsiz:2:nurse"),
    (0, lambda s: next(r for r in s["servis_rol"] if (r["servis_id"], r["rol"]) == (B, "nurse")).update(acik_tam=0),
     "H10_acik_tutarsiz:2:nurse"),
    (1, lambda s: next(a for a in s["atamalar"] if a["personel"] == 11).update(servis=B), "H9_ev_servisi_asgari_alti:1"),
])  # fmt: skip
def test_k1_partial_solution_keeps_h1_h10(frozen_solutions, which, change, code):
    """Kısmi (deneysel) çözümde H1–H10 bütün çiftlerde aynen denetlenir; dondurma hiçbir kuralı gevşetmez."""
    inp, sol = frozen_solutions[which]
    result = V.verify(inp, _tamper(sol, change))
    assert not result.passed and any(x.startswith(code) for x in result.violations), result.violations


def test_k1_partial_solution_keeps_lock(frozen_lock_solution):
    inp, sol = frozen_lock_solution  # onaylı kilit dondurulan çifte (B hemşire) yerleşimi korur
    assert V.verify(inp, sol).passed
    moved = V.verify(inp, _tamper(sol, lambda s: next(a for a in s["atamalar"] if a["personel"] == 11).update(servis=A)))
    assert not moved.passed and any(x.startswith("H8_kilit_ihlali:11") for x in moved.violations), moved.violations


# ---- Kısmi açık servis / H7: asgari açık atomlarda U3 etiketinden bağımsız yeniden hesaplanır --------------

PARTIAL_AT = T0 + 8 * H + 30 * M  # 16:30-17:30; poliklinik 17:00'ye kadar açık


def _strip_violation_label(inp, unit, role="nurse"):
    rows = [dict(r, durum="uygun", durumlar=["uygun"], nedenler=[]) if (r["servis_id"], r["rol"]) == (unit, role) else r
            for r in inp.u3_rows]  # fmt: skip
    return replace(inp, u3_rows=rows)


def test_partial_unit_h7_recomputed_when_u3_label_removed():
    inp = build({A: 10, B: 150}, at=PARTIAL_AT, min_nurse={CL: 2})  # poliklinikte 1 hemşire, açık atomlarda asgari 2
    assert any("asgari_personel_ihlali" in r["durumlar"] for r in inp.u3_rows if r["servis_id"] == CL)
    forged = _strip_violation_label(inp, CL)  # karşı örnek: U3 ihlal etiketi kaldırıldı
    res = exp(forged)
    assert res["durum"] == L.INFEASIBLE and res["cozumsuzluk_turu"] == "yapisal_plan_eksigi"
    assert {c.split(":")[0:3] == ["plan_asgari_karsilanamaz", "4", "nurse"] for c in res["tani"]} == {True}
    assert len(res["tani"]) == 2  # yalnız açık bölümdeki iki atom (16:30, 16:45); kapalı bölüm sayılmaz
    assert res["deneysel_sonuc"] is None and res["cozum"] is None and not res["oneri_yayinlanabilir"]


def test_partial_unit_h7_verifier_rejects_solution_from_relabelled_input():
    honest = build({A: 10, B: 150}, at=PARTIAL_AT)  # asgari 1: geçerli deneysel çözüm
    sol = verified_exp(exp(honest))["cozum"]
    assert V.verify(honest, sol).passed
    forged = _strip_violation_label(build({A: 10, B: 150}, at=PARTIAL_AT, min_nurse={CL: 2}), CL)
    result = V.verify(forged, sol)  # aynı çözüm, asgari 2 ama ihlal etiketi silinmiş girdi
    assert not result.passed
    assert sum(v.startswith("H7_asgari_alti:4:nurse:") for v in result.violations) == 2, result.violations


def test_experimental_output_fields_empty_even_with_lock_and_moves():
    lock = MoveLock(11, B, T - 30 * M, T + 90 * M, "onay-1", T - 30 * M)
    res = exp(build({A: 10, B: 60}, review=[(ED, "nurse")], locks=[lock]))
    verified_exp(res)  # üst düzey alanlar boş (yardımcı denetler)
    assert res["deneysel_sonuc"]["kilitli_yerlesimler"][0]["personel"] == 11  # kilit yalnız deneysel_sonuc içinde
    moved = exp(build({A: 30, B: 120, ED: 400}, review=[(ED, "nurse")]))
    verified_exp(moved)
    assert moved["tasimalar"] == [] and [m["hedef_servis"] for m in moved["deneysel_sonuc"]["tasimalar"]] == [B]
