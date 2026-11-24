# D5 sonrası kaynaklı cevap mimarileri: karşılaştırmalı rapor

- Dal: `feature/asama-8-rag-generation`. Tarih: 2026-11-24.
- Ön kayıt: `ASAMA_8_D5_MIMARI_DENEY_KAYDI.md` (commit `3ebbdad`; sonuçlar görülmeden önce).
- **A6 KARŞILANMADI.**
  - İnsan incelemesi ve S4 tamamlanmadı. Gold ve `expected_claims` TASLAK.
  - Dev soruları kör test değildir; teyit kümesi bağımsız değildir.
  - Kapı kabulü ve kaynak sunumu **cevap doğruluğu değildir.**
- **Hiçbir mimari, model ya da eşik varsayılan yapılmadı.**
  - Gerçek indeks, eşikler (`null`) ve onay kayıtları değişmedi. Normal RAG kapalı.
  - Yeni model ya da paket yok; D5 ortamı ve dört model korundu.
- **Ham veriler:**
  - `docs/veri/asama8_d5_mimari_kalite.json`
  - `docs/veri/asama8_d5_mimari_gecikme.json`

## 1. Özet

| Mimari | Kapı | Cevap katmanı | Doğru kaçınma (tüm) | Yanlış kaçınma (tüm) | Yanlış kabul | Kullanıcıya sunulan |
|---|---|---|---|---|---|---|
| A0 (kontrol) | yok | Gemma M1 | 0,933 | 0,300–0,367 | 2 (V003, V005) | 19–21 cevap, inceleme bekliyor |
| A1 | LAMAR | Gemma M1 | **1,000** | 0,333 | 0 | 20 cevap, inceleme bekliyor |
| A2 | LAMAR | **Gemma yok:** kaynak sunumu | **1,000** | **0,033** | 0 | 29 **kaynak parçası** (cevap değil) |
| A3 | ModernBERT-TR | Gemma M1 | **1,000** | 0,367–0,400 | 0 | 18–19 cevap, inceleme bekliyor |
| A4 | ModernBERT-TR | **Gemma yok:** kaynak sunumu | **1,000** | **0,067** | 0 | 28 **kaynak parçası** (cevap değil) |

(3 tekrar; aralık tekrarlar arasındaki değişimi gösterir. Teknik hata, güvenlik reddi ve kaynak çatışması bütün mimarilerde 0.)

**Ana bulgular:**
1. **Kapılar kaynak dışı soruları kusursuz ayırdı.**
   - LAMAR ve ModernBERT kapıları yanlış kabulü 2'den 0'a indirdi; doğru kaçınma 1,0.
   - A0'ın iki yanlış kabulü (V003, V005) kapı tarafından reddedildi.
2. **Gemma'lı mimarilerde asıl kayıp Gemma'nın kararı yeniden vermesi.**
   - Kapının kabul ettiği cevaplanabilir vakaların **A1'de 9'unu, A3'te 9–10'unu** Gemma yeniden reddetti: "kanıt yetersiz" 7–8, sahte alıntı 1–2.
   - Sonuç: uçtan uca yanlış kaçınma A0 ile aynı düzeyde (0,33–0,40). **A6'nın yanlış kaçınma hedefi (≤ 0,15) karşılanmadı.**
3. **Gemma'sız mimarilerde (A2, A4) yanlış kaçınma yalnız kapının retleri.**
   - A2'de 0,033, A4'te 0,067.
   - Ama sunulan şey **doğrulanmış kaynak parçasıdır, cevap değildir.**
   - Sunulan parça taslak gold aralığını bütün kabul edilen cevaplanabilir vakalarda içeriyor (29/29, 28/28). Soruyu gerçekten cevaplayıp cevaplamadığı **insan incelemesi bekliyor.**
4. **Güvenlik:**
   - Yayımlanan geçersiz alıntı 0.
   - Olumlu kapı kararı doğrulamayı gevşetmedi: Gemma'nın 1–2 sahte alıntısı reddedildi.
   - Enjeksiyon sonuçları §5'te.
5. **Gecikme ve bellek:** §6. Gerçek uçtan uca ölçüm; yükleme ve boşaltma dahil.

## 2. Kapı katmanı (kabul/ret kararı; uçtan uca değil)

**Eşik:** her model için ayrı; 60 vakada grup CV, eğitim katlarından. Kat eşikleri (skor uzayı):
- LAMAR: dört katta 2,478, bir katta 2,537.
- ModernBERT: dört katta 2,141, bir katta 2,727.

| Kapı | Tüm: kaçınma / yanlış kaçınma / yanlış kabul | Seçim | Teyit |
|---|---|---|---|
| LAMAR (A1, A2) | 1,000 / 0,033 / 0 | 1,000 / 0,000 / 0 | 1,000 / 0,091 / 0 |
| ModernBERT-TR (A3, A4) | 1,000 / 0,067 / 0 | 1,000 / 0,105 / 0 | 1,000 / 0,000 / 0 |

**Not:**
- D5'te seçim ve teyit eşikleri farklı kalibre edilmişti: seçim kümesinde ayrı CV, teyitte seçim kalibrasyonu. Bu deneyde 60 vakalık tek CV'nin kararları kümelere bölündü.
- Bu yüzden LAMAR'ın teyit yanlış kaçınması burada 0,091 (D5'te seçim kalibrasyonuyla **0,182**; o değer kayıtta korunur).
- Teyit kümesi bağımsız değildir.

## 3. Uçtan uca sonuçlar (D0 sınıfları; 3 tekrar)

| Ölçü (tüm, 60 vaka) | A0 | A1 | A2 | A3 | A4 |
|---|---|---|---|---|---|
| Bilinçli kaçınma (karar verilen = bütün; teknik hata 0) | 0,933 | 1,000 | 1,000 | 1,000 | 1,000 |
| Yanlış kabul | 2 / 30 | 0 | 0 | 0 | 0 |
| Yanlış ret oranı | 0,333 / 0,367 / 0,300 | 0,333 ×3 | 0,033 ×3 | 0,400 / 0,400 / 0,367 | 0,067 ×3 |
| Yanlış ret alt nedenleri (tekrar 1) | kanıt yetersiz 9, adaylarda gold yok 1 | kanıt yetersiz 8, sahte alıntı 1, kapı reddi 1 | kapı reddi 1 | kanıt yetersiz 8, sahte alıntı 2, kapı reddi 2 | kapı reddi 2 |
| Cevap, inceleme bekliyor | 20 / 19 / 21 | 20 | — | 18 / 18 / 19 | — |
| **Kaynak sunuldu, inceleme bekliyor (cevap değil)** | — | — | 29 | — | 28 |
| Gemma'nın yeniden reddettiği, kapının kabul ettiği cevaplanabilir | — | 9 ×3 | — | 10 / 10 / 9 | — |
| Gemma'nın kurtardığı yanlış kabul | — | 0 | — | 0 | — |
| Geçersiz alıntı önerisi (reddedildi) / **yayımlanan geçersiz alıntı** | 0–1 / **0** | 1 / **0** | — / **0** | 2 / **0** | — / **0** |
| Sunulan içerikte taslak gold kapsamı | 0,85–0,89 | 0,90 | **1,00** | **1,00** | **1,00** |
| Gold'a değmeyen cevap ya da sunum | C003, E002, E011 | E008, E011 | — | — | — |
| Vaka başına model çağrısı (Gemma) | 1,00 | 0,48 | 0 | 0,47 | 0 |

**Seçim / teyit (yanlış ret oranı):**
- A0: 0,37–0,42 / 0,18–0,27.
- A1: 0,316 / 0,364.
- A2: 0,000 / 0,091.
- A3: 0,421 / 0,27–0,36.
- A4: 0,105 / 0,000.

**Yanlış reddedilen cevaplanabilir vakalar (tüm, tekrar 1):**
- A1: C001, C003, C024, C036, C057, E002, E003, E006, E009, E012.
- A3: C003, C006, C024, C036, C057, E001, E002, E003, E006, E009, E011, E012.
- A2: E002. A4: E001, E011.

Gemma'nın kapıdan sonra reddettiği vakalar büyük ölçüde D2 ve D3'te "gold verilse bile reddedilen" vakalarla aynı: C057, E003, E006, E012, C024, C036. Bunlar insan inceleme listesinde öncelikli.

## 4. Gemma'lı ve Gemma'sız mimarinin farkı

- **Gemma'lı (A1, A3):**
  - Kullanıcıya birebir doğrulanmış **alıntılardan oluşan** ayıklayıcı bir cevap verir; ama kapının kabul ettiği her 3 cevaplanabilir sorudan yaklaşık 1'ini yeniden reddeder.
  - Kapı kaynak dışı soruları zaten ayırdığı için Gemma'nın ek bir güvenlik katkısı bu veride görülmedi: kurtarılan yanlış kabul 0.
- **Gemma'sız (A2, A4):**
  - Kullanıcıya en üstteki **doğrulanmış kaynak parçasını** birebir ve "cevap değildir" etiketiyle sunar. Serbest metin ya da çıkarım yoktur.
  - Yanlış ret yalnız kapının retleri.
  - **Sınır:** parçanın soruyu cevapladığı makine tarafından doğrulanmaz. İlgili ama cevaplamayan bir parça sunulabilir; D5 smoke testlerinde reranker'lar çelişkili belgelere de yüksek skor verdi.
  - Anlamsal doğruluk ancak insan onaylı `expected_claims` ile ölçülebilir.

## 5. Güvenlik ve enjeksiyon

**Enjeksiyon koşusu TAMAMLANMADI.** Gecikme koşusunun içindeydi ve zaman önceliği nedeniyle 2026-11-24 21:35'te güvenli biçimde durduruldu (§6). Enjeksiyonun cevap ya da kaynak olarak yayımlanıp yayımlanmadığı bu deneyde **ölçülmedi**; D3'teki ölçüm (M2 hariç yayımlanma 0) ve aşağıdaki değişmezler geçerlidir.

**Değişmezler (birim testler):**
- `conflicting_sources`, `rejected`, `unavailable` ve retrieval `no_source` kapıyı ve Gemma'yı aşar; model çağrılmaz.
- Kapı yalnız doğrulanmış adayları yeniden sıralar.
- Eksik ya da sonlu olmayan skor ⇒ teknik hata.
- Olumlu kapı birebir doğrulamayı gevşetmez.
- Kaynak sunumu yalnız doğrulanmış parçanın birebir metni ve etiketiyle mümkündür.

**Kalite koşusunda:**
- Yayımlanan her alıntı belgedeki aralıkla bağımsız olarak yeniden karşılaştırıldı; uyuşmayan 0.
- Onaysız, emekli, geçerlilik dışı ve çatışmalı belgeler retrieval'ın izinli kümesine girmediği için hiçbir mimaride aday olmadı.

## 6. Gerçek uçtan uca gecikme ve bellek

**Gerçek uçtan uca gecikme koşusu TAMAMLANMADI.** Koşu (S ve W politikaları, 3 tekrar) 1. tekrarı bitirdikten sonra zaman önceliği nedeniyle durduruldu. Betik sonuçları yalnız iş sonunda yazdığı için 1. tekrarın gecikme ve bellek verisi **kaydedilmedi**; hiçbir gecikme değeri raporlanmıyor. Koşu öncesi küçük doğrulamada (tek vaka) ölçülen soğuk maliyetler bilgi amaçlıdır: işçi süreç başlatma (torch içe aktarma) yaklaşık 44 sn, ModernBERT-TR soğuk yükleme yaklaşık 22 sn, ilk skorlama yaklaşık 7 sn; canlı skorların D5 skorlarıyla farkı 6e-6. D5 reranker skorlama maliyetleri `ASAMA_8_D5_KARSILASTIRMA_RAPORU.md` §4.4'tedir. Betik (`scripts/rag_d5_mimari_gecikme.py`) korunuyor; koşu 2 Aralık sonrası planına alındı. Durdurmadan sonra Ollama'da yüklü model yok, VRAM 0 MiB.

## 7. Sınırlılıklar

- **Küçük örneklem:** 60 dev vakası. Kaynak dışı soruların hepsini geliştirici yazdı. Kapıların kusursuz ayrımı genelleme kanıtı değildir.
- **Kapı skorları** D5'in deterministik skorlarından alındı. Canlı skorlarla farkı gecikme koşusunda ölçüldü (§6).
- **Gemma çıktısı** tekrarlar arasında 1–2 vakada değişti (A0 ve A3).
- **Gemma'sız sunum** bir cevap değildir; kabul edilen kaynak soruyu cevaplamayabilir.

## 8. Testler

Yeni birim testleri `tests/test_rag_d5_mimari.py` (9 test) ve ilgili RAG testleri geçti (114). Tam regresyon son doğrulamayla birlikte koşuldu (teslim notu).

## 9. Proje sahibinin kararını bekleyenler

1. **Mimari yönü:**
   - Gemma'lı (alıntı cevabı, yüksek yanlış ret), Gemma'sız (kaynak sunumu, düşük yanlış ret ama cevap değil) ya da ikisinin birleşimi.
   - **Önerilen ve ön kayıtsız bir seçenek:** kapı kabul ettiğinde Gemma'nın kararını beklemeden en az doğrulanmış kaynağı sunmak; Gemma alıntısı varsa eklemek.
2. **Kapı modeli:**
   - LAMAR: daha az yanlış ret, 2,3 GB VRAM.
   - ModernBERT-TR: 0,7 GB VRAM, daha hızlı.
3. **İnsan incelemesi:** E002, E011 ve öncelikli vakalar ile Gemma'nın sürekli reddettiği C057, E003, E006, E012, C024, C036. S4 hazırlayıcısı.
