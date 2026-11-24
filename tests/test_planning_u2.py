"""Aşama 5 / U2: planlı kapasite sözleşmesi, karar anı görünümü (known_view) ve kapasite hesabı.

Veritabanı yok. Asgari personel kuralları ve planlama yapılandırması burada AÇIKÇA SENTETİK test
sabitleridir (kaynak alanı 'test_sentetik'); gerçek güvenlik standardı değildir.
"""

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from medakis.domain.capacity import Unavailability
from medakis.domain.models import Staff, StaffShift
from medakis.domain.planning import (
    INVALID,
    MISSING,
    OK,
    PlannedShift,
    PlannedUnavailability,
    PlanningConfig,
    PlanSet,
    StaffingRule,
    atoms,
    known_view,
    plan_problems,
    realized_unit_minutes,
    unit_capacity_at,
)
from medakis.domain.time import Interval

TZ = ZoneInfo("Europe/Istanbul")
T0 = datetime(2026, 3, 2, 5, 0, tzinfo=UTC)  # yerel 08:00
H = timedelta(hours=1)
M = timedelta(minutes=1)
PUBLISHED = T0 - timedelta(days=14)
STAFF = {1: Staff(1, "H1", "nurse", True), 2: Staff(2, "H2", "nurse", True),
         3: Staff(3, "S1", "supervisor", True), 4: Staff(4, "D1", "support", True)}  # fmt: skip
ROLES_OF = {k: v.role for k, v in STAFF.items()}
UNITS = {1, 2}
CONFIG = PlanningConfig(0.85, 0.2, 1.0, 10.0, "test_sentetik")


def rule(unit, role, band=(0, 24), n=1, **kw):
    base = dict(source="test_sentetik", approved_by="test", valid_from=T0 - timedelta(days=30), valid_until=None,
                known_at=T0 - timedelta(days=30))  # fmt: skip
    base.update(kw)
    return StaffingRule(unit, role, band, n, **base)


RULES = tuple(rule(u, r) for u in UNITS for r in ("nurse", "supervisor", "support"))


def sh(i, staff, unit=1, start=T0, hours=12, template_id=1, break_minutes=0, planned_at=PUBLISHED, status="planned",
       cancelled_at=None):  # fmt: skip
    return PlannedShift(i, staff, unit, ROLES_OF[staff], template_id, start, start + hours * H, break_minutes, planned_at,
                        status, cancelled_at, "test")  # fmt: skip


def un(i, staff, a, b, reason="izin", recorded=PUBLISHED, kind="planli"):
    return PlannedUnavailability(i, staff, a, b, reason, recorded, kind)


def plan(shifts, unav=(), rules=RULES, config=CONFIG):
    return PlanSet(tuple(shifts), tuple(unav), tuple(rules), (), config)


def cap(p, at, window, unit=1, **kw):
    return unit_capacity_at(p, at, STAFF, unit, UNITS, window, TZ, **kw)


DAY = Interval(T0, T0 + 8 * H)  # yerel 08:00-16:00


# ---- Sözleşme ----------------------------------------------------------------------------------


def test_planning_config_has_no_defaults_and_validates():
    with pytest.raises(TypeError):
        PlanningConfig()  # type: ignore[call-arg]
    bad = PlanningConfig(1.5, 0, -1, math.nan, " ")
    assert set(bad.problems()) == {"kullanim_orani_gecersiz", "belirsizlik_esigi_gecersiz", "hareket_cezasi_gecersiz",
                                   "cozum_suresi_gecersiz", "yapilandirma_kaynagi_yok"}  # fmt: skip
    assert CONFIG.problems() == []


def test_missing_config_or_utilization_is_safe_refusal():
    p = plan([sh(1, 1)], config=None)
    r = cap(p, T0, DAY)
    assert r.status == MISSING and "planlama_yapilandirmasi_yok" in r.reasons and r.roles == {}
    r = cap(plan([sh(1, 1)], config=replace(CONFIG, utilization_target=0.0)), T0, DAY)
    assert r.status == MISSING and "kullanim_orani_gecersiz" in r.reasons and r.roles == {}


def test_missing_minimum_staff_rule_is_safe_refusal():
    rules = tuple(r for r in RULES if not (r.unit_id == 1 and r.role == "support"))
    r = cap(plan([sh(1, 1)], rules=rules), T0, DAY)
    assert r.status == MISSING and r.roles == {}
    assert any(x.startswith("asgari_personel_kurali_yok:support:") for x in r.reasons)


def test_rule_known_only_after_known_at_and_valid_period():
    late = tuple(replace(r, known_at=T0 + H) if r.role == "nurse" and r.unit_id == 1 else r for r in RULES)
    assert cap(plan([sh(1, 1)], rules=late), T0, DAY).status == MISSING
    assert cap(plan([sh(1, 1)], rules=late), T0 + H, DAY).status == OK
    expired = tuple(replace(r, valid_until=T0) if r.role == "nurse" and r.unit_id == 1 else r for r in RULES)
    assert cap(plan([sh(1, 1)], rules=expired), T0, DAY).status == MISSING


def test_unsourced_or_unapproved_rule_is_invalid_plan():
    for bad in (rule(1, "nurse", source=" "), rule(1, "nurse", approved_by="")):
        r = cap(plan([sh(1, 1)], rules=RULES + (bad,)), T0, DAY)
        assert r.status == INVALID and any(x.startswith("kural_kaynaksiz_veya_gecersiz") for x in r.reasons)


def test_no_planned_shift_is_missing_not_filled_from_realized():
    r = cap(plan([sh(1, 1, unit=2)]), T0, DAY, unit=1)
    assert r.status == MISSING and "planli_vardiya_yok" in r.reasons and r.roles == {}
    # Sonradan değerlendirme yardımcısı gerçekleşeni görür ama plan kapasitesine hiçbir yoldan girmez.
    realized = [StaffShift(9, 1, 1, T0, T0 + 12 * H, 0)]
    assert realized_unit_minutes(STAFF, realized, [], 1, DAY)["nurse"] == 480
    assert cap(plan([sh(1, 1, unit=2)]), T0, DAY, unit=1, require_contract=False).status == MISSING


@pytest.mark.parametrize("mutate,code", [
    (lambda s: replace(s, ends_at=s.starts_at), "ters_aralik:vardiya"),
    (lambda s: replace(s, status="bilinmiyor"), "gecersiz_durum:vardiya"),
    (lambda s: replace(s, status="cancelled"), "iptal_alani_tutarsiz:vardiya"),
    (lambda s: replace(s, cancelled_at=T0), "iptal_alani_tutarsiz:vardiya"),
    (lambda s: replace(s, status="cancelled", cancelled_at=PUBLISHED - H), "iptal_plandan_once:vardiya"),
    (lambda s: replace(s, staff_id=99), "bilinmeyen_personel:vardiya"),
    (lambda s: replace(s, role="supervisor"), "rol_uyusmuyor:vardiya"),
    (lambda s: replace(s, unit_id=7), "bilinmeyen_servis:vardiya"),
])  # fmt: skip
def test_inconsistent_shift_is_invalid_plan(mutate, code):
    p = PlanSet((mutate(sh(1, 1)),), (), RULES, (), CONFIG)
    assert any(x.startswith(code) for x in plan_problems(p, ROLES_OF, UNITS))
    assert unit_capacity_at(p, T0, STAFF, 1, UNITS, DAY, TZ).status == INVALID


def test_physician_role_is_not_modelled():
    staff = {**STAFF, 5: Staff(5, "X", "physician", True)}
    p = PlanSet((PlannedShift(1, 5, 1, "physician", 1, T0, T0 + 8 * H, 0, PUBLISHED, "planned", None, "t"),), ())
    assert any(x.startswith("modellenmeyen_rol") for x in plan_problems(p, {k: v.role for k, v in staff.items()}, UNITS))


def test_same_person_in_two_units_at_once_is_rejected():
    p = plan([sh(1, 1, unit=1), sh(2, 1, unit=2, start=T0 + 4 * H)])
    probs = plan_problems(p, ROLES_OF, UNITS)
    assert "cakisan_vardiya:personel:1:1/2" in probs
    assert cap(p, T0, DAY).status == INVALID
    # Aynı servis içi çakışma da reddedilir; uç uca vardiya ve iptal edilmiş çakışma sorun değildir.
    assert plan_problems(plan([sh(1, 1), sh(2, 1, start=T0 + H)]), ROLES_OF, UNITS)
    assert plan_problems(plan([sh(1, 1), sh(2, 1, unit=2, start=T0 + 12 * H)]), ROLES_OF, UNITS) == []
    cancelled = sh(2, 1, unit=2, start=T0 + 4 * H, status="cancelled", cancelled_at=PUBLISHED + H)
    assert plan_problems(plan([sh(1, 1), cancelled]), ROLES_OF, UNITS) == []


# ---- Bilinme zamanı (known_view) ---------------------------------------------------------------


def test_planned_at_boundary():
    s = sh(1, 1, planned_at=T0 - H)
    assert known_view(plan([s]), T0 - H).shifts == (s,)  # planned_at == t: görünür
    assert known_view(plan([s]), T0 - H - timedelta(microseconds=1)).shifts == ()  # planned_at > t: görünmez


def test_recorded_at_boundary():
    u = un(1, 1, T0 + H, T0 + 2 * H, recorded=T0 - H)
    assert known_view(plan([sh(1, 1)], [u]), T0 - H).unavailability == (u,)
    assert known_view(plan([sh(1, 1)], [u]), T0 - H - timedelta(seconds=1)).unavailability == ()


def test_cancellation_after_t_does_not_leak_into_past():
    s = sh(1, 1, status="cancelled", cancelled_at=T0 + 2 * H)
    before = known_view(plan([s]), T0 + H)
    assert before.shifts[0].status == "planned" and before.shifts[0].cancelled_at is None
    after = known_view(plan([s]), T0 + 2 * H)
    assert after.shifts[0].status == "cancelled"
    w = Interval(T0 + 3 * H, T0 + 4 * H)
    assert cap(plan([s, sh(2, 2)]), T0 + H, w).roles["nurse"].planned_headcount_max == 2
    assert cap(plan([s, sh(2, 2)]), T0 + 2 * H, w).roles["nurse"].planned_headcount_max == 1


def test_sudden_absence_not_visible_before_report():
    report = T0 + 20 * M
    absent = un(1, 1, T0, T0 + 12 * H, reason="devamsizlik", recorded=report, kind="ani")
    p = plan([sh(1, 1), sh(2, 2)], [absent])
    w = Interval(T0 + H, T0 + 2 * H)
    early = cap(p, report - timedelta(seconds=1), w).roles["nurse"]
    late = cap(p, report, w).roles["nurse"]
    assert early.available_headcount_min_atom == 2 and early.available_minutes == 120
    assert late.available_headcount_min_atom == 1 and late.available_minutes == 60
    # Plan aynı kalır: gelinmeyen vardiya planda görünmeye devam eder.
    assert late.planned_headcount_max == 2 and late.planned_minutes == 120


def test_later_changes_do_not_alter_past_view():
    t = T0 - H
    base = plan([sh(1, 1), sh(2, 2)], [un(1, 1, T0 + 2 * H, T0 + 3 * H, recorded=T0 - 2 * H)])
    later = PlanSet(
        (sh(1, 1), sh(2, 2, status="cancelled", cancelled_at=T0), sh(3, 1, unit=2, start=T0 + 13 * H, planned_at=T0)),
        base.unavailability + (un(2, 2, T0, T0 + 4 * H, recorded=T0 + 10 * M, kind="ani"),),
        RULES + (rule(1, "nurse", n=3, known_at=T0),), (), CONFIG)  # fmt: skip
    assert known_view(base, t) == known_view(later, t)
    assert cap(base, t, DAY) == cap(later, t, DAY)


# ---- Kapasite ve mola ----------------------------------------------------------------------------


def test_headcount_and_minutes_are_separate():
    p = plan([sh(1, 1), sh(2, 2, hours=4)])
    r = cap(p, T0, DAY)
    nurse = r.roles["nurse"]
    assert r.status == OK
    assert nurse.planned_headcount_max == 2  # pencerede vardiyası olan kişi
    assert nurse.planned_headcount_min_atom == 1  # 12:00 sonrası tek kişi
    assert nurse.planned_minutes == 8 * 60 + 4 * 60  # personel-dakika
    assert r.roles["supervisor"].planned_headcount_max == 0 and r.roles["supervisor"].planned_minutes == 0


def test_breaks_and_unavailability_subtracted_once():
    brk = un(1, 1, T0 + 4 * H, T0 + 5 * H, reason="mola")  # yerel 12:00-13:00
    leave = un(2, 1, T0 + 4 * H + 30 * M, T0 + 6 * H, reason="izin")  # 12:30-14:00, molayla örtüşür
    p = plan([sh(1, 1, break_minutes=60)], [brk, leave])
    nurse = cap(p, T0, Interval(T0, T0 + 12 * H)).roles["nurse"]
    assert nurse.planned_minutes == 720 - 60  # somut mola bir kez; break_minutes özeti ikinci kez düşülmez
    assert nurse.available_minutes == 720 - 120  # mola ∪ izin = 12:00-14:00
    # Somut mola yoksa özet alan düşülmez (Aşama 3 kuralı).
    only_summary = cap(plan([sh(1, 1, break_minutes=60)]), T0, Interval(T0, T0 + 12 * H)).roles["nurse"]
    assert only_summary.planned_minutes == 720


def test_atoms_split_on_off_grid_boundaries():
    brk = un(1, 1, T0 + 5 * M, T0 + 35 * M, reason="mola")
    nurse = cap(plan([sh(1, 1)], [brk]), T0, Interval(T0, T0 + H)).roles["nurse"]
    spans = [(a, b, p) for a, b, p, _ in nurse.atom_counts]
    assert (T0, T0 + 5 * M, 1) in spans and (T0 + 5 * M, T0 + 15 * M, 0) in spans and (T0 + 30 * M, T0 + 35 * M, 0) in spans
    assert (T0 + 35 * M, T0 + 45 * M, 1) in spans
    assert nurse.planned_headcount_min_atom == 0 and nurse.planned_minutes == 30
    assert atoms(Interval(T0, T0 + 30 * M), []) == [Interval(T0, T0 + 15 * M), Interval(T0 + 15 * M, T0 + 30 * M)]


def test_shift_change_inside_window_counts_both_sides():
    p = plan([sh(1, 1, hours=2), sh(2, 2, start=T0 + 2 * H, hours=2)])
    nurse = cap(p, T0, Interval(T0 + H, T0 + 3 * H)).roles["nurse"]
    assert nurse.planned_headcount_max == 2 and nurse.planned_headcount_min_atom == 1 and nurse.planned_minutes == 120


def test_night_shift_across_midnight_and_wrapping_rule_band():
    night_start = T0 + 12 * H  # yerel 20:00
    window = Interval(night_start + 3 * H, night_start + 5 * H)  # yerel 23:00-01:00
    night_rules = tuple(rule(1, r, band=(20, 8)) for r in ("nurse", "supervisor", "support"))
    nurse = cap(plan([sh(1, 1, start=night_start)], rules=night_rules), T0, window).roles["nurse"]
    assert nurse.planned_minutes == 120 and nurse.planned_headcount_min_atom == 1
    day_rules = tuple(rule(1, r, band=(8, 20)) for r in ("nurse", "supervisor", "support"))
    r = cap(plan([sh(1, 1, start=night_start)], rules=day_rules), T0, window)
    assert r.status == MISSING and "asgari_personel_kurali_yok:nurse:23:00" in r.reasons


def test_half_day_shift_capacity():
    half = sh(1, 1, hours=5, template_id=4)  # yarım gün (yerel 08:00-13:00)
    nurse = cap(plan([half]), T0, DAY).roles["nurse"]
    assert nurse.planned_minutes == 300 and nurse.planned_headcount_min_atom == 0 and nurse.planned_headcount_max == 1


def test_realized_minutes_separate_from_planned():
    absent = un(1, 1, T0, T0 + 12 * H, reason="devamsizlik", recorded=T0 + 10 * M, kind="ani")
    p = plan([sh(1, 1), sh(2, 2)], [absent])
    planned = cap(p, T0 + 12 * H, DAY).roles["nurse"]
    realized = realized_unit_minutes(STAFF, [StaffShift(2, 2, 1, T0, T0 + 12 * H, 0)],
                                     [Unavailability(1, Interval(T0, T0 + 12 * H), "devamsizlik")], 1, DAY)  # fmt: skip
    assert planned.planned_minutes == 960 and planned.available_minutes == 480 and realized["nurse"] == 480


# ---- İnceleme düzeltmesi 1: karar anı zorunlu, görünüm içeride kurulur --------------------------

W = Interval(T0 + H, T0 + 2 * H)  # yerel 09:00-10:00
T = T0 + 30 * M


def _late_absence_plan():
    absent = un(1, 1, T0, T0 + 12 * H, reason="devamsizlik", recorded=T0 + 90 * M, kind="ani")  # t'den SONRA bildirilir
    return plan([sh(1, 1), sh(2, 2)], [absent])


def test_raw_plan_equals_known_view_at_decision_time():
    raw = _late_absence_plan()
    from_raw = unit_capacity_at(raw, T, STAFF, 1, UNITS, W, TZ)
    from_view = unit_capacity_at(known_view(raw, T), T, STAFF, 1, UNITS, W, TZ)
    assert from_raw == from_view
    assert from_raw.status == OK and from_raw.roles["nurse"].available_minutes == 120  # ham planla 60 değil
    assert unit_capacity_at(raw, T0 + 90 * M, STAFF, 1, UNITS, W, TZ).roles["nurse"].available_minutes == 60


def test_raw_plan_future_cancellation_does_not_leak():
    raw = plan([sh(1, 1, status="cancelled", cancelled_at=T0 + 90 * M), sh(2, 2)])
    assert unit_capacity_at(raw, T, STAFF, 1, UNITS, W, TZ).roles["nurse"].planned_headcount_max == 2
    assert unit_capacity_at(raw, T0 + 90 * M, STAFF, 1, UNITS, W, TZ).roles["nurse"].planned_headcount_max == 1


def test_manually_set_as_of_is_not_trusted():
    raw = _late_absence_plan()
    fake = replace(raw, as_of=T)  # filtrelenmemiş ama as_of doldurulmuş
    assert unit_capacity_at(fake, T, STAFF, 1, UNITS, W, TZ).roles["nurse"].available_minutes == 120


def test_older_view_with_later_decision_time_is_refused():
    raw = _late_absence_plan()
    old = known_view(raw, T)
    r = unit_capacity_at(old, T0 + 2 * H, STAFF, 1, UNITS, W, TZ)
    assert r.status == INVALID and r.roles == {} and r.reasons[0].startswith("gorunum_karar_anindan_eski")
    newer = known_view(raw, T0 + 2 * H)  # daha yeni görünüm, daha erken karar anı: yeniden süzülür
    assert unit_capacity_at(newer, T, STAFF, 1, UNITS, W, TZ) == unit_capacity_at(raw, T, STAFF, 1, UNITS, W, TZ)


def test_result_as_of_is_decision_time_for_every_status():
    raw = _late_absence_plan()
    assert unit_capacity_at(raw, T, STAFF, 1, UNITS, W, TZ).as_of == T
    assert unit_capacity_at(replace(raw, config=None), T, STAFF, 1, UNITS, W, TZ).as_of == T  # veri_eksik
    bad = plan([sh(1, 1), sh(2, 1, unit=2, start=T0 + H)])
    assert unit_capacity_at(bad, T, STAFF, 1, UNITS, W, TZ).as_of == T  # gecersiz_plan
    assert unit_capacity_at(known_view(raw, T), T, STAFF, 1, UNITS, W, TZ).as_of == T


# ---- İnceleme düzeltmesi 2: asgari personel kuralı her atomda ------------------------------------


def _rules(unit=1, **kw):
    return tuple(rule(unit, r, **kw) for r in ("nurse", "supervisor", "support"))


def _cap_rules(rules, window=W, tz=TZ, at=T):
    return unit_capacity_at(plan([sh(1, 1), sh(2, 2)], rules=rules), at, STAFF, 1, UNITS, window, tz)


def test_rule_ending_inside_window_is_missing():
    r = _cap_rules(_rules(valid_until=W.start + 5 * M))
    assert r.status == MISSING and r.roles == {}
    assert "asgari_personel_kurali_yok:nurse:09:05" in r.reasons


def test_rule_starting_inside_window_without_predecessor_is_missing():
    r = _cap_rules(_rules(valid_from=W.start + 5 * M))
    assert r.status == MISSING and "asgari_personel_kurali_yok:nurse:09:00" in r.reasons


def test_rule_handover_at_same_instant_is_accepted():
    cut = W.start + 5 * M
    r = _cap_rules(_rules(valid_until=cut) + _rules(valid_from=cut))
    assert r.status == OK
    spans = [(a, b) for a, b, *_ in r.roles["nurse"].atom_counts]
    assert (W.start, cut) in spans  # kural sınırı atom sınırıdır
    assert _cap_rules(_rules(valid_until=cut) + _rules(valid_from=cut + M)).status == MISSING  # 1 dk boşluk


def test_rule_ending_exactly_at_window_end_is_accepted():
    assert _cap_rules(_rules(valid_until=W.end)).status == OK
    assert _cap_rules(_rules(valid_until=W.end - M)).status == MISSING


def test_unaligned_window_crossing_hour_boundary():
    odd = Interval(W.start + 7 * M, W.end + 7 * M)  # yerel 09:07-10:07
    r = _cap_rules(_rules(band=(8, 10)), window=odd)
    assert r.status == MISSING and "asgari_personel_kurali_yok:nurse:10:00" in r.reasons
    ok = _cap_rules(_rules(band=(8, 11)), window=odd)
    assert ok.status == OK
    spans = [(a, b) for a, b, *_ in ok.roles["nurse"].atom_counts]
    assert (T0 + 2 * H - 8 * M, T0 + 2 * H) in spans and (T0 + 2 * H, T0 + 2 * H + 7 * M) in spans  # 10:00 sınırı


def test_rules_across_midnight():
    night = sh(1, 1, start=T0 + 12 * H)  # yerel 20:00-08:00
    window = Interval(T0 + 15 * H + 30 * M, T0 + 16 * H + 30 * M)  # yerel 23:30-00:30
    evening, morning = _rules(band=(20, 24)), _rules(band=(0, 8))

    def run(rules):
        return unit_capacity_at(plan([night], rules=rules), T0, STAFF, 1, UNITS, window, TZ)

    assert run(evening + morning).status == OK
    missing = run(evening)
    assert missing.status == MISSING and "asgari_personel_kurali_yok:nurse:00:00" in missing.reasons
    assert run(_rules(band=(20, 8))).status == OK  # gece yarısını aşan tek bant


def test_rule_hours_follow_non_whole_hour_timezone():
    kolkata = ZoneInfo("Asia/Kolkata")  # UTC+05:30: yerel tam saat UTC'de :30
    window = Interval(T0 - 100 * M, T0 - 40 * M)  # UTC 03:20-04:20 = yerel 08:50-09:50
    shift_ = sh(1, 1, start=T0 - 3 * H)

    def run(rules):
        return unit_capacity_at(plan([shift_], rules=rules), T0 - 2 * H, STAFF, 1, UNITS, window, kolkata)

    r = run(_rules(band=(8, 9)))
    assert r.status == MISSING and "asgari_personel_kurali_yok:nurse:09:00" in r.reasons
    assert run(_rules(band=(8, 10))).status == OK


def test_corrupt_cancel_record_not_hidden_by_internal_view():
    """'cancelled' ama cancelled_at yok: görünüm bunu 'iptal henüz bilinmiyor' diye gizlememeli."""
    corrupt = replace(sh(1, 1), status="cancelled")
    r = unit_capacity_at(plan([corrupt, sh(2, 2)]), T, STAFF, 1, UNITS, W, TZ)
    assert r.status == INVALID and any(x.startswith("iptal_alani_tutarsiz:vardiya:1") for x in r.reasons)
    # t'de henüz yayımlanmamış kayıt t'deki kararı etkilemez (gelecek bilgisi yok).
    unpublished = replace(sh(3, 1, unit=2, start=T0 + 13 * H), status="cancelled", planned_at=T0 + H)
    assert unit_capacity_at(plan([sh(1, 1), sh(2, 2), unpublished]), T, STAFF, 1, UNITS, W, TZ).status == OK
