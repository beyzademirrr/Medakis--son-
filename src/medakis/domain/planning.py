"""Planlı kapasite veri sözleşmesi (Aşama 5 / U2, tasarım K-7) ve karar anındaki bilgi görünümü.

Saf alan kodudur: veritabanı yok, çözücü yok, öneri yok. Kurallar:
- Planlı vardiya ile gerçekleşen vardiya ayrı kaynaklardır; plan eksikse gerçekleşenden TAMAMLANMAZ.
- Kapasite her zaman bir karar anıyla istenir; unit_capacity_at görünümü kendisi kurar.
- known_view(t): planned_at > t vardiya görünmez; cancelled_at > t iptal henüz bilinmez (vardiya planlı
  görünür); recorded_at > t uygunsuzluk görünmez; known_at > t kural görünmez. Sonraki değişiklikler
  geçmiş görünümü değiştirmez.
- Asgari personel ve planlama yapılandırması ZORUNLU girdilerdir; kaynaksız değer kabul edilmez,
  eksikse sonuç 'veri_eksik' olur. Tutarsız planda 'gecersiz_plan'.
- Kapasite Aşama 3 kuralıyla hesaplanır (domain.capacity.staff_capacity): somut mola ve uygunsuzlukların
  birleşimi bir kez düşülür; break_minutes özeti ikinci kez düşülmez.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo

from medakis.domain.capacity import Unavailability, merge, staff_capacity
from medakis.domain.models import Staff, StaffShift
from medakis.domain.time import Interval, ensure_utc

SHIFT_STATUSES = ("planned", "confirmed", "cancelled")
ACTIVE_STATUSES = ("planned", "confirmed")
UNAVAILABILITY_KINDS = ("planli", "ani")
ROLES = ("nurse", "supervisor", "support")
OK, MISSING, INVALID = "ok", "veri_eksik", "gecersiz_plan"
GRID_MINUTES = 15


@dataclass(frozen=True)
class PlannedShift:
    id: int
    staff_id: int
    unit_id: int
    role: str
    template_id: int | None
    starts_at: datetime
    ends_at: datetime
    break_minutes: int
    planned_at: datetime  # planın yayımlandığı (bilindiği) an
    status: str  # planned / confirmed / cancelled (kaydın SON durumu)
    cancelled_at: datetime | None
    source: str

    @property
    def interval(self) -> Interval:
        return Interval(self.starts_at, self.ends_at)


@dataclass(frozen=True)
class PlannedUnavailability:
    id: int
    staff_id: int
    starts_at: datetime
    ends_at: datetime
    reason: str  # mola / izin / devamsizlik / egitim / diger
    recorded_at: datetime  # sisteme bildirildiği an
    kind: str  # planli / ani


@dataclass(frozen=True)
class StaffingRule:
    """Asgari güvenli personel: kaynak ve onay olmadan geçersizdir (uydurma değer yok)."""

    unit_id: int
    role: str
    band: tuple[int, int]  # yerel saat [başlangıç, bitiş), ör. (8, 16); gece için (20, 8) desteklenir
    min_staff: int
    source: str
    approved_by: str
    valid_from: datetime
    valid_until: datetime | None
    known_at: datetime

    def applies(self, local_hour: int, at: datetime) -> bool:
        lo, hi = self.band
        in_band = lo <= local_hour < hi if lo < hi else (local_hour >= lo or local_hour < hi)
        return in_band and self.valid_from <= at and (self.valid_until is None or at < self.valid_until)

    def covers(self, atom: Interval, local_tz: tzinfo) -> bool:
        """Kural atomun TAMAMINDA geçerli mi? Atom yerel saat ve geçerlilik sınırlarında bölünmüş olmalı."""
        local_hour = atom.start.astimezone(local_tz).hour  # type: ignore[union-attr]
        return self.applies(local_hour, atom.start) and (self.valid_until is None or atom.end <= self.valid_until)  # type: ignore[operator]


@dataclass(frozen=True)
class FloatRule:
    """Servisler arası destek: kim (personel veya rol), nereye, hangi yetkinlikle, en az ne kadar."""

    role: str
    staff_id: int | None  # None: rolün tamamı
    target_units: tuple[int, ...]
    required_skills: tuple[str, ...]
    min_stay_minutes: int
    valid_from: datetime
    valid_until: datetime | None
    known_at: datetime
    source: str


@dataclass(frozen=True)
class PlanningConfig:
    """Zorunlu yapılandırma; varsayılan değer YOK (tasarım K-8, K-5)."""

    utilization_target: float
    uncertainty_threshold: float
    move_penalty: float
    solver_time_limit_s: float
    source: str

    def problems(self) -> list[str]:
        out = []
        if not (0 < self.utilization_target <= 1) or not math.isfinite(self.utilization_target):
            out.append("kullanim_orani_gecersiz")
        if not (self.uncertainty_threshold > 0) or not math.isfinite(self.uncertainty_threshold):
            out.append("belirsizlik_esigi_gecersiz")
        if not (self.move_penalty >= 0) or not math.isfinite(self.move_penalty):
            out.append("hareket_cezasi_gecersiz")
        if not (self.solver_time_limit_s > 0) or not math.isfinite(self.solver_time_limit_s):
            out.append("cozum_suresi_gecersiz")
        if not self.source.strip():
            out.append("yapilandirma_kaynagi_yok")
        return out


@dataclass(frozen=True)
class PlanSet:
    shifts: tuple[PlannedShift, ...]
    unavailability: tuple[PlannedUnavailability, ...]
    staffing_rules: tuple[StaffingRule, ...] = ()
    float_rules: tuple[FloatRule, ...] = ()
    config: PlanningConfig | None = None
    as_of: datetime | None = None  # None: kayıtların son hali; değer: known_view anı


# ---- Karar anındaki görünüm -----------------------------------------------------------------


def known_view(plan: PlanSet, at: datetime) -> PlanSet:
    """`at` anında bilinen plan. Sonradan bilinen hiçbir kayıt veya durum değişikliği görünmez."""
    at = ensure_utc(at, "Karar anı")
    shifts = []
    for s in plan.shifts:
        if s.planned_at > at:
            continue
        if s.status == "cancelled" and (s.cancelled_at is None or s.cancelled_at > at):
            s = replace(s, status="planned", cancelled_at=None)  # iptal henüz bilinmiyordu
        shifts.append(s)
    unavailability = tuple(u for u in plan.unavailability if u.recorded_at <= at)
    rules = tuple(r for r in plan.staffing_rules if r.known_at <= at)
    floats = tuple(r for r in plan.float_rules if r.known_at <= at)
    return PlanSet(tuple(shifts), unavailability, rules, floats, plan.config, at)


# ---- Doğrulama --------------------------------------------------------------------------------


def plan_problems(plan: PlanSet, staff_roles: dict[int, str], unit_ids: set[int]) -> list[str]:
    """Tutarsız plan: çakışma, ters aralık, rol uyuşmazlığı, iptal alanı tutarsızlığı, bilinmeyen kayıt."""
    out: list[str] = []
    active: dict[int, list[tuple[datetime, datetime, int]]] = {}
    for s in plan.shifts:
        if s.ends_at <= s.starts_at:
            out.append(f"ters_aralik:vardiya:{s.id}")
        if s.status not in SHIFT_STATUSES:
            out.append(f"gecersiz_durum:vardiya:{s.id}")
        if (s.status == "cancelled") != (s.cancelled_at is not None):
            out.append(f"iptal_alani_tutarsiz:vardiya:{s.id}")
        if s.cancelled_at is not None and s.cancelled_at < s.planned_at:
            out.append(f"iptal_plandan_once:vardiya:{s.id}")
        if s.staff_id not in staff_roles:
            out.append(f"bilinmeyen_personel:vardiya:{s.id}")
        elif staff_roles[s.staff_id] != s.role:
            out.append(f"rol_uyusmuyor:vardiya:{s.id}")
        if s.unit_id not in unit_ids:
            out.append(f"bilinmeyen_servis:vardiya:{s.id}")
        if s.role not in ROLES:
            out.append(f"modellenmeyen_rol:vardiya:{s.id}")
        if s.status in ACTIVE_STATUSES and s.ends_at > s.starts_at:
            active.setdefault(s.staff_id, []).append((s.starts_at, s.ends_at, s.id))
    for sid, items in active.items():
        items.sort()
        for (a0, a1, ida), (b0, _b1, idb) in zip(items, items[1:]):
            if b0 < a1:  # aynı personel aynı anda iki vardiyada (aynı veya farklı serviste)
                out.append(f"cakisan_vardiya:personel:{sid}:{ida}/{idb}")
    for u in plan.unavailability:
        if u.ends_at <= u.starts_at:
            out.append(f"ters_aralik:uygunsuzluk:{u.id}")
        if u.kind not in UNAVAILABILITY_KINDS:
            out.append(f"gecersiz_tur:uygunsuzluk:{u.id}")
        if u.staff_id not in staff_roles:
            out.append(f"bilinmeyen_personel:uygunsuzluk:{u.id}")
    for r in plan.staffing_rules:
        if r.min_staff < 0 or not r.source.strip() or not r.approved_by.strip():
            out.append(f"kural_kaynaksiz_veya_gecersiz:{r.unit_id}:{r.role}")
    return out


# ---- Alt zaman dilimleri (atomlar) ve kapasite ------------------------------------------------


def atoms(window: Interval, boundaries: list[datetime]) -> list[Interval]:
    """Pencereyi vardiya/mola/uygunsuzluk sınırları ve 15 dk ızgarasıyla alt dilimlere böler."""
    cuts = {window.start, window.end}
    t = window.start
    while t < window.end:  # type: ignore[operator]
        cuts.add(t)
        t += timedelta(minutes=GRID_MINUTES)
    cuts.update(b for b in boundaries if window.start < b < window.end)  # type: ignore[operator]
    ordered = sorted(cuts)
    return [Interval(a, b) for a, b in zip(ordered, ordered[1:]) if b > a]


def rule_boundaries(window: Interval, rules: tuple[StaffingRule, ...], local_tz: tzinfo) -> list[datetime]:
    """Kural denetimi için ek atom sınırları: pencere içindeki kural başlangıç/bitişleri ve yerel tam saatler.

    Yerel tam saatler, UTC'de 15 dakikanın katı olan anlardan seçilir (bütün gerçek saat dilimi farkları
    15 dakikanın katıdır); böylece +03:00 gibi sabit farklarda da yaz saati geçişlerinde de doğru bölünür.
    """
    cuts = [t for r in rules for t in (r.valid_from, r.valid_until) if t is not None and window.start < t < window.end]  # type: ignore[operator]
    step = timedelta(minutes=GRID_MINUTES)
    epoch = datetime(1970, 1, 1, tzinfo=window.start.tzinfo)  # type: ignore[union-attr]
    t = epoch + ((window.start - epoch) // step + 1) * step  # type: ignore[operator]
    while t < window.end:  # type: ignore[operator]
        local = t.astimezone(local_tz)
        if local.minute == 0 and local.second == 0:
            cuts.append(t)
        t += step
    return cuts


def missing_rule_atoms(plan_view: PlanSet, unit_id: int, role: str, window: Interval, local_tz: tzinfo) -> list[Interval]:
    """Hiçbir geçerli asgari personel kuralının tamamını kapsamadığı alt dilimler (zaman sırasıyla)."""
    rules = tuple(r for r in plan_view.staffing_rules if r.unit_id == unit_id and r.role == role)
    cells = atoms(window, rule_boundaries(window, rules, local_tz))
    return [a for a in cells if not any(r.covers(a, local_tz) for r in rules)]


@dataclass(frozen=True)
class RoleCapacity:
    role: str
    planned_headcount_max: int  # pencerede planlı vardiyası olan farklı kişi
    planned_headcount_min_atom: int  # en zayıf alt dilimde vardiyada ve molada olmayan kişi (planlı)
    available_headcount_min_atom: int  # en zayıf alt dilimde, bilinen uygunsuzluklar sonrası kişi
    planned_minutes: float  # vardiya ∩ pencere − planlı molalar
    available_minutes: float  # vardiya ∩ pencere − (mola ∪ bilinen izin/devamsızlık), bir kez
    atom_counts: tuple[tuple[datetime, datetime, int, int], ...]  # (baş, bit, planlı, kullanılabilir)


@dataclass(frozen=True)
class CapacityResult:
    status: str  # ok / veri_eksik / gecersiz_plan
    reasons: tuple[str, ...]
    unit_id: int
    window: Interval
    as_of: datetime | None
    roles: dict[str, RoleCapacity] = field(default_factory=dict)


def _available_intervals(person: Staff, shifts: list[StaffShift], unav: list[Unavailability], window: Interval) -> list[Interval]:
    return list(staff_capacity(person, shifts, unav, window).available)


def unit_capacity_at(plan: PlanSet, at: datetime, staff: dict[int, Staff], unit_id: int, unit_ids: set[int],
                     window: Interval, local_tz: tzinfo, require_contract: bool = True) -> CapacityResult:  # fmt: skip
    """`at` karar anında bilinen plandan planlı ve kullanılabilir kapasite. Öneri üretmez.

    Görünüm her zaman burada known_view(plan, at) ile kurulur: ham PlanSet verilse de sonradan bilinen kayıt
    sızmaz; `as_of` alanına güvenilmez. `at`'ten ESKİ bir görünüm verilirse (plan.as_of < at) filtrelenmiş
    kayıtlar geri getirilemeyeceği için 'gecersiz_plan' döner. Sonuçtaki as_of daima `at`'tir.

    Tutarsız plan → 'gecersiz_plan'; plan, yapılandırma veya asgari personel kuralı eksik → 'veri_eksik'.
    Her iki durumda da kapasite sayısı DÖNMEZ (güvenli ret). Asgari personel kuralı her alt dilimin (atom)
    tamamını kapsamalıdır. require_contract=False yalnız yapılandırma ve kural denetimini atlar (ör.
    sonradan değerlendirmede at=dönem sonu); planlı vardiyası olmayan servis yine 'veri_eksik'tir.
    Gerçekleşen kayıtlar burada kullanılmaz (bkz. realized_unit_minutes).
    """
    at = ensure_utc(at, "Karar anı")
    if plan.as_of is not None and plan.as_of < at:
        return CapacityResult(INVALID, (f"gorunum_karar_anindan_eski:{plan.as_of.isoformat()}",), unit_id, window, at)
    plan_view = known_view(plan, at)
    reasons: list[str] = []
    roles_of = {s.id: s.role for s in staff.values()}
    # Kayıt düzeyi bütünlük (ör. 'cancelled' ama cancelled_at yok) `at`'te görünen kayıtların HAM halinde
    # denetlenir; known_view bunu "iptal henüz bilinmiyor" diye gizlememeli. Çakışma ise `at`'te bilinen
    # etkin vardiyalar üzerinden (görünümde) denetlenir.
    visible_raw = PlanSet(tuple(s for s in plan.shifts if s.planned_at <= at),
                          tuple(u for u in plan.unavailability if u.recorded_at <= at),
                          tuple(r for r in plan.staffing_rules if r.known_at <= at))  # fmt: skip
    record_level = [p for p in plan_problems(visible_raw, roles_of, unit_ids) if not p.startswith("cakisan_vardiya")]
    problems = list(dict.fromkeys(record_level + plan_problems(plan_view, roles_of, unit_ids)))
    if problems:
        return CapacityResult(INVALID, tuple(problems), unit_id, window, at)
    if unit_id not in unit_ids:
        return CapacityResult(INVALID, (f"bilinmeyen_servis:{unit_id}",), unit_id, window, at)
    shifts = [s for s in plan_view.shifts if s.unit_id == unit_id and s.status in ACTIVE_STATUSES and s.interval.overlaps(window)]
    if not shifts:
        reasons.append("planli_vardiya_yok")  # gerçekleşen vardiyalardan TAMAMLANMAZ
    if require_contract:
        if plan_view.config is None:
            reasons.append("planlama_yapilandirmasi_yok")
        else:
            reasons += plan_view.config.problems()
        for role in ROLES:
            gaps = missing_rule_atoms(plan_view, unit_id, role, window, local_tz)
            if gaps:  # ilk kuralsız alt dilimin yerel başlangıcı
                reasons.append(f"asgari_personel_kurali_yok:{role}:{gaps[0].start.astimezone(local_tz):%H:%M}")  # type: ignore[union-attr]
    if reasons:
        return CapacityResult(MISSING, tuple(sorted(set(reasons))), unit_id, window, at)
    unit_rules = tuple(r for r in plan_view.staffing_rules if r.unit_id == unit_id)
    roles: dict[str, RoleCapacity] = {}
    for role in ROLES:
        own = [s for s in shifts if s.role == role]
        boundaries: list[datetime] = rule_boundaries(window, unit_rules, local_tz)  # U3 atom hizası
        per_person: dict[int, tuple[list[Interval], list[Interval]]] = {}
        for sid in sorted({s.staff_id for s in own}):
            person = staff[sid]
            pshifts = [StaffShift(s.id, s.staff_id, s.unit_id, s.starts_at, s.ends_at, s.break_minutes) for s in own if s.staff_id == sid]
            uall = [Unavailability(u.staff_id, Interval(u.starts_at, u.ends_at), "Mola" if u.reason == "mola" else u.reason)
                    for u in plan_view.unavailability if u.staff_id == sid]  # fmt: skip
            breaks = [u for u in uall if u.reason == "Mola"]
            planned = _available_intervals(person, pshifts, breaks, window)
            available = _available_intervals(person, pshifts, uall, window)
            per_person[sid] = (planned, available)
            for s in pshifts:
                boundaries += [s.starts_at, s.ends_at]
            for u in uall:
                boundaries += [u.interval.start, u.interval.end]  # type: ignore[list-item]
        cells = atoms(window, boundaries)
        counts = []
        for a in cells:
            p = sum(1 for planned, _ in per_person.values() if any(i.covers(a) for i in planned))
            v = sum(1 for _, available in per_person.values() if any(i.covers(a) for i in available))
            counts.append((a.start, a.end, p, v))
        roles[role] = RoleCapacity(
            role=role,
            planned_headcount_max=len(per_person),
            planned_headcount_min_atom=min((c[2] for c in counts), default=0),
            available_headcount_min_atom=min((c[3] for c in counts), default=0),
            planned_minutes=sum(i.minutes() for planned, _ in per_person.values() for i in merge(planned)),
            available_minutes=sum(i.minutes() for _, available in per_person.values() for i in merge(available)),
            atom_counts=tuple(counts),
        )  # fmt: skip
    return CapacityResult(OK, (), unit_id, window, at, roles)


def realized_unit_minutes(staff: dict[int, Staff], realized_shifts: list[StaffShift], realized_unavailability: list[Unavailability],
                          unit_id: int, window: Interval) -> dict[str, float]:  # fmt: skip
    """YALNIZ SONRADAN DEĞERLENDİRME: gerçekleşen vardiyalarla kullanılabilir personel-dakika (rol bazında).

    Karar anında kullanılmaz; known_view bu kayıtları hiç görmez. Planlı kapasiteyle karşılaştırma için.
    """
    out = {role: 0.0 for role in ROLES}
    own = [s for s in realized_shifts if s.unit_id == unit_id]
    for sid in sorted({s.staff_id for s in own}):
        person = staff[sid]
        cap = staff_capacity(person, [s for s in own if s.staff_id == sid], realized_unavailability, window)
        out[person.role] += sum(i.minutes() for i in cap.available)
    return out
