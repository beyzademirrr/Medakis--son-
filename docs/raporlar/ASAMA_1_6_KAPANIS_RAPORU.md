# Aşama 1–6 kapanış raporu

- Dal: `feature/asama-6-arayuz-onay` (`main` ile birleştirilmedi).
- Tarih ve talimat: 2026-11-23, **KM28** ("MedAkis Aşama 1–6 son eksiklerini kapat").
- Dayanak: `ASAMA_1_6_TAMAMLAYICI_ISLER_RAPORU.md` (S12, S13, §7) ve şartname.
- PostgreSQL, Docker, migration ve seed çalıştırılmadı. Aşama 7'ye geçilmedi.

## 1. Görev ve personel ekranları (S12)

Yetkinlik, bakım gereksinimi ve uygunsuzluk aralıkları **salt okunur** olarak eklendi. Hiçbir ekran bu alanları değiştiremez. Eksik kayıt "—" ile ve nedeniyle gösterilir, değer uydurulmaz.

| Alan | Normal mod (servis paneli, veritabanı) | Sentetik Demo (öneri inceleme, senaryo verisi) |
|---|---|---|
| Personel yetkinliği | `staff_skills` + `skills`; seçilen anda geçerli kayıtlar | `StaffSkill` kayıtları; karar anında geçerli olanlar |
| Görevin gerekli yetkinliği | `task_skill_requirements` | Görev kaydının `required_skills` alanı |
| Bakım düzeyi ve katsayısı | Hastanın seçilen anda bilinen son `acuity_assessments` kaydı | Aynı kural, üretici 2.0.0 tablolarından |
| Uygunsuzluk aralıkları | Vardiyayla kesişen `staff_unavailability` kayıtları | Karar anında bilinen (`known_view`) ve [t, t+60) penceresiyle kesişen kayıtlar |

- Normal mod yalnız SELECT sorguları kullanır (`workload_repo.skills_valid_at`, `task_type_skill_names`, `latest_acuity_at`).
- Demo görünümü (`ReviewService.people_and_tasks`) yalnız çalıştırmanın kendi girdilerini okur; pencere çözücülerle aynıdır. Sonradan bildirilen kayıt görünmez.
- Bakım değerlendirmesi sağlamayan senaryoda bakım alanları boş kalır ve not düşülür.

**Sınırlama:** Normal modun canlı veritabanıyla testi çalıştırılmadı (veritabanı yasağı). Sorgular sahte bağlantıyla (yalnız SELECT, zaman filtreleri) ve panel sahte anlık görüntüyle sınandı.

## 2. Çözücü süre sınırı (S13)

- İnceleme senaryosu Katman A, Katman B ve planlama yapılandırmasındaki süre sınırını `config.Settings.solver_time_limit_seconds` (10 sn) değerinden okur. Ortam değişkenleri bu değeri değiştirmez.
- U4 ve U6 çözücüleri değişmedi; yapılandırma girdisini kullanmaya devam eder.
- Deney betikleri (`asama5_guvenli_degerlendirme.py`, `asama5_u4_ornek.py`, `asama5_u5_degerlendirme.py`, `asama5_u6_degerlendirme.py`) açıkça verdikleri 10 sn'yi korur. Kayıtlı deney sonuçları değişmedi.
- Süre sınırı plan SHA-256 özetine girer. Değer 10 sn kaldığı için özetler aynıdır (§4).

## 3. Karar kaydı

`ASAMA_1_6_TAMAMLAYICI_ISLER_RAPORU.md` §3 ve §7 güncellendi:

| # | Konu | Karar |
|---|---|---|
| S12 | Görev ve personel ekranı alanları | Eklendi (salt okunur) |
| S13 | Merkezi çözücü süre sınırı | İnceleme senaryosu `config.py` değerini okuyor |
| S15 | Tahmin kartı ve panel veri geçişi | Ayrı altyapı çalışması |
| S16 | Görev süresi varsayımını düzenleme | Ertelendi |
| S17 | PostgreSQL kalıcılığı | Migration, roller ve seed güvenliği için ayrı onay bekliyor |
| S18 | `main` birleştirmesi | Ertelendi; önce ilgili PostgreSQL testleri doğrulanacak |

## 4. Testler ve altın özetler

- Yeni dosya: `tests/test_asama1_6_kapanis.py` (18 test):
  - Normal mod sorgularının yalnız SELECT olması ve zaman filtreleri;
  - panelde yeni sütunlar ve eksik alanda "—";
  - demo görünümünün çalıştırma girdileriyle eşleşmesi, sonradan bildirilen ve pencere dışı kaydın gizlenmesi;
  - bakım değerinin uydurulmaması;
  - süre sınırının merkezi yapılandırmadan gelmesi ve deney betiklerinin açık değerini koruması;
  - gerçek senaryoda plan özetlerinin değişmemesi.
- Varsayılan paket: **1049 geçti**, 510 seçim dışı (PostgreSQL/Docker), 0 başarısız. Önceki taban 1031 + 18 yeni test.
- Altın özetler doğrulandı:

| Özet | Değer | Kanıt |
|---|---|---|
| Üretici 1.0.0 | `83a619cb…` | `test_synthetic_generator`, `test_generator_v21` |
| Üretici 2.0.0 | `2a1fdfb0…` | `test_synthetic_v2`, `test_generator_v21` |
| Plan 2.1 | `09c0315d…` | `test_generator_v21` |
| U1 kalibrasyonu | `02fbfbe3…` | Senaryo yükleyicisinin özet denetimi (gerçek senaryo testi) |
| Aşama 4 veri çerçevesi | `f35899f7…` | `test_asama1_6_tamamlayici` |
| Plan SHA-256 (19.01.2027 09:30) | A `51e1e11b60f6bbd3…`, B `31f0d653a70ad9df…` | `test_real_scenario_plan_hashes_unchanged_with_config_limit` |

## 5. Değişmeyenler

- Tahmin modelleri, model seçimi ve U1 kalibrasyonu.
- CP-SAT Katman A ve B kuralları, bağımsız doğrulayıcılar.
- Yetki politikası, onay durum makinesi, plan özeti ve eskime denetimi.
- Demo modunun veritabanı yalıtımı.

## 6. Açık kalan işler (tamamlanmadı)

1. **PostgreSQL kalıcılığı (S17):** ayrı onay bekliyor; tasarım `ASAMA_1_6_TAMAMLAYICI_ISLER_RAPORU.md` §5'te.
2. **Seçim dışı 510 PostgreSQL/Docker testi:** bu dalda çalıştırılmadı. Normal mod ekran eklerinin canlı doğrulaması da buna bağlı.
3. **`main` birleştirmesi (S18):** ertelendi; 2. madde önce yapılmalı.
4. **Tahmin kartı ve panel veri geçişi (S15):** ayrı altyapı çalışması; hedef aşama belirlenmedi.
5. **Görev süresi varsayımını düzenleme (S16):** ertelendi.
