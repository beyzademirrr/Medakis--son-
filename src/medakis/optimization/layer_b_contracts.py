"""Aşama 5 / U6: Katman B (görev ataması uygulanabilirlik denetimi) sözleşmeleri.

Çözücü (layer_b.py) ve bağımsız doğrulayıcı (layer_b_verifier.py) bu modülü paylaşır; burada çözücü kodu yoktur.
Kurallar şartname Bölüm 6'dan gelir (tek personel, bölünemez, tek servis; rol ve yetkinlik işin tüm süresinde;
vardiya, mola, izin ve diğer görevlerle çakışma yok; son tarih; pencere). Yeni klinik kural veya yetki tanımlanmaz.
Çıktı bir DENETİMDİR: gerçek atama yapılmaz, hiçbir tabloya yazılmaz, insan incelemesi zorunludur.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from medakis.domain.models import Staff
from medakis.domain.planning import ROLES, PlanSet
from medakis.optimization.layer_a_contracts import WINDOW_MINUTES, StaffSkill

STAGES_B = ("O1_atanmayan_gorev_dk", "O2_yuk_farki_dk")
# Atanmayan görev nedenleri (öncelik sırasıyla; ilk geçerli neden yazılır)
GAP_DEADLINE_PASSED = "son_tarih_gecmis"
GAP_DEADLINE_TOO_CLOSE = "son_tarihe_kadar_sure_yetmiyor"
GAP_NO_ELIGIBLE_STAFF = "rol_yetkinlik_vardiya_uygun_personel_yok"
GAP_NO_SLOT = "uygun_zaman_araligi_yok"
GAP_CAPACITY = "kapasite_yetersiz"
GAP_REASONS = (GAP_DEADLINE_PASSED, GAP_DEADLINE_TOO_CLOSE, GAP_NO_ELIGIBLE_STAFF, GAP_NO_SLOT, GAP_CAPACITY)


@dataclass(frozen=True)
class TaskSpec:
    """Karar anında bilinen bekleyen somut görev (sentetik). Süre tahmini bilinir; gerçek süre bilinmez."""

    task_id: int
    unit_id: int
    role: str
    task_type: str
    estimated_minutes: float
    created_at: datetime
    due_at: datetime
    required_skills: tuple[str, ...]


@dataclass(frozen=True)
class LayerBConfig:
    """Zorunlu yapılandırma; varsayılan değer YOK."""

    time_limit_s: float  # her sözlük aşaması için çözücü süre sınırı
    source: str
    approved_by: str
    known_at: datetime

    def problems(self) -> list[str]:
        out = []
        if not self.source.strip() or not self.approved_by.strip():
            out.append("katman_b_yapilandirmasi_kaynaksiz")
        if not (isinstance(self.time_limit_s, (int, float)) and math.isfinite(self.time_limit_s) and self.time_limit_s > 0):
            out.append("katman_b_sure_siniri_gecersiz")
        return out


@dataclass(frozen=True)
class LayerBInputs:
    at: datetime
    plan: PlanSet  # ham plan; görünüm her zaman known_view(plan, at) ile kurulur
    staff: Mapping[int, Staff]
    units: Sequence[int]
    tasks: Sequence[TaskSpec]  # karar anında bilinen BEKLEYEN görevler (tasks_known_at)
    skills: Sequence[StaffSkill]
    skill_catalog: frozenset[str]
    config: LayerBConfig | None
    forecasts: Mapping[tuple[int, str], float] = field(default_factory=dict)  # (servis, rol) → ŷ iş-dk, [t, t+60)
    uncertain_task_ids: Sequence[int] = ()  # iptal edilmiş ama iptal zamanı bilinmeyen görevler (atanmaz)
    extra: Mapping[str, Any] = field(default_factory=dict)


def task_minutes(estimated: float) -> int:
    """Görev süresi tam sayı dakikaya YUKARI yuvarlanır (şartname Bölüm 6)."""
    return math.ceil(float(estimated) - 1e-9)


def window_minutes() -> int:
    return WINDOW_MINUTES


def tasks_known_at(tables: Mapping[str, Sequence[Mapping[str, Any]]], at: datetime,
                   units: Sequence[int]) -> tuple[tuple[TaskSpec, ...], tuple[int, ...]]:  # fmt: skip
    """`at` anında bilinen bekleyen görevler ve iptal zamanı bilinmeyen (belirsiz) görevler.

    Bekleyen: created_at ≤ at ve (tamamlanmadı ya da tamamlanma > at) ve (iptal edilmedi ya da iptal > at).
    İptal zamanı bilinmeyen kayıt (cancelled_at yok) sessizce bekleyen ya da kapanmış sayılmaz; belirsiz raporlanır.
    """
    types = {t["id"]: t for t in tables["task_types"]}
    skill_code = {s["id"]: s["code"] for s in tables["skills"]}
    req: dict[int, list[str]] = {}
    for r in tables["task_skill_requirements"]:
        req.setdefault(r["task_type_id"], []).append(skill_code[r["skill_id"]])
    wanted = set(units)
    pending: list[TaskSpec] = []
    uncertain: list[int] = []
    for r in tables["care_tasks"]:
        if r["unit_id"] not in wanted or r["created_at"] > at:
            continue
        if r["status"] == "completed" and r["completed_at"] is not None and r["completed_at"] <= at:
            continue
        if r["status"] == "cancelled":
            if r.get("cancelled_at") is None:
                uncertain.append(r["id"])
                continue
            if r["cancelled_at"] <= at:
                continue
        t = types[r["task_type_id"]]
        pending.append(TaskSpec(r["id"], r["unit_id"], t["required_role"], t["code"], float(r["estimated_minutes"]),
                                r["created_at"], r["due_at"], tuple(sorted(req.get(t["id"], ())))))  # fmt: skip
    return tuple(sorted(pending, key=lambda x: x.task_id)), tuple(sorted(uncertain))


def input_problems(inp: LayerBInputs) -> list[str]:
    """MODEL_INVALID nedenleri (çözücü ve doğrulayıcı ayrı ayrı çağırır; kurallar burada tanımlıdır)."""
    out: list[str] = []
    if inp.config is None:
        out.append("katman_b_yapilandirmasi_yok")
    else:
        out += inp.config.problems()
        if inp.config.known_at > inp.at:  # gelecek bilgi: yapılandırma karar anında bilinmiyordu
            out.append("katman_b_yapilandirmasi_karar_aninda_bilinmiyor")
    if inp.plan.as_of is not None and inp.plan.as_of < inp.at:  # Katman A ile aynı kural: eski görünüm kabul edilmez
        out.append("plan_gorunumu_karar_anindan_eski")
    seen: set[int] = set()
    units = set(inp.units)
    for t in inp.tasks:
        tag = f"gorev:{t.task_id}"
        if t.task_id in seen:
            out.append(f"yinelenen_gorev:{t.task_id}")
        seen.add(t.task_id)
        if t.unit_id not in units:
            out.append(f"bilinmeyen_servis:{tag}")
        if t.role not in ROLES:
            out.append(f"gecersiz_rol:{tag}")
        if not (math.isfinite(t.estimated_minutes) and t.estimated_minutes > 0):
            out.append(f"gecersiz_sure:{tag}")
        if t.due_at < t.created_at:  # eşitlik geçerli (anında yapılacak iş; üretici denetimi created ≤ due)
            out.append(f"ters_son_tarih:{tag}")
        if t.created_at > inp.at:
            out.append(f"gelecekte_olusan_gorev:{tag}")
        for sk in t.required_skills:
            if sk not in inp.skill_catalog:
                out.append(f"bilinmeyen_yetkinlik:{tag}:{sk}")
    for s in inp.skills:
        if s.valid_until is not None and s.valid_until <= s.valid_from:
            out.append(f"gecersiz_yetkinlik_araligi:{s.staff_id}:{s.skill}")
    for key, value in inp.forecasts.items():
        if value is None or not math.isfinite(float(value)) or float(value) < 0:
            out.append(f"gecersiz_tahmin:{key[0]}:{key[1]}")
    return sorted(set(out))
