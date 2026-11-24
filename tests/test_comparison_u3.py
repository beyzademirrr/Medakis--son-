"""Aşama 5 / U3: ihtiyaç–planlı kapasite karşılaştırma motoru (el ile kurulmuş girdiler; model yok).

Asgari personel, kullanım oranı ve çalışma takvimi değerleri AÇIKÇA 'test_sentetik' etiketlidir.
"""

import math
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from medakis.domain.models import Staff
from medakis.domain.planning import (
    PlannedShift,
    PlannedUnavailability,
    PlanningConfig,
    PlanSet,
    StaffingRule,
    known_view,
)
from medakis.domain.service_calendar import (
    CLOSED,
    CONFLICT,
    OPEN,
    PARTIAL,
    UNKNOWN,
    ServiceCalendar,
    always_open,
    calendar_state,
    weekly_calendar,
)
from medakis.domain.time import Interval
from medakis.optimization import comparison as X

TZ = ZoneInfo("Europe/Istanbul")
T0 = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)  # Salı, yerel 08:00
H, M = timedelta(hours=1), timedelta(minutes=1)
T = T0 + 2 * H  # karar anı: yerel 10:00
PUB = T0 - timedelta(days=14)
KNOWN = T0 - timedelta(days=30)
SRC = "test_sentetik"
STAFF = {1: Staff(1, "H1", "nurse", True), 2: Staff(2, "H2", "nurse", True), 3: Staff(3, "S1", "supervisor", True),
         4: Staff(4, "D1", "support", True), 5: Staff(5, "H3", "nurse", True)}  # fmt: skip
UNITS = {1: X.UnitInfo(1, "Yatakli", "inpatient"), 4: X.UnitInfo(4, "Poliklinik", "outpatient")}
CONFIG = PlanningConfig(0.5, 0.35, 1.0, 10.0, SRC)
COVER = Interval(T0 - timedelta(days=3), T0 + timedelta(days=5))


def rule(unit, role, n, band=(0, 24), **kw):
    base = dict(source=SRC, approved_by="test", valid_from=KNOWN, valid_until=None, known_at=KNOWN)
    base.update(kw)
    return StaffingRule(unit, role, band, n, **base)


RULES = tuple(rule(u, r, 1 if r == "nurse" else 0) for u in (1, 4) for r in ("nurse", "supervisor", "support"))


def sh(i, staff, unit=1, start=T0, hours=12, status="planned", cancelled_at=None, planned_at=PUB):
    return PlannedShift(i, staff, unit, STAFF[staff].role, 1, start, start + hours * H, 0, planned_at, status, cancelled_at, "t")


def un(i, staff, a, b, reason="izin", recorded=PUB, kind="planli"):
    return PlannedUnavailability(i, staff, a, b, reason, recorded, kind)


def plan(shifts, unav=(), rules=RULES, config=CONFIG):
    return PlanSet(tuple(shifts), tuple(unav), tuple(rules), (), config)


def dem(unit, role, point, buffer=0.0, at=T, status="uygun", reasons=(), horizon=60):
    return X.DemandEstimate(unit, role, at, horizon, point, buffer, point + buffer, status, tuple(reasons), (),
                            "random_forest", "random_forest@abc", "abc")  # fmt: skip


def demands(nurse1=100.0, nurse4=0.0, over=None):
    out = {(u, r): dem(u, r, 0.0) for u in (1, 4) for r in ("nurse", "supervisor", "support")}
    out[(1, "nurse")] = dem(1, "nurse", nurse1)
    out[(4, "nurse")] = dem(4, "nurse", nurse4)
    out.update(over or {})
    return [d for d in out.values() if d is not None]


CALS = (always_open(1, COVER, SRC, "test", KNOWN),
        weekly_calendar(4, [(date(2026, 10, 26) + timedelta(days=i), "yok") for i in range(5)],
                        {wd: (time(8), time(17)) for wd in range(5)}, time(13), "Europe/Istanbul", SRC, "test", KNOWN))  # fmt: skip


def run(p, ds=None, at=T, cals=CALS):
    rows = X.compare(at, ds if ds is not None else demands(), p, STAFF, UNITS, cals, TZ)
    return {(r["servis_id"], r["rol"]): r for r in rows}


BASE = [sh(1, 1), sh(2, 2), sh(3, 3), sh(4, 4), sh(5, 5, unit=4, hours=9)]


# ---- Matematik: talep, kapasite, kullanım oranı bir kez ------------------------------------------


def test_demand_capacity_gap_math_and_single_utilization():
    r = run(plan(BASE))[(1, "nurse")]
    assert r["durum"] == X.UYGUN
    assert r["tamponlu_talep_dk"] == 100.0  # D = ŷ + B; u talebe uygulanmaz
    assert r["kullanilabilir_personel_dk"] == 120.0  # 2 hemşire × 60 dk
    assert r["etkin_kapasite_dk"] == 60.0  # C · u, u bir kez
    assert r["acik_dk"] == 40.0 and r["fazla_dk"] == 0.0
    assert r["gerekli_personel_dk"] == 200.0  # tasarım biçimi D / u
    assert r["acik_personel_dk"] == pytest.approx(r["acik_dk"] / CONFIG.utilization_target)
    assert r["acik_personel_dk"] == pytest.approx(max(0.0, r["gerekli_personel_dk"] - r["kullanilabilir_personel_dk"]))
    assert r["kullanim_orani"] == 0.5 and r["kullanim_orani_kaynagi"] == SRC
    surplus = run(plan(BASE), demands(nurse1=20.0, over={(1, "nurse"): dem(1, "nurse", 15.0, 5.0)}))[(1, "nurse")]
    assert surplus["tamponlu_talep_dk"] == 20.0 and surplus["acik_dk"] == 0.0 and surplus["fazla_dk"] == 40.0


def test_buffer_is_taken_once_from_u1():
    r = run(plan(BASE), demands(over={(1, "nurse"): dem(1, "nurse", 70.0, 30.0)}))[(1, "nurse")]
    assert (r["nokta_tahmin_dk"], r["tampon_dk"], r["tamponlu_talep_dk"]) == (70.0, 30.0, 100.0)
    assert r["acik_dk"] == 40.0


def test_break_and_leave_subtracted_once():
    brk = un(1, 1, T + 15 * M, T + 45 * M, reason="mola")
    leave = un(2, 1, T + 30 * M, T + 60 * M)  # molayla örtüşür
    r = run(plan(BASE, [brk, leave]))[(1, "nurse")]
    assert r["planli_personel_dk"] == 60 - 30 + 60  # yalnız mola
    assert r["kullanilabilir_personel_dk"] == 60 - 45 + 60  # mola ∪ izin = 45 dk, bir kez
    assert r["etkin_kapasite_dk"] == pytest.approx(75 * 0.5)


def test_headcount_and_minutes_reported_separately():
    p = plan([sh(1, 1), sh(2, 2, start=T0, hours=2.5), *BASE[2:]])  # ikinci hemşire 10:30'da çıkar
    r = run(p)[(1, "nurse")]
    assert r["planli_kisi_en_fazla"] == 2 and r["kullanilabilir_kisi_en_az_atom"] == 1
    assert r["kullanilabilir_personel_dk"] == 90.0


# ---- Karar anı ve sızıntı --------------------------------------------------------------------------


def test_sudden_absence_only_after_report():
    absent = un(9, 2, T0, T0 + 12 * H, reason="devamsizlik", recorded=T + 20 * M, kind="ani")
    p = plan(BASE, [absent])
    before = run(p)[(1, "nurse")]
    assert before["kullanilabilir_personel_dk"] == 120.0  # bildirim t'den sonra: görünmez
    later = T + 30 * M
    after = run(p, [replace(d, at=later) for d in demands()], at=later)[(1, "nurse")]
    assert after["kullanilabilir_personel_dk"] == 60.0 and after["planli_personel_dk"] == 120.0


def test_future_cancellation_not_leaked_and_known_cancellation_applied():
    cancelled = sh(2, 2, status="cancelled", cancelled_at=T + 10 * M)
    p = plan([BASE[0], cancelled, *BASE[2:]])
    assert run(p)[(1, "nurse")]["planli_kisi_en_fazla"] == 2
    later = T + 15 * M
    r = run(p, [replace(d, at=later) for d in demands()], at=later)[(1, "nurse")]
    assert r["planli_kisi_en_fazla"] == 1


def test_older_view_with_later_decision_time_refused():
    stale = known_view(plan(BASE), T - H)
    r = run(stale)[(1, "nurse")]
    assert r["durum"] == X.GECERSIZ_PLAN and r["kullanilabilir_personel_dk"] is None and r["acik_dk"] is None
    assert any(c.startswith("gorunum_karar_anindan_eski") for c in r["nedenler"])


def test_demand_for_other_decision_time_is_rejected():
    shifted = [replace(d, at=T - 15 * M) if (d.unit_id, d.role) == (1, "nurse") else d for d in demands()]
    r = run(plan(BASE), shifted)[(1, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and "tahmin_karar_ani_uyusmuyor" in r["nedenler"] and r["acik_dk"] is None


def test_same_inputs_same_output():
    p = plan(BASE, [un(1, 1, T + 15 * M, T + 45 * M, reason="mola")])
    assert X.compare(T, demands(), p, STAFF, UNITS, CALS, TZ) == X.compare(T, demands(), p, STAFF, UNITS, CALS, TZ)


# ---- Asgari personel ---------------------------------------------------------------------------------


def test_highest_minimum_among_overlapping_rules():
    strict = rule(1, "nurse", 3, source=SRC + ":ikinci", approved_by="test2")
    r = run(plan(BASE, rules=RULES + (strict,)))[(1, "nurse")]
    assert {a["asgari_kisi"] for a in r["atomlar"]} == {3}
    assert r["durum"] == X.IHLAL and any(c.startswith("asgari_personel_ihlali:nurse:10:00:2<3") for c in r["nedenler"])
    sources = {(k["kaynak"], k["onaylayan"], k["asgari_kisi"]) for k in r["asgari_kurallar"]}
    assert sources == {(SRC, "test", 1), (SRC + ":ikinci", "test2", 3)}
    assert all({"bilinme_ani", "gecerlilik_baslangic", "gecerlilik_bitis", "yerel_saat_bandi"} <= set(k) for k in r["asgari_kurallar"])


def test_stricter_rule_expiring_mid_window_applies_only_before():
    strict = rule(1, "nurse", 3, valid_until=T + 20 * M)
    r = run(plan(BASE, rules=RULES + (strict,)))[(1, "nurse")]
    req = [(a["baslangic"], a["asgari_kisi"]) for a in r["atomlar"]]
    assert (T, 3) in req and (T + 20 * M, 1) in req and req[-1][1] == 1


def test_min_staff_violation_reported_separately_not_offset_by_surplus():
    p = plan([sh(1, 1, hours=2.25), *BASE[1:]])  # 1. hemşire 10:15'te çıkar
    strict = tuple(replace(r, min_staff=2) if (r.unit_id, r.role) == (1, "nurse") else r for r in RULES)
    r = run(replace(p, staffing_rules=strict), demands(nurse1=1.0))[(1, "nurse")]
    assert r["fazla_dk"] > 0  # toplamda fazla var
    assert r["durum"] == X.IHLAL and "asgari_personel_ihlali:nurse:10:15:1<2" in r["nedenler"]
    assert [a["ihlal"] for a in r["atomlar"]] == [False, True, True, True]


def test_violation_during_break_atom():
    brk = un(1, 1, T + 20 * M, T + 40 * M, reason="mola")
    strict = tuple(replace(r, min_staff=2) if (r.unit_id, r.role) == (1, "nurse") else r for r in RULES)
    r = run(plan(BASE, [brk], rules=strict))[(1, "nurse")]
    assert r["durum"] == X.IHLAL and "asgari_personel_ihlali:nurse:10:20:1<2" in r["nedenler"]


def test_missing_rule_atom_is_missing_data_without_capacity_numbers():
    partial = tuple(replace(r, valid_until=T + 30 * M) if (r.unit_id, r.role) == (1, "nurse") else r for r in RULES)
    r = run(plan(BASE, rules=partial))[(1, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and "asgari_personel_kurali_yok:nurse:10:30" in r["nedenler"]
    assert r["kullanilabilir_personel_dk"] is None and r["acik_dk"] is None and r["fazla_dk"] is None
    assert [a["asgari_kisi"] for a in r["atomlar"]][-1] is None  # tanı için atomlar korunur


# ---- Takvim: kapalı servis ve veri eksikliği -----------------------------------------------------------


def _night(unit_demand=0.0, shifts=BASE, cals=CALS):
    at = T0 + 18 * H  # yerel 02:00 (ertesi gün)
    ds = [replace(d, at=at) for d in demands(nurse4=unit_demand)]
    return run(plan(shifts), ds, at=at, cals=cals)


def test_verified_closed_service_with_zero_demand():
    r = _night()[(4, "nurse")]
    assert r["durum"] == X.KAPALI and r["takvim_durumu"] == CLOSED
    assert r["kullanilabilir_personel_dk"] == 0.0 and r["acik_dk"] is None and r["fazla_dk"] is None
    assert r["takvim_kaynaklari"] and SRC in r["takvim_kaynaklari"][0]


def test_closed_service_with_positive_demand_goes_to_review():
    r = _night(unit_demand=12.0)[(4, "nurse")]
    assert r["durum"] == X.INCELEME and "kapali_serviste_talep" in r["nedenler"] and r["acik_dk"] is None


def test_closed_service_with_planned_shift_goes_to_review():
    night_shift = sh(9, 5, unit=4, start=T0 + 17 * H, hours=3)
    r = _night(shifts=[*BASE[:4], night_shift])[(4, "nurse")]
    assert r["durum"] == X.INCELEME and "kapali_serviste_planli_vardiya" in r["nedenler"]


def test_no_calendar_and_no_shift_is_missing_not_closed():
    r = _night(cals=CALS[:1])[(4, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and {"planli_vardiya_yok", "calisma_takvimi_yok"} <= set(r["nedenler"])
    assert r["takvim_durumu"] == UNKNOWN
    assert X.KAPALI not in r["durumlar"]


def test_unsourced_or_future_calendar_cannot_prove_closure():
    unsourced = replace(CALS[1], source=" ")
    r = _night(cals=(CALS[0], unsourced))[(4, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and any(w.startswith("takvim_kaynaksiz") for w in r["uyarilar"])
    future = replace(CALS[1], known_at=T0 + 30 * H)
    assert _night(cals=(CALS[0], future))[(4, "nurse")]["durum"] == X.VERI_EKSIK


def test_conflicting_calendars_are_missing_data():
    other = weekly_calendar(4, [(date(2026, 10, 26) + timedelta(days=i), "yok") for i in range(5)],
                            {wd: (time(0), time(23)) for wd in range(5)}, time(13), "Europe/Istanbul", SRC, "b", KNOWN)  # fmt: skip
    r = _night(cals=(CALS[0], CALS[1], other))[(4, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and "takvim_celiskili" in r["nedenler"]


def test_holiday_half_day_and_weekend_calendar_states():
    days = [(date(2026, 10, 27), "yok"), (date(2026, 10, 28), "yarim_13"), (date(2026, 10, 29), "tam"),
            (date(2026, 10, 30), "yok"), (date(2026, 10, 31), "yok")]  # fmt: skip
    cal = weekly_calendar(4, days, {wd: (time(8), time(17)) for wd in range(5)}, time(13), "Europe/Istanbul", SRC, "t", KNOWN)

    def state(day, hh, mm=0):
        start = datetime(2026, 10, day, hh, mm, tzinfo=TZ).astimezone(UTC)
        return calendar_state([cal], 4, Interval(start, start + H), start)

    assert state(27, 10).state == OPEN
    half = state(28, 12, 30)
    assert half.state == PARTIAL and half.open_minutes == 30
    assert state(28, 13).state == CLOSED
    assert state(29, 10).state == CLOSED  # tam gün tatil
    assert state(31, 10).state == CLOSED  # Cumartesi
    closing = state(30, 16, 30)
    assert closing.state == PARTIAL and closing.open_minutes == 30
    assert state(27, 7, 30).state == PARTIAL  # açılış sınırı


def test_partial_window_requires_rules_only_in_open_atoms():
    at = datetime(2026, 10, 27, 16, 30, tzinfo=TZ).astimezone(UTC)  # poliklinik 17:00'de kapanır
    clinic = sh(9, 5, unit=4, start=T0, hours=9)
    day_only = tuple(r for r in RULES if r.unit_id != 4) + tuple(
        rule(4, role, 1 if role == "nurse" else 0, valid_until=datetime(2026, 10, 27, 17, tzinfo=TZ)) for role in ("nurse", "supervisor", "support"))  # fmt: skip
    ds = [replace(d, at=at) for d in demands(nurse4=10.0)]
    r = run(plan([*BASE[:4], clinic], rules=day_only), ds, at=at)[(4, "nurse")]
    assert r["takvim_durumu"] == PARTIAL and "kismi_acik_pencere" in r["uyarilar"]
    assert r["durum"] == X.UYGUN and r["kullanilabilir_personel_dk"] == 30.0
    assert [a["takvime_gore_acik"] for a in r["atomlar"]] == [True, True, False, False]


# ---- Güvenli ret ve durum öncelikleri ------------------------------------------------------------------


def test_invalid_forecast_gives_no_comparison():
    bad = dem(1, "nurse", math.nan, math.nan, status="inceleme_gerekli", reasons=("gecerli_tahmin_yok", "uyumsuz_sozlesme:rf"))
    r = run(plan(BASE), demands(over={(1, "nurse"): bad}))[(1, "nurse")]
    assert r["durum"] == X.INCELEME and "gecerli_tahmin_yok" in r["nedenler"]
    assert r["tamponlu_talep_dk"] is None and r["acik_dk"] is None and r["fazla_dk"] is None
    assert r["kullanilabilir_personel_dk"] == 120.0  # kapasite bilgisi doğru, ama karşılaştırma yok


def test_missing_or_duplicate_demand_is_missing_data():
    r = run(plan(BASE), demands(over={(1, "nurse"): None}))[(1, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and "tahmin_satiri_yok" in r["nedenler"]
    r = run(plan(BASE), demands() + [dem(1, "nurse", 5.0)])[(1, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and "tahmin_satiri_cift" in r["nedenler"]


def test_missing_or_invalid_utilization_is_missing_data():
    r = run(plan(BASE, config=None))[(1, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and "planlama_yapilandirmasi_yok" in r["nedenler"] and r["acik_dk"] is None
    r = run(plan(BASE, config=replace(CONFIG, utilization_target=1.4)))[(1, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and "kullanim_orani_gecersiz" in r["nedenler"] and r["etkin_kapasite_dk"] is None


def test_invalid_plan_refused_and_other_problems_kept():
    overlap = sh(8, 1, unit=4, start=T0 + H)
    r = run(plan([*BASE, overlap]), demands(over={(1, "nurse"): None}))[(1, "nurse")]
    assert r["durum"] == X.GECERSIZ_PLAN and r["durumlar"] == [X.GECERSIZ_PLAN, X.VERI_EKSIK]
    assert any(c.startswith("cakisan_vardiya") for c in r["nedenler"]) and "tahmin_satiri_yok" in r["nedenler"]
    assert r["kullanilabilir_personel_dk"] is None and r["atomlar"] == []


def test_priority_violation_over_review_and_all_reasons_kept():
    strict = tuple(replace(r, min_staff=3) if (r.unit_id, r.role) == (1, "nurse") else r for r in RULES)
    review = dem(1, "nurse", 50.0, 40.0, status="inceleme_gerekli", reasons=("yuksek_goreli_tampon",))
    r = run(plan(BASE, rules=strict), demands(over={(1, "nurse"): review}))[(1, "nurse")]
    assert r["durum"] == X.IHLAL and r["durumlar"] == [X.IHLAL, X.INCELEME]
    assert "yuksek_goreli_tampon" in r["nedenler"] and r["acik_dk"] is not None  # sayılar belirsizlik etiketiyle
    assert X.STATUS_PRIORITY == (X.GECERSIZ_PLAN, X.VERI_EKSIK, X.IHLAL, X.INCELEME, X.KAPALI, X.UYGUN)


# ---- Çıktı sözleşmesi ------------------------------------------------------------------------------------


REQUIRED = {"servis_id", "servis_turu", "karar_ani", "pencere_baslangic", "pencere_bitis", "rol", "model", "model_surumu",
            "model_sha256", "nokta_tahmin_dk", "tampon_dk", "tamponlu_talep_dk", "planli_kisi_en_fazla",
            "kullanilabilir_kisi_en_az_atom", "planli_personel_dk", "kullanilabilir_personel_dk", "kullanim_orani",
            "kullanim_orani_kaynagi", "etkin_kapasite_dk", "acik_dk", "fazla_dk", "atomlar", "asgari_kurallar", "durum",
            "durumlar", "nedenler", "uyarilar", "sentetik_uyari", "insan_onayi_gerekli"}  # fmt: skip


def test_output_contract_and_no_decision_fields():
    rows = X.compare(T, demands(), plan(BASE), STAFF, UNITS, CALS, TZ)
    assert len(rows) == len(UNITS) * 3
    for r in rows:
        assert REQUIRED <= set(r)
        assert r["insan_onayi_gerekli"] is True and "klinik standart değildir" in r["sentetik_uyari"]
        assert not set(r) & set(X.DECISION_FIELDS_FORBIDDEN)
        assert not any(k.startswith(("onerilen", "atanan")) for k in r)
        assert r["karar_ani"] == T and r["pencere_bitis"] - r["pencere_baslangic"] == H
    data = X.jsonable(rows)
    assert isinstance(data[0]["karar_ani"], str) and data[0]["karar_ani"].endswith("+00:00")


def test_service_calendar_validation():
    reversed_ = ServiceCalendar(4, COVER, (Interval(T0, T0 + H), Interval(T0 + 30 * M, T0 + 2 * H)), SRC, "t", KNOWN)
    assert any(p.startswith("takvim_araliklari_cakisiyor") for p in reversed_.problems())
    outside = ServiceCalendar(4, Interval(T0, T0 + H), (Interval(T0, T0 + 2 * H),), SRC, "t", KNOWN)
    assert any(p.startswith("takvim_araligi_kapsam_disi") for p in outside.problems())
    with pytest.raises(ValueError):
        weekly_calendar(4, [(date(2026, 10, 27), "bayram")], {}, time(13), "Europe/Istanbul", SRC, "t", KNOWN)
    assert calendar_state([reversed_], 4, Interval(T0, T0 + H), T).state == UNKNOWN
    assert CONFLICT == "celiskili"


# ---- İnceleme düzeltmeleri: kısmen açık pencere ve eksik takvim ------------------------------------


def _loc(hh, mm=0):
    return datetime(2026, 10, 27, hh, mm, tzinfo=TZ).astimezone(UTC)


def _clinic_cal(close, **kw):
    days = [(date(2026, 10, 26) + timedelta(days=i), "yok") for i in range(5)]
    cal = weekly_calendar(4, days, {wd: (time(8), close) for wd in range(5)}, time(13), "Europe/Istanbul", SRC, "test", KNOWN)
    return replace(cal, **kw) if kw else cal


LONG = sh(9, 5, unit=4, start=_loc(8), hours=10)  # poliklinik hemşiresi 08:00-18:00
AT_1630 = _loc(16, 30)


def _clinic(p, at=AT_1630, close=time(17), d4=10.0, cals=None):
    ds = [replace(d, at=at) for d in demands(nurse4=d4)]
    return run(p, ds, at=at, cals=cals if cals is not None else (CALS[0], _clinic_cal(close)))


def _open_atom_minutes(r, idx):
    return sum((a["bitis"] - a["baslangic"]).total_seconds() / 60 * a[idx] for a in r["atomlar"] if a["takvime_gore_acik"])


def test_partial_window_counts_only_open_time():
    r = _clinic(plan([*BASE[:4], LONG]))[(4, "nurse")]
    assert r["takvim_durumu"] == PARTIAL and r["takvime_gore_acik_dk"] == 30.0
    assert r["planli_personel_dk"] == 30.0 and r["kullanilabilir_personel_dk"] == 30.0  # 17:00-17:30 katılmaz
    assert r["etkin_kapasite_dk"] == 15.0 and r["acik_dk"] == 0.0 and r["fazla_dk"] == 5.0  # D = 10
    assert [a["takvime_gore_acik"] for a in r["atomlar"]] == [True, True, False, False]
    assert _open_atom_minutes(r, "kullanilabilir_kisi") == r["kullanilabilir_personel_dk"]  # bağımsız yeniden hesap
    assert all(a["asgari_kisi"] is None for a in r["atomlar"] if not a["takvime_gore_acik"])


def test_partial_window_break_and_leave_subtracted_once_inside_open_part():
    brk = un(1, 5, _loc(16, 40), _loc(17, 10), reason="mola")
    leave = un(2, 5, _loc(16, 50), _loc(17, 20))  # molayla örtüşür, kapanışı aşar
    r = _clinic(plan([*BASE[:4], LONG], [brk, leave]))[(4, "nurse")]
    assert r["planli_personel_dk"] == 30 - 20  # açık bölümde yalnız mola (16:40-17:00)
    assert r["kullanilabilir_personel_dk"] == 30 - 20  # mola ∪ izin açık bölümde 16:40-17:00, bir kez
    assert _open_atom_minutes(r, "kullanilabilir_kisi") == 10.0 and _open_atom_minutes(r, "planli_kisi") == 10.0
    assert r["acik_dk"] == 5.0  # D 10 − C·u 5
    assert r["durum"] == X.IHLAL and "asgari_personel_ihlali:nurse:16:40:0<1" in r["nedenler"]


def test_partial_window_off_grid_closing_splits_atoms():
    r = _clinic(plan([*BASE[:4], LONG]), close=time(17, 10))[(4, "nurse")]
    assert r["takvime_gore_acik_dk"] == 40.0 and r["kullanilabilir_personel_dk"] == 40.0 and r["planli_personel_dk"] == 40.0
    spans = [(a["baslangic"], a["bitis"], a["takvime_gore_acik"], a["asgari_kisi"]) for a in r["atomlar"]]
    assert (_loc(17), _loc(17, 10), True, 1) in spans  # kapanış sınırında bölünür, kural açık parçada aranır
    assert (_loc(17, 10), _loc(17, 15), False, None) in spans
    rule_until_close = tuple(r_ for r_ in RULES if r_.unit_id != 4) + tuple(
        rule(4, role, 1 if role == "nurse" else 0, valid_until=_loc(17, 10)) for role in ("nurse", "supervisor", "support"))  # fmt: skip
    ok = _clinic(plan([*BASE[:4], LONG], rules=rule_until_close), close=time(17, 10))[(4, "nurse")]
    assert ok["durum"] == X.UYGUN  # kapalı bölümde kural aranmaz


def test_fully_open_service_results_unchanged():
    p = plan(BASE, [un(1, 1, T + 15 * M, T + 45 * M, reason="mola"), un(2, 1, T + 30 * M, T + 60 * M)])
    r = run(p)[(1, "nurse")]
    assert (r["planli_personel_dk"], r["kullanilabilir_personel_dk"], r["etkin_kapasite_dk"]) == (90.0, 75.0, 37.5)
    assert (r["acik_dk"], r["fazla_dk"], r["planli_kisi_en_fazla"], r["kullanilabilir_kisi_en_az_atom"]) == (62.5, 0.0, 2, 1)
    assert _open_atom_minutes(r, "kullanilabilir_kisi") == 75.0
    # Poliklinik takviminin kısmi olması diğer servisi etkilemez.
    other = _clinic(plan([*BASE[:4], LONG]))
    assert other[(1, "nurse")]["kullanilabilir_personel_dk"] == 120.0 and other[(1, "nurse")]["takvim_durumu"] == OPEN


def test_missing_calendar_is_missing_data_even_with_full_contract():
    strict = tuple(replace(r_, min_staff=5) if (r_.unit_id, r_.role) == (1, "nurse") else r_ for r_ in RULES)
    r = run(plan(BASE, rules=strict), cals=())[(1, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and r["durumlar"] == [X.VERI_EKSIK] and "calisma_takvimi_yok" in r["nedenler"]
    for k in ("planli_personel_dk", "kullanilabilir_personel_dk", "etkin_kapasite_dk", "acik_dk", "fazla_dk", "planli_kisi_en_fazla"):
        assert r[k] is None
    assert all(a["takvime_gore_acik"] is None and a["asgari_kisi"] is None and not a["ihlal"] for a in r["atomlar"])
    assert r["tamponlu_talep_dk"] == 100.0  # talep bilgisi korunur, kıyas yapılmaz


@pytest.mark.parametrize("bad", [
    replace(CALS[0], source=" "),  # kaynaksız
    replace(CALS[0], approved_by=""),  # onaysız
    replace(CALS[0], known_at=T + M),  # karar anından sonra bilinen
    ServiceCalendar(1, COVER, (Interval(COVER.start, T + H), Interval(T, COVER.end)), SRC, "t", KNOWN),  # çakışan aralık
])  # fmt: skip
def test_invalid_or_future_calendar_does_not_replace_missing_one(bad):
    r = run(plan(BASE), cals=(bad, CALS[1]))[(1, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and "calisma_takvimi_yok" in r["nedenler"]
    assert r["kullanilabilir_personel_dk"] is None and r["acik_dk"] is None


def test_conflicting_calendars_still_refused_with_shifts():
    closed_now = ServiceCalendar(1, COVER, (Interval(COVER.start, T - H),), SRC, "b", KNOWN)
    r = run(plan(BASE), cals=(CALS[0], closed_now, CALS[1]))[(1, "nurse")]
    assert r["durum"] == X.VERI_EKSIK and "takvim_celiskili" in r["nedenler"] and r["kullanilabilir_personel_dk"] is None


def test_future_absence_does_not_leak_into_partial_window():
    absent = un(9, 5, _loc(8), _loc(18), reason="devamsizlik", recorded=_loc(16, 40), kind="ani")
    p = plan([*BASE[:4], LONG], [absent])
    before = _clinic(p)[(4, "nurse")]
    assert before["kullanilabilir_personel_dk"] == 30.0 and before["planli_personel_dk"] == 30.0
    at = _loc(16, 45)
    after = _clinic(p, at=at)[(4, "nurse")]
    assert after["takvime_gore_acik_dk"] == 15.0 and after["planli_personel_dk"] == 15.0
    assert after["kullanilabilir_personel_dk"] == 0.0 and after["durum"] == X.IHLAL


# ---- K2: ham takvimle kapalı ve vardiyasız serviste tampon uygulanmaz --------------------------------


def _closed(point, buffer, status="uygun", reasons=(), shifts=BASE, cals=CALS):
    at = T0 + 18 * H  # yerel 02:00, poliklinik kapalı
    clinic = dem(4, "nurse", point, buffer, at=at, status=status, reasons=reasons)
    ds = [replace(d, at=at) for d in demands(over={(4, "nurse"): clinic})]
    return run(plan(shifts), ds, at=at, cals=cals)[(4, "nurse")]


def test_k2_closed_zero_point_ignores_buffer():
    r = _closed(0.0, 35.4)
    assert r["durum"] == X.KAPALI and r["tampon_uygulandi"] is False
    assert (r["nokta_tahmin_dk"], r["tampon_dk"], r["tamponlu_talep_dk"]) == (0.0, 0.0, 0.0)
    assert "kapali_serviste_tampon_uygulanmadi:35.400" in r["uyarilar"]


def test_k2_buffer_derived_u1_warning_not_blocking_but_kept():
    r = _closed(0.0, 35.4, status="inceleme_gerekli", reasons=("yuksek_goreli_tampon",))
    assert r["durum"] == X.KAPALI and "kapali_serviste_gecersiz_u1_uyarisi:yuksek_goreli_tampon" in r["uyarilar"]
    assert "yuksek_goreli_tampon" not in r["nedenler"]


def test_k2_other_u1_warnings_still_block():
    r = _closed(0.0, 35.4, status="inceleme_gerekli", reasons=("dagilim_disi:bilinen_plan_dk_30", "yuksek_goreli_tampon"))
    assert r["durum"] == X.INCELEME and "dagilim_disi:bilinen_plan_dk_30" in r["nedenler"]
    assert "yuksek_goreli_tampon" not in r["nedenler"]


def test_k2_positive_point_forecast_is_safe_refusal():
    r = _closed(0.2, 35.4)
    assert r["durum"] == X.INCELEME and "kapali_serviste_talep" in r["nedenler"]
    assert r["tamponlu_talep_dk"] == 0.2 and r["tampon_uygulandi"] is False


def test_k2_buffer_kept_when_closed_unit_has_shift():
    night_shift = sh(9, 5, unit=4, start=T0 + 17 * H, hours=3)
    r = _closed(0.0, 35.4, shifts=[*BASE[:4], night_shift])
    assert r["durum"] == X.INCELEME and r["tampon_uygulandi"] is True
    assert {"kapali_serviste_planli_vardiya", "kapali_serviste_talep"} <= set(r["nedenler"])


def test_k2_unknown_calendar_unchanged():
    r = _closed(0.0, 35.4, cals=CALS[:1])
    assert r["durum"] == X.VERI_EKSIK and "calisma_takvimi_yok" in r["nedenler"] and r["tampon_uygulandi"] is True
