"""U7: Onaya sunulan planın canonical anlık görüntüsü ve SHA-256 özeti (şartname Bölüm 11).

Özet şunları sıralı canonical JSON olarak içerir:
- görev veya personel kimlikleri ve başlangıç/bitiş;
- çalışma yapılandırması;
- çözüm sürümü: doğrulanmış çözüm özeti, çözücü ve algoritma sürümü;
- tahmin modeli sürümleri;
- karar anı ve pencere;
- karar penceresindeki güvenlik girdilerinin parmak izi (plan, yetkinlik, takvim, kilit, görev durumu).

Karar bu özete bağlanır. Özet ya da parmak izi değişirse plan yeniden incelenmelidir. Hasta bilgisi içermez.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from importlib import metadata
from typing import Any

from medakis.domain.planning import ACTIVE_STATUSES, PlanSet, known_view
from medakis.domain.time import Interval

SNAPSHOT_VERSION = "u7-anlik-goruntu-1"
KIND_A, KIND_B = "katman_a_kadro_onerisi", "katman_b_gorev_denetimi"


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    return value


def canonical_json(content: Any) -> str:
    """Anahtarları sıralı, boşluksuz, ASCII canonical JSON (zamanlar ISO 8601, UTC)."""
    return json.dumps(_plain(content), sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def sha256_of(content: Any) -> str:
    return hashlib.sha256(canonical_json(content).encode("ascii")).hexdigest()


def solver_version() -> str:
    try:
        return f"ortools-cp-sat-{metadata.version('ortools')}"
    except metadata.PackageNotFoundError:  # pragma: no cover - ortools kurulu değilse
        return "ortools-cp-sat-bilinmiyor"


@dataclass(frozen=True)
class SafetyInputs:
    """Öneriyi güvenli kılan girdilerin GÜNCEL kaynaklardan alınmış hâli (onay ve geçerlilik denetiminde zorunlu).

    None = kaynak sağlanmadı (eksik). Boş demet = kaynak sağlandı ve kayıt yok (ör. hiç kilit yok).
    Katman A için plan, yetkinlik, takvim ve kilit; Katman B için plan, yetkinlik ve görev zorunludur.
    """

    plan: PlanSet | None
    skills: Sequence[Any] | None = None
    calendars: Sequence[Any] | None = None
    locks: Sequence[Any] | None = None
    tasks: Sequence[Any] | None = None
    uncertain_task_ids: Sequence[int] = ()

    @staticmethod
    def from_layer_a(inp: Any) -> SafetyInputs:
        return SafetyInputs(inp.plan, tuple(inp.skills), tuple(inp.calendars), tuple(inp.locks))

    @staticmethod
    def from_layer_b(inp: Any) -> SafetyInputs:
        return SafetyInputs(inp.plan, tuple(inp.skills), None, None, tuple(inp.tasks), tuple(inp.uncertain_task_ids))


REQUIRED = {KIND_A: ("plan", "yetkinlik", "takvim", "kilit"), KIND_B: ("plan", "yetkinlik", "gorev")}
_FIELD = {"plan": "plan", "yetkinlik": "skills", "takvim": "calendars", "kilit": "locks", "gorev": "tasks"}


def _plan_part(plan: PlanSet, known_at: datetime, window: Interval, units: set[int]) -> dict[str, Any]:
    view = known_view(plan, known_at)
    shifts = sorted(
        (s.id, s.staff_id, s.unit_id, s.role, s.starts_at, s.ends_at, s.break_minutes, s.status)
        for s in view.shifts if s.status in ACTIVE_STATUSES and Interval(s.starts_at, s.ends_at).overlaps(window)
    )  # fmt: skip
    unav = sorted((u.id, u.staff_id, u.starts_at, u.ends_at, u.reason) for u in view.unavailability
                  if u.ends_at > window.start and u.starts_at < window.end)  # fmt: skip
    rules = sorted(((r.unit_id, r.role, tuple(r.band), r.min_staff, r.source, r.valid_from, r.valid_until)
                    for r in view.staffing_rules if r.unit_id in units), key=str)  # fmt: skip
    floats = sorted(((f.role, f.staff_id or 0, tuple(f.target_units), tuple(f.required_skills), f.min_stay_minutes, f.source,
                      f.valid_from, f.valid_until) for f in view.float_rules), key=str)  # fmt: skip
    return {"vardiyalar": shifts, "uygunsuzluklar": unav, "asgari": rules, "destek": floats}


def _skills_part(skills: Sequence[Any], window: Interval) -> list[Any]:
    """Pencereyle kesişen (ya da ters/bozuk) bütün yetkinlik kayıtları: geri alınan ya da kısalan yetkinlik özeti değiştirir."""
    rows = [(k.staff_id, k.skill, k.valid_from, k.valid_until) for k in skills
            if k.valid_from < window.end and (k.valid_until is None or k.valid_until > window.start or k.valid_until <= k.valid_from)]  # fmt: skip
    return sorted(rows, key=str)


def _calendar_part(calendars: Sequence[Any], known_at: datetime, window: Interval, units: set[int]) -> list[Any]:
    from medakis.domain.service_calendar import calendar_state

    out = []
    for u in sorted(units):
        c = calendar_state(calendars, u, window, known_at)
        out.append((u, c.state, [(i.start, i.end) for i in c.open_parts], sorted(c.sources)))
    return out


def _lock_part(locks: Sequence[Any], known_at: datetime, window: Interval) -> list[Any]:
    """t'de bilinen ve pencereyle kesişen onaylı kilitler; sonradan bilinen geri alma ya da yeni kilit özeti değiştirir."""
    rows = [(k.staff_id, k.target_unit_id, k.starts_at, k.lock_until, k.approval_id, k.approved_at)
            for k in locks if k.active_at(known_at, window.end)]  # fmt: skip
    return sorted(rows, key=str)


def _task_part(tasks: Sequence[Any], uncertain: Sequence[int], window: Interval, units: set[int]) -> dict[str, Any]:
    """Son tarihi pencerede olan bekleyen görevler (Katman B kapsamı) ve iptal zamanı belirsiz görevler."""
    rows = sorted((t.task_id, t.unit_id, t.role, t.task_type, t.estimated_minutes, t.created_at, t.due_at, tuple(t.required_skills))
                  for t in tasks if t.unit_id in units and t.due_at <= window.end)  # fmt: skip
    return {"gorevler": rows, "belirsiz": sorted(set(uncertain))}


def fingerprint_components(kind: str, sources: SafetyInputs | None, known_at: datetime, window: Interval,
                           units: Iterable[int]) -> tuple[dict[str, str], list[str]]:  # fmt: skip
    """Bileşen özetleri ve eksik kaynaklar. Eksik zorunlu kaynak varsa çağıran öneriyi REDDETMELİDİR (fail-closed)."""
    if kind not in REQUIRED:
        return {}, [f"bilinmeyen_oneri_turu:{kind}"]
    if sources is None:
        return {}, ["guvenlik_girdisi_eksik:hepsi"]
    missing = [name for name in REQUIRED[kind] if getattr(sources, _FIELD[name]) is None]
    if missing:
        return {}, [f"guvenlik_girdisi_eksik:{m}" for m in missing]
    wanted = set(units)
    parts: dict[str, Any] = {"plan": _plan_part(sources.plan, known_at, window, wanted),  # type: ignore[arg-type]
                             "yetkinlik": _skills_part(sources.skills, window)}  # type: ignore[arg-type]  # fmt: skip
    if kind == KIND_A:
        parts["takvim"] = _calendar_part(sources.calendars, known_at, window, wanted)  # type: ignore[arg-type]
        parts["kilit"] = _lock_part(sources.locks, known_at, window)  # type: ignore[arg-type]
    else:
        parts["gorev"] = _task_part(sources.tasks, sources.uncertain_task_ids, window, wanted)  # type: ignore[arg-type]
    return {name: sha256_of({"pencere": [window.start, window.end], name: value}) for name, value in parts.items()}, []


def window_fingerprint(kind: str, sources: SafetyInputs | None, known_at: datetime, window: Interval,
                       units: Iterable[int]) -> tuple[str | None, dict[str, str], list[str]]:  # fmt: skip
    """`known_at` anında bilinen güvenlik girdilerinin karar penceresine dokunan kısmının özeti.

    Kapsam: plan (etkin vardiya, uygunsuzluk, asgari ve destek kuralları), yetkinlikler, servis takvimleri ve onaylı
    taşıma kilitleri (Katman A) ya da bekleyen görev durumu (Katman B). Sonradan bilinen ve pencereye dokunan her
    değişiklik özeti değiştirir; öneri eskimiş sayılır. Kapsam dışı servislerin vardiyaları da dahildir (güvenli taraf).
    Dönüş: (toplam özet, bileşen özetleri, eksik kaynaklar). Eksik varsa toplam özet None'dır.
    """
    parts, missing = fingerprint_components(kind, sources, known_at, window, units)
    if missing:
        return None, {}, missing
    return sha256_of(parts), parts, []


def model_versions_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """U3 satırlarındaki tahmin modeli sürümleri (tekrarsız, sıralı)."""
    seen = {(r.get("model"), r.get("model_surumu"), r.get("model_sha256")) for r in rows if r.get("model") is not None}
    return [{"model": m, "model_surumu": v, "model_sha256": h} for m, v, h in sorted(seen, key=lambda x: tuple(str(p) for p in x))]


def snapshot_layer_a(inp: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    """Katman A yayımlanabilir öneriden anlık görüntü. Deneysel ya da yayımlanamaz sonuç kabul edilmez."""
    sol = result["cozum"]
    cfg = inp.config
    window = Interval(result["pencere_baslangic"], result["pencere_bitis"])
    items = [{"tur": "tasima", "personel": m["personel"], "rol": m["rol"], "kaynak_servis": m["son_onayli_servis"],
              "hedef_servis": m["hedef_servis"], "baslangic": m["baslangic"], "bitis": m["bitis"]} for m in sol["tasimalar"]]  # fmt: skip
    items += [{"tur": "kilitli_yerlesim", "personel": k["personel"], "rol": k["rol"], "hedef_servis": k["servis"],
               "onay_kimligi": k["onay_kimligi"], "bitis": k["kilit_bitis"]} for k in sol["kilitli_yerlesimler"]]  # fmt: skip
    units = sorted(inp.units)
    return {
        "anlik_goruntu_surumu": SNAPSHOT_VERSION, "tur": KIND_A, "karar_ani": result["karar_ani"],
        "pencere": [window.start, window.end], "servisler": units,
        "kalemler": sorted(items, key=lambda x: (x["tur"], x["personel"])),
        "calisma_yapilandirmasi": {
            "agirliklar": dict(cfg.weights), "fazla_tolerans": dict(cfg.surplus_tolerance), "sure_siniri_sn": cfg.time_limit_s,
            "kaynak": cfg.source, "onaylayan": cfg.approved_by, "bilinme_ani": cfg.known_at,
            "dondurma_politikasi": result["dondurma_politikasi"], "cozucu_isci": 1, "cozucu_tohum": 0,
        },
        "cozum_surumu": {"cozum_ozeti": sol["ozet_sha256"], "cozucu": solver_version(), "algoritma": "katman_a_u4_sozluk_sirali",
                         "asama_degerleri": dict(sol["asama_degerleri"]), "oneri_kapsami": sol["oneri_kapsami"]},
        "model_surumleri": model_versions_from_rows(inp.u3_rows),
        **_fingerprint_fields(KIND_A, SafetyInputs.from_layer_a(inp), result["karar_ani"], window, units),
    }  # fmt: skip


def snapshot_layer_b(inp: Any, result: Mapping[str, Any], model_versions: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Katman B doğrulanmış denetiminden anlık görüntü (görev, personel, başlangıç/bitiş)."""
    sol = result["cozum"]
    cfg = inp.config
    window = Interval(result["pencere_baslangic"], result["pencere_bitis"])
    items = [{"tur": "gorev_atamasi", "gorev": a["gorev"], "personel": a["personel"], "servis": a["servis"],
              "baslangic": a["baslangic"], "bitis": a["bitis"]} for a in sol["atamalar"]]  # fmt: skip
    units = sorted(inp.units)
    return {
        "anlik_goruntu_surumu": SNAPSHOT_VERSION, "tur": KIND_B, "karar_ani": result["karar_ani"],
        "pencere": [window.start, window.end], "servisler": units, "kalemler": sorted(items, key=lambda x: x["gorev"]),
        "atanmayanlar": sorted(((g["gorev"], g["neden"], g["acik_dk"]) for g in sol["atanmayanlar"])),
        "calisma_yapilandirmasi": {"sure_siniri_sn": cfg.time_limit_s, "kaynak": cfg.source, "onaylayan": cfg.approved_by,
                                   "bilinme_ani": cfg.known_at, "cozucu_isci": 1, "cozucu_tohum": 0},
        "cozum_surumu": {"cozum_ozeti": sol["ozet_sha256"], "cozucu": solver_version(), "algoritma": "katman_b_u6_sozluk_sirali",
                         "amac_degerleri": dict(sol["amac_degerleri"])},
        "model_surumleri": [dict(m) for m in model_versions],
        **_fingerprint_fields(KIND_B, SafetyInputs.from_layer_b(inp), result["karar_ani"], window, units),
    }  # fmt: skip


def _fingerprint_fields(kind: str, sources: SafetyInputs, at: datetime, window: Interval, units: list[int]) -> dict[str, Any]:
    total, parts, missing = window_fingerprint(kind, sources, at, window, units)
    if missing:  # kayıtta girdi her zaman öneriyi üreten girdinin kendisidir; eksik olamaz
        raise ValueError(f"anlik_goruntu_girdisi_eksik:{missing}")
    return {"girdi_parmak_izi": total, "girdi_bilesenleri": parts}


def plan_sha256(snapshot: Mapping[str, Any]) -> str:
    return sha256_of(snapshot)
