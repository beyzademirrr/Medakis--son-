"""Üretici 2.1: planlı vardiya, planlı/ani uygunsuzluk ve bilinme zamanları.

Yöntem (2.0.0 koduna dokunmadan):
1. 2.0.0 veri kümesi aynen üretilir (generate_v2) — tabloları ve içerik özeti değişmez.
2. Planlı kadro, 2.0.0'ın deterministik rotasyon kurallarıyla (ROTATION, kadro tabloları, şablonlar)
   yeniden kurulur; 2.0.0'ın gelinmeme çekilişi uygulanMAZ: bütün planlı vardiyalar kayıtta kalır.
3. Uzlaşma denetimi: planlı vardiyalardan gerçekleşenler (staff_shifts) birebir eşleşmeli ve
   eşleşmeyen sayısı 2.0.0'ın `skipped_shifts` sayısına eşit olmalı; değilse üretim durur.
4. Gelinmeyen vardiya iki biçimde temsil edilir (2.1 akışı, VARSAYIM paylarıyla):
   - önceden bildirilmiş plan iptali (status=cancelled, cancelled_at = başlangıçtan 1-3 gün önce),
   - ani devamsızlık (vardiya planlı kalır + kind=ani 'devamsizlik' uygunsuzluğu, başlangıca yakın bildirim).
5. 2.0.0'ın somut molaları planlı uygunsuzluk (mola, planli, planla birlikte bilinir); gelinmeyen
   vardiyaların molası 2.0.0'da üretilmediği için 2.1 aynı kuralla tamamlar. 2.0.0 izinleri 'planli'
   (önceden) veya 'ani' (kısa süre önce) olarak sınıflanır.
Asgari personel kuralları ve planlama yapılandırması ÜRETİLMEZ (kaynaksız değer yok); boş bırakılır.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from medakis.domain.capacity import Unavailability
from medakis.domain.models import Staff, StaffShift
from medakis.domain.planning import PlannedShift, PlannedUnavailability, PlanSet
from medakis.domain.time import Interval, shift_from_template
from medakis.repositories.schema import SEED_RESET_TABLES
from medakis.synthetic.v2 import ParamsV2, generate_v2
from medakis.synthetic.v2 import profiles as P
from medakis.synthetic.v2.calendar import day_infos
from medakis.synthetic.v2.model import DatasetV2
from medakis.synthetic.v2.staffing import _need, build_dictionaries, build_staff, build_structure, build_templates, unit_specs

GENERATOR_VERSION = "2.1.0"
STREAM_V21 = 201  # 2.0.0 akışlarından (101-110) ayrı


@dataclass(frozen=True)
class ParamsV21:
    base: ParamsV2 = field(default_factory=ParamsV2)
    plan_lead_days: int = 14  # VARSAYIM: çizelge vardiyadan 14 gün önce yayımlanır
    cancel_share: float = 0.3  # VARSAYIM: gelinmeyen vardiyaların önceden iptal edilen payı
    cancel_notice_days: tuple[float, float] = (1.0, 3.0)  # VARSAYIM: iptal bildirimi, gün önce
    absence_notice_min: tuple[float, float] = (-120.0, 30.0)  # VARSAYIM: ani devamsızlık bildirimi (başlangıca göre dk)
    leave_planned_share: float = 0.5  # VARSAYIM: 2.0.0 izinlerinin önceden bilinen payı
    leave_planned_lead_days: int = 7  # VARSAYIM
    leave_short_notice_min: tuple[float, float] = (30.0, 240.0)  # VARSAYIM: ani izin, başlangıçtan önce dk

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["base"] = self.base.as_dict()
        return data

    def fingerprint(self) -> str:
        raw = json.dumps({"generator_version": GENERATOR_VERSION, **self.as_dict()}, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(raw.encode("ascii")).hexdigest()


@dataclass
class DatasetV21:
    params: ParamsV21
    base: DatasetV2
    plan: PlanSet
    staff_roles: dict[int, str]
    unit_ids: set[int]
    reconciliation: dict[str, int]

    def base_content_sha256(self) -> str:
        """2.0.0 tablolarının özeti: 2.0.0 ile birebir aynı olmalı."""
        return self.base.content_sha256()

    def plan_sha256(self) -> str:
        """Plan katmanının ayrı özeti (2.1)."""
        payload = {
            "surum": GENERATOR_VERSION,
            "vardiyalar": [_row(asdict(s)) for s in sorted(self.plan.shifts, key=lambda s: s.id)],
            "uygunsuzluklar": [_row(asdict(u)) for u in sorted(self.plan.unavailability, key=lambda u: u.id)],
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("ascii")).hexdigest()


    def staff(self) -> dict[int, Staff]:
        return {s["id"]: Staff(s["id"], s["synthetic_code"], s["role"], s["active"]) for s in self.base.tables["staff"]}

    def realized(self) -> tuple[list[StaffShift], list[Unavailability]]:
        """Gerçekleşen vardiya ve uygunsuzluklar (2.0.0 tabloları). YALNIZ sonradan değerlendirme içindir."""
        shifts = [StaffShift(s["id"], s["staff_id"], s["unit_id"], s["starts_at"], s["ends_at"], s["break_minutes"])
                  for s in self.base.tables["staff_shifts"]]  # fmt: skip
        unav = [Unavailability(u["staff_id"], Interval(u["starts_at"], u["ends_at"]), u["reason"])
                for u in self.base.tables["staff_unavailability"]]  # fmt: skip
        return shifts, unav


def _row(data: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in data.items():
        if isinstance(v, datetime):
            out[k] = v.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        elif isinstance(v, float):
            out[k] = format(round(v, 6), ".6f")
        else:
            out[k] = v
    return out


def _minutes(value: float) -> timedelta:
    return timedelta(minutes=int(round(value)))


def _rotation_plan(params: ParamsV2):  # noqa: ANN202
    """2.0.0 build_shifts ile aynı sıra ve kuralla planlı vardiyalar (gelinmeme çekilişi olmadan)."""
    scratch: dict[str, list[dict]] = {name: [] for name in SEED_RESET_TABLES}
    specs = unit_specs(params)
    build_structure(params, specs, scratch)
    skill_ids, _ = build_dictionaries(scratch)
    templates = build_templates(params, scratch)
    tz = ZoneInfo(params.timezone)
    start = datetime.combine(params.start_day, time(0), tzinfo=tz).astimezone(UTC)
    staff_plan = build_staff(params, specs, skill_ids, scratch, start)
    end_day = params.start_day + timedelta(days=params.days)
    warmup = params.inpatient_warmup_days
    calendar = day_infos(params.start_day - timedelta(days=warmup), warmup + params.days + 1)
    staff_days = [d for d in calendar if params.start_day <= d.day <= end_day]
    rows = []
    for offset, info in enumerate(staff_days):
        for u in specs:
            for role in P.ROLES:
                members = staff_plan.pools.get((u.id, role), [])
                if not members:
                    continue
                planned = []
                if u.kind == "outpatient":
                    if not info.is_workday:
                        continue
                    tmpl = templates["outpatient"]["half" if info.is_half_holiday else "clinic"]
                    need = params.outpatient_nurses_per_day if role == "nurse" else 0
                    planned = [(members[(offset + j) % len(members)], tmpl) for j in range(min(need, len(members)))]
                else:
                    n = len(members)
                    for slot, tmpl in templates[u.kind].items():
                        need = _need(u.kind, u.profile, role, slot, info)
                        if need == 0:
                            continue
                        available = [sid for i, sid in enumerate(members) if P.ROTATION[(offset + (i * 7) // n) % 7] == slot]
                        planned += [(sid, tmpl) for sid in available[:need]]
                for sid, tmpl in planned:
                    shift = shift_from_template(info.day, tmpl.start, tmpl.minutes, params.timezone)
                    rows.append((sid, u.id, role, tmpl, shift))
    return rows


def generate_v21(params: ParamsV21 | None = None) -> DatasetV21:
    params = params or ParamsV21()
    base = generate_v2(params.base)
    rng = np.random.default_rng([params.base.seed, STREAM_V21])
    realized = {(s["staff_id"], s["starts_at"]): s for s in base.tables["staff_shifts"]}
    breaks_by_staff: dict[int, list[dict]] = {}
    leaves = []
    for u in base.tables["staff_unavailability"]:
        if u["reason"] == "Mola":
            breaks_by_staff.setdefault(u["staff_id"], []).append(u)
        else:
            leaves.append(u)

    shifts: list[PlannedShift] = []
    unav: list[PlannedUnavailability] = []
    matched = 0
    not_realized = cancelled = absent = 0

    def add_unav(staff_id: int, a: datetime, b: datetime, reason: str, recorded: datetime, kind: str) -> None:
        unav.append(PlannedUnavailability(len(unav) + 1, staff_id, a, b, reason, recorded, kind))

    for sid, unit_id, role, tmpl, shift in _rotation_plan(params.base):
        planned_at = shift.start - timedelta(days=params.plan_lead_days)
        total_break = params.base.break_minutes_per_12h * tmpl.break_slices
        shift_id = len(shifts) + 1
        real = realized.get((sid, shift.start))
        status, cancelled_at = "planned", None
        if real is not None:
            if real["unit_id"] != unit_id or real["ends_at"] != shift.end or real["template_id"] != tmpl.id:
                raise RuntimeError(f"Plan ile gerçekleşen vardiya uyuşmuyor: personel {sid} {shift.start}")
            matched += 1
            for b in breaks_by_staff.get(sid, []):
                if shift.covers(Interval(b["starts_at"], b["ends_at"])):
                    add_unav(sid, b["starts_at"], b["ends_at"], "mola", planned_at, "planli")
        else:
            not_realized += 1
            # 2.0.0 gelinmeyen vardiyaya mola üretmez; plan aynı kuralla (sabit ortalama konum) tamamlanır.
            for j in range(tmpl.break_slices):
                center = tmpl.minutes * (j + 1) / (tmpl.break_slices + 1)
                offset = max(15, min(int(center // 15) * 15, tmpl.minutes - params.base.break_minutes_per_12h - 15))
                begin = shift.start + timedelta(minutes=offset)
                add_unav(sid, begin, begin + timedelta(minutes=params.base.break_minutes_per_12h), "mola", planned_at, "planli")
            if rng.random() < params.cancel_share:
                status = "cancelled"
                days = float(rng.uniform(*params.cancel_notice_days))
                cancelled_at = max(planned_at, shift.start - timedelta(days=days))
                cancelled += 1
            else:
                notice = shift.start + _minutes(float(rng.uniform(*params.absence_notice_min)))
                add_unav(sid, shift.start, shift.end, "devamsizlik", max(planned_at, notice), "ani")  # type: ignore[arg-type]
                absent += 1
        shifts.append(PlannedShift(shift_id, sid, unit_id, role, tmpl.id, shift.start, shift.end, total_break,  # type: ignore[arg-type]
                                   planned_at, status, cancelled_at, "uretici_2.1:rotasyon"))  # fmt: skip

    if matched != len(base.tables["staff_shifts"]) or not_realized != base.skipped_shifts:
        raise RuntimeError(
            f"Uzlaşma başarısız: eşleşen {matched}/{len(base.tables['staff_shifts'])}, "
            f"gerçekleşmeyen {not_realized} ≠ 2.0.0 skipped_shifts {base.skipped_shifts}"
        )
    for leave in sorted(leaves, key=lambda u: u["id"]):
        if rng.random() < params.leave_planned_share:
            recorded, kind = leave["starts_at"] - timedelta(days=params.leave_planned_lead_days), "planli"
        else:
            recorded, kind = leave["starts_at"] - _minutes(float(rng.uniform(*params.leave_short_notice_min))), "ani"
        add_unav(leave["staff_id"], leave["starts_at"], leave["ends_at"], "izin", recorded, kind)

    plan = PlanSet(tuple(shifts), tuple(unav))
    return DatasetV21(
        params=params, base=base, plan=plan,
        staff_roles={s["id"]: s["role"] for s in base.tables["staff"]},
        unit_ids={u["id"] for u in base.tables["units"]},
        reconciliation={"planli": len(shifts), "gerceklesen": matched, "gerceklesmeyen": not_realized,
                        "onceden_iptal": cancelled, "ani_devamsizlik": absent, "izin": len(leaves)},
    )  # fmt: skip
