"""Sentetik veri parametreleri.

Bütün süreler, katsayılar ve kadro sayıları değiştirilebilir simülasyon varsayımlarıdır;
klinik standart veya onaylı personel oranı değildir.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date

# Üretim mantığı veya varsayılan değerler değişince artırılır; sabit içerik özeti testi de güncellenir.
GENERATOR_VERSION = "1.0.0"

# Bakım gereksinimi düzeyi -> süre katsayısı (sentetik varsayım).
ACUITY_MULTIPLIERS = {1: 0.8, 2: 1.0, 3: 1.2, 4: 1.5, 5: 1.8}

# kod: (ad, temel dakika, gerekli rol, gerekli yetkinlikler) — sentetik varsayım
TASK_TYPES = {
    "VITAL": ("Yaşam bulgusu ölçümü", 8.0, "nurse", ("MON",)),
    "MED": ("İlaç uygulama", 10.0, "nurse", ("MED",)),
    "IV": ("Damar yolu bakımı", 15.0, "nurse", ("IV",)),
    "WOUND": ("Yara bakımı", 20.0, "nurse", ("WOUND",)),
    "HYGIENE": ("Hijyen desteği", 15.0, "support", ()),
    "MOBIL": ("Mobilizasyon desteği", 10.0, "support", ()),
    "TRIAGE": ("Triaj", 6.0, "nurse", ("TRIAGE",)),
    "ROUND": ("Sorumlu vizitesi", 5.0, "supervisor", ()),
}

SKILLS = {
    "MON": "Monitör takibi",
    "MED": "İlaç uygulama",
    "IV": "Damar yolu bakımı",
    "WOUND": "Yara bakımı",
    "TRIAGE": "Triaj",
}


@dataclass(frozen=True)
class SyntheticParams:
    seed: int = 42
    days: int = 28
    start_day: date = date(2026, 10, 1)  # yerel takvim günü; gerçek saat okunmaz
    timezone: str = "Europe/Istanbul"
    inpatient_rooms: int = 15
    beds_per_room: int = 2  # yaklaşık 30 yatak
    emergency_beds: int = 8
    emergency_arrivals_per_hour: float = 1.5
    outpatient_visits_per_weekday: int = 30
    # (servis türü, rol): personel havuzu büyüklüğü
    staff_pool: dict[str, int] = field(
        default_factory=lambda: {
            "inpatient:nurse": 22,
            "inpatient:support": 6,
            "inpatient:supervisor": 3,
            "emergency:nurse": 12,
            "emergency:support": 3,
            "outpatient:nurse": 4,
        }
    )
    # (servis türü, rol, vardiya): vardiya başına planlanan kişi (gece bilerek daha az)
    shift_staffing: dict[str, int] = field(
        default_factory=lambda: {
            "inpatient:nurse:day": 5,
            "inpatient:nurse:night": 3,
            "inpatient:support:day": 2,
            "inpatient:support:night": 1,
            "inpatient:supervisor:day": 1,
            "inpatient:supervisor:night": 1,
            "emergency:nurse:day": 3,
            "emergency:nurse:night": 3,
            "emergency:support:day": 1,
            "emergency:support:night": 1,
            "outpatient:nurse:clinic": 2,
        }
    )
    break_minutes: int = 30
    pending_horizon_hours: int = 12

    def __post_init__(self) -> None:
        if self.days < 1 or self.days > 120:
            raise ValueError("days 1-120 aralığında olmalı")
        if self.seed < 0:
            raise ValueError("seed negatif olamaz")

    def fingerprint(self) -> str:
        """Seed, parametreler ve üretici sürümünden kanonik parmak izi."""
        payload = {"generator_version": GENERATOR_VERSION, **asdict(self)}
        payload["start_day"] = self.start_day.isoformat()
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("ascii")).hexdigest()

    def as_dict(self) -> dict:
        data = asdict(self)
        data["start_day"] = self.start_day.isoformat()
        return data
