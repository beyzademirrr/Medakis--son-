"""Aşama 5 / U3: açıklanabilir ihtiyaç–planlı kapasite karşılaştırması (çözücüsüz, salt okunur).

Girdi: U1 güvenli tahmin satırları (ŷ, B), U2 planı (unit_capacity_at ile karar anı görünümü), servis
çalışma takvimi ve planlama yapılandırması. Çıktı: servis × rol × 60 dk pencere için talep, kapasite,
açık/fazla personel-dakika, atom bazında asgari personel denetimi, durum ve neden kodları.

ÜRETMEZ: personel sayısı önerisi, atama, servisler arası taşıma, vardiya değişikliği. Gerçekleşen vardiya
veya karar anından sonra bilinen hiçbir kayıt kullanılmaz (kapasite yalnız unit_capacity_at(plan, at)).

Matematik (tek kullanım oranı, tek tampon):
    D        = max(0, ŷ + B)                      tamponlu talep (iş dakikası; B U1'den, bir kez)
    C        = kullanılabilir personel-dakika     (mola ∪ bilinen uygunsuzluk bir kez düşülmüş; U2)
    C_etkin  = C · u                              u = utilization_target (yetkili kaynak), bir kez
    acik_dk  = max(0, D − C_etkin)                fazla_dk = max(0, C_etkin − D)
    Tasarımın biçimi D/u ile kıyas: max(0, D/u − C) = acik_dk / u  (acik_personel_dk); aynı işaret, u bir kez.

Çalışma takvimi zorunludur: takvim yoksa (bilinmiyor) servisin açık olup olmadığı bilinmez → 'veri_eksik'.
Kısmen açık pencerede C ve P yalnız takvime göre AÇIK bölümlerden hesaplanır (U2, her açık bölüm için
aynı karar anıyla); kapalı bölümdeki vardiya kapasiteye katılmaz, asgari kural yalnız açık atomlarda aranır.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

import pandas as pd

from medakis.domain.capacity import subtract
from medakis.domain.models import Staff
from medakis.domain.planning import INVALID, MISSING, ROLES, PlanSet, StaffingRule, known_view, unit_capacity_at
from medakis.domain.service_calendar import CLOSED, CONFLICT, OPEN, PARTIAL, UNKNOWN, ServiceCalendar, calendar_state
from medakis.domain.time import Interval, ensure_utc

HORIZON_MINUTES = 60
GECERSIZ_PLAN, VERI_EKSIK, IHLAL, INCELEME, KAPALI, UYGUN = (
    "gecersiz_plan", "veri_eksik", "asgari_personel_ihlali", "inceleme_gerekli", "kapali_servis", "uygun")  # fmt: skip
# En ciddiden en hafife. Durum, tetiklenen en ciddi koddur; diğerleri `durumlar` ve `nedenler`de korunur.
STATUS_PRIORITY = (GECERSIZ_PLAN, VERI_EKSIK, IHLAL, INCELEME, KAPALI, UYGUN)
DEMAND_EPSILON = 1e-9  # kapalı serviste "pozitif talep" eşiği (kayan nokta artığı)
# K2: ham takvimle doğrulanmış kapalı ve vardiyasız serviste tampon uygulanmadığında, YALNIZ tampondan doğan
# U1 uyarıları engelleyici olmaktan çıkar (uyarı olarak görünür kalır). Diğer U1 uyarıları engellemeye devam eder.
BUFFER_DERIVED_U1 = ("yuksek_goreli_tampon", "tampon_yok")
DECIMALS = 6
SYNTHETIC_NOTICE = ("Sentetik veriye ve 'test_sentetik' etiketli varsayımlara dayanır; klinik standart değildir. "
                    "Personel sayısı, atama, taşıma veya vardiya önerisi değildir; insan onayı gerekir.")  # fmt: skip
DECISION_FIELDS_FORBIDDEN = ("onerilen_personel", "atanan_personel", "tasima", "vardiya_degisikligi")


@dataclass(frozen=True)
class UnitInfo:
    id: int
    name: str
    kind: str


@dataclass(frozen=True)
class DemandEstimate:
    """U1 güvenli çıktısının bir satırı (servis × rol × ufuk)."""

    unit_id: int
    role: str
    at: datetime
    horizon_minutes: int
    point: float  # ŷ (NaN: geçerli tahmin yok)
    buffer: float  # B
    buffered: float  # ŷ + B
    status: str  # U1: uygun / inceleme_gerekli
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    model: str
    model_version: str
    model_sha256: str | None

    @property
    def valid(self) -> bool:
        return all(math.isfinite(x) for x in (self.point, self.buffer, self.buffered)) and self.point >= 0 and self.buffer >= 0


def demand_from_u1(out: pd.DataFrame, manifests: Mapping[str, Mapping[str, Any]], horizon: int = HORIZON_MINUTES) -> list[DemandEstimate]:
    """U1 safe_forecast çıktısından istenen ufuktaki satırlar. Model özeti manifestten okunur."""
    rows = out[out["ufuk_dk"] == horizon]
    result = []
    for r in rows.itertuples(index=False):
        at = pd.Timestamp(r.an_utc).to_pydatetime()
        manifest = manifests.get(r.model, {})
        result.append(DemandEstimate(
            unit_id=int(r.servis_id), role=str(r.rol), at=at, horizon_minutes=int(r.ufuk_dk),
            point=float(r.nokta_tahmin_dk), buffer=float(r.tampon_dk), buffered=float(r.tamponlu_talep_dk),
            status=str(r.durum), reasons=tuple(r.inceleme_nedenleri), warnings=tuple(r.uyarilar),
            model=str(r.model), model_version=str(r.model_surumu), model_sha256=manifest.get("model_sha256"),
        ))  # fmt: skip
    return result


# ---- Asgari personel: atom bazında en yüksek geçerli kural ---------------------------------------


def atom_requirement(rules: Sequence[StaffingRule], unit_id: int, role: str, atom: Interval,
                     local_tz: tzinfo) -> tuple[int | None, list[StaffingRule]]:  # fmt: skip
    """Atomun tamamını kapsayan kurallar ve güvenli değer (en yüksek min_staff). Kural yoksa None."""
    covering = [r for r in rules if r.unit_id == unit_id and r.role == role and r.covers(atom, local_tz)]
    return (max(r.min_staff for r in covering) if covering else None), covering


def _rule_dict(r: StaffingRule) -> dict[str, Any]:
    return {"servis_id": r.unit_id, "rol": r.role, "yerel_saat_bandi": list(r.band), "asgari_kisi": r.min_staff,
            "kaynak": r.source, "onaylayan": r.approved_by, "gecerlilik_baslangic": r.valid_from,
            "gecerlilik_bitis": r.valid_until, "bilinme_ani": r.known_at}  # fmt: skip


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), DECIMALS)


def _hhmm(t: datetime, tz: tzinfo) -> str:
    return f"{t.astimezone(tz):%H:%M}"


# ---- Karşılaştırma ----------------------------------------------------------------------------------


def compare(at: datetime, demands: Sequence[DemandEstimate], plan: PlanSet, staff: Mapping[int, Staff],
            units: Mapping[int, UnitInfo], calendars: Sequence[ServiceCalendar], local_tz: tzinfo,
            horizon: int = HORIZON_MINUTES) -> list[dict[str, Any]]:  # fmt: skip
    """Her servis × rol için bir sonuç. Aynı karar anı ve girdiler aynı çıktıyı verir (deterministik)."""
    at = ensure_utc(at, "Karar anı")
    window = Interval(at, at + timedelta(minutes=horizon))
    unit_ids = set(units)
    by_key: dict[tuple[int, str], list[DemandEstimate]] = {}
    for d in demands:
        by_key.setdefault((d.unit_id, d.role), []).append(d)
    rows = []
    for unit_id in sorted(units):
        cap = unit_capacity_at(plan, at, dict(staff), unit_id, unit_ids, window, local_tz, require_contract=False)
        rules = known_view(plan, at).staffing_rules if cap.status != INVALID else ()
        cal = calendar_state(calendars, unit_id, window, at)
        # Kısmen açık pencere: kapasite yalnız açık bölümlerde, aynı karar anıyla (U2 sızıntı güvenliği korunur).
        part_caps = ([unit_capacity_at(plan, at, dict(staff), unit_id, unit_ids, part, local_tz, require_contract=False)
                      for part in cal.open_parts] if cal.state == PARTIAL and cap.status != INVALID else [])  # fmt: skip
        for role in ROLES:
            rows.append(_role_row(at, window, horizon, units[unit_id], role, cap, rules, cal, plan,
                                  by_key.get((unit_id, role), []), local_tz, part_caps))  # fmt: skip
    return rows


def _role_row(at, window, horizon, unit, role, cap, rules, cal, plan, demands, tz, part_caps) -> dict[str, Any]:  # noqa: ANN001, C901, PLR0912, PLR0915
    reasons: dict[str, list[str]] = {s: [] for s in STATUS_PRIORITY}
    warnings: list[str] = list(cal.warnings)

    # -- Kapasite (U2; karar anı görünümü içeride kurulur) --
    rc = None
    capacity_known = False
    if cap.status == INVALID:
        reasons[GECERSIZ_PLAN] += list(cap.reasons)
    elif cap.status == MISSING:
        if cal.state == CLOSED and cap.reasons == ("planli_vardiya_yok",):
            capacity_known = True  # doğrulanmış kapalı: planlı kapasitenin 0 olması normal
        else:
            reasons[VERI_EKSIK] += list(cap.reasons)  # takvim yoksa vardiya yokluğu "kapalı" SAYILMAZ
    else:
        rc = cap.roles[role]
        capacity_known = True
    if cal.state == CONFLICT:
        reasons[VERI_EKSIK].append("takvim_celiskili")
        capacity_known = False
    if cal.state == UNKNOWN:  # yetkili takvim yok: açık/kapalı bilinmez, karşılaştırma güvenilir değil
        reasons[VERI_EKSIK].append("calisma_takvimi_yok")
        capacity_known = False
    open_rcs = []
    if cal.state == PARTIAL:
        warnings.append("kismi_acik_pencere")
        for pc in part_caps:
            if pc.status == INVALID:
                reasons[GECERSIZ_PLAN] += [r for r in pc.reasons if r not in reasons[GECERSIZ_PLAN]]
            elif pc.status == MISSING:  # açık bölümde servisin hiç planlı vardiyası yok
                reasons[VERI_EKSIK].append("acik_bolumde_planli_vardiya_yok")
                capacity_known = False
            else:
                open_rcs.append(pc.roles[role])

    # -- Alt dilimler: açık bölüm atomları (açık bölüm sınırlarında bölünmüş) + kapalı parçalar --
    cells: list[tuple[datetime, datetime, int, int, bool | None]] = []
    if rc is not None:
        if cal.state == PARTIAL:
            for orc in open_rcs:
                cells += [(s, e, p, v, True) for s, e, p, v in orc.atom_counts]
            for s, e, p, v in rc.atom_counts:  # kapalı parçalar yalnız gösterim içindir
                cells += [(i.start, i.end, p, v, False) for i in subtract([Interval(s, e)], list(cal.open_parts))]  # type: ignore[misc]
            cells.sort(key=lambda c: c[0])
        else:
            flag = True if cal.state == OPEN else False if cal.state == CLOSED else None
            cells = [(s, e, p, v, flag) for s, e, p, v in rc.atom_counts]

    # -- Atom bazında asgari personel (yalnız takvime göre açık atomlarda) --
    atoms_out: list[dict[str, Any]] = []
    used_rules: list[StaffingRule] = []
    missing_atoms: list[Interval] = []
    for start, end, planned, available, is_open in cells:
        atom = Interval(start, end)
        required, covering = atom_requirement(rules, unit.id, role, atom, tz) if is_open else (None, [])
        used_rules += [r for r in covering if r not in used_rules]
        violation = required is not None and available < required
        if is_open and required is None:
            missing_atoms.append(atom)
        if violation:
            reasons[IHLAL].append(f"asgari_personel_ihlali:{role}:{_hhmm(start, tz)}:{available}<{required}")
        atoms_out.append({"baslangic": start, "bitis": end, "takvime_gore_acik": is_open,
                          "asgari_kisi": required, "planli_kisi": planned, "kullanilabilir_kisi": available,
                          "ihlal": violation})  # fmt: skip
    if missing_atoms:
        reasons[VERI_EKSIK].append(f"asgari_personel_kurali_yok:{role}:{_hhmm(missing_atoms[0].start, tz)}")
        capacity_known = False  # kuralsız kapasite güvenli sonuç gibi gösterilmez

    # -- Talep (U1) --
    demand = demands[0] if len(demands) == 1 else None
    if len(demands) > 1:
        reasons[VERI_EKSIK].append("tahmin_satiri_cift")
    elif not demands:
        reasons[VERI_EKSIK].append("tahmin_satiri_yok")
    elif demand.at != at or demand.horizon_minutes != horizon:  # type: ignore[union-attr]
        reasons[VERI_EKSIK].append("tahmin_karar_ani_uyusmuyor")
        demand = None
    demand_ok = demand is not None and demand.valid
    if demand is not None:
        if demand.status != UYGUN:
            reasons[INCELEME] += list(demand.reasons) or ["u1_inceleme"]
        if not demand.valid and "gecerli_tahmin_yok" not in demand.reasons:
            reasons[INCELEME].append("gecerli_tahmin_yok")
        warnings += list(demand.warnings)

    # -- Kapalı servis --
    closed_ok = False
    buffer_applied = True
    if cal.state == CLOSED:
        no_shift_unit = cap.status == MISSING and cap.reasons == ("planli_vardiya_yok",)
        if rc is not None and rc.planned_headcount_max > 0:
            reasons[INCELEME].append("kapali_serviste_planli_vardiya")
        if no_shift_unit and demand_ok:
            # K2: tampon kapalı saatte anlamsız (açık saat artıklarından öğrenildi); D = ŷ. ŷ > 0 güvenli ret.
            buffer_applied = False
            warnings.append(f"kapali_serviste_tampon_uygulanmadi:{demand.buffer:.3f}")  # type: ignore[union-attr]
            kept = []
            for code in reasons[INCELEME]:
                if code.startswith(BUFFER_DERIVED_U1):
                    warnings.append(f"kapali_serviste_gecersiz_u1_uyarisi:{code}")
                else:
                    kept.append(code)
            reasons[INCELEME] = kept
            if demand.point > 0:  # type: ignore[union-attr]
                reasons[INCELEME].append("kapali_serviste_talep")
        elif demand_ok and demand.buffered > DEMAND_EPSILON:  # type: ignore[union-attr]
            reasons[INCELEME].append("kapali_serviste_talep")
        closed_ok = demand_ok and not reasons[INCELEME] and not reasons[VERI_EKSIK] and not reasons[GECERSIZ_PLAN]

    # -- Kullanım oranı (yalnız açık pencerede karşılaştırma için zorunlu) --
    u = None
    config = plan.config
    if cal.state != CLOSED:
        if config is None:
            reasons[VERI_EKSIK].append("planlama_yapilandirmasi_yok")
        elif config.problems():
            reasons[VERI_EKSIK] += config.problems()
        else:
            u = config.utilization_target

    # -- Sayılar: yalnız güvenilir girdilerle --
    C = P = hc_max = hc_min_planned = hc_min_available = None
    if capacity_known and not reasons[GECERSIZ_PLAN]:
        if rc is None:  # doğrulanmış kapalı ve vardiyasız
            C = P = 0.0
            hc_max = hc_min_planned = hc_min_available = 0
        elif cal.state == PARTIAL:  # yalnız açık bölümler (mola ∪ izin her bölümde U2'de bir kez düşülür)
            C = sum(o.available_minutes for o in open_rcs)
            P = sum(o.planned_minutes for o in open_rcs)
            hc_max = max((o.planned_headcount_max for o in open_rcs), default=0)
            hc_min_planned = min((c[2] for c in cells if c[4]), default=0)
            hc_min_available = min((c[3] for c in cells if c[4]), default=0)
        else:
            C, P = rc.available_minutes, rc.planned_minutes
            hc_max, hc_min_planned, hc_min_available = (rc.planned_headcount_max, rc.planned_headcount_min_atom,
                                                        rc.available_headcount_min_atom)  # fmt: skip
    D = (demand.buffered if buffer_applied else demand.point) if demand_ok else None  # type: ignore[union-attr]
    effective = gap = surplus = need_staff = gap_staff = None
    if C is not None and D is not None and u is not None and cal.state != CLOSED:
        effective = C * u
        gap, surplus = max(0.0, D - effective), max(0.0, effective - D)
        need_staff, gap_staff = D / u, max(0.0, D - effective) / u

    triggered = [s for s in STATUS_PRIORITY if s not in (KAPALI, UYGUN) and reasons[s]]
    if not triggered:
        triggered = [KAPALI] if closed_ok else [UYGUN]
    elif closed_ok:
        triggered.append(KAPALI)
    codes = sorted({c for s in STATUS_PRIORITY for c in reasons[s]})
    return {
        "servis_id": unit.id, "servis_adi": unit.name, "servis_turu": unit.kind,
        "karar_ani": at, "pencere_baslangic": window.start, "pencere_bitis": window.end, "ufuk_dk": horizon, "rol": role,
        "model": demand.model if demand else None, "model_surumu": demand.model_version if demand else None,
        "model_sha256": demand.model_sha256 if demand else None, "u1_durum": demand.status if demand else None,
        "nokta_tahmin_dk": _round(demand.point) if demand_ok else None,
        "tampon_dk": (_round(demand.buffer) if buffer_applied else 0.0) if demand_ok else None,
        "tampon_uygulandi": buffer_applied if demand_ok else None,
        "tamponlu_talep_dk": _round(D),
        "planli_kisi_en_fazla": hc_max, "planli_kisi_en_az_atom": hc_min_planned,
        "kullanilabilir_kisi_en_az_atom": hc_min_available,
        "planli_personel_dk": _round(P), "kullanilabilir_personel_dk": _round(C),
        "kullanim_orani": u, "kullanim_orani_kaynagi": config.source if (config is not None and u is not None) else None,
        "etkin_kapasite_dk": _round(effective), "acik_dk": _round(gap), "fazla_dk": _round(surplus),
        "gerekli_personel_dk": _round(need_staff), "acik_personel_dk": _round(gap_staff),
        "takvim_durumu": cal.state, "takvim_kaynaklari": list(cal.sources), "takvime_gore_acik_dk": _round(cal.open_minutes),
        "atomlar": atoms_out, "asgari_kurallar": [_rule_dict(r) for r in used_rules],
        "durum": triggered[0], "durumlar": triggered, "nedenler": codes, "uyarilar": sorted(set(warnings)),
        "sentetik_uyari": SYNTHETIC_NOTICE, "insan_onayi_gerekli": True,
    }  # fmt: skip


def jsonable(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Zaman damgalarını UTC ISO metne çevirir (rapor ve dosya çıktısı için)."""

    def conv(v: Any) -> Any:
        if isinstance(v, datetime):
            return v.astimezone(UTC).isoformat()
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        return v

    return [conv(r) for r in rows]
