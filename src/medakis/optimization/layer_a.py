"""Aşama 5 / U4: Katman A personel yeniden dağıtım optimizasyonu (OR-Tools CP-SAT).

Tek karar penceresi [t, t+60). Öneri üretir; HİÇBİR ŞEYİ uygulamaz veya kaydetmez (veritabanı yok,
vardiya/görev tablosuna yazma yok). Her öneri bağımsız doğrulayıcıdan (verifier.py) geçmeden yayımlanmaz;
her çıktıda insan onayı zorunludur.

Model kararları (rapor §1, §4):
- x[s,u] ∈ {0,1}: s personeli pencerenin TAMAMINDA u servisindedir (değişim yalnız pencere başında, t).
  Kapasite ve asgari personel yine ATOM bazında sayılır: kişi yalnız gerçekten kullanılabilir olduğu
  atomlarda katkı verir. Böylece kısmi vardiya, mola ve devamsızlık bütün pencereye yayılmaz.
- Pencere içinde başlayan/biten onaylı kilit kişiyi pencerenin tamamında kilit servisinde tutar
  (güvenli taraf: kilit erken bırakılmaz; gerekirse sonuç INFEASIBLE olur, kısıt gevşetilmez).
- Birden fazla serviste vardiyası pencereyle kesişen personel taşınmaz (sabit katkı).
- g = max(0, D − cap), e = max(0, cap − D) EŞİTLİK olarak kurulur (tasarımın g ≥ D − cap, g ≥ 0,
  e ≥ cap − D, e ≥ 0 eşitsizliklerini içerir); böylece her uygulanabilir çözümde gerçek açık/fazladır.
- Sözlük sıralı amaç O1 → O2 → O3 → O4: her aşamanın bulunan değeri sonraki aşamada kısıt olarak
  sabitlenir; ağırlık toplamıyla taklit edilmez.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ortools.sat.python import cp_model

from medakis.domain.capacity import Unavailability, clip, intersect, merge, staff_capacity, total_minutes
from medakis.domain.models import StaffShift
from medakis.domain.planning import ACTIVE_STATUSES, ROLES, FloatRule, atoms, known_view, rule_boundaries
from medakis.domain.rules import skill_valid_throughout
from medakis.domain.service_calendar import CLOSED, CONFLICT, OPEN, PARTIAL, calendar_state
from medakis.domain.service_calendar import UNKNOWN as CAL_UNKNOWN  # çözücü UNKNOWN durumuyla karışmasın
from medakis.domain.time import Interval, ensure_utc
from medakis.optimization import verifier
from medakis.optimization.comparison import GECERSIZ_PLAN, INCELEME, SYNTHETIC_NOTICE, VERI_EKSIK, atom_requirement
from medakis.optimization.layer_a_contracts import (
    GRID_MINUTES,
    SCALE_O2,
    WINDOW_MINUTES,
    LayerAInputs,
    MoveLock,
    UnitSkillRequirement,
    demand_staff_minutes,
)

OPTIMAL, FEASIBLE, INFEASIBLE, UNKNOWN, MODEL_INVALID = "OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"
REVIEW = "inceleme_gerekli"
# K1: servis-rol dondurma politikası. "kapali" = küresel ret (U4/U5 davranışı); "iki_yonlu" = yalnız güvenli
# olduğu kanıtlanan inceleme durumlarında sorunlu servis-rol dondurulur, diğerleri çözülür (kısmi öneri).
FREEZE_OFF, FREEZE_TWO_WAY = "kapali", "iki_yonlu"
# Varsayılan küresel ret. İki yönlü dondurma klinik onay olmadan etkin değildir: yalnız açıkça belirtilen deneysel
# modda (test / gölge değerlendirme) çalışır ve o modda hiçbir çözüm yayımlanabilir sayılmaz (yalnız ölçüm için
# doğrulanır). Gölge kanıtı: docs/veri/asama5_guvenli_golge.json.
DEFAULT_FREEZE_POLICY = FREEZE_OFF
EXPERIMENTAL_MODES = ("test", "golge_degerlendirme")
# Dondurulabilir neden kodları: yalnız U1 belirsizlik ve dağılım dışı uyarıları (+ kısmi açık pencere, servis düzeyinde).
FREEZABLE_U1 = ("yuksek_goreli_tampon", "dagilim_disi", "gorulmemis_kategori", "toplam_rol_tutarsiz", "tolerans_yok", "tampon_yok")
CLOSED_DEMAND_EPSILON = 1e-9
STAGES = ("O1_karsilanmayan_talep", "O2_denge", "O3_hareket", "O4_fazla_kapasite")


# ---- Ön koşullar (güvenli ret) --------------------------------------------------------------------


def freeze_scope(rows: list[Mapping[str, Any]], raw: Mapping[int, str]) -> tuple[dict[tuple[int, str], tuple[str, ...]], list[str]]:
    """İki yönlü dondurma kapsamı: (dondurulan servis-rol → nedenler, dondurulamayan inceleme nedenleri).

    Dondurulur: durumu yalnız `inceleme_gerekli` olan ve BÜTÜN nedenleri FREEZABLE_U1 olan satır; ham takvime göre
    kısmen açık servisin bütün rolleri. Dondurulmaz (küresel ret kalır): bilinen asgari ihlalli satır (güvenli bir
    giriş gerekebilirdi ama dondurma bunu yasaklar), başka her inceleme nedeni (geçersiz tahmin, kapalı serviste
    talep veya vardiya…). veri_eksik/gecersiz_plan zaten MODEL_INVALID'dir.
    """
    frozen: dict[tuple[int, str], tuple[str, ...]] = {}
    blocked: list[str] = []
    by_unit: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_unit.setdefault(row["servis_id"], []).append(row)
        states = list(row.get("durumlar") or [row.get("durum")])
        if INCELEME not in states:
            continue
        tag = f"{row['servis_id']}:{row['rol']}"
        if "asgari_personel_ihlali" in states:
            blocked.append(f"dondurma_guvenli_degil:bilinen_asgari_ihlal:{tag}")
        elif set(states) - {INCELEME}:
            blocked.append(f"u3_inceleme_gerekli:{tag}")
        elif not row.get("nedenler") or not all(c.startswith(FREEZABLE_U1) for c in row["nedenler"]):
            blocked.append(f"u3_inceleme_gerekli:{tag}")
        else:
            frozen[(row["servis_id"], row["rol"])] = tuple(row["nedenler"])
    for u, state in sorted(raw.items()):
        if state != PARTIAL:
            continue
        unit_rows = by_unit.get(u, [])
        if any("asgari_personel_ihlali" in (r.get("durumlar") or []) for r in unit_rows):
            blocked.append(f"dondurma_guvenli_degil:bilinen_asgari_ihlal:{u}:kismi_acik_pencere")
            continue
        for role in ROLES:
            frozen[(u, role)] = tuple(sorted(set(frozen.get((u, role), ())) | {"kismi_acik_pencere"}))
    return frozen, sorted(set(blocked))


def preconditions(inp: LayerAInputs, freeze_policy: str = DEFAULT_FREEZE_POLICY) -> tuple[list[str], list[str]]:
    """(geçersiz/eksik → MODEL_INVALID nedenleri, inceleme nedenleri). Boşsa çözüm denenebilir."""
    invalid: list[str] = []
    review: list[str] = []
    if inp.at.tzinfo is None:
        return ["karar_ani_saat_dilimsiz"], []
    at = ensure_utc(inp.at, "Karar anı")
    end = at + timedelta(minutes=WINDOW_MINUTES)
    if at.minute % GRID_MINUTES or at.second or at.microsecond:
        invalid.append("karar_ani_izgarada_degil")
    unit_ids = set(inp.units)
    # Ham ServiceCalendar kayıtlarından t için bağımsız durum (U3'ün beyanı tek başına kabul edilmez).
    raw = {u: calendar_state(inp.calendars, u, Interval(at, end), at).state for u in unit_ids}
    for u in sorted(unit_ids):
        if raw[u] in (CAL_UNKNOWN, CONFLICT):
            invalid.append(f"calisma_takvimi_dogrulanamadi:{u}:{raw[u]}")
        elif raw[u] == PARTIAL and freeze_policy != FREEZE_TWO_WAY:
            review.append(f"kismi_acik_pencere:{u}")
    seen: dict[tuple[int, str], Mapping[str, Any]] = {}
    for row in inp.u3_rows:
        key = (row.get("servis_id"), row.get("rol"))
        if key[0] not in unit_ids or key[1] not in ROLES:
            invalid.append(f"bilinmeyen_servis_rol:{key[0]}:{key[1]}")
            continue
        if key in seen:
            invalid.append(f"u3_satiri_cift:{key[0]}:{key[1]}")
        seen[key] = row
        tag = f"{key[0]}:{key[1]}"
        if row.get("karar_ani") != at or row.get("ufuk_dk") != WINDOW_MINUTES:
            invalid.append(f"u3_karar_ani_uyusmuyor:{tag}")
        states = list(row.get("durumlar") or [row.get("durum")])
        if GECERSIZ_PLAN in states:
            invalid.append(f"u3_gecersiz_plan:{tag}")
        if VERI_EKSIK in states:
            invalid.append(f"u3_veri_eksik:{tag}")
        if INCELEME in states and freeze_policy != FREEZE_TWO_WAY:
            review.append(f"u3_inceleme_gerekli:{tag}")
        cal = row.get("takvim_durumu")
        if cal != raw[key[0]]:
            invalid.append(f"takvim_u3_uyusmuyor:{key[0]}:{cal}!={raw[key[0]]}")
        d_raw = row.get("tamponlu_talep_dk")
        if d_raw is not None and (not math.isfinite(float(d_raw)) or float(d_raw) < 0):
            invalid.append(f"talep_gecersiz:{tag}")
        if raw[key[0]] == OPEN and (d_raw is None or row.get("kullanim_orani") is None
                                    or not row.get("kullanim_orani_kaynagi")):  # fmt: skip
            invalid.append(f"talep_veya_kullanim_orani_yok:{tag}")
        point = row.get("nokta_tahmin_dk")
        if raw[key[0]] == CLOSED and (d_raw is None or float(d_raw) > CLOSED_DEMAND_EPSILON or point is None or float(point) > 0):
            invalid.append(f"kapali_serviste_talep:{tag}")  # K2: yalnız ham takvimle kapalı ve ŷ = 0, D = 0 olan kabul edilir
    for u in sorted(unit_ids):
        for r in ROLES:
            if (u, r) not in seen:
                invalid.append(f"u3_satiri_yok:{u}:{r}")
    if freeze_policy == FREEZE_TWO_WAY:
        review += freeze_scope(list(seen.values()), raw)[1]
    elif freeze_policy != FREEZE_OFF:
        invalid.append(f"dondurma_politikasi_gecersiz:{freeze_policy}")
    cfg = inp.config
    if cfg is None:
        invalid.append("optimizasyon_yapilandirmasi_yok")
    else:
        if cfg.known_at > at:
            invalid.append("optimizasyon_yapilandirmasi_karar_aninda_bilinmiyor")
        invalid += cfg.problems(unit_ids)
    view = known_view(inp.plan, at) if (inp.plan.as_of is None or inp.plan.as_of >= at) else None
    if view is None:
        invalid.append("plan_gorunumu_karar_anindan_eski")
        return sorted(set(invalid)), sorted(set(review))
    reqs = [q for q in inp.unit_skill_requirements if q.known_at <= at]
    req_keys: dict[tuple[int, str], UnitSkillRequirement] = {}
    for q in reqs:
        if (q.unit_id, q.role) in req_keys:
            invalid.append(f"yetkinlik_kurali_cift:{q.unit_id}:{q.role}")
        req_keys[(q.unit_id, q.role)] = q
        if q.unit_id not in unit_ids or q.role not in ROLES:
            invalid.append(f"bilinmeyen_servis_rol:{q.unit_id}:{q.role}")
        if not q.source.strip() or not q.approved_by.strip():
            invalid.append(f"yetkinlik_kurali_kaynaksiz:{q.unit_id}:{q.role}")
        if set(q.required_skills) - inp.skill_catalog:
            invalid.append(f"bilinmeyen_yetkinlik:{q.unit_id}:{q.role}")
    for fr in view.float_rules:
        tag = f"{fr.role}:{fr.staff_id}"
        if not fr.source.strip():
            invalid.append(f"destek_kurali_kaynaksiz:{tag}")
        if fr.role not in ROLES or set(fr.target_units) - unit_ids or not fr.target_units:
            invalid.append(f"destek_kurali_bilinmeyen_servis_rol:{tag}")
        if fr.staff_id is not None and (fr.staff_id not in inp.staff or inp.staff[fr.staff_id].role != fr.role):
            invalid.append(f"destek_kurali_personel_gecersiz:{tag}")
        if set(fr.required_skills) - inp.skill_catalog:
            invalid.append(f"bilinmeyen_yetkinlik:{tag}")
        if fr.min_stay_minutes < 0:
            invalid.append(f"asgari_kalis_gecersiz:{tag}")
        for u in fr.target_units:
            if (u, fr.role) not in req_keys:
                invalid.append(f"yetkinlik_kurali_yok:{u}:{fr.role}")
    for sk in inp.skills:
        if sk.skill not in inp.skill_catalog:
            invalid.append(f"bilinmeyen_yetkinlik:personel:{sk.staff_id}")
        if sk.valid_until is not None and sk.valid_until <= sk.valid_from:
            invalid.append(f"gecersiz_yetkinlik_araligi:{sk.staff_id}:{sk.skill}")  # istisna değil, kodlu ret
    active = [lk for lk in inp.locks if lk.active_at(at, end)]
    per_staff: dict[int, int] = {}
    for lk in active:
        per_staff[lk.staff_id] = per_staff.get(lk.staff_id, 0) + 1
        if lk.staff_id not in inp.staff:
            invalid.append(f"kilit_bilinmeyen_personel:{lk.staff_id}")
        if lk.target_unit_id not in unit_ids:
            invalid.append(f"kilit_bilinmeyen_servis:{lk.staff_id}")
        if lk.lock_until <= lk.starts_at or not lk.approval_id.strip():
            invalid.append(f"kilit_gecersiz:{lk.staff_id}")
        state = calendar_state(inp.calendars, lk.target_unit_id, Interval(at, end), at).state
        if state != OPEN:
            review.append(f"kilit_hedefi_acik_degil:{lk.staff_id}:{lk.target_unit_id}")
    invalid += [f"cakisan_kilit:{sid}" for sid, n in sorted(per_staff.items()) if n > 1]
    return sorted(set(invalid)), sorted(set(review))


# ---- Hazırlık: kişiler, atomlar, uygunluk -----------------------------------------------------------


@dataclass
class Person:
    staff_id: int
    role: str
    by_unit: dict[int, list[Interval]]  # servis → pencere içindeki kullanılabilir aralıklar (vardiya servisine göre)
    shift_end: datetime
    home: int | None  # tek servis; birden çok servisse None (sabit)
    lock: MoveLock | None = None
    allowed: dict[int, FloatRule | None] = field(default_factory=dict)  # servis → izin veren kural (ev/kilit: None)

    @property
    def available(self) -> list[Interval]:
        return merge([i for v in self.by_unit.values() for i in v])

    @property
    def movable(self) -> bool:
        return self.home is not None

    @property
    def baseline(self) -> int | None:
        """Son onaylı yerleşim: etkin kilit hedefi, yoksa planlı servis."""
        return self.lock.target_unit_id if self.lock is not None else self.home


@dataclass
class Prepared:
    at: datetime
    window: Interval
    people: dict[int, Person]
    cells: list[Interval]
    open_units: set[int]
    demand: dict[tuple[int, str], int]  # D (personel-dk, yukarı yuvarlanmış)
    demand_raw: dict[tuple[int, str], tuple[float, float | None]]  # (ŷ+B, u)
    requirement: dict[tuple[int, str, int], int]  # (servis, rol, atom indeksi) → asgari kişi
    invalid: list[str]
    review: list[str]
    frozen: dict[tuple[int, str], tuple[str, ...]] = field(default_factory=dict)  # K1 dondurulan servis-rol
    open_cells: dict[int, tuple[int, ...]] = field(default_factory=dict)  # H7 denetlenen atomlar


def select_float_rule(inp: LayerAInputs, floats: list[FloatRule], reqs: Mapping[tuple[int, str], UnitSkillRequirement],
                      p: Person, unit: int, window: Interval, lock: MoveLock | None = None) -> tuple[FloatRule | None, str]:  # fmt: skip
    """Ev servisi dışındaki `unit` için izin veren ilk destek kuralı (sıralı) ya da ret nedeni.

    Taşıma ve onaylı kilit AYNI güvenlik koşullarına tabidir (kilit yerleşimi zorunlu kılar, muafiyet vermez):
    kural t'de bilinir ve pencere boyunca geçerlidir; servis-rol yetkinlik eşlemesi vardır; bütün yetkinlikler
    pencerenin tamamında geçerlidir. Asgari kalış: yeni taşımada kalan vardiya ≥ min_stay; kilitte kilit süresi
    ≥ min_stay (ardışık çalıştırmada kalan vardiya doğal olarak kısalır, kilit bu yüzden yanlışlıkla reddedilmez).
    """
    at, end = window.start, window.end
    if (unit, p.role) not in reqs:
        return None, "yetkinlik_kurali_yok"
    reason = "yetki_yok"
    for fr in floats:
        if fr.role != p.role or fr.staff_id not in (None, p.staff_id) or unit not in fr.target_units:
            continue
        if fr.valid_from > at or (fr.valid_until is not None and fr.valid_until < end):  # type: ignore[operator]
            continue
        stay = timedelta(minutes=fr.min_stay_minutes)
        if (lock.lock_until - lock.starts_at < stay) if lock is not None else (p.shift_end - at < stay):  # type: ignore[operator]
            reason = "asgari_kalis_saglanmiyor"
            continue
        need = set(fr.required_skills) | set(reqs[(unit, p.role)].required_skills)
        if all(_skill_ok(inp, p.staff_id, k, window) for k in sorted(need)):
            return fr, ""
        reason = "yetkinlik_eksik"
    return None, reason


def _skill_ok(inp: LayerAInputs, staff_id: int, skill: str, window: Interval) -> bool:
    """Yetkinlik pencerenin tamamında geçerli mi? Ters/sıfır aralıklı kayıt (ön koşulda zaten MODEL_INVALID)
    burada da Interval hatasına yol açmaz ve hiçbir zaman geçerli yetkinlik sayılmaz."""
    validities = [Interval(s.valid_from, s.valid_until) for s in inp.skills if s.staff_id == staff_id and s.skill == skill
                  and (s.valid_until is None or s.valid_until > s.valid_from)]  # fmt: skip
    return skill_valid_throughout(validities, window)


def prepare(inp: LayerAInputs, frozen: Mapping[tuple[int, str], tuple[str, ...]] | None = None) -> Prepared:
    at = ensure_utc(inp.at, "Karar anı")
    window = Interval(at, at + timedelta(minutes=WINDOW_MINUTES))
    view = known_view(inp.plan, at)
    invalid: list[str] = []
    review: list[str] = []
    shifts_of: dict[int, list] = {}
    for s in view.shifts:
        if s.status in ACTIVE_STATUSES and s.interval.overlaps(window):
            shifts_of.setdefault(s.staff_id, []).append(s)
    unav_of: dict[int, list[Unavailability]] = {}
    for u in view.unavailability:
        reason = "Mola" if u.reason == "mola" else u.reason
        unav_of.setdefault(u.staff_id, []).append(Unavailability(u.staff_id, Interval(u.starts_at, u.ends_at), reason))
    people: dict[int, Person] = {}
    for sid in sorted(shifts_of):
        person = inp.staff[sid]
        own = shifts_of[sid]
        sshifts = [StaffShift(s.id, s.staff_id, s.unit_id, s.starts_at, s.ends_at, s.break_minutes) for s in own]
        avail = list(staff_capacity(person, sshifts, unav_of.get(sid, []), window).available)
        by_unit = {}
        for unit in sorted({s.unit_id for s in own}):
            span = merge(clip([s.interval for s in own if s.unit_id == unit], window))
            by_unit[unit] = intersect(avail, span)
        units_here = sorted(by_unit)
        people[sid] = Person(sid, person.role, by_unit, max(s.ends_at for s in own), units_here[0] if len(units_here) == 1 else None)
    cal_state = {u: calendar_state(inp.calendars, u, window, at) for u in inp.units}
    open_units = {u for u, c in cal_state.items() if c.state == OPEN}
    end = window.end
    for lk in inp.locks:
        if lk.active_at(at, end) and lk.staff_id in people:  # type: ignore[arg-type]
            p = people[lk.staff_id]
            if not p.movable:
                review.append(f"kilitli_personel_birden_cok_serviste:{lk.staff_id}")
            elif total_minutes(p.available) == 0:
                review.append(f"kilitli_personel_kullanilamiyor:{lk.staff_id}")  # kilit sessizce kaldırılmaz
            p.lock = lk
    reqs = {(q.unit_id, q.role): q for q in inp.unit_skill_requirements if q.known_at <= at}
    floats = sorted(view.float_rules, key=float_rule_order)
    frozen = dict(frozen or {})
    frozen_pairs = set(frozen)
    uncertain_units = {u for u, _ in frozen_pairs}
    for p in people.values():
        if not p.movable:
            continue
        if p.lock is not None:  # kilit yerleşimi zorunlu kılar; güvenlik koşulları ayrıca aranır
            target = p.lock.target_unit_id
            fr = None
            if target != p.home:
                fr, why = select_float_rule(inp, floats, reqs, p, target, window, lock=p.lock)
                if fr is None:
                    review.append(f"kilit_guvenlik_kosulu_saglanmiyor:{p.staff_id}:{why}")
            if p.lock.lock_until > p.shift_end:  # type: ignore[operator]
                review.append(f"kilit_guvenlik_kosulu_saglanmiyor:{p.staff_id}:vardiya_disina_tasiyor")
            p.allowed = {target: fr}
            continue
        p.allowed = {p.home: None}  # type: ignore[dict-item]
        if total_minutes(p.available) == 0:
            continue  # kullanılamayan kişi taşınmaz
        if (p.home, p.role) in frozen_pairs or p.home in uncertain_units:
            continue  # K1: dondurulan çiftten ve belirsiz servisten (hiçbir rolde) çıkış yok
        for u in sorted(inp.units):
            if u == p.home or u not in open_units or p.home not in open_units or (u, p.role) in frozen_pairs:
                continue  # K1: dondurulan çifte giriş yok
            fr, _ = select_float_rule(inp, floats, reqs, p, u, window)
            if fr is not None:
                p.allowed[u] = fr
    bounds: list[datetime] = []
    for p in people.values():
        for ivs in p.by_unit.values():
            bounds += [t for i in ivs for t in (i.start, i.end)]  # type: ignore[misc]
    for own in shifts_of.values():  # yalnız etkin ve pencereyle kesişen vardiyalar
        bounds += [t for s in own for t in (s.starts_at, s.ends_at)]
    for c in cal_state.values():
        bounds += [t for i in c.open_parts for t in (i.start, i.end)]  # type: ignore[misc]
    bounds += [t for lk in inp.locks if lk.active_at(at, end) for t in (lk.starts_at, lk.lock_until)]  # type: ignore[arg-type]
    bounds += rule_boundaries(window, tuple(view.staffing_rules), inp.local_tz)
    cells = atoms(window, bounds)
    # H7 açık atomlarda U3 etiketinden bağımsız yeniden hesaplanır: açık servis bütün atomlar, kısmi açık servis
    # yalnız ham takvimin açık bölümündeki atomlar (atom sınırları açık bölüm sınırlarını içerir).
    open_cells: dict[int, tuple[int, ...]] = {u: tuple(range(len(cells))) for u in open_units}
    for u, c in cal_state.items():
        if c.state == PARTIAL:
            open_cells[u] = tuple(k for k, a in enumerate(cells) if any(op.covers(a) for op in c.open_parts))
    requirement: dict[tuple[int, str, int], int] = {}
    for u in sorted(open_cells):
        for r in ROLES:
            for k in open_cells[u]:
                a = cells[k]
                req, _ = atom_requirement(view.staffing_rules, u, r, a, inp.local_tz)
                if req is None:
                    invalid.append(f"asgari_kural_yok:{u}:{r}:{a.start.astimezone(inp.local_tz):%H:%M}")  # type: ignore[union-attr]
                elif req > 0:
                    requirement[(u, r, k)] = req
    demand: dict[tuple[int, str], int] = {}
    demand_raw: dict[tuple[int, str], tuple[float, float | None]] = {}
    for row in inp.u3_rows:
        key = (row["servis_id"], row["rol"])
        d_raw, util = row.get("tamponlu_talep_dk"), row.get("kullanim_orani")
        if key[0] in open_units:
            demand[key] = demand_staff_minutes(float(d_raw), float(util))
            demand_raw[key] = (float(d_raw), float(util))
        elif cal_state[key[0]].state == CLOSED and d_raw is not None and float(d_raw) <= CLOSED_DEMAND_EPSILON:
            demand[key] = 0  # yalnız ham takvimle doğrulanmış kapalı ve talebi 0
            demand_raw[key] = (float(d_raw), None)
        elif cal_state[key[0]].state == PARTIAL and key in frozen_pairs:
            demand[key] = 0  # K1: kısmi açık ve dondurulmuş; talep güvenilir değil, amaçlara girmez
            demand_raw[key] = (float(d_raw or 0.0), None)
        else:
            invalid.append(f"talep_sifirlanamaz:{key[0]}:{key[1]}")
            demand[key], demand_raw[key] = 0, (float(d_raw or 0.0), None)
    return Prepared(at, window, people, cells, open_units, demand, demand_raw, requirement, sorted(set(invalid)), sorted(set(review)),
                    frozen, open_cells)  # fmt: skip


def person_minutes(intervals: list[Interval]) -> int:
    """c[s]: kullanılabilir dakika, AŞAĞI yuvarlanmış tam sayı."""
    return math.floor(total_minutes(intervals) + 1e-9)


def _avail_in(intervals: list[Interval], cell: Interval) -> bool:
    return any(i.covers(cell) for i in intervals)


# ---- Çözücü -------------------------------------------------------------------------------------------


@dataclass
class StageRun:
    status: str
    objective: float | None
    bound: float | None
    wall_s: float
    solver: cp_model.CpSolver


def _run_solver(model: cp_model.CpModel, time_limit_s: float, overrides: Mapping[str, Any] | None) -> StageRun:
    """Tek aşama çözümü. Belirlenimcilik için tek işçi ve sabit tohum (testlerde bu işlev sarılabilir)."""
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    for key, value in (overrides or {}).items():
        setattr(solver.parameters, key, value)
    status = solver.status_name(solver.solve(model))
    found = status in (OPTIMAL, FEASIBLE)
    return StageRun(status, solver.objective_value if found else None, solver.best_objective_bound if found else None,
                    solver.wall_time, solver)  # fmt: skip


def _canonical_hash(content: Mapping[str, Any]) -> str:
    raw = json.dumps(content, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def solution_hash(solution: Mapping[str, Any]) -> str:
    return _canonical_hash({k: v for k, v in solution.items() if k != "ozet_sha256"})


def experimental_gate(freeze_policy: str, experimental_mode: str | None) -> list[str]:
    """Deneysel mod kapısı: iki yönlü dondurma yalnız açık deneysel modla; bilinmeyen mod reddedilir."""
    if experimental_mode is not None and experimental_mode not in EXPERIMENTAL_MODES:
        return [f"deneysel_mod_gecersiz:{experimental_mode}"]
    if freeze_policy == FREEZE_TWO_WAY and experimental_mode is None:
        return ["deneysel_dondurma_acik_mod_gerekli:klinik_onay_yok"]
    return []


def optimize(inp: LayerAInputs, solver_overrides: Mapping[str, Any] | None = None,
             freeze_policy: str = DEFAULT_FREEZE_POLICY, experimental_mode: str | None = None) -> dict[str, Any]:  # fmt: skip
    """Katman A çalıştırması. Öneri yalnız çözücü OPTIMAL/FEASIBLE ve doğrulayıcı geçerse yayımlanabilir."""
    at = ensure_utc(inp.at, "Karar anı") if inp.at.tzinfo is not None else inp.at
    window_end = at + timedelta(minutes=WINDOW_MINUTES)
    out: dict[str, Any] = {
        "karar_ani": at, "pencere_baslangic": at, "pencere_bitis": window_end, "durum": None,
        "cozucu_asamalari": [], "sozluk_sirali_tam": False, "nedenler": [], "uyarilar": [],
        "dogrulama": {"calisti": False, "gecti": False, "ihlaller": []}, "oneri_yayinlanabilir": False,
        "tasimalar": [], "kilitli_yerlesimler": [], "servis_rol": [], "cozum": None, "tani": [], "cozumsuzluk_turu": None,
        "dondurma_politikasi": freeze_policy, "dondurulan_servis_roller": [], "oneri_kapsami": None,
        "deneysel_mod": experimental_mode, "deneysel_sonuc": None, "sentetik_uyari": SYNTHETIC_NOTICE, "insan_onayi_gerekli": True,
    }  # fmt: skip
    gate = experimental_gate(freeze_policy, experimental_mode)
    if gate:
        out["durum"] = MODEL_INVALID
        out["nedenler"] = gate
        return out
    if experimental_mode is not None:
        out["uyarilar"].append(f"deneysel_mod:{experimental_mode}:klinik_onay_yok:yayin_kapali")
    invalid, review = preconditions(inp, freeze_policy)
    if invalid or review:
        out["durum"] = MODEL_INVALID if invalid else REVIEW
        out["nedenler"] = invalid + review
        return out
    frozen: dict[tuple[int, str], tuple[str, ...]] = {}
    if freeze_policy == FREEZE_TWO_WAY:
        raw = {u: calendar_state(inp.calendars, u, Interval(at, window_end), at).state for u in inp.units}
        frozen = freeze_scope(list(inp.u3_rows), raw)[0]
    out["dondurulan_servis_roller"] = [{"servis_id": u, "rol": r, "nedenler": list(why)} for (u, r), why in sorted(frozen.items())]
    prep = prepare(inp, frozen)
    if prep.invalid or prep.review:
        out["durum"] = MODEL_INVALID if prep.invalid else REVIEW
        out["nedenler"] = prep.invalid + prep.review
        return out
    cfg = inp.config
    assert cfg is not None
    people = prep.people
    open_pairs = [(u, r) for u in sorted(prep.open_units) for r in ROLES]
    if frozen and all(pair in frozen for pair in open_pairs):
        out["durum"] = REVIEW  # dondurmadan sonra çözülecek açık servis-rol kalmadı
        out["nedenler"] = ["tum_servis_roller_donduruldu"]
        return out
    structural = structural_shortfalls(prep, people)
    if structural:  # K3: olası en fazla kişi bile asgariye yetmiyor → vardiya planı sorunu (çözücüye gerek yok)
        out["durum"] = INFEASIBLE
        out["cozumsuzluk_turu"] = "yapisal_plan_eksigi"
        out["tani"] = structural
        out["nedenler"] = ["vardiya_plani_geri_bildirimi"] + sorted(
            {f"yetkili_klinik_karar_gerekli:asgari_personel:{c.split(':')[1]}:{c.split(':')[2]}" for c in structural})
        return out
    model = cp_model.CpModel()
    pairs = [(u, r) for u in sorted(inp.units) for r in ROLES]
    x = {(s, u): model.new_bool_var(f"x_{s}_{u}") for s, p in people.items() if p.movable for u in sorted(p.allowed)}
    for s, p in people.items():
        if p.movable:
            model.add_exactly_one([x[(s, u)] for u in sorted(p.allowed)])  # H1 (+ H8: kilitli kişide tek seçenek)
    ub = WINDOW_MINUTES * max(1, len(people))
    cap, y, g, e, over = {}, {}, {}, {}, {}
    for u, r in pairs:
        movers = [(s, p) for s, p in people.items() if p.movable and p.role == r and (s, u) in x]
        fixed = [p for p in people.values() if not p.movable and p.role == r and u in p.by_unit]
        fixed_cap = sum(person_minutes(p.by_unit[u]) for p in fixed)
        cap[(u, r)] = model.new_int_var(0, ub, f"cap_{u}_{r}")
        model.add(cap[(u, r)] == sum(person_minutes(p.available) * x[(s, u)] for s, p in movers) + fixed_cap)
        y[(u, r)] = model.new_int_var(0, len(people), f"y_{u}_{r}")
        model.add(y[(u, r)] == sum(x[(s, u)] for s, _ in movers) + len(fixed))
        d = prep.demand[(u, r)]
        g[(u, r)] = model.new_int_var(0, max(d, 0), f"g_{u}_{r}")
        e[(u, r)] = model.new_int_var(0, ub, f"e_{u}_{r}")
        model.add_max_equality(g[(u, r)], [0, d - cap[(u, r)]])  # H10: g ≥ D − cap, g ≥ 0 (eşitlikle)
        model.add_max_equality(e[(u, r)], [0, cap[(u, r)] - d])  # H10: e ≥ cap − D, e ≥ 0
        over[(u, r)] = model.new_int_var(0, ub, f"o_{u}_{r}")
        model.add_max_equality(over[(u, r)], [0, e[(u, r)] - cfg.surplus_tolerance[u]])
        if u in prep.open_cells:  # H7 (+ H9): her açık atomda asgari kişi (kısmi açık serviste açık bölüm)
            for k in prep.open_cells[u]:
                cell = prep.cells[k]
                req = prep.requirement.get((u, r, k), 0)
                if req <= 0:
                    continue
                present = sum(x[(s, u)] for s, p in movers if _avail_in(p.available, cell))
                present_fixed = sum(1 for p in fixed if _avail_in(p.by_unit[u], cell))
                model.add(present + present_fixed >= req)
    moved = {s: model.new_bool_var(f"m_{s}") for s, p in people.items() if p.movable}
    for s, mv in moved.items():
        model.add(mv == 1 - x[(s, people[s].baseline)])  # type: ignore[index]
    z = model.new_int_var(0, SCALE_O2, "z_denge")
    scored = [pair for pair in pairs if pair not in frozen]  # K1: dondurulan çiftin belirsiz D'si amaçlara girmez
    for (u, r), d in prep.demand.items():
        if d > 0 and (u, r) not in frozen:  # O2: göreli açık; D = 0 olan çift g = 0 olduğundan ölçüye girmez
            model.add(SCALE_O2 * g[(u, r)] <= d * z)
    objectives = {
        STAGES[0]: sum(cfg.weights[u] * g[(u, r)] for u, r in scored),
        STAGES[1]: z,
        STAGES[2]: sum(moved.values()),
        STAGES[3]: sum(over[pair] for pair in scored),
    }
    variables = {**{f"x:{s}:{u}": v for (s, u), v in x.items()}, **{f"m:{s}": v for s, v in moved.items()}}
    values: dict[str, int] | None = None
    complete = True
    for name in STAGES:
        expr = objectives[name]
        model.minimize(expr)
        model.clear_hints()
        if values is not None:
            for key, var in variables.items():
                model.add_hint(var, values[key])
        run = _run_solver(model, cfg.time_limit_s, solver_overrides)
        found = run.status in (OPTIMAL, FEASIBLE)
        out["cozucu_asamalari"].append({"asama": name, "durum": run.status, "amac_degeri": run.objective,
                                        "alt_sinir": run.bound, "sure_sn": round(run.wall_s, 4),
                                        "optimal": run.status == OPTIMAL})  # fmt: skip
        if not found:
            complete = False
            if values is None:
                out["durum"] = run.status if run.status in (INFEASIBLE, UNKNOWN, MODEL_INVALID) else UNKNOWN
                if run.status == INFEASIBLE:
                    out["tani"] = diagnose(prep, people)
                    out["cozumsuzluk_turu"] = "kisit_birlesimi"
                out["nedenler"] = [f"cozucu:{name}:{run.status}"]
                return out
            out["uyarilar"].append(f"sozluk_sirasi_tamamlanamadi:{name}:{run.status}")
            break
        if run.status != OPTIMAL:
            complete = False
            out["uyarilar"].append(f"asama_optimal_degil:{name}")
        values = {key: int(run.solver.value(var)) for key, var in variables.items()}
        values.update({f"stage:{name}": int(round(run.objective))})  # type: ignore[arg-type]
        model.add(expr <= int(round(run.objective)))  # type: ignore[arg-type]  # bu aşamanın değeri sabitlenir
    assert values is not None
    out["durum"] = OPTIMAL if complete else FEASIBLE
    out["sozluk_sirali_tam"] = complete
    assignment = {s: next(u for u in sorted(p.allowed) if values[f"x:{s}:{u}"] == 1) for s, p in people.items() if p.movable}
    solution = build_solution(inp, prep, assignment, freeze_policy, experimental_mode)
    check = verifier.verify(inp, solution)
    violations = list(check.violations)
    for stage in out["cozucu_asamalari"]:  # OPTIMAL aşamanın değeri nihai çözümün gerçek değerine eşit olmalı
        final = solution["asama_degerleri"][stage["asama"]]
        if stage["optimal"] and stage["amac_degeri"] is not None and int(round(stage["amac_degeri"])) != final:
            violations.append(f"asama_degeri_nihai_cozumle_uyusmuyor:{stage['asama']}")
    check = verifier.VerificationResult(not violations, sorted(set(violations)))
    out["dogrulama"] = {"calisti": True, "gecti": check.passed, "ihlaller": check.violations}
    if not check.passed:
        out["nedenler"] = ["dogrulayici_reddetti"]
        return out
    out["oneri_kapsami"] = solution["oneri_kapsami"]
    if frozen:
        out["uyarilar"].append(f"kismi_oneri:dondurulan_servis_rol={len(frozen)}")
    result = {"cozum": solution, "tasimalar": solution["tasimalar"], "kilitli_yerlesimler": solution["kilitli_yerlesimler"],
              "servis_rol": solution["servis_rol_aciklama"]}  # fmt: skip
    if experimental_mode is not None:
        # Deneysel: yayın her koşulda kapalı; normal çıktı alanları boş kalır, doğrulanmış sonuç yalnız ayrı alanda
        out["deneysel_sonuc"] = {**result, "dogrulandi": True, "yayinlanabilir": False, "deneysel_mod": experimental_mode}
        return out
    out["oneri_yayinlanabilir"] = True
    out.update(result)
    return out


def build_solution(inp: LayerAInputs, prep: Prepared, assignment: Mapping[int, int],
                   freeze_policy: str = DEFAULT_FREEZE_POLICY, experimental_mode: str | None = None) -> dict[str, Any]:  # fmt: skip
    """Doğrulayıcının denetleyeceği çözüm içeriği (tam sayı değerler) ve açıklama alanları (ham değerler)."""
    people = prep.people
    rows = []
    explain = []
    for u in sorted(inp.units):
        for r in ROLES:
            members = [p for p in people.values() if p.role == r and (assignment.get(p.staff_id) == u if p.movable else u in p.by_unit)]
            cap_int = sum(person_minutes(p.available if p.movable else p.by_unit[u]) for p in members)
            cap_raw = sum(total_minutes(p.available if p.movable else p.by_unit[u]) for p in members)
            base = [p for p in people.values() if p.role == r and (p.baseline == u if p.movable else u in p.by_unit)]
            base_int = sum(person_minutes(p.available if p.movable else p.by_unit[u]) for p in base)
            d = prep.demand[(u, r)]
            d_raw, util = prep.demand_raw[(u, r)]
            gap, surplus = max(0, d - cap_int), max(0, cap_int - d)
            rows.append({"servis_id": u, "rol": r, "talep_tam": d, "kapasite_tam": cap_int, "acik_tam": gap,
                         "fazla_tam": surplus, "onerilen_kisi": len(members)})  # fmt: skip
            need_raw = d_raw / util if util else 0.0
            explain.append({
                "servis_id": u, "rol": r, "tamponlu_talep_dk_ham": round(d_raw, 6), "kullanim_orani": util,
                "ihtiyac_personel_dk_ham": round(need_raw, 6), "ihtiyac_personel_dk_tam": d,
                "kapasite_personel_dk_ham": round(cap_raw, 6), "kapasite_personel_dk_tam": cap_int,
                "acik_personel_dk_tam": gap, "fazla_personel_dk_tam": surplus,
                "acik_personel_dk_ham": round(max(0.0, need_raw - cap_raw), 6),
                "mevcut_kapasite_tam": base_int, "mevcut_acik_tam": max(0, d - base_int),
                "mevcut_kisi": len(base), "onerilen_kisi": len(members), "donduruldu": (u, r) in prep.frozen,
                "atomlar": [{"baslangic": c.start, "bitis": c.end, "asgari_kisi": prep.requirement.get((u, r, k), 0),
                             "kisi": sum(1 for p in members if _avail_in(p.available if p.movable else p.by_unit[u], c))}
                            for k, c in enumerate(prep.cells) if k in prep.open_cells.get(u, ())],
            })  # fmt: skip
    moves, locked = [], []
    for s in sorted(assignment):
        p = people[s]
        target = assignment[s]
        if p.lock is not None:
            locked.append({"personel": s, "rol": p.role, "servis": target, "onay_kimligi": p.lock.approval_id,
                           "kilit_bitis": p.lock.lock_until})  # fmt: skip
        if target != p.baseline:
            fr = p.allowed.get(target)
            end = min(prep.window.end, p.shift_end)  # type: ignore[type-var]
            moves.append({
                "personel": s, "rol": p.role, "ev_servis": p.home, "son_onayli_servis": p.baseline, "hedef_servis": target,
                "baslangic": prep.at, "bitis": end, "sure_dk": int((end - prep.at).total_seconds() // 60),
                "asgari_kalis_dk": fr.min_stay_minutes if fr else None, "destek_kurali_kaynagi": fr.source if fr else None,
                "gerekli_yetkinlikler": sorted(set(fr.required_skills) | set(_req_skills(inp, target, p.role, prep.at))) if fr else [],
                "kullanilabilir_dk_ham": round(total_minutes(p.available), 6), "kullanilabilir_dk_tam": person_minutes(p.available),
            })  # fmt: skip
    solution = {
        "karar_ani": prep.at,
        "atamalar": [{"personel": s, "rol": p.role, "servis": assignment.get(s) if p.movable else None, "sabit": not p.movable}
                     for s, p in sorted(people.items())],  # fmt: skip
        "servis_rol": rows,
        "asama_degerleri": objective_values(inp, rows, len(moves), set(prep.frozen)),
        "dondurma_politikasi": freeze_policy,
        "dondurulan_servis_roller": [[u, r] for (u, r) in sorted(prep.frozen)],
        "oneri_kapsami": "kismi" if prep.frozen else "tam",
        "tasimalar": moves,
        "kilitli_yerlesimler": locked,
        "servis_rol_aciklama": explain,
    }
    if experimental_mode is not None:  # yalnız deneysel çalıştırmada; varsayılan çözüm içeriği (ve özeti) değişmez
        solution["deneysel_mod"] = experimental_mode
    solution["ozet_sha256"] = solution_hash(solution)
    return solution


def float_rule_order(f: FloatRule) -> tuple:
    """Destek kurallarının belirlenimci seçim sırası (çözücü ve doğrulayıcı aynı sırayı kullanır)."""
    return (f.min_stay_minutes, f.source, f.staff_id or 0, tuple(f.target_units))


def objective_values(inp: LayerAInputs, rows: list[dict[str, Any]], n_moves: int,
                     frozen: set[tuple[int, str]] | None = None) -> dict[str, int]:  # fmt: skip
    """Nihai çözümün GERÇEK O1–O4 değerleri (çözücünün aşama telemetrisinden bağımsız; dondurulan çiftler hariç)."""
    cfg = inp.config
    assert cfg is not None
    rows = [r for r in rows if (r["servis_id"], r["rol"]) not in (frozen or set())]
    return {
        STAGES[0]: sum(cfg.weights[r["servis_id"]] * r["acik_tam"] for r in rows),
        STAGES[1]: max([math.ceil(SCALE_O2 * r["acik_tam"] / r["talep_tam"]) for r in rows if r["talep_tam"] > 0], default=0),
        STAGES[2]: n_moves,
        STAGES[3]: sum(max(0, r["fazla_tam"] - cfg.surplus_tolerance[r["servis_id"]]) for r in rows),
    }


def _req_skills(inp: LayerAInputs, unit: int, role: str, at: datetime) -> tuple[str, ...]:
    for q in inp.unit_skill_requirements:
        if q.unit_id == unit and q.role == role and q.known_at <= at:
            return q.required_skills
    return ()


def structural_shortfalls(prep: Prepared, people: Mapping[int, Person]) -> list[str]:
    """K3: Bir açık atomda, uygun olabilecek BÜTÜN kişiler (yetki, kilit ve kullanılabilirlik dahil) orada olsa
    bile asgari karşılanamıyorsa yapısal plan eksiği. Kural değiştirilmez; vardiya planına geri bildirim olarak
    raporlanır. Kişiler arası çakışmayı içermez (o durum çözücüde 'kisit_birlesimi' olarak tanılanır)."""
    out = []
    for (u, r, k), req in sorted(prep.requirement.items()):
        cell = prep.cells[k]
        possible = sum(1 for p in people.values() if p.role == r and (
            (p.movable and u in p.allowed and _avail_in(p.available, cell)) or
            (not p.movable and u in p.by_unit and _avail_in(p.by_unit[u], cell))))  # fmt: skip
        if possible < req:
            out.append(f"plan_asgari_karsilanamaz:{u}:{r}:{cell.start.isoformat()}:{possible}<{req}")
    return out


def diagnose(prep: Prepared, people: Mapping[int, Person]) -> list[str]:
    """INFEASIBLE açıklaması (ÖNERİ DEĞİL): H1, kilit ve yetki kısıtları aynen korunur; yalnız H7'ye eksik
    değişkeni eklenip toplam eksik en aza indirilir. Sonuç, hangi servis × rol × atomda en az kaç kişi
    eksik kaldığını söyler. Bu tanı modelinin ataması hiçbir yerde kullanılmaz veya yayımlanmaz."""
    model = cp_model.CpModel()
    x = {(s, u): model.new_bool_var(f"x_{s}_{u}") for s, p in people.items() if p.movable for u in sorted(p.allowed)}
    for s, p in people.items():
        if p.movable:
            model.add_exactly_one([x[(s, u)] for u in sorted(p.allowed)])
    slack = {}
    for (u, r, k), req in sorted(prep.requirement.items()):
        cell = prep.cells[k]
        present = sum(x[(s, u)] for s, p in people.items() if p.movable and p.role == r and (s, u) in x and _avail_in(p.available, cell))
        fixed = sum(1 for p in people.values() if not p.movable and p.role == r and u in p.by_unit and _avail_in(p.by_unit[u], cell))
        slack[(u, r, k)] = model.new_int_var(0, req, f"eksik_{u}_{r}_{k}")
        model.add(present + fixed + slack[(u, r, k)] >= req)
    model.minimize(sum(slack.values()))
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = 10.0
    if solver.status_name(solver.solve(model)) not in (OPTIMAL, FEASIBLE):
        return ["tani_cozulemedi"]
    out = []
    for (u, r, k), var in slack.items():
        short = int(solver.value(var))
        if short > 0:
            req = prep.requirement[(u, r, k)]
            out.append(f"H7_karsilanamaz:{u}:{r}:{prep.cells[k].start.isoformat()}:{req - short}<{req}")
    return out or ["kisit_birlesimi_cozumsuz"]


def jsonable_result(result: Mapping[str, Any]) -> Any:
    def conv(v: Any) -> Any:
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, Mapping):
            return {str(k): conv(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        return v

    return conv(result)


__all__ = ["optimize", "preconditions", "prepare", "build_solution", "solution_hash"]
