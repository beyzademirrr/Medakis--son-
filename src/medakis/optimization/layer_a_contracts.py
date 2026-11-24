"""Aşama 5 / U4 Katman A girdi sözleşmeleri (yalnız bellek; çözücü ve doğrulayıcı ortak kullanır).

Hiçbir alanın kaynaksız varsayılan değeri yoktur: servis öncelik ağırlıkları, fazla kapasite toleransı,
servis-yetkinlik eşlemesi ve onay kilitleri çağırandan gelir. Örneklerde yalnız 'test_sentetik' değerler.
Bu modül çözücü veya veritabanı içe aktarmaz.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from decimal import ROUND_CEILING, Decimal
from typing import Any

from medakis.domain.models import Staff
from medakis.domain.planning import PlanSet
from medakis.domain.service_calendar import ServiceCalendar

WINDOW_MINUTES = 60
GRID_MINUTES = 15
SCALE_O2 = 1000  # O2 göreli açık binde birimi


@dataclass(frozen=True)
class OptimizationConfig:
    """Zorunlu optimizasyon yapılandırması (tasarım K-10, O4 toleransı). Varsayılan YOK."""

    weights: Mapping[int, int]  # servis → O1 öncelik ağırlığı (tam sayı ≥ 1)
    surplus_tolerance: Mapping[int, int]  # servis → O4 toleransı ε (personel-dk, tam sayı ≥ 0)
    time_limit_s: float  # her sözlük aşaması için çözücü süre sınırı
    source: str
    approved_by: str
    known_at: datetime

    def problems(self, unit_ids: set[int]) -> list[str]:
        out = []
        if not self.source.strip() or not self.approved_by.strip():
            out.append("optimizasyon_yapilandirmasi_kaynaksiz")
        for u in sorted(unit_ids):
            w, eps = self.weights.get(u), self.surplus_tolerance.get(u)
            if not isinstance(w, int) or isinstance(w, bool) or w < 1:
                out.append(f"oncelik_agirligi_gecersiz:{u}")
            if not isinstance(eps, int) or isinstance(eps, bool) or eps < 0:
                out.append(f"fazla_toleransi_gecersiz:{u}")
        if set(self.weights) - unit_ids or set(self.surplus_tolerance) - unit_ids:
            out.append("yapilandirmada_bilinmeyen_servis")
        if not (isinstance(self.time_limit_s, (int, float)) and math.isfinite(self.time_limit_s) and self.time_limit_s > 0):
            out.append("cozum_suresi_gecersiz")
        return out


@dataclass(frozen=True)
class UnitSkillRequirement:
    """Bir servise başka servisten gelen rol için zorunlu yetkinlikler (açık yapılandırma, K-6)."""

    unit_id: int
    role: str
    required_skills: tuple[str, ...]
    source: str
    approved_by: str
    known_at: datetime


@dataclass(frozen=True)
class StaffSkill:
    staff_id: int
    skill: str
    valid_from: datetime
    valid_until: datetime | None


@dataclass(frozen=True)
class MoveLock:
    """Onaylı servisler arası taşıma kilidi (tasarım §5.3 Düzeltme 3). Onaysız öneri kilit DEĞİLDİR."""

    staff_id: int
    target_unit_id: int
    starts_at: datetime
    lock_until: datetime
    approval_id: str
    approved_at: datetime
    revoked_at: datetime | None = None

    def active_at(self, at: datetime, window_end: datetime) -> bool:
        """t'de bilinen, iptali t'de bilinmeyen ve pencereyle kesişen kilit."""
        known = self.approved_at <= at
        revoked = self.revoked_at is not None and self.revoked_at <= at
        return known and not revoked and self.starts_at < window_end and self.lock_until > at


@dataclass(frozen=True)
class LayerAInputs:
    at: datetime
    plan: PlanSet  # ham plan; görünüm her zaman known_view(plan, at) ile kurulur
    staff: Mapping[int, Staff]
    units: Mapping[int, Any]  # servis → UnitInfo (comparison.UnitInfo)
    calendars: Sequence[ServiceCalendar]
    u3_rows: Sequence[Mapping[str, Any]]  # U3 compare çıktısı (aynı karar anı)
    skills: Sequence[StaffSkill]
    skill_catalog: frozenset[str]
    unit_skill_requirements: Sequence[UnitSkillRequirement]
    locks: Sequence[MoveLock]
    config: OptimizationConfig | None
    local_tz: tzinfo
    extra: Mapping[str, Any] = field(default_factory=dict)


def demand_staff_minutes(buffered: float, utilization: float) -> int:
    """D = ⌈(ŷ + B) / u⌉ personel-dk; ondalık (Decimal) ile kesin yukarı yuvarlama (kayan nokta artığı yok)."""
    if not (math.isfinite(buffered) and math.isfinite(utilization)) or buffered < 0 or not (0 < utilization <= 1):
        raise ValueError("Talep veya kullanım oranı geçersiz.")
    value = Decimal(repr(float(buffered))) / Decimal(repr(float(utilization)))
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def staff_skills_from_tables(tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[tuple[StaffSkill, ...], frozenset[str]]:
    """Sentetik veri tablolarından personel yetkinlikleri ve yetkinlik sözlüğü."""
    code = {s["id"]: s["code"] for s in tables["skills"]}
    rows = tuple(StaffSkill(r["staff_id"], code[r["skill_id"]], r["valid_from"], r["valid_until"]) for r in tables["staff_skills"])
    return rows, frozenset(code.values())
