# Üretici 2.0 parametre bildirimi

Üretici sürümü: `2.0.0`. Bu dosya `declaration_markdown()` ile üretilir; elle düzenlenmez.
Kaynaklı değerler yalnız kalibrasyon içindir. Bağımsız doğrulama kaynakları (SB 2024 bülteni,
Bozyaka 2011 çalışması) burada kullanılmaz.

## Kaynaklı (kalibrasyon)

| Ad | Varsayılan | Açıklama | Kaynak | Yıl |
|---|---|---|---|---|
| `inpatient_profiles` | `(('normal', 0.545), ('yuksek', 0.88))` | normal = ülke ortalaması yatak doluluğu %54,5; yuksek = %88 stres profili VARSAYIM (Türkiye kaynağı yok) | Sağlık Bakanlığı, Sağlık İstatistikleri Yıllığı 2025 haber bülteni (Tablo 16) | 2025 |
| `inpatient_alos_days` | `4.0` | Ortalama kalış süresi 4,0 gün | Sağlık Bakanlığı, Sağlık İstatistikleri Yıllığı 2025 haber bülteni (Tablo 16) | 2025 |

## Kural (mevzuat)

| Ad | Varsayılan | Açıklama | Kaynak | Yıl |
|---|---|---|---|---|
| `ed_level_label` | `II` | Acil düzeyi yalnız bağlam etiketi; yönetmelikteki sağlık personeli asgarisi kadroya eşlenmez | Acil Sağlık Hizmetleri Yönetmeliği (RG 13.09.2022/31952; değişiklik RG 11.10.2024/32689) | 2022/2024 |
| `ED_MAX_STAY_MIN` | sabit | Acilde izlem en fazla 8 saat (m.11/3; D2) | Acil Sağlık Hizmetleri Yönetmeliği (RG 13.09.2022/31952; değişiklik RG 11.10.2024/32689) | 2022 |
| `ED_MAX_TRANSFER_WAIT_MIN` | sabit | Karar sonrası en fazla 2 saatte transfer (m.11/4; D2) | Acil Sağlık Hizmetleri Yönetmeliği (RG 13.09.2022/31952; değişiklik RG 11.10.2024/32689) | 2022 |
| `calendar.FIXED_FULL / FIXED_HALF` | sabit | Resmî tatiller; 28 Ekim ve arefe 13.00'ten itibaren; Cuma kuralı; 31 Aralık tatil değil (D1) | 2429 sayılı Ulusal Bayram ve Genel Tatiller Hakkında Kanun (son değişiklik 6752 s. Kanun) | 2016 |

## Varsayım

| Ad | Varsayılan | Açıklama | Kaynak | Yıl |
|---|---|---|---|---|
| `inpatient_los_sigma` | `0.5` | Log-normal kalış dağılımının biçimi | — | — |
| `inpatient_shift_pattern` | `12_12` | 12/12 varsayılan; 8/16 duyarlılık profili (birincil kaynak okunmadı) | — | — |
| `ed_beds` | `8` | Acil gözlem yatağı sayısı | — | — |
| `ed_base_arrivals_per_hour` | `1.5` | Ülke düzeyinde acil başvuru hacmi okunamadı (D4) | — | — |
| `ed_volume_profile` | `taban` | taban ×1, yuksek ×2 | — | — |
| `ed_triage_profile` | `orta` | Türkiye çalışmalarında yeşil alan %22-75 (D3); üç profil bu aralığı kapsar | — | — |
| `ed_staffing_profile` | `temel` | Acil hemşire/destek/sorumlu kadrosu; mevzuattan türetilmez | — | — |
| `outpatient_rooms` | `3` | Poliklinik slot kaynağı (hekim modellenmez) | — | — |
| `outpatient_slot_minutes` | `15` | MHRS en az 10 dk aralığı D3; varsayılan 15 dk | — | — |
| `outpatient_fill_rate` | `0.9` | Slot doluluk oranı | — | — |
| `outpatient_no_show_rate` | `0.1` | Gelmeme oranı; Türkiye kaynağı bulunamadı; duyarlılık %5-20 | — | — |
| `outpatient_walk_in_rate` | `0.1` | Randevusuz başvuru oranı | — | — |
| `outpatient_nurses_per_day` | `3` | Poliklinik hemşire sayısı | — | — |
| `staff_pool` | `{'inpatient:nurse': 22, 'inpatient:support': 8, 'inpatient:supervisor': 4, 'emergency:nurse': 20, 'emergency:support': 8, 'emergency:supervisor': 4, 'outpatient:nurse': 5}` | Rotasyon için personel havuzu büyüklüğü | — | — |
| `clinical_cancel_rate` | `0.05` | Klinik nedenle iptal (gerek kalmadı) oranı | — | — |
| `shift_no_show_rate` | `0.03` | Planlanmış ama gerçekleşmeyen vardiya oranı (kadro açığı dönemleri) | — | — |
| `short_leave_rate` | `0.02` | Vardiya içi saatlik izin olasılığı | — | — |
| `short_leave_minutes` | `120` | Saatlik izin süresi | — | — |
| `break_minutes_per_12h` | `30` | 12 saatlik vardiyada somut mola süresi | — | — |
| `SHIFT_PATTERNS` | sabit | Vardiya şablonları ve mola dilimleri | — | — |
| `ROTATION` | sabit | 7 günlük rotasyon: 2 gündüz, 2 gece, 3 izin | — | — |
| `INPATIENT_STAFFING` | sabit | Yataklı kadro (hafta içi gündüz / gece / hafta sonu-tatil gündüz) | — | — |
| `ED_STAFFING_PROFILES` | sabit | Acil kadro profilleri; mevzuattaki sağlık personeli asgarisinden türetilmez | — | — |
| `ED_VOLUME_PROFILES` | sabit | Acil hacim çarpanları | — | — |
| `ED_HOURLY_PROFILE` | sabit | Saatlik acil profil; Bozyaka 2011 (D1) yalnız bağımsız biçim doğrulaması | — | — |
| `ED_WEEKDAY_FACTOR` | sabit | Haftanın günü acil çarpanı | — | — |
| `ED_HOLIDAY_FACTOR` | sabit | Tatil günü acil çarpanı | — | — |
| `ED_AFTER_HOLIDAY_FACTOR` | sabit | Tatil ertesi acil çarpanı | — | — |
| `TRIAGE_PROFILES` | sabit | Triaj dağılım profilleri (D3 aralık %22-75 yeşil) | — | — |
| `TRIAGE_ACUITY` | sabit | Triaj -> bakım düzeyi eşlemesi | — | — |
| `TRIAGE_LOS_MEDIAN_H` | sabit | Triaja göre acil kalış medyanı | — | — |
| `TRIAGE_LOS_SIGMA` | sabit | Acil kalış log-normal biçimi | — | — |
| `ED_MIN_STAY_MIN` | sabit | En kısa acil kalış | — | — |
| `TRIAGE_OUTCOME` | sabit | Triaja göre taburcu/yatış/sevk olasılığı | — | — |
| `TRIAGE_VITAL_INTERVAL_MIN` | sabit | Triaja göre yaşam bulgusu aralığı | — | — |
| `TRIAGE_MED_P` | sabit | Triaja göre ilaç olasılığı | — | — |
| `TRIAGE_SAMPLE_P` | sabit | Triaja göre numune olasılığı | — | — |
| `INPATIENT_WEEKDAY_FACTOR` | sabit | Haftanın günü yatış çarpanı (haftalık ortalamaya normalize edilir) | — | — |
| `INPATIENT_HOLIDAY_FACTOR` | sabit | Tatil günü yatış çarpanı | — | — |
| `INPATIENT_ADMIT_HOUR_WEIGHTS` | sabit | Yatış saati ağırlıkları | — | — |
| `INPATIENT_DISCHARGE_WINDOW_H` | sabit | Taburculuk saat penceresi 10-16 | — | — |
| `INPATIENT_WEEKEND_DISCHARGE_KEEP` | sabit | Hafta sonu taburculuk olasılığı 0,6 | — | — |
| `INPATIENT_LOS_CLIP_H` | sabit | Kalış kırpma aralığı 12 sa - 14 gün | — | — |
| `INPATIENT_ACUITY` | sabit | Yataklı bakım düzeyi dağılımı | — | — |
| `INPATIENT_IV_P` | sabit | Damar yolu bakımı gereken hasta oranı | — | — |
| `INPATIENT_WOUND_P` | sabit | Yara bakımı gereken hasta oranı | — | — |
| `INPATIENT_SAMPLE_P` | sabit | Günlük numune olasılığı | — | — |
| `BED_TURNAROUND_MIN` | sabit | Taburculuk sonrası yatak hazırlığı 60 dk | — | — |
| `OUTPATIENT_SESSIONS` | sabit | Poliklinik oturumları 08-12 ve 13-17 (kamu mesai düzeni birincil kaynaktan okunmadı) | — | — |
| `OUTPATIENT_ACUITY` | sabit | Poliklinik bakım düzeyi dağılımı | — | — |
| `OUTPATIENT_LATENESS` | sabit | Geç/erken gelme dağılımı | — | — |
| `OUTPATIENT_EXAM_MIN` | sabit | Muayene süresi (hekim modellenmez; kalış bileşeni) | — | — |
| `OUTPATIENT_POST_TASK_P` | sabit | Muayene sonrası hemşirelik görevi olasılıkları | — | — |
| `OUTPATIENT_POST_TASK_BUFFER_MIN` | sabit | Muayene sonrası işlem için ayrılan süre | — | — |
| `ACUITY_MULTIPLIERS` | sabit | Bakım düzeyi süre katsayıları (1.0.0 ile aynı) | — | — |
| `ACUITY_DAILY_CHANGE_P` | sabit | Günlük bakım düzeyi değişim olasılığı | — | — |
| `TASK_TYPES` | sabit | Görev türleri ve temel süreler (şema: duration_source = synthetic_assumption) | — | — |
| `NURSE_SKILL_P` | sabit | Hemşire yetkinlik dağılımı | — | — |
| `EARLY_START_MIN` | sabit | Planlı görevin vadesinden önce başlayabileceği süre | — | — |
| `LATE_AFTER_MIN` | sabit | Gecikmeli sayılma eşiği | — | — |
| `GIVE_UP_AFTER_MIN` | sabit | Kapasite nedeniyle karşılanamayan iş eşiği | — | — |
| `TASK_LOOKAHEAD_H` | sabit | Rutin bakım planının önceden bilinme süresi | — | — |
| `ACTUAL_SIGMA` | sabit | Gerçek/tahmini süre log-normal biçimi | — | — |
| `NIGHT_FACTOR` | sabit | Gece görev süresi katsayısı | — | — |
| `NIGHT_HOURS` | sabit | Gece saatleri 00-06 | — | — |
| `ACTUAL_RATIO_CLIP` | sabit | Gerçek/tahmini süre oranı kırpması (korunum C5) | — | — |
| `calendar.dini_bayram_kayitlari` | sabit | Dini bayram tarihleri D3; yalnız koruma penceresi, tatil olarak kullanılmaz | — | — |

## Tasarım seçimi

| Ad | Varsayılan | Açıklama | Kaynak | Yıl |
|---|---|---|---|---|
| `seed` | `42` | Tekrarlanabilirlik tohumu | — | — |
| `start_day` | `2026-10-05` | Dönem başlangıcı; 16 haftalık önerilen dönem (tasarım D9) | — | — |
| `days` | `112` | 12-16 hafta (tasarım D9); üst sınır 120 | — | — |
| `timezone` | `Europe/Istanbul` | Europe/Istanbul | — | — |
| `inpatient_rooms_per_unit` | `15` | Servis büyüklüğü tasarım seçimi | — | — |
| `inpatient_beds_per_room` | `2` | Servis büyüklüğü tasarım seçimi | — | — |
| `inpatient_warmup_days` | `28` | Dönem başında servisin dolu başlaması için ısınma süresi | — | — |
| `pending_horizon_hours` | `12` | Senaryo anından sonra bilinen planlı iş ufku | — | — |
| `TRIAGE_PRIORITY` | sabit | Hizmet sırası önceliği (eşit vadede) | — | — |
| `SKILLS` | sabit | Yetkinlik sözlüğü | — | — |
| `encounters.CALIBRATION_MAX_FACTOR / CALIBRATION_STEPS` | sabit | Yataklı doluluk kalibrasyonu: hız çarpanı 0,5-2,0 aralığında 18 adım ikiye bölme (ortak rastgele sayılar) | — | — |
| `encounters.discharge_calendar_shift_days` | sabit | Taburculuk saat penceresi ve hafta sonu ertelemesinin kalışa eklediği süre ham ortalamadan düşülür | — | — |
