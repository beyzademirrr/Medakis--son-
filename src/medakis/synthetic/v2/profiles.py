"""Üretici 2.0 parametreleri, profilleri ve köken etiketleri (kaynak / kural / varsayım).

Her parametrenin bir köken etiketi vardır; etiketler parmak izine girmez (yalnız değerler girer).
Kaynaklı değerler yalnız kalibrasyon içindir; bağımsız doğrulama kaynakları ayrı tutulur ve
kalibrasyonda kullanılmaz. Hekim rolü yoktur: yalnız nurse, supervisor, support.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import date

GENERATOR_VERSION = "2.0.0"
ROLES = ("nurse", "supervisor", "support")

# ---- Köken etiketleri -----------------------------------------------------------------------


@dataclass(frozen=True)
class Provenance:
    kind: str  # "kaynak" | "kural" | "varsayim" | "tasarim"
    note: str
    source: str = ""
    year: str = ""
    purpose: str = ""  # kaynakta: "kalibrasyon"


SB_2025 = "Sağlık Bakanlığı, Sağlık İstatistikleri Yıllığı 2025 haber bülteni (Tablo 16)"
ACIL_YON = "Acil Sağlık Hizmetleri Yönetmeliği (RG 13.09.2022/31952; değişiklik RG 11.10.2024/32689)"


def _v(note: str) -> Provenance:
    return Provenance("varsayim", note)


def _d(note: str) -> Provenance:
    return Provenance("tasarim", note)


# ---- Parametreler ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamsV2:
    seed: int = 42
    start_day: date = date(2026, 10, 5)  # pazartesi; gerçek saat okunmaz
    days: int = 112  # 16 hafta
    timezone: str = "Europe/Istanbul"
    # Yataklı servis: (profil adı, hedef doluluk) -> her profil ayrı bir yataklı servis
    inpatient_profiles: tuple[tuple[str, float], ...] = (("normal", 0.545), ("yuksek", 0.88))
    inpatient_rooms_per_unit: int = 15
    inpatient_beds_per_room: int = 2
    inpatient_alos_days: float = 4.0
    inpatient_los_sigma: float = 0.5
    inpatient_shift_pattern: str = "12_12"  # "12_12" | "8_16"
    inpatient_warmup_days: int = 28
    # Acil servis
    ed_level_label: str = "II"  # yalnız bağlam etiketi; kadro bundan türetilmez
    ed_beds: int = 8
    ed_base_arrivals_per_hour: float = 1.5
    ed_volume_profile: str = "taban"  # "taban" | "yuksek"
    ed_triage_profile: str = "orta"  # "orta" | "yesil_yogun" | "sari_yogun"
    ed_staffing_profile: str = "temel"  # "temel" | "yuksek"
    # Poliklinik
    outpatient_rooms: int = 3
    outpatient_slot_minutes: int = 15
    outpatient_fill_rate: float = 0.90
    outpatient_no_show_rate: float = 0.10
    outpatient_walk_in_rate: float = 0.10
    outpatient_nurses_per_day: int = 3
    # Personel (havuz büyüklüğü)
    staff_pool: dict[str, int] = field(
        default_factory=lambda: {
            "inpatient:nurse": 22, "inpatient:support": 8, "inpatient:supervisor": 4,
            "emergency:nurse": 20, "emergency:support": 8, "emergency:supervisor": 4,
            "outpatient:nurse": 5,
        }  # fmt: skip
    )
    # Yürütme
    clinical_cancel_rate: float = 0.05
    shift_no_show_rate: float = 0.03
    short_leave_rate: float = 0.02
    short_leave_minutes: int = 120
    break_minutes_per_12h: int = 30
    pending_horizon_hours: int = 12

    def __post_init__(self) -> None:
        if not 1 <= self.days <= 120:
            raise ValueError("days 1-120 aralığında olmalı")
        if self.seed < 0:
            raise ValueError("seed negatif olamaz")
        if self.inpatient_shift_pattern not in SHIFT_PATTERNS:
            raise ValueError("inpatient_shift_pattern geçersiz")
        if self.ed_volume_profile not in ED_VOLUME_PROFILES:
            raise ValueError("ed_volume_profile geçersiz")
        if self.ed_triage_profile not in TRIAGE_PROFILES:
            raise ValueError("ed_triage_profile geçersiz")
        if self.ed_staffing_profile not in ED_STAFFING_PROFILES:
            raise ValueError("ed_staffing_profile geçersiz")
        for name, occupancy in self.inpatient_profiles:
            if not 0 < occupancy < 1:
                raise ValueError(f"{name}: hedef doluluk 0-1 arasında olmalı")
        if not self.inpatient_profiles:
            raise ValueError("en az bir yataklı servis profili gerekli")
        for key in self.staff_pool:
            if key.split(":")[1] not in ROLES:
                raise ValueError(f"{key}: yalnız nurse, supervisor, support modellenir")

    def as_dict(self) -> dict:
        data = asdict(self)
        data["start_day"] = self.start_day.isoformat()
        data["inpatient_profiles"] = [list(p) for p in self.inpatient_profiles]
        return data

    def fingerprint(self) -> str:
        payload = {"generator_version": GENERATOR_VERSION, **self.as_dict()}
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("ascii")).hexdigest()


PARAM_PROVENANCE: dict[str, Provenance] = {
    "seed": _d("Tekrarlanabilirlik tohumu"),
    "start_day": _d("Dönem başlangıcı; 16 haftalık önerilen dönem (tasarım D9)"),
    "days": _d("12-16 hafta (tasarım D9); üst sınır 120"),
    "timezone": _d("Europe/Istanbul"),
    "inpatient_profiles": Provenance(
        "kaynak", "normal = ülke ortalaması yatak doluluğu %54,5; yuksek = %88 stres profili VARSAYIM (Türkiye kaynağı yok)",
        SB_2025, "2025", "kalibrasyon",
    ),
    "inpatient_rooms_per_unit": _d("Servis büyüklüğü tasarım seçimi"),
    "inpatient_beds_per_room": _d("Servis büyüklüğü tasarım seçimi"),
    "inpatient_alos_days": Provenance("kaynak", "Ortalama kalış süresi 4,0 gün", SB_2025, "2025", "kalibrasyon"),
    "inpatient_los_sigma": _v("Log-normal kalış dağılımının biçimi"),
    "inpatient_shift_pattern": _v("12/12 varsayılan; 8/16 duyarlılık profili (birincil kaynak okunmadı)"),
    "inpatient_warmup_days": _d("Dönem başında servisin dolu başlaması için ısınma süresi"),
    "ed_level_label": Provenance(
        "kural", "Acil düzeyi yalnız bağlam etiketi; yönetmelikteki sağlık personeli asgarisi kadroya eşlenmez",
        ACIL_YON, "2022/2024",
    ),
    "ed_beds": _v("Acil gözlem yatağı sayısı"),
    "ed_base_arrivals_per_hour": _v("Ülke düzeyinde acil başvuru hacmi okunamadı (D4)"),
    "ed_volume_profile": _v("taban ×1, yuksek ×2"),
    "ed_triage_profile": _v("Türkiye çalışmalarında yeşil alan %22-75 (D3); üç profil bu aralığı kapsar"),
    "ed_staffing_profile": _v("Acil hemşire/destek/sorumlu kadrosu; mevzuattan türetilmez"),
    "outpatient_rooms": _v("Poliklinik slot kaynağı (hekim modellenmez)"),
    "outpatient_slot_minutes": _v("MHRS en az 10 dk aralığı D3; varsayılan 15 dk"),
    "outpatient_fill_rate": _v("Slot doluluk oranı"),
    "outpatient_no_show_rate": _v("Gelmeme oranı; Türkiye kaynağı bulunamadı; duyarlılık %5-20"),
    "outpatient_walk_in_rate": _v("Randevusuz başvuru oranı"),
    "outpatient_nurses_per_day": _v("Poliklinik hemşire sayısı"),
    "staff_pool": _v("Rotasyon için personel havuzu büyüklüğü"),
    "clinical_cancel_rate": _v("Klinik nedenle iptal (gerek kalmadı) oranı"),
    "shift_no_show_rate": _v("Planlanmış ama gerçekleşmeyen vardiya oranı (kadro açığı dönemleri)"),
    "short_leave_rate": _v("Vardiya içi saatlik izin olasılığı"),
    "short_leave_minutes": _v("Saatlik izin süresi"),
    "break_minutes_per_12h": _v("12 saatlik vardiyada somut mola süresi"),
    "pending_horizon_hours": _d("Senaryo anından sonra bilinen planlı iş ufku"),
}

# ---- Profiller ve sabitler (hepsi köken etiketli) -------------------------------------------

SHIFT_PATTERNS = {
    # vardiya adı -> (şablon adı, yerel başlangıç saati, dakika, mola dilimleri)
    "12_12": {"day": ("GUNDUZ", 8, 720, 1), "night": ("GECE", 20, 720, 1)},
    "8_16": {"day": ("GUNDUZ8", 8, 480, 1), "night": ("GECE16", 16, 960, 2)},
}
ROTATION = ("day", "day", "night", "night", None, None, None)  # 7 günlük döngü

INPATIENT_STAFFING = {  # rol -> (hafta içi gündüz, gece, hafta sonu/tatil gündüz)
    "nurse": (5, 3, 4), "support": (2, 1, 1), "supervisor": (1, 1, 1),
}  # fmt: skip
ED_STAFFING_PROFILES = {
    "temel": {"nurse": (3, 3), "support": (1, 1), "supervisor": (1, 0)},
    "yuksek": {"nurse": (5, 5), "support": (2, 2), "supervisor": (1, 1)},
}
ED_VOLUME_PROFILES = {"taban": 1.0, "yuksek": 2.0}
# Saat başına acil başvuru çarpanı (1.0.0 ile aynı; Bozyaka 2011 yalnız biçim doğrulamasıdır)
ED_HOURLY_PROFILE = (0.4, 0.3, 0.3, 0.3, 0.3, 0.4, 0.6, 0.8, 1.0, 1.1, 1.2, 1.2,
                     1.2, 1.1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.5, 1.4, 1.2, 0.9, 0.6)  # fmt: skip
ED_WEEKDAY_FACTOR = (1.10, 1.0, 1.0, 1.0, 1.0, 0.95, 1.0)
ED_HOLIDAY_FACTOR = 1.10
ED_AFTER_HOLIDAY_FACTOR = 1.10
TRIAGE_PROFILES = {
    "orta": {"red": 0.10, "yellow": 0.40, "green": 0.50},
    "yesil_yogun": {"red": 0.05, "yellow": 0.20, "green": 0.75},
    "sari_yogun": {"red": 0.15, "yellow": 0.55, "green": 0.30},
}
TRIAGE_ACUITY = {"red": (4, 5), "yellow": (2, 3, 4), "green": (1, 2)}
TRIAGE_LOS_MEDIAN_H = {"red": 4.0, "yellow": 3.0, "green": 1.5}
TRIAGE_LOS_SIGMA = 0.6
ED_MAX_STAY_MIN = 480  # yönetmelik m.11/3: acilde izlem en fazla 8 saat
ED_MAX_TRANSFER_WAIT_MIN = 120  # m.11/4: karar sonrası en fazla 2 saat
ED_MIN_STAY_MIN = 20
TRIAGE_OUTCOME = {  # taburcu, yatış, sevk
    "red": (0.45, 0.45, 0.10), "yellow": (0.75, 0.21, 0.04), "green": (0.95, 0.04, 0.01),
}  # fmt: skip
TRIAGE_VITAL_INTERVAL_MIN = {"red": 15, "yellow": 30, "green": 60}
TRIAGE_MED_P = {"red": 0.9, "yellow": 0.7, "green": 0.4}
TRIAGE_SAMPLE_P = {"red": 0.9, "yellow": 0.7, "green": 0.3}
TRIAGE_PRIORITY = {"red": 0, "yellow": 1, "green": 2}

INPATIENT_WEEKDAY_FACTOR = (1.1, 1.1, 1.1, 1.1, 1.1, 0.8, 0.6)
INPATIENT_HOLIDAY_FACTOR = 0.6
INPATIENT_ADMIT_HOUR_WEIGHTS = (0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 4, 3, 3, 3, 3, 3, 2, 2, 1, 1, 1, 0, 0)
INPATIENT_DISCHARGE_WINDOW_H = (10, 16)
INPATIENT_WEEKEND_DISCHARGE_KEEP = 0.6  # hafta sonu taburculuğun gerçekleşme olasılığı; kalanı ertelenir
INPATIENT_LOS_CLIP_H = (12, 14 * 24)
INPATIENT_ACUITY = (0.15, 0.35, 0.30, 0.15, 0.05)
INPATIENT_IV_P = 0.3
INPATIENT_WOUND_P = 0.2
INPATIENT_SAMPLE_P = 0.6
BED_TURNAROUND_MIN = 60

OUTPATIENT_SESSIONS = ((8, 0, 12, 0), (13, 0, 17, 0))
OUTPATIENT_ACUITY = (0.50, 0.40, 0.10, 0.0, 0.0)
OUTPATIENT_LATENESS = (-5.0, 10.0, -30.0, 45.0)  # ortalama, sd, alt, üst (dk)
OUTPATIENT_EXAM_MIN = (8.0, 20.0)
OUTPATIENT_POST_TASK_P = {"MED": 0.15, "SAMPLE": 0.25, "WOUND": 0.05, "EDU": 0.20}
OUTPATIENT_POST_TASK_BUFFER_MIN = 45

ACUITY_MULTIPLIERS = {1: 0.8, 2: 1.0, 3: 1.2, 4: 1.5, 5: 1.8}
ACUITY_DAILY_CHANGE_P = 0.3

# kod: (ad, temel dakika, rol, yetkinlikler)
TASK_TYPES = {
    "VITAL": ("Yaşam bulgusu ölçümü", 8.0, "nurse", ("MON",)),
    "MED": ("İlaç uygulama", 10.0, "nurse", ("MED",)),
    "IV": ("Damar yolu bakımı", 15.0, "nurse", ("IV",)),
    "WOUND": ("Yara bakımı / pansuman", 20.0, "nurse", ("WOUND",)),
    "HYGIENE": ("Hijyen desteği", 15.0, "support", ()),
    "MOBIL": ("Mobilizasyon desteği", 10.0, "support", ()),
    "TRIAGE": ("Triaj değerlendirmesi", 6.0, "nurse", ("TRIAGE",)),
    "ROUND": ("Sorumlu hemşire vizitesi", 5.0, "supervisor", ()),
    "ADMIT": ("Kabul / hemşirelik ön değerlendirmesi", 20.0, "nurse", ("MON",)),
    "ADMIT_P": ("Poliklinik ön değerlendirmesi", 6.0, "nurse", ("MON",)),
    "DISCH": ("Taburculuk eğitimi ve işlemleri", 20.0, "nurse", ()),
    "SAMPLE": ("Kan / numune alma", 6.0, "nurse", ("SAMPLE",)),
    "EDU": ("Hasta eğitimi", 10.0, "nurse", ()),
    "TRANSF": ("Hasta transferi ve refakat", 15.0, "support", ()),
    "HANDOVER": ("Vardiya devir teslimi (hemşire)", 1.0, "nurse", ()),
    "HANDOVER_SUP": ("Vardiya devir teslimi (sorumlu hemşire)", 1.0, "supervisor", ()),
}
SKILLS = {
    "MON": "Monitör takibi", "MED": "İlaç uygulama", "IV": "Damar yolu bakımı",
    "WOUND": "Yara bakımı", "TRIAGE": "Triaj", "SAMPLE": "Numune alma",
}  # fmt: skip
NURSE_SKILL_P = {"IV": 0.8, "WOUND": 0.6, "TRIAGE_inpatient": 0.2}

# Yürütme kuralları (sabit hizmet sırası kuralı; amaç fonksiyonu yok)
EARLY_START_MIN = {"inpatient": 15, "emergency": 0, "outpatient": 0}
LATE_AFTER_MIN = {"inpatient": 30, "emergency": 10, "outpatient": 20}
GIVE_UP_AFTER_MIN = {"inpatient": 120, "emergency": 30, "outpatient": 60}
TASK_LOOKAHEAD_H = 12
ACTUAL_SIGMA = 0.2
NIGHT_FACTOR = 1.15
NIGHT_HOURS = (0, 6)
ACTUAL_RATIO_CLIP = (0.4, 3.0)

CONSTANT_PROVENANCE: dict[str, Provenance] = {
    "SHIFT_PATTERNS": _v("Vardiya şablonları ve mola dilimleri"),
    "ROTATION": _v("7 günlük rotasyon: 2 gündüz, 2 gece, 3 izin"),
    "INPATIENT_STAFFING": _v("Yataklı kadro (hafta içi gündüz / gece / hafta sonu-tatil gündüz)"),
    "ED_STAFFING_PROFILES": _v("Acil kadro profilleri; mevzuattaki sağlık personeli asgarisinden türetilmez"),
    "ED_VOLUME_PROFILES": _v("Acil hacim çarpanları"),
    "ED_HOURLY_PROFILE": _v("Saatlik acil profil; Bozyaka 2011 (D1) yalnız bağımsız biçim doğrulaması"),
    "ED_WEEKDAY_FACTOR": _v("Haftanın günü acil çarpanı"),
    "ED_HOLIDAY_FACTOR": _v("Tatil günü acil çarpanı"),
    "ED_AFTER_HOLIDAY_FACTOR": _v("Tatil ertesi acil çarpanı"),
    "TRIAGE_PROFILES": _v("Triaj dağılım profilleri (D3 aralık %22-75 yeşil)"),
    "TRIAGE_ACUITY": _v("Triaj -> bakım düzeyi eşlemesi"),
    "TRIAGE_LOS_MEDIAN_H": _v("Triaja göre acil kalış medyanı"),
    "TRIAGE_LOS_SIGMA": _v("Acil kalış log-normal biçimi"),
    "ED_MAX_STAY_MIN": Provenance("kural", "Acilde izlem en fazla 8 saat (m.11/3; D2)", ACIL_YON, "2022"),
    "ED_MAX_TRANSFER_WAIT_MIN": Provenance("kural", "Karar sonrası en fazla 2 saatte transfer (m.11/4; D2)", ACIL_YON, "2022"),
    "ED_MIN_STAY_MIN": _v("En kısa acil kalış"),
    "TRIAGE_OUTCOME": _v("Triaja göre taburcu/yatış/sevk olasılığı"),
    "TRIAGE_VITAL_INTERVAL_MIN": _v("Triaja göre yaşam bulgusu aralığı"),
    "TRIAGE_MED_P": _v("Triaja göre ilaç olasılığı"),
    "TRIAGE_SAMPLE_P": _v("Triaja göre numune olasılığı"),
    "TRIAGE_PRIORITY": _d("Hizmet sırası önceliği (eşit vadede)"),
    "INPATIENT_WEEKDAY_FACTOR": _v("Haftanın günü yatış çarpanı (haftalık ortalamaya normalize edilir)"),
    "INPATIENT_HOLIDAY_FACTOR": _v("Tatil günü yatış çarpanı"),
    "INPATIENT_ADMIT_HOUR_WEIGHTS": _v("Yatış saati ağırlıkları"),
    "INPATIENT_DISCHARGE_WINDOW_H": _v("Taburculuk saat penceresi 10-16"),
    "INPATIENT_WEEKEND_DISCHARGE_KEEP": _v("Hafta sonu taburculuk olasılığı 0,6"),
    "INPATIENT_LOS_CLIP_H": _v("Kalış kırpma aralığı 12 sa - 14 gün"),
    "INPATIENT_ACUITY": _v("Yataklı bakım düzeyi dağılımı"),
    "INPATIENT_IV_P": _v("Damar yolu bakımı gereken hasta oranı"),
    "INPATIENT_WOUND_P": _v("Yara bakımı gereken hasta oranı"),
    "INPATIENT_SAMPLE_P": _v("Günlük numune olasılığı"),
    "BED_TURNAROUND_MIN": _v("Taburculuk sonrası yatak hazırlığı 60 dk"),
    "OUTPATIENT_SESSIONS": _v("Poliklinik oturumları 08-12 ve 13-17 (kamu mesai düzeni birincil kaynaktan okunmadı)"),
    "OUTPATIENT_ACUITY": _v("Poliklinik bakım düzeyi dağılımı"),
    "OUTPATIENT_LATENESS": _v("Geç/erken gelme dağılımı"),
    "OUTPATIENT_EXAM_MIN": _v("Muayene süresi (hekim modellenmez; kalış bileşeni)"),
    "OUTPATIENT_POST_TASK_P": _v("Muayene sonrası hemşirelik görevi olasılıkları"),
    "OUTPATIENT_POST_TASK_BUFFER_MIN": _v("Muayene sonrası işlem için ayrılan süre"),
    "ACUITY_MULTIPLIERS": _v("Bakım düzeyi süre katsayıları (1.0.0 ile aynı)"),
    "ACUITY_DAILY_CHANGE_P": _v("Günlük bakım düzeyi değişim olasılığı"),
    "TASK_TYPES": _v("Görev türleri ve temel süreler (şema: duration_source = synthetic_assumption)"),
    "SKILLS": _d("Yetkinlik sözlüğü"),
    "NURSE_SKILL_P": _v("Hemşire yetkinlik dağılımı"),
    "EARLY_START_MIN": _v("Planlı görevin vadesinden önce başlayabileceği süre"),
    "LATE_AFTER_MIN": _v("Gecikmeli sayılma eşiği"),
    "GIVE_UP_AFTER_MIN": _v("Kapasite nedeniyle karşılanamayan iş eşiği"),
    "TASK_LOOKAHEAD_H": _v("Rutin bakım planının önceden bilinme süresi"),
    "ACTUAL_SIGMA": _v("Gerçek/tahmini süre log-normal biçimi"),
    "NIGHT_FACTOR": _v("Gece görev süresi katsayısı"),
    "NIGHT_HOURS": _v("Gece saatleri 00-06"),
    "ACTUAL_RATIO_CLIP": _v("Gerçek/tahmini süre oranı kırpması (korunum C5)"),
    "calendar.FIXED_FULL / FIXED_HALF": Provenance(
        "kural", "Resmî tatiller; 28 Ekim ve arefe 13.00'ten itibaren; Cuma kuralı; 31 Aralık tatil değil (D1)",
        "2429 sayılı Ulusal Bayram ve Genel Tatiller Hakkında Kanun (son değişiklik 6752 s. Kanun)", "2016",
    ),
    "encounters.CALIBRATION_MAX_FACTOR / CALIBRATION_STEPS": _d(
        "Yataklı doluluk kalibrasyonu: hız çarpanı 0,5-2,0 aralığında 18 adım ikiye bölme (ortak rastgele sayılar)"
    ),
    "encounters.discharge_calendar_shift_days": _d(
        "Taburculuk saat penceresi ve hafta sonu ertelemesinin kalışa eklediği süre ham ortalamadan düşülür"
    ),
    "calendar.dini_bayram_kayitlari": _v("Dini bayram tarihleri D3; yalnız koruma penceresi, tatil olarak kullanılmaz"),
}

KIND_TITLES = {"kaynak": "Kaynaklı (kalibrasyon)", "kural": "Kural (mevzuat)", "varsayim": "Varsayım", "tasarim": "Tasarım seçimi"}


def declaration_markdown() -> str:
    """Parametre bildirimi: kaynaklı, kural, varsayım ve tasarım parametreleri ayrı tablolarda."""
    defaults = ParamsV2()
    rows: dict[str, list[str]] = {k: [] for k in KIND_TITLES}
    for f in fields(ParamsV2):
        p = PARAM_PROVENANCE[f.name]
        value = getattr(defaults, f.name)
        rows[p.kind].append(f"| `{f.name}` | `{value}` | {p.note} | {p.source or '—'} | {p.year or '—'} |")
    for name, p in CONSTANT_PROVENANCE.items():
        rows[p.kind].append(f"| `{name}` | sabit | {p.note} | {p.source or '—'} | {p.year or '—'} |")
    out = [
        "# Üretici 2.0 parametre bildirimi",
        "",
        f"Üretici sürümü: `{GENERATOR_VERSION}`. Bu dosya `declaration_markdown()` ile üretilir; elle düzenlenmez.",
        "Kaynaklı değerler yalnız kalibrasyon içindir. Bağımsız doğrulama kaynakları (SB 2024 bülteni,",
        "Bozyaka 2011 çalışması) burada kullanılmaz.",
        "",
    ]
    for kind, title in KIND_TITLES.items():
        out += [f"## {title}", "", "| Ad | Varsayılan | Açıklama | Kaynak | Yıl |", "|---|---|---|---|---|"]
        out += rows[kind] or ["| — | — | — | — | — |"]
        out.append("")
    return "\n".join(out)
