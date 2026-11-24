"""Türkiye tatil takvimi (2429 sayılı Ulusal Bayram ve Genel Tatiller Hakkında Kanun, güncel metin).

Dayanak (Mevzuat Bilgi Sistemi, son değişiklik 6752 sayılı Kanun, yürürlük 29.10.2016):
- m.1: 29 Ekim Ulusal Bayram; bayram 28 Ekim saat 13.00'ten itibaren başlar.
- m.2/A: 23 Nisan, 19 Mayıs, 30 Ağustos resmî bayram.
- m.2/B: Ramazan Bayramı arefe 13.00'ten itibaren 3,5 gün; Kurban Bayramı 4,5 gün (tarih kanunda yok).
- m.2/C: 1 Ocak yılbaşı, 1 Mayıs, 15 Temmuz genel tatil.
- m.2/D: Ulusal bayram ve genel tatil Cuma akşamı sona erdiğinde ertesi Cumartesi tamamı tatildir.
- 31 Aralık tatil DEĞİLDİR (kanundaki tek hüküm yalnız 1999 yılı için geçici maddedir).

Dini bayram tarihleri yalnız doğrulama düzeyi D1 olan kayıtlardan kullanılır; diğer kayıtlar üretim
dönemini reddeden koruma pencereleridir. İdari izin (köprü) günleri modellenmez.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path

DATA_FILE = Path(__file__).with_name("data") / "tr_dini_bayramlar.json"
HALF_DAY_START = time(13, 0)
LAW = "2429 sayılı Kanun"

FIXED_FULL = {
    (1, 1): ("Yılbaşı", "m.2/C"),
    (4, 23): ("Ulusal Egemenlik ve Çocuk Bayramı", "m.2/A-1"),
    (5, 1): ("Emek ve Dayanışma Günü", "m.2/C"),
    (5, 19): ("Atatürk'ü Anma, Gençlik ve Spor Bayramı", "m.2/A-2"),
    (7, 15): ("Demokrasi ve Millî Birlik Günü", "m.2/C"),
    (8, 30): ("Zafer Bayramı", "m.2/A-3"),
    (10, 29): ("Cumhuriyet Bayramı", "m.1"),
}
FIXED_HALF = {(10, 28): ("Cumhuriyet Bayramı arifesi (13.00'ten itibaren)", "m.1")}


class CalendarError(ValueError):
    """Takvim, doğrulanmış kaynakla üretilemiyor."""


@dataclass(frozen=True)
class HolidayDay:
    day: date
    kind: str  # "tam" | "yarim_13"
    name: str
    basis: str

    @property
    def closes_at(self) -> time | None:
        """Yarım günde resmî dairelerin kapandığı yerel saat; tam günde None."""
        return HALF_DAY_START if self.kind == "yarim_13" else None


def _religious_records() -> tuple[int, list[dict]]:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return int(data["koruma_tamponu_gun"]), list(data["kayitlar"])


def _religious_days(record: dict) -> list[HolidayDay]:
    arefe = date.fromisoformat(record["arefe"])
    days = [HolidayDay(arefe, "yarim_13", f"{record['ad']} arefesi", f"{LAW} m.2/B")]
    days += [
        HolidayDay(arefe + timedelta(days=i), "tam", f"{record['ad']} {i}. gün", f"{LAW} m.2/B")
        for i in range(1, int(record["gun"]) + 1)
    ]
    return days


def holidays_between(first: date, last: date) -> dict[date, HolidayDay]:
    """[first, last] aralığındaki resmî tatiller. Doğrulanmamış dini bayram penceresine değerse hata."""
    if last < first:
        raise CalendarError("Takvim aralığı ters.")
    buffer, records = _religious_records()
    covered_years = {date.fromisoformat(r["arefe"]).year for r in records}
    missing = {y for y in range(first.year, last.year + 1)} - covered_years
    if missing:
        raise CalendarError(f"Dini bayram kaydı olmayan yıl: {sorted(missing)}; takvim üretilemez.")

    out: dict[date, HolidayDay] = {}
    for record in records:
        days = _religious_days(record)
        guard_start = days[0].day - timedelta(days=buffer)
        guard_end = days[-1].day + timedelta(days=buffer)
        if guard_end < first or guard_start > last:
            continue
        if record["dogrulama"] != "D1":
            raise CalendarError(
                f"{record['ad']} ({record['arefe']}) doğrulanmamış ({record['dogrulama']}); "
                "üretim dönemi bu bayramın koruma penceresine değiyor."
            )
        out.update({d.day: d for d in days if first <= d.day <= last})

    day = first
    while day <= last:
        key = (day.month, day.day)
        if key in FIXED_FULL:
            name, article = FIXED_FULL[key]
            out[day] = HolidayDay(day, "tam", name, f"{LAW} {article}")
        elif key in FIXED_HALF and day not in out:
            name, article = FIXED_HALF[key]
            out[day] = HolidayDay(day, "yarim_13", name, f"{LAW} {article}")
        day += timedelta(days=1)

    # m.2/D: tam gün tatil Cuma ise ertesi Cumartesi de tatildir.
    for holiday in sorted(out.values(), key=lambda h: h.day):
        saturday = holiday.day + timedelta(days=1)
        if holiday.kind == "tam" and holiday.day.weekday() == 4 and saturday <= last and saturday not in out:
            out[saturday] = HolidayDay(saturday, "tam", f"{holiday.name} (Cuma kuralı)", f"{LAW} m.2/D")
    return dict(sorted(out.items()))


@dataclass(frozen=True)
class DayInfo:
    day: date
    weekday: int
    holiday: HolidayDay | None
    after_holiday: bool  # tam gün tatilden sonraki ilk tatil olmayan gün

    @property
    def is_full_holiday(self) -> bool:
        return self.holiday is not None and self.holiday.kind == "tam"

    @property
    def is_half_holiday(self) -> bool:
        return self.holiday is not None and self.holiday.kind == "yarim_13"

    @property
    def is_weekend(self) -> bool:
        return self.weekday >= 5

    @property
    def is_workday(self) -> bool:
        """Poliklinik açık mı (hafta içi ve tam gün tatil değil)."""
        return not self.is_weekend and not self.is_full_holiday


def day_infos(first: date, days: int) -> list[DayInfo]:
    last = first + timedelta(days=days - 1)
    holidays = holidays_between(first - timedelta(days=1), last)
    out = []
    for i in range(days):
        day = first + timedelta(days=i)
        prev = holidays.get(day - timedelta(days=1))
        holiday = holidays.get(day)
        out.append(DayInfo(day, day.weekday(), holiday, holiday is None and prev is not None and prev.kind == "tam"))
    return out
