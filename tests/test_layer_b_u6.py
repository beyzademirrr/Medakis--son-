"""Aşama 5 / U6: Katman B görev ataması uygulanabilirlik denetimi (test_sentetik fikstürler; veritabanı yok).

Şartname Bölüm 6 ve Aşama 5 kabul ölçütleri: sıfır personelde atama yok, açık var; eksik yetkinlikte ihlal yok;
mola ve gece yarısı doğru; süre sınırı durumu açık. Bağımsız doğrulayıcı kurcalanmış çözümleri reddeder.
"""

import ast
import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from medakis.domain.models import Staff
from medakis.domain.planning import PlannedShift, PlannedUnavailability, PlanSet
from medakis.optimization import layer_b as B
from medakis.optimization import layer_b_verifier as BV
from medakis.optimization.layer_a_contracts import StaffSkill
from medakis.optimization.layer_b_contracts import (
    GAP_CAPACITY,
    GAP_DEADLINE_PASSED,
    GAP_DEADLINE_TOO_CLOSE,
    GAP_NO_ELIGIBLE_STAFF,
    GAP_NO_SLOT,
    LayerBConfig,
    LayerBInputs,
    TaskSpec,
    task_minutes,
    tasks_known_at,
)

M, H = timedelta(minutes=1), timedelta(hours=1)
T0 = datetime(2026, 10, 26, 5, 0, tzinfo=UTC)  # 08:00 yerel
T = T0 + 2 * H  # karar anı
KNOWN = T0 - timedelta(days=10)
A, BB = 1, 2
SRC = "test_sentetik"
STAFF = {11: Staff(11, "N11", "nurse", True), 12: Staff(12, "N12", "nurse", True), 21: Staff(21, "N21", "nurse", True),
         16: Staff(16, "D16", "support", True), 13: Staff(13, "N13", "nurse", False)}  # fmt: skip
HOME = {11: A, 12: A, 21: BB, 16: A, 13: A}
SKILLS = (StaffSkill(11, "MON", KNOWN, None), StaffSkill(21, "MON", KNOWN, None), StaffSkill(13, "MON", KNOWN, None))
CATALOG = frozenset({"MON", "MED"})
CONFIG = LayerBConfig(10.0, SRC, "test", KNOWN)


def shifts(staff=None, start=T0, hours=12):
    return tuple(PlannedShift(i, sid, HOME[sid], STAFF[sid].role, 1, start, start + hours * H, 30, KNOWN, "planned", None, SRC)
                 for i, sid in enumerate(sorted(staff if staff is not None else HOME), start=1))  # fmt: skip


def task(tid, minutes=10.0, due=T + 60 * M, unit=A, role="nurse", skills=(), kind="VITAL", created=T - 30 * M):
    return TaskSpec(tid, unit, role, kind, minutes, created, due, tuple(skills))


def build(tasks, staff=None, unav=(), sk=SKILLS, at=T, forecasts=None, uncertain=(), config=CONFIG, start=T0, hours=12, people=STAFF):
    plan = PlanSet(shifts(staff, start, hours), tuple(unav))
    return LayerBInputs(at, plan, people, (A, BB), tuple(tasks), sk, CATALOG, config, forecasts or {}, tuple(uncertain))


def shown(res):
    assert res["denetim_gosterilebilir"] and res["dogrulama"]["gecti"], res["dogrulama"]
    assert res["gercek_atama_yok"] is True and res["kayit_yok"] is True and res["insan_incelemesi_gerekli"] is True
    return res["cozum"]


def by_task(sol):
    return {a["gorev"]: a for a in sol["atamalar"]}


def gaps(sol):
    return {g["gorev"]: g["neden"] for g in sol["atanmayanlar"]}


# ---- Temel kurallar ---------------------------------------------------------------------------------------


def test_basic_assignment_verified_no_overlap():
    sol = shown(B.check_tasks(build([task(1), task(2), task(3)])))
    assert set(by_task(sol)) == {1, 2, 3} and sol["atanmayanlar"] == []
    assert sol["amac_degerleri"] == {"O1_atanmayan_gorev_dk": 0, "O2_yuk_farki_dk": 10}  # 3 görev, 2 hemşire
    for a in sol["atamalar"]:
        assert a["servis"] == A and a["rol"] == "nurse" and a["personel"] in (11, 12) and a["bitis"] - a["baslangic"] == 10 * M
        assert T <= a["baslangic"] and a["bitis"] <= T + 60 * M


def test_zero_staff_no_assignment_but_gap():
    res = B.check_tasks(build([task(1), task(2, 20.0)], staff=()))
    sol = shown(res)
    assert sol["atamalar"] == [] and gaps(sol) == {1: GAP_NO_ELIGIBLE_STAFF, 2: GAP_NO_ELIGIBLE_STAFF}
    assert sol["amac_degerleri"]["O1_atanmayan_gorev_dk"] == 30


def test_missing_skill_never_assigned_and_verifier_rejects_forgery():
    inp = build([task(1, skills=("MON",))], staff=(12,))  # 12'de MON yok
    sol = shown(B.check_tasks(inp))
    assert sol["atamalar"] == [] and gaps(sol) == {1: GAP_NO_ELIGIBLE_STAFF}
    ok = shown(B.check_tasks(build([task(1, skills=("MON",))], staff=(11, 12))))
    assert by_task(ok)[1]["personel"] == 11
    forged = _tamper(ok, lambda s: s["atamalar"][0].update(personel=12))
    assert "B2_yetkinlik_gecersiz:1:12:MON" in BV.verify_tasks(build([task(1, skills=("MON",))], staff=(11, 12)), forged).violations


def test_skill_must_hold_for_whole_task_duration():
    expiring = (StaffSkill(11, "MON", KNOWN, T + 15 * M),)
    sol = shown(B.check_tasks(build([task(1, 20.0, skills=("MON",))], staff=(11,), sk=expiring)))
    assert gaps(sol) == {1: GAP_NO_SLOT}  # 15 dk geçerlilik 20 dk işe yetmez
    sol = shown(B.check_tasks(build([task(1, 10.0, skills=("MON",))], staff=(11,), sk=expiring)))
    assert by_task(sol)[1]["bitis"] <= T + 15 * M


def test_break_respected_and_overlap_forgery_rejected():
    brk = (PlannedUnavailability(1, 11, T + 10 * M, T + 40 * M, "mola", KNOWN, "planli"),)
    inp = build([task(1, 35.0)], staff=(11,), unav=brk)
    sol = shown(B.check_tasks(inp))
    assert gaps(sol) == {1: GAP_NO_SLOT}  # mola öncesi 10, sonrası 20 dk: 35 dk iş sığmaz, bölünmez
    sol2 = shown(B.check_tasks(build([task(1, 15.0)], staff=(11,), unav=brk)))
    a = by_task(sol2)[1]
    assert a["baslangic"] >= T + 40 * M  # yalnız molanın sonrası
    forged = _tamper(sol2, lambda s: s["atamalar"][0].update(baslangic=T + 5 * M, bitis=T + 20 * M))
    assert any(x.startswith("B3_uygunsuzlukla_cakisiyor:1:11") for x in BV.verify_tasks(build([task(1, 15.0)], staff=(11,), unav=brk),
                                                                                        forged).violations)  # fmt: skip


def test_midnight_window_correct():
    night = datetime(2026, 10, 26, 20, 30, tzinfo=UTC)  # 23:30 yerel; pencere yerel gece yarısını keser
    tasks = [task(1, 25.0, due=night + 60 * M, created=night - H), task(2, 25.0, due=night + 60 * M, created=night - H)]
    sol = shown(B.check_tasks(build(tasks, staff=(11,), at=night, start=datetime(2026, 10, 26, 17, 0, tzinfo=UTC), hours=12)))
    ivs = sorted((a["baslangic"], a["bitis"]) for a in sol["atamalar"])
    assert len(ivs) == 2 and ivs[0][1] <= ivs[1][0] and ivs[1][1] <= night + 60 * M  # gece yarısını geçen, çakışmayan


def test_shift_end_inside_window_limits_work():
    sol = shown(B.check_tasks(build([task(1, 40.0)], staff=(11,), start=T - 11 * H - 30 * M, hours=12)))  # vardiya T+30'da biter
    assert gaps(sol) == {1: GAP_NO_SLOT}


def test_deadline_rules():
    sol = shown(B.check_tasks(build([task(1, due=T - 5 * M, created=T - H), task(2, 10.0, due=T + 5 * M), task(3, 10.0, due=T + 30 * M)])))
    assert gaps(sol) == {1: GAP_DEADLINE_PASSED, 2: GAP_DEADLINE_TOO_CLOSE}
    assert by_task(sol)[3]["bitis"] <= T + 30 * M


def test_capacity_gap_and_lexicographic_o1_first():
    sol = shown(B.check_tasks(build([task(i, 20.0) for i in range(1, 5)], staff=(11,))))
    assert len(sol["atamalar"]) == 3 and list(gaps(sol).values()) == [GAP_CAPACITY]
    assert sol["amac_degerleri"]["O1_atanmayan_gorev_dk"] == 20


def test_load_balance_second_objective():
    sol = shown(B.check_tasks(build([task(1), task(2)], staff=(11, 12))))
    assert {a["personel"] for a in sol["atamalar"]} == {11, 12} and sol["amac_degerleri"]["O2_yuk_farki_dk"] == 0


def test_no_cross_unit_transfer_and_inactive_staff_unused():
    sol = shown(B.check_tasks(build([task(1, unit=A)], staff=(21, 13))))  # 21 B serviste, 13 aktif değil
    assert sol["atamalar"] == [] and gaps(sol) == {1: GAP_NO_ELIGIBLE_STAFF}


def test_role_must_match():
    sol = shown(B.check_tasks(build([task(1, role="support"), task(2)], staff=(16, 11))))
    assert by_task(sol)[1]["personel"] == 16 and by_task(sol)[2]["personel"] == 11


def test_later_and_uncertain_tasks_not_assigned_but_counted():
    tasks = [task(1), task(2, due=T + 3 * H), task(3)]
    sol = shown(B.check_tasks(build(tasks, uncertain=(3,))))
    assert set(by_task(sol)) == {1} and sol["atanmayanlar"] == []
    assert sol["kapsam"]["sonraki_pencere_gorev"] == 1 and sol["kapsam"]["iptal_zamani_belirsiz_gorev"] == 1


def test_reserve_reported_separately_not_assigned():
    sol = shown(B.check_tasks(build([task(1, 30.0)], forecasts={(A, "nurse"): 50.0})))
    row = next(r for r in sol["rezerv"] if (r["servis"], r["rol"]) == (A, "nurse"))
    assert row["somut_gorev_dk"] == 30 and row["rezerv_dk"] == 20.0 and row["not"] == "kimliksiz_rezerv_atanmaz"
    other = next(r for r in sol["rezerv"] if (r["servis"], r["rol"]) == (BB, "nurse"))
    assert other["tahmin_dk"] is None and other["rezerv_dk"] is None and other["not"] == "tahmin_yok"
    assert all(a["gorev"] == 1 for a in sol["atamalar"])  # rezerv için sahte görev yok


def test_duration_rounded_up():
    assert task_minutes(10.2) == 11 and task_minutes(10.0) == 10
    sol = shown(B.check_tasks(build([task(1, 10.2)], staff=(11,))))
    assert by_task(sol)[1]["sure_dk"] == 11 and sol["atamalar"][0]["bitis"] - sol["atamalar"][0]["baslangic"] == 11 * M


# ---- Durumlar ---------------------------------------------------------------------------------------------


def test_unknown_status_produces_no_plan(monkeypatch):
    real = B._run_solver

    def unknown(model, limit, overrides):
        r = real(model, limit, overrides)
        return B._Run("UNKNOWN", None, r.wall_s, r.solver)

    monkeypatch.setattr(B, "_run_solver", unknown)
    res = B.check_tasks(build([task(1)]))
    assert res["durum"] == B.UNKNOWN and res["cozum"] is None and not res["denetim_gosterilebilir"]
    assert [s["asama"] for s in res["cozucu_asamalari"]] == ["O1_atanmayan_gorev_dk"]  # çalışmayan O2 OPTIMAL görünmez
    assert res["cozucu_asamalari"][0]["durum"] == B.UNKNOWN and not res["cozucu_asamalari"][0]["optimal"]


def test_time_limit_override_reported():
    res = B.check_tasks(build([task(i, 5.0) for i in range(1, 9)]), solver_overrides={"max_time_in_seconds": 5.0})
    assert res["durum"] in (B.OPTIMAL, B.FEASIBLE) and all(s["sure_sn"] >= 0 for s in res["cozucu_asamalari"])


@pytest.mark.parametrize("change,code", [
    (lambda i: replace(i, config=None), "katman_b_yapilandirmasi_yok"),
    (lambda i: replace(i, config=LayerBConfig(10.0, " ", "test", KNOWN)), "katman_b_yapilandirmasi_kaynaksiz"),
    (lambda i: replace(i, config=LayerBConfig(float("nan"), SRC, "test", KNOWN)), "katman_b_sure_siniri_gecersiz"),
    (lambda i: replace(i, tasks=(task(1, float("nan")),)), "gecersiz_sure:gorev:1"),
    (lambda i: replace(i, tasks=(task(1, due=T - 2 * H, created=T - H),)), "ters_son_tarih:gorev:1"),
    (lambda i: replace(i, tasks=(task(1, skills=("XYZ",)),)), "bilinmeyen_yetkinlik:gorev:1:XYZ"),
    (lambda i: replace(i, tasks=(task(1), task(1))), "yinelenen_gorev:1"),
    (lambda i: replace(i, tasks=(task(1, unit=9),)), "bilinmeyen_servis:gorev:1"),
    (lambda i: replace(i, tasks=(task(1, created=T + M),)), "gelecekte_olusan_gorev:gorev:1"),
    (lambda i: replace(i, skills=(StaffSkill(11, "MON", T, T - H),)), "gecersiz_yetkinlik_araligi:11:MON"),
    (lambda i: replace(i, forecasts={(A, "nurse"): float("inf")}), "gecersiz_tahmin:1:nurse"),
])  # fmt: skip
def test_invalid_inputs_model_invalid(change, code):
    res = B.check_tasks(change(build([task(1)])))
    assert res["durum"] == B.MODEL_INVALID and code in res["nedenler"] and res["cozum"] is None and res["cozucu_asamalari"] == []


def test_invalid_plan_model_invalid():
    bad = shifts((11,)) + (PlannedShift(99, 11, A, "nurse", 1, T0 + H, T0 + 5 * H, 0, KNOWN, "planned", None, SRC),)  # çakışan vardiya
    inp = replace(build([task(1)], staff=(11,)), plan=PlanSet(bad, ()))
    res = B.check_tasks(inp)
    assert res["durum"] == B.MODEL_INVALID and any(n.startswith("gecersiz_plan:") for n in res["nedenler"])


def test_deterministic_hash():
    a = shown(B.check_tasks(build([task(i, 7.0) for i in range(1, 7)])))
    b = shown(B.check_tasks(build([task(i, 7.0) for i in range(1, 7)])))
    assert a["ozet_sha256"] == b["ozet_sha256"]


# ---- Bilinen görev görünümü (sızıntı yok) ----------------------------------------------------------------


def test_tasks_known_at_semantics():
    tables = {
        "task_types": [{"id": 1, "code": "VITAL", "required_role": "nurse"}, {"id": 2, "code": "MONI", "required_role": "nurse"}],
        "skills": [{"id": 1, "code": "MON"}], "task_skill_requirements": [{"task_type_id": 2, "skill_id": 1}],
        "care_tasks": [
            dict(id=1, unit_id=A, task_type_id=1, created_at=T - H, due_at=T + H, estimated_minutes=8.0, status="pending",
                 completed_at=None, cancelled_at=None),
            dict(id=2, unit_id=A, task_type_id=2, created_at=T - H, due_at=T + H, estimated_minutes=8.0, status="completed",
                 completed_at=T + 10 * M, cancelled_at=None),  # t'den sonra tamamlandı → t'de bekliyor
            dict(id=3, unit_id=A, task_type_id=1, created_at=T - H, due_at=T + H, estimated_minutes=8.0, status="completed",
                 completed_at=T - 10 * M, cancelled_at=None),  # t'den önce tamamlandı
            dict(id=4, unit_id=A, task_type_id=1, created_at=T + M, due_at=T + H, estimated_minutes=8.0, status="pending",
                 completed_at=None, cancelled_at=None),  # t'den sonra oluştu
            dict(id=5, unit_id=A, task_type_id=1, created_at=T - H, due_at=T + H, estimated_minutes=8.0, status="cancelled",
                 completed_at=None, cancelled_at=T + 5 * M),  # t'den sonra iptal → t'de bekliyor
            dict(id=6, unit_id=A, task_type_id=1, created_at=T - H, due_at=T + H, estimated_minutes=8.0, status="cancelled",
                 completed_at=None, cancelled_at=None),  # iptal zamanı bilinmiyor → belirsiz
            dict(id=7, unit_id=9, task_type_id=1, created_at=T - H, due_at=T + H, estimated_minutes=8.0, status="pending",
                 completed_at=None, cancelled_at=None),  # kapsam dışı servis
        ],
    }  # fmt: skip
    tasks, uncertain = tasks_known_at(tables, T, (A, BB))
    assert [t.task_id for t in tasks] == [1, 2, 5] and uncertain == (6,)
    assert next(t for t in tasks if t.task_id == 2).required_skills == ("MON",)


# ---- Bağımsız doğrulayıcı: kurcalama ----------------------------------------------------------------------


def _tamper(sol, fn, rehash=True):
    bad = copy.deepcopy(sol)
    fn(bad)
    if rehash:
        bad["ozet_sha256"] = B.solution_hash(bad)
    return bad


@pytest.fixture(scope="module")
def solved():
    inp = build([task(1), task(2), task(3, role="support"), task(4, 20.0, skills=("MON",)), task(5, due=T - M, created=T - H)],
                forecasts={(A, "nurse"): 60.0})  # fmt: skip
    return inp, shown(B.check_tasks(inp))


def _first(sol, tid):
    return next(a for a in sol["atamalar"] if a["gorev"] == tid)


@pytest.mark.parametrize("change,code", [
    (lambda s: s["atamalar"].append(copy.deepcopy(_first(s, 1))), "B1_gorev_birden_fazla_atandi:1"),
    (lambda s: _first(s, 1).update(personel=16), "B2_rol_uyusmuyor:1:16"),
    (lambda s: _first(s, 1).update(personel=13), "B2_personel_aktif_degil:1:13"),
    (lambda s: _first(s, 4).update(personel=12), "B2_yetkinlik_gecersiz:4:12:MON"),
    (lambda s: _first(s, 1).update(personel=21), "B3_vardiya_disi:1:21"),
    (lambda s: _first(s, 1).update(servis=BB), "B6_servis_uyusmuyor:1"),
    (lambda s: _first(s, 1).update(baslangic=T - 10 * M, bitis=T), "B5_pencere_disi:1"),
    (lambda s: _first(s, 1).update(bitis=_first(s, 1)["bitis"] - M), "B4_sure_tutarsiz:1"),
    (lambda s: s["atamalar"].append({**copy.deepcopy(_first(s, 1)), "gorev": 5, "son_tarih": T - M}), "B5_son_tarih_asildi:5"),
    (lambda s: s["atamalar"].remove(_first(s, 2)), "B7_atanmayan_kumesi_tutarsiz"),
    (lambda s: s["atanmayanlar"][0].update(neden=GAP_CAPACITY), "B7_neden_tutarsiz:5"),
    (lambda s: s["atanmayanlar"][0].update(acik_dk=1), "B7_acik_dk_tutarsiz:5"),
    (lambda s: s["amac_degerleri"].update(O1_atanmayan_gorev_dk=0), "B_amac_degeri_tutarsiz:O1"),
    (lambda s: s["amac_degerleri"].update(O2_yuk_farki_dk=99), "B_amac_degeri_tutarsiz:O2"),
    (lambda s: s["rezerv"][0].update(rezerv_dk=0.0), "B_rezerv_tutarsiz"),
    (lambda s: s["kapsam"].update(sonraki_pencere_gorev=7), "B_kapsam_tutarsiz"),
    (lambda s: s["atamalar"].append({**copy.deepcopy(_first(s, 1)), "gorev": 999}), "B_kapsam_disi_gorev:999"),
])  # fmt: skip
def test_verifier_rejects_tampering(solved, change, code):
    inp, sol = solved
    result = BV.verify_tasks(inp, _tamper(sol, change))
    assert not result.passed and any(v.startswith(code) for v in result.violations), result.violations


def test_verifier_rejects_same_staff_overlap(solved):
    inp, sol = solved
    a1, a2 = _first(sol, 1), _first(sol, 2)

    def overlap(s):
        x, y = _first(s, 1), _first(s, 2)
        y.update(personel=x["personel"], baslangic=x["baslangic"], bitis=x["bitis"])

    result = BV.verify_tasks(inp, _tamper(sol, overlap))
    assert any(v.startswith("B3_gorev_cakismasi:") for v in result.violations), (a1, a2, result.violations)


def test_verifier_rejects_unrehashed_and_accepts_original(solved):
    inp, sol = solved
    assert BV.verify_tasks(inp, sol).passed
    bad = _tamper(sol, lambda s: s["atamalar"].pop(), rehash=False)
    assert "ozet_uyusmuyor" in BV.verify_tasks(inp, bad).violations


def test_verifier_independent_of_solver():
    src = Path(BV.__file__).read_text(encoding="utf-8")
    mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m.startswith(("ortools", "medakis.optimization.layer_b")) and m != "medakis.optimization.layer_b_contracts" for m in mods)


def test_due_equal_to_created_is_valid_and_passed():
    res = B.check_tasks(build([task(1, due=T - 10 * M, created=T - 10 * M), task(2)]))  # anında yapılacak iş (created = due)
    sol = shown(res)
    assert gaps(sol) == {1: GAP_DEADLINE_PASSED} and set(by_task(sol)) == {2}


def test_independent_groups_solved_as_components():
    tasks = [task(1), task(2, unit=BB), task(3, role="support")]
    res = B.check_tasks(build(tasks))
    sol = shown(res)
    assert set(by_task(sol)) == {1, 2, 3} and all(s["bilesen"] == 3 for s in res["cozucu_asamalari"])  # A-hemşire, B-hemşire, A-destek


def test_staff_on_two_units_couples_components_and_never_overlaps():
    two = (PlannedShift(1, 11, A, "nurse", 1, T0, T + 30 * M, 0, KNOWN, "planned", None, SRC),
           PlannedShift(2, 11, BB, "nurse", 1, T + 30 * M, T0 + 12 * H, 0, KNOWN, "planned", None, SRC))  # fmt: skip
    inp = replace(build([task(1, 30.0, unit=A), task(2, 30.0, unit=BB)], staff=()), plan=PlanSet(two, ()))
    res = B.check_tasks(inp)
    sol = shown(res)
    assert all(s["bilesen"] == 1 for s in res["cozucu_asamalari"])  # ortak personel: tek bileşen
    a1, a2 = by_task(sol)[1], by_task(sol)[2]
    assert a1["bitis"] <= T + 30 * M <= a2["baslangic"] and a1["personel"] == a2["personel"] == 11


# ---- Gelecek bilgi sızıntısı (U7 öncesi tamamlama) -----------------------------------------------------------


def test_config_known_after_decision_is_model_invalid():
    res = B.check_tasks(replace(build([task(1)]), config=LayerBConfig(10.0, SRC, "test", T + M)))
    assert res["durum"] == B.MODEL_INVALID and "katman_b_yapilandirmasi_karar_aninda_bilinmiyor" in res["nedenler"]


def test_plan_view_older_than_decision_is_model_invalid_in_solver_and_verifier():
    inp = build([task(1)])
    sol = shown(B.check_tasks(inp))
    old = replace(inp, plan=replace(inp.plan, as_of=T - M))
    res = B.check_tasks(old)
    assert res["durum"] == B.MODEL_INVALID and "plan_gorunumu_karar_anindan_eski" in res["nedenler"]
    assert "girdi_gecersiz:plan_gorunumu_karar_anindan_eski" in BV.verify_tasks(old, sol).violations


def test_plan_viewed_later_gives_identical_result():
    from medakis.domain.planning import known_view

    inp = build([task(1), task(2, 25.0)])
    later = replace(inp, plan=known_view(inp.plan, T + 3 * H))  # as_of > t: yeniden görünüm t'ye göre kurulur
    assert shown(B.check_tasks(later))["ozet_sha256"] == shown(B.check_tasks(inp))["ozet_sha256"]


def test_information_known_after_decision_does_not_change_result():
    base = build([task(1, 20.0), task(2, 20.0)], staff=(11, 12))
    ref = shown(B.check_tasks(base))
    future_shift = PlannedShift(50, 21, A, "nurse", 1, T0, T0 + 12 * H, 0, T + 5 * M, "planned", None, SRC)  # t'den sonra planlandı
    late_unav = PlannedUnavailability(60, 11, T, T + 60 * M, "devamsizlik", T + 10 * M, "ani")  # t'den sonra bildirildi
    cancelled = replace(base.plan.shifts[1], status="cancelled", cancelled_at=T + 15 * M)  # iptal t'den sonra biliniyor
    plan = PlanSet((base.plan.shifts[0], cancelled, future_shift), (late_unav,))
    res = shown(B.check_tasks(replace(base, plan=plan)))
    assert res["ozet_sha256"] == ref["ozet_sha256"] and res["atamalar"] == ref["atamalar"]


def test_tasks_known_at_ignores_future_rows_and_closures():
    def row(i, **kw):
        base = dict(id=i, unit_id=A, task_type_id=1, created_at=T - H, due_at=T + H, estimated_minutes=8.0, status="pending",
                    completed_at=None, cancelled_at=None)  # fmt: skip
        return {**base, **kw}

    tables = {"task_types": [{"id": 1, "code": "VITAL", "required_role": "nurse"}], "skills": [], "task_skill_requirements": [],
              "care_tasks": [row(1), row(2)]}  # fmt: skip
    noisy = dict(tables, care_tasks=[row(1, status="completed", completed_at=T + M), row(2, status="cancelled", cancelled_at=T + 2 * M),
                                     row(3, created_at=T + 5 * M)])  # fmt: skip
    assert tasks_known_at(tables, T, (A,)) == tasks_known_at(noisy, T, (A,))
