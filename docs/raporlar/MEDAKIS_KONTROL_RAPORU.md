# MedAkis — Kontrol raporu

- **Tarih:** 2026-11-21
- **Dal:** `feature/asama-6-arayuz-onay` (Aşama 4–6 ve iyileştirmelerin hepsini içerir; `main`'den 36+ commit ileride)
- **`main`:** `6e00d38` (Aşama 3). Birleştirme yapılmadı; ayrı karardır.
- **Kapsam:** Sentetik verilerle çalışan idari karar destek prototipi. Klinik ürün değildir; gerçek hasta verisi yoktur; sistem hiçbir planı uygulamaz.

## 1. Tamamlananlar

| Aşama | İçerik | Kanıt (rapor) |
|---|---|---|
| 1 | Ortam, modüler iskelet, Türkçe Streamlit | `FAZ_1_1_RAPORU.md` |
| 2 | PostgreSQL veri katmanı, Alembic, deterministik sentetik veri (1.0.0 `83a619cb…`), üretici 2.0.0 (`2a1fdfb0…`) | `FAZ_1_2A_RAPORU.md`, `FAZ_1_2B_RAPORU.md`, `FAZ_1_2C_KAPANIS_RAPORU.md`, `URETICI_2_0_*` |
| 3 | İş yükü, personel kapasitesi, Servis paneli (veritabanlı, salt okunur) | `ASAMA_3_RAPORU.md` |
| 4 | 30/60 dk iş yükü tahmini: Random Forest ve baseline. Test talep_60 (tüm servisler): RF MAE 16,10, WAPE 0,132; baseline MAE 55,46 | `ASAMA_4_TAHMIN_MODELLERI_RAPORU.md` |
| 5 / U1 | Rol bazında model seçimi, tampon kalibrasyonu (`02fbfbe3…`), dağılım dışı denetim | `ASAMA_5_U1_KALIBRASYON_RAPORU.md` |
| 5 / U2 | Planlı kadro sözleşmesi, üretici 2.1 (plan `09c0315d…`), sızıntı testleri | `ASAMA_5_U2_*` |
| 5 / U3 | İhtiyaç–kapasite karşılaştırması (D = ŷ + B), güvenli durum sınıfları | `ASAMA_5_U3_*` |
| 5 / U4 | CP-SAT Katman A kadro önerisi (H1–H10, sözlük sıralı O1–O4), bağımsız doğrulayıcı | `ASAMA_5_U4_*` |
| 5 / U5 | T1–T15 matrisi 32/32, saldırılar, 104 karar anı değerlendirmesi | `ASAMA_5_U5_*` |
| 5 / iyileştirme | K2 kapalı poliklinik, K3 yapısal eksik sınıfı. K1 dondurma yalnız deneysel; varsayılan küresel ret. Kısmi açık serviste H7 ve deneysel çıktı ayrımı düzeltildi | `ASAMA_5_GUVENLI_*`, `ASAMA_5_GUVENLIK_BULGULARI_*` |
| 5 / U6 | Katman B görev ataması uygulanabilirlik denetimi. 104 anda 98 OPTIMAL ve 6 FEASIBLE; hepsi doğrulandı, ihlal 0 | `ASAMA_5_U6_KATMAN_B_RAPORU.md` |
| 5 / U7 | Plan SHA-256 anlık görüntüsü, onay, ret, iptal ve geri alma durum makinesi, zincirli denetim izi, güvenlik girdisi parmak izi (plan, yetkinlik, takvim, kilit, görev) | `ASAMA_5_U7_RAPORU.md` |
| 6 | Streamlit öneri inceleme ve onay ekranı, onaylı taşıma kilidi, simülasyon saati, veritabanısız Sentetik Demo Modu | `ASAMA_6_UYGULAMA_RAPORU.md`, `ASAMA_6_DEMO_MODU_DUZELTME_RAPORU.md` |

**Kontrol kapsamında yapılanlar** (bu rapor; yeni özellik yok):
- **Ana ekran:** Eski "Aşama 4'te eklenecek" ifadesi kaldırıldı. Genel bakış, Aşama 3–6 özelliklerini doğru tanıtıyor. Aşama tablosunda 1–6 "Tamamlandı (prototip)", 7–9 "Planlandı".
- **README:**
  - "Mevcut durum" güncellendi; beş sekme ve kenar çubuğu tanıtıldı.
  - **Sentetik Demo** bölümü eklendi: kurulum, gerekli model dosyaları ve bunları üreten betikler, çalıştırma komutu, sunum akışı.
  - Sınırlamalar güncellendi.
  - README değiştiği için paket sağlama manifesti (`SHA256SUMS.txt`) `checksums.py update` ile yenilendi; `check` geçiyor.
- **Eksik ya da bozuk model dosyası:**
  - "İnceleme senaryosunu yükle" adımında ön kontrol yapılıyor: önce model ve kalibrasyon dosyaları (hızlı), sonra üretici ve plan özetleri.
  - Eksik dosya (`dosya_yok:…`), yüklenemeyen model (`model_yuklenemedi:…`) ya da özet uyuşmazlığı (`ozet_uyusmuyor:…`) açık hata olarak gösteriliyor, yanında çözüm komutları.
  - Sahte model çıktısı üretilmiyor; değerlendirme düğmesi hiç görünmüyor.

## 2. Testler ve doğrulama

| Kontrol | Sonuç |
|---|---|
| Varsayılan test paketi | **1017 passed** (1012 + 5 uçtan uca test; gerçek senaryo uçtan uca dahil), 510 deselected (PostgreSQL, Ollama ve rol testleri), 5 uyarı (Streamlit içi pandas uyarısı) |
| Uçtan uca demo (sahte senaryo, `AppTest`) | Açılış → senaryo yükle → oturum → değerlendir → kaydet → onayla ("Onay şu an geçerli") → geri al (gerekçeli) → denetim geçmişi `[oneri_kaydedildi, onaylandi, geri_alindi]`, zincir 3 olay. **Veritabanı servisi, repository ya da bağlantı fabrikası çağrısı: 0** |
| Uçtan uca demo (**gerçek** sentetik senaryo; üretici 2.1 + RF + U1, 19.01.2027 09:30) | Aynı akış; model sürümleri anlık görüntüde (`random_forest`, `baseline_bilgi`); veritabanı çağrısı 0 (model dosyaları yoksa test atlanır) |
| Eksik ya da bozuk model dosyası | Açık hata, çözüm komutları, değerlendirme yok |
| Normal mod | Önceki davranış: Servis paneli veritabanını dener, bağlantı yoksa nedenini yazar |
| Tarayıcı (ana uygulama, `-- --demo`) | Açılış, demo uyarısı, güncel genel bakış ve aşama tablosu; önceki turda kayıt ve onay; sunucu günlüğünde veritabanı denemesi yok |
| U1–U7 ve Aşama 6 regresyonları | Varsayılan pakette geçiyor; T1–T15 32/32, saldırılar 25/25, U6 59, U7 54, Aşama 6 19, demo 21 |
| Altın özetler | 1.0.0 `83a619cb…`, 2.0.0 `2a1fdfb0…`, 2.1 plan `09c0315d…`, U1 kalibrasyonu `02fbfbe3…`: değişmedi (testler ve senaryo ön kontrolü denetler) |
| Paket sağlama özetleri | `scripts\checksums.py check` geçiyor |

## 3. Eksikler ve açık kararlar

**Kapsam dışı ya da sonraki aşamalar:**
- **Aşama 7–9 (RAG):** Kontrollü corpus, indeksleme, Ollama cevabı ve karşılaştırma yapılmadı.
- **`main` birleştirmesi:** Aşama 4–6 geliştirme dallarında; birleştirme ayrı karar.
- **Veritabanı kalıcılığı:** Onay ve denetim izi dosya tabanlı (tek yazıcılı prototip). PostgreSQL tasarımı (migration `0005` önerisi) `ASAMA_5_U7_RAPORU.md` §6'da; uygulanması migration onayına bağlı.
- **Kimlik doğrulama yok:** Aktör seçimi yerel prototip oturumu (şartname: üretim kimlik doğrulaması kapsam dışı).

**Yetkili karar bekleyenler** (sistem uydurmaz; hepsi `test_sentetik`):
- Asgari personel değerleri (K-13, K-18): Acil gece sorumlu; tek sorumlunun molasında asgari kuralın yorumu.
- Destek (servisler arası) yetkileri, servis yetkinlik gereksinimleri, hedef kullanım oranı (K-8), servis ağırlıkları (K-10).
- Onay yetki politikası (kim hangi serviste onaylar), en geç karar süresi, görevler ayrılığı.
- K1 servis-rol dondurmanın üretimde açılması (şu an yalnız deneysel, yayın kapalı).
- Son tarihi geçmiş görev birikiminin ele alınışı (Katman B).

**Bilinen sınırlar:**
- **Az sayıda yayımlanabilir öneri:** Varsayılan politikada 104 anın yalnız 2'sinde Katman A önerisi yayımlanabilir; diğerlerinde güvenli ret ve nedenleri gösterilir.
- **Katman B çabuk eskir:** Gerçek veride karar anından sonra yeni görev gelince denetim kısa sürede eskimiş sayılır (güvenli taraf).
- **Performans:** Tek grupta 40 personel ve 400 görev gibi büyük Katman B örneklerinde süre sınırı aşılabilir; bu durumda plan üretilmez.
- **İlk yükleme:** Senaryo 30–45 sn sürer. Model dosyaları Git dışında; başka bilgisayarda önce üretilmeli ya da kopyalanmalı. Betiklerle yeniden üretim temiz ortamda doğrulandı: ~10 dk, dosyalar baytı baytına aynı (`MEDAKIS_SON_TESLIM_DOGRULAMASI.md`).
- **Sentetik sınır:** Bütün sonuçlar sentetik veriyle; gerçek hastane güvenliği doğrulanmadı.

## 4. Sunum adımları (yaklaşık 10 dakika)

1. **Açılış:** `streamlit run app.py -- --demo`. Sarı prototip uyarısını ve mavi Sentetik Demo uyarısını gösterin: veritabanı yok, `test_sentetik` kimlik ve yetki, gerçek atama yok.
2. **Genel bakış:** Aşama 3–6 özellikleri. **Mimari ve aşamalar:** aşama tablosu, modüller.
3. **Servis paneli:** Demo modunda bağlantısız açıklama (veritabanlı ekran Normal modda).
4. **Öneri inceleme ve onay:**
   1. **İnceleme senaryosunu yükle** (~30 sn; bu sırada tahmin → kalibrasyon → ihtiyaç → CP-SAT zincirini anlatın).
   2. `demo_yonetici` ile **Oturum aç**.
   3. Karar anı `2027-01-19 09:30` → **Değerlendir**: Katman A önerisi (taşımalar, plan SHA-256, doğrulayıcı geçti) ve Katman B görev denetimi (kapsam, atanabilir ve atanmayan görevler, nedenler).
   4. Güvenli ret örneği: herhangi bir başka karar anı → "Yayımlanabilir öneri yok (güvenli ret)" ve nedenleri.
   5. **Katman A önerisini incelemeye kaydet** → kayıtlı öneri ayrıntısı: tam özet, çözücü ve model sürümleri.
   6. **Onayla** → "Onay şu an geçerli". Tekrar **Onayla** → `zaten_onaylandi` ile ret.
   7. İsteğe bağlı: `demo_gozlemci` ile onay denemesi → `yetkisiz:onayla:…` ile ret.
   8. **Onayı geri al** (önce gerekçesiz → `gerekce_zorunlu`, sonra gerekçeyle) → "Geri alındı".
   9. **Denetim geçmişi:** bütün olaylar ve reddedilen denemeler; "Özet zinciri doğrulandı".
5. **Kapanış:** Sistem önerir, insan karar verir; hiçbir plan uygulanmaz. Açık kararlar (§3) yetkili birimindir.

## 5. Çalıştırma komutları (PowerShell, proje kökü)

**Kurulum (bir kez):**

```powershell
py -3.14 -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Model dosyaları** (`artifacts/` yoksa; sırayla):

```powershell
.\.venv\Scripts\python.exe scripts\asama4_egit.py --cikti artifacts\forecasting\asama4_seed42
```

```powershell
.\.venv\Scripts\python.exe scripts\asama5_u1_kalibrasyon.py
```

**Sunum (veritabanısız Sentetik Demo):**

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py -- --demo
```

**Normal mod** (veritabanlı Servis paneli; PostgreSQL gerekir):

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

**Testler:**

```powershell
.\.venv\Scripts\python.exe -m pytest
```

**Paket bütünlüğü:**

```powershell
.\.venv\Scripts\python.exe scripts\checksums.py check
```
