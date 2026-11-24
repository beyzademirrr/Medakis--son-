"""Aşama 5 / U4 bağımsız çözüm doğrulayıcısı (saf Python; çözücüyü ve model kurucuyu İÇE AKTARMAZ).

Çözücünün raporladığı hiçbir ara değere güvenmez: karar anı görünümünü, ham çalışma takvimlerini, kişi
uygunluğunu, kapasiteyi, atomları, asgari personeli, kilitleri, taşıma yetkisini, açık/fazla ve amaç
değerlerini ham girdi sözleşmelerinden yeniden üretir. Yayımlanacak alanları (taşımalar, kilitli
yerleşimler, açıklama satırları, nihai amaç değerleri) da atamalardan bağımsız yeniden kurar ve birebir
karşılaştırır. İçerik özeti yalnız bütünlük denetimidir; geçerli özet tek başına doğrulama değildir.
Tek bir ihlal varsa öneri yayımlanmaz. İhlaller kodludur (H1–H10, takvim, yayın alanları, bütünlük).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from medakis.domain.capacity import clip, intersect, merge, subtract, total_minutes
from medakis.domain.planning import ACTIVE_STATUSES, ROLES, PlanSet, atoms, known_view, plan_problems, rule_boundaries
from medakis.domain.rules import skill_valid_throughout
from medakis.domain.service_calendar import CLOSED, OPEN, PARTIAL, calendar_state
from medakis.domain.time import Interval, ensure_utc

WINDOW = timedelta(minutes=60)
CLOSED_EPS = 1e-9
FLOAT_TOL = 1e-6
STAGES = ("O1_karsilanmayan_talep", "O2_denge", "O3_hareket", "O4_fazla_kapasite")
# K1 dondurma: dondurulabilir U1 neden kodları (çözücüden bağımsız tanım; anlam aynı olmalı)
# Deneysel modlar (çözücüden bağımsız tanım): iki yönlü dondurma yalnız bu etiketle doğrulanır
EXPERIMENTAL = ("test", "golge_degerlendirme")
FREEZABLE = ("yuksek_goreli_tampon", "dagilim_disi", "gorulmemis_kategori", "toplam_rol_tutarsiz", "tolerans_yok", "tampon_yok")


def _expected_freeze(rows: Mapping[tuple[Any, Any], Mapping[str, Any]], cal: Mapping[int, Any]) -> tuple[set, list[str]]:
    """Ham U3 satırları ve ham takvimden beklenen dondurma kümesi ve dondurulamayan inceleme satırları."""
    frozen: set[tuple[int, str]] = set()
    blocked: list[str] = []
    for (u, role), row in rows.items():
        states = set(row.get("durumlar") or [row.get("durum")])
        if "inceleme_gerekli" not in states:
            continue
        codes = list(row.get("nedenler") or [])
        if states - {"inceleme_gerekli"} or not codes or not all(c.startswith(FREEZABLE) for c in codes):
            blocked.append(f"{u}:{role}")
        else:
            frozen.add((u, role))
    for u, state in cal.items():
        if state.state == PARTIAL:
            if any("asgari_personel_ihlali" in (r.get("durumlar") or []) for (uu, _), r in rows.items() if uu == u):
                blocked.append(f"{u}:kismi_acik_pencere")
            else:
                frozen |= {(u, role) for role in ROLES}
    return frozen, sorted(blocked)


@dataclass
class VerificationResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def _hash(solution: Mapping[str, Any]) -> str:
    content = {k: v for k, v in solution.items() if k != "ozet_sha256"}
    raw = json.dumps(content, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _ceil_div(value: float, util: float) -> int:
    return int((Decimal(repr(float(value))) / Decimal(repr(float(util)))).to_integral_value(rounding=ROUND_CEILING))


def _floor_minutes(intervals: list[Interval]) -> int:
    return math.floor(total_minutes(intervals) + 1e-9)


def _same(a: Any, b: Any) -> bool:
    """Yayın alanı karşılaştırması: ondalıklar 1e-6 toleransla, gerisi birebir."""
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= FLOAT_TOL
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def verify(inp: Any, solution: Mapping[str, Any]) -> VerificationResult:  # noqa: C901, PLR0912, PLR0915
    v: list[str] = []
    if solution.get("ozet_sha256") != _hash(solution):
        v.append("ozet_uyusmuyor")
    at = ensure_utc(inp.at, "Karar anı")
    window = Interval(at, at + WINDOW)
    if solution.get("karar_ani") != at:
        v.append("karar_ani_uyusmuyor")
    units = set(inp.units)

    # -- Ön koşullar ve HAM takvim (U3 beyanına güvenilmez) --
    cal = {u: calendar_state(inp.calendars, u, window, at) for u in units}
    rows = {(r.get("servis_id"), r.get("rol")): r for r in inp.u3_rows}
    policy = solution.get("dondurma_politikasi", "kapali")
    mode = solution.get("deneysel_mod")
    if mode is not None and mode not in EXPERIMENTAL:
        v.append(f"deneysel_mod_gecersiz:{mode}")
    if policy == "iki_yonlu" and mode is None:
        v.append("dondurma_deneysel_mod_eksik")  # klinik onaysız dondurma deneysel etiket olmadan geçerli değil
    frozen: set[tuple[int, str]] = set()
    if policy == "iki_yonlu":
        frozen, blocked = _expected_freeze(rows, cal)
        v += [f"on_kosul_dondurulamaz:{b}" for b in blocked]
    elif policy != "kapali":
        v.append(f"dondurma_politikasi_gecersiz:{policy}")
    uncertain = {u for u, _ in frozen}
    declared = {tuple(x) for x in solution.get("dondurulan_servis_roller", [])}
    if declared != frozen:
        v.append("dondurma_kumesi_tutarsiz")
    if solution.get("oneri_kapsami") != ("kismi" if frozen else "tam"):
        v.append("oneri_kapsami_tutarsiz")
    for u in sorted(units):
        partial_frozen = cal[u].state == PARTIAL and all((u, r) in frozen for r in ROLES)
        if cal[u].state not in (OPEN, CLOSED) and not partial_frozen:
            v.append(f"on_kosul_takvim_dogrulanamadi:{u}:{cal[u].state}")
        for role in ROLES:
            row = rows.get((u, role))
            if row is None or row.get("karar_ani") != at:
                v.append(f"on_kosul_u3_satiri:{u}:{role}")
                continue
            states = set(row.get("durumlar") or [row.get("durum")])
            if states & {"gecersiz_plan", "veri_eksik"} or ("inceleme_gerekli" in states and (u, role) not in frozen):
                v.append(f"on_kosul_u3_durumu:{u}:{role}")
            if row.get("takvim_durumu") != cal[u].state:
                v.append(f"on_kosul_takvim_u3_uyusmuyor:{u}")
            d_raw = row.get("tamponlu_talep_dk")
            point = row.get("nokta_tahmin_dk")
            if cal[u].state == CLOSED and (d_raw is None or float(d_raw) > CLOSED_EPS or point is None or float(point) > 0):
                v.append(f"on_kosul_kapali_serviste_talep:{u}:{role}")  # K2: kapalıda ŷ = 0 ve D = 0 zorunlu
            if cal[u].state == OPEN and (d_raw is None or not row.get("kullanim_orani")):
                v.append(f"on_kosul_talep_yok:{u}:{role}")
    cfg = inp.config
    if cfg is None or cfg.known_at > at or cfg.problems(units):
        v.append("on_kosul_yapilandirma")
    if inp.plan.as_of is not None and inp.plan.as_of < at:
        return VerificationResult(False, v + ["plan_gorunumu_karar_anindan_eski"])
    view = known_view(inp.plan, at)

    # -- Girdi bütünlüğü: aralık hesaplarından ÖNCE (U2 plan sözleşmesinin kuralları yeniden kullanılır) --
    # Kayıt düzeyi sorunlar t'de görünen HAM kayıtlarda, çakışma t görünümünde denetlenir (U2 unit_capacity_at ile aynı).
    # Bozuk kayıt veri kümesinden sessizce çıkarılmaz: ihlal kodu çözümün geçmesini engeller; yalnız aralık
    # hesaplarında atlanır ki doğrulayıcı istisna yerine kodlu sonuç döndürsün.
    roles_of = {sid: person.role for sid, person in inp.staff.items()}
    visible = PlanSet(tuple(s for s in inp.plan.shifts if s.planned_at <= at),
                      tuple(u for u in inp.plan.unavailability if u.recorded_at <= at),
                      tuple(r for r in inp.plan.staffing_rules if r.known_at <= at))  # fmt: skip
    problems = [p for p in plan_problems(visible, roles_of, units) if not p.startswith("cakisan_vardiya")]
    problems += plan_problems(view, roles_of, units)
    v += [f"girdi_gecersiz_plan:{p}" for p in dict.fromkeys(problems)]
    v += [f"H5_gecersiz_vardiya_araligi:{s.staff_id}:{s.id}" for s in visible.shifts if s.ends_at <= s.starts_at]
    v += [f"H4_gecersiz_uygunsuzluk_araligi:{u.staff_id}:{u.id}" for u in visible.unavailability if u.ends_at <= u.starts_at]
    v += [f"H3_gecersiz_yetkinlik_araligi:{k.staff_id}:{k.skill}" for k in inp.skills
          if k.valid_until is not None and k.valid_until <= k.valid_from]  # fmt: skip

    # -- Kişiler ve kullanılabilirlik (Aşama 3 kuralı: vardiya ∩ pencere − uygunsuzluk birleşimi, bir kez) --
    shifts: dict[int, list] = {}
    for s in view.shifts:
        if s.status in ACTIVE_STATUSES and s.ends_at > s.starts_at and s.interval.overlaps(window):
            shifts.setdefault(s.staff_id, []).append(s)
    unav: dict[int, list[Interval]] = {}
    for u_ in view.unavailability:
        if u_.ends_at > u_.starts_at:  # ters aralık yukarıda H4 ihlali olarak kaydedildi
            unav.setdefault(u_.staff_id, []).append(Interval(u_.starts_at, u_.ends_at))
    avail: dict[int, list[Interval]] = {}
    by_unit: dict[int, dict[int, list[Interval]]] = {}
    for sid, own in shifts.items():
        work = merge(clip([s.interval for s in own], window))
        avail[sid] = subtract(work, merge(clip(unav.get(sid, []), window)))
        by_unit[sid] = {u: intersect(avail[sid], merge(clip([s.interval for s in own if s.unit_id == u], window)))
                        for u in {s.unit_id for s in own}}  # fmt: skip
    home = {sid: next(iter(units_)) for sid, units_ in by_unit.items() if len(units_) == 1}
    shift_end = {sid: max(s.ends_at for s in own) for sid, own in shifts.items()}

    # -- Atamalar: H1, H2, H6 --
    assigned: dict[int, int | None] = {}
    for a in solution.get("atamalar", []):
        sid = a.get("personel")
        if sid in assigned:
            v.append(f"H1_cift_atama:{sid}")
        if sid not in shifts:
            v.append(f"H6_plan_disi_personel:{sid}")
            continue
        if inp.staff[sid].role != a.get("rol"):
            v.append(f"H2_rol_degisti:{sid}")
        if sid in home:
            if a.get("sabit") or a.get("servis") not in units:
                v.append(f"H1_gecersiz_servis:{sid}")
            assigned[sid] = a.get("servis")
        else:
            if not a.get("sabit") or a.get("servis") is not None:
                v.append(f"H1_cok_servisli_personel_tasindi:{sid}")
            assigned[sid] = None
    for sid in sorted(set(shifts) - set(assigned)):
        v.append(f"H1_atamasiz_personel:{sid}")

    # -- Kilitler: H8 --
    active = [lk for lk in inp.locks if lk.approved_at <= at and not (lk.revoked_at is not None and lk.revoked_at <= at)
              and lk.starts_at < window.end and lk.lock_until > at]  # fmt: skip
    lock_of: dict[int, Any] = {}
    for lk in active:
        if lk.staff_id in lock_of:
            v.append(f"H8_cakisan_kilit:{lk.staff_id}")
        lock_of[lk.staff_id] = lk
        if lk.staff_id in assigned and assigned[lk.staff_id] != lk.target_unit_id:
            v.append(f"H8_kilit_ihlali:{lk.staff_id}")

    # -- Taşıma ve kilit yerleşimi için yetki ve yetkinlik (H3–H6); kilit muafiyet sağlamaz --
    reqs = {(q.unit_id, q.role): q for q in inp.unit_skill_requirements if q.known_at <= at}
    floats = sorted(view.float_rules, key=lambda f: (f.min_stay_minutes, f.source, f.staff_id or 0, tuple(f.target_units)))

    def has(sid: int, skill: str) -> bool:
        return skill_valid_throughout([Interval(k.valid_from, k.valid_until) for k in inp.skills
                                       if k.staff_id == sid and k.skill == skill
                                       and (k.valid_until is None or k.valid_until > k.valid_from)], window)  # fmt: skip

    def authorize(sid: int, unit: int, lk: Any) -> tuple[Any, str]:
        role = inp.staff[sid].role
        if (unit, role) not in reqs:
            return None, "H3_yetkinlik_kurali_yok"
        reason = "H6_tasima_yetkisi_yok"
        for f in floats:
            if f.role != role or f.staff_id not in (None, sid) or unit not in f.target_units or not f.source.strip():
                continue
            if f.valid_from > at or (f.valid_until is not None and f.valid_until < window.end):
                continue
            stay = timedelta(minutes=f.min_stay_minutes)
            if (lk.lock_until - lk.starts_at < stay) if lk is not None else (shift_end[sid] - at < stay):
                reason = "H8_asgari_kalis_saglanmiyor"
                continue
            if all(has(sid, k) for k in set(f.required_skills) | set(reqs[(unit, role)].required_skills)):
                return f, ""
            reason = "H3_yetkinlik_eksik"
        return None, reason

    rule_used: dict[int, Any] = {}
    moved: list[int] = []
    for sid, unit in sorted(assigned.items()):
        if unit is None or sid not in home:
            continue
        lk = lock_of.get(sid)
        baseline = lk.target_unit_id if lk is not None else home[sid]
        if unit != baseline:
            moved.append(sid)
            role_ = inp.staff[sid].role
            if (home[sid], role_) in frozen or home[sid] in uncertain:
                v.append(f"dondurma_ihlali_cikis:{sid}")  # dondurulan çiftten / belirsiz servisten çıkış yok
            if (unit, role_) in frozen:
                v.append(f"dondurma_ihlali_giris:{sid}:{unit}")  # dondurulan çifte giriş yok
        if lk is not None and lk.lock_until > shift_end[sid]:
            v.append(f"H5_kilit_vardiya_disina_tasiyor:{sid}")
        if unit == home[sid]:
            continue
        if cal[unit].state != OPEN or cal[home[sid]].state != OPEN:
            v.append(f"H4_kapali_veya_dogrulanmamis_servis:{sid}:{unit}")
            continue
        if _floor_minutes(avail[sid]) <= 0:
            v.append(f"H4_kullanilamayan_personel_tasindi:{sid}")
        rule, why = authorize(sid, unit, lk if (lk is not None and lk.target_unit_id == unit) else None)
        if rule is None:
            v.append(f"{why}:{sid}:{unit}")
        rule_used[sid] = rule

    # -- Kapasite (H4/H5), talep–kapasite bağı (H10), önerilen kişi --
    def member_ivs(sid: int, u: int) -> list[Interval]:
        return avail[sid] if sid in home else by_unit[sid][u]

    def members_of(u: int, role: str, placement: Mapping[int, int | None]) -> list[int]:
        return [sid for sid, unit in placement.items() if inp.staff[sid].role == role and
                ((unit == u) if sid in home else (u in by_unit[sid]))]  # fmt: skip

    claimed = {(r.get("servis_id"), r.get("rol")): r for r in solution.get("servis_rol", [])}
    baseline_of = {sid: (lock_of[sid].target_unit_id if sid in lock_of else home[sid]) if sid in home else None for sid in assigned}
    gaps: dict[tuple[int, str], tuple[int, int, int]] = {}
    expected_rows, expected_explain = [], []
    for u in sorted(units):
        for role in ROLES:
            members = members_of(u, role, assigned)
            cap = sum(_floor_minutes(member_ivs(sid, u)) for sid in members)
            cap_raw = sum(total_minutes(member_ivs(sid, u)) for sid in members)
            row = rows.get((u, role), {})
            d_raw = float(row.get("tamponlu_talep_dk") or 0.0)
            util = row.get("kullanim_orani") if cal[u].state == OPEN else None
            d = _ceil_div(d_raw, util) if (cal[u].state == OPEN and util) else 0
            g, e = max(0, d - cap), max(0, cap - d)
            gaps[(u, role)] = (d, g, e)
            expected_rows.append({"servis_id": u, "rol": role, "talep_tam": d, "kapasite_tam": cap, "acik_tam": g,
                                  "fazla_tam": e, "onerilen_kisi": len(members)})  # fmt: skip
            c = claimed.get((u, role))
            if c is None:
                v.append(f"servis_rol_satiri_yok:{u}:{role}")
            else:
                if c.get("kapasite_tam") != cap:
                    v.append(f"H4_kapasite_tutarsiz:{u}:{role}:{c.get('kapasite_tam')}!={cap}")
                if c.get("talep_tam") != d:
                    v.append(f"H10_talep_tutarsiz:{u}:{role}")
                if c.get("acik_tam") != g:
                    v.append(f"H10_acik_tutarsiz:{u}:{role}")
                if c.get("fazla_tam") != e:
                    v.append(f"H10_fazla_tutarsiz:{u}:{role}")
                if c.get("onerilen_kisi") != len(members):
                    v.append(f"onerilen_kisi_tutarsiz:{u}:{role}")
            base = members_of(u, role, baseline_of)
            base_cap = sum(_floor_minutes(member_ivs(sid, u)) for sid in base)
            need_raw = d_raw / util if util else 0.0
            expected_explain.append({
                "servis_id": u, "rol": role, "tamponlu_talep_dk_ham": round(d_raw, 6), "kullanim_orani": util,
                "ihtiyac_personel_dk_ham": round(need_raw, 6), "ihtiyac_personel_dk_tam": d,
                "kapasite_personel_dk_ham": round(cap_raw, 6), "kapasite_personel_dk_tam": cap,
                "acik_personel_dk_tam": g, "fazla_personel_dk_tam": e,
                "acik_personel_dk_ham": round(max(0.0, need_raw - cap_raw), 6),
                "mevcut_kapasite_tam": base_cap, "mevcut_acik_tam": max(0, d - base_cap),
                "mevcut_kisi": len(base), "onerilen_kisi": len(members), "donduruldu": (u, role) in frozen, "atomlar": [],
            })  # fmt: skip
    if len(claimed) != len(expected_rows):
        v.append("servis_rol_satir_sayisi_tutarsiz")

    # -- Asgari personel her açık atomda (H7) ve ev servisi (H9) --
    bounds = [t for ivs in avail.values() for i in ivs for t in (i.start, i.end)]
    bounds += [t for own in shifts.values() for s in own for t in (s.starts_at, s.ends_at)]
    bounds += [t for c in cal.values() for i in c.open_parts for t in (i.start, i.end)]
    bounds += [t for lk in active for t in (lk.starts_at, lk.lock_until)]
    bounds += rule_boundaries(window, tuple(view.staffing_rules), inp.local_tz)
    cells = atoms(window, bounds)
    short_units: set[int] = set()
    explain_by = {(x["servis_id"], x["rol"]): x for x in expected_explain}
    for u in sorted(units):
        if cal[u].state == OPEN:
            unit_cells = cells
        elif cal[u].state == PARTIAL:  # kısmi açık: ham takvimin açık bölümündeki atomlar (U3 etiketine güvenilmez)
            unit_cells = [c for c in cells if any(op.covers(c) for op in cal[u].open_parts)]
        else:
            continue
        for role in ROLES:
            members = members_of(u, role, assigned)
            for cell in unit_cells:
                covering = [r for r in view.staffing_rules if r.unit_id == u and r.role == role and r.covers(cell, inp.local_tz)]
                present = sum(1 for sid in members if any(i.covers(cell) for i in member_ivs(sid, u)))
                need = max((r.min_staff for r in covering), default=None)
                explain_by[(u, role)]["atomlar"].append({"baslangic": cell.start, "bitis": cell.end,
                                                         "asgari_kisi": max(need or 0, 0), "kisi": present})  # fmt: skip
                if need is None:
                    v.append(f"H7_kural_yok:{u}:{role}:{cell.start.isoformat()}")
                elif present < need:
                    v.append(f"H7_asgari_alti:{u}:{role}:{cell.start.isoformat()}:{present}<{need}")
                    short_units.add(u)
    for sid in moved:
        if home.get(sid) in short_units:
            v.append(f"H9_ev_servisi_asgari_alti:{sid}:{home[sid]}")

    # -- Yayımlanacak alanlar: atamalardan ve ham girdilerden bağımsız yeniden kurulur --
    expected_moves = []
    for sid in moved:
        rule = rule_used.get(sid)
        unit = assigned[sid]
        role = inp.staff[sid].role
        end = min(window.end, shift_end[sid])
        expected_moves.append({
            "personel": sid, "rol": role, "ev_servis": home[sid], "son_onayli_servis": baseline_of[sid], "hedef_servis": unit,
            "baslangic": at, "bitis": end, "sure_dk": int((end - at).total_seconds() // 60),
            "asgari_kalis_dk": rule.min_stay_minutes if rule else None, "destek_kurali_kaynagi": rule.source if rule else None,
            "gerekli_yetkinlikler": sorted(set(rule.required_skills) | set(reqs[(unit, role)].required_skills)) if rule else [],
            "kullanilabilir_dk_ham": round(total_minutes(avail[sid]), 6), "kullanilabilir_dk_tam": _floor_minutes(avail[sid]),
        })  # fmt: skip
    expected_locked = [{"personel": sid, "rol": inp.staff[sid].role, "servis": assigned[sid],
                        "onay_kimligi": lock_of[sid].approval_id, "kilit_bitis": lock_of[sid].lock_until}
                       for sid in sorted(assigned) if sid in lock_of and sid in home]  # fmt: skip
    if not _same(list(solution.get("tasimalar", [])), expected_moves):
        v.append("yayin_tasimalar_tutarsiz")
    if not _same(list(solution.get("kilitli_yerlesimler", [])), expected_locked):
        v.append("yayin_kilitli_yerlesimler_tutarsiz")
    claimed_explain = {(x.get("servis_id"), x.get("rol")): x for x in solution.get("servis_rol_aciklama", [])}
    if claimed_explain.keys() != explain_by.keys():
        v.append("yayin_aciklama_satirlari_tutarsiz")
    for key, exp in explain_by.items():
        if key in claimed_explain and not _same(claimed_explain[key], exp):
            v.append(f"yayin_aciklama_tutarsiz:{key[0]}:{key[1]}")

    # -- Nihai amaç değerleri: EŞİTLİK (beyan ne küçük ne büyük olabilir) --
    if cfg is not None and not cfg.problems(units):
        recomputed = {
            STAGES[0]: sum(cfg.weights[u] * g for (u, r), (_, g, _) in gaps.items() if (u, r) not in frozen),
            STAGES[1]: max([math.ceil(1000 * g / d) for key, (d, g, _) in gaps.items() if d > 0 and key not in frozen], default=0),
            STAGES[2]: len(moved),
            STAGES[3]: sum(max(0, e - cfg.surplus_tolerance[u]) for (u, r), (_, _, e) in gaps.items() if (u, r) not in frozen),
        }
        declared = dict(solution.get("asama_degerleri") or {})
        if declared.keys() != recomputed.keys():
            v.append("amac_degerleri_eksik")
        for name, value in recomputed.items():
            if declared.get(name) != value:
                v.append(f"amac_degeri_tutarsiz:{name}:{declared.get(name)}!={value}")
    return VerificationResult(not v, sorted(set(v)))
