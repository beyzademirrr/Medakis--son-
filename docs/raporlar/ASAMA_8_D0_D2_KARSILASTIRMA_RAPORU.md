# D0–D2 karşılaştırmalı rapor

- Dal: `feature/asama-8-rag-generation`. Tarih: 2026-11-24.
- Ön kayıt: `ASAMA_8_D0_D2_DENEY_KAYDI.md` (commit `8ba529b`; ölçümden önce). Kod: `11fe948`.
- **A6 KARŞILANMADI.** A6 kabul hedefleri değişmedi.
- **Gold etiketleri ve `expected_claims` insan onayı almamış TASLAKTIR.** D2 bir oracle (üst sınır) deneyidir; gerçek sistem başarısı ya da A6 kabulü değildir.
- Dev soruları daha önce yöntem ve istem seçiminde kullanıldı; bağımsız ya da kör veri değildir. S4 bağımsız soru çalışması açık.
- **Bu çalışmada yapılmayanlar:**
  - istem, şema ve model ayarı değişikliği;
  - eşik yazma, gerçek indeks değişikliği, normal RAG, kör test;
  - model indirme, paket ya da LangGraph kurulumu;
  - D3–D5 ve 8.7.
- **Sabitler:** `gemma3:4b` digest `68ceab7e468a…` (koşu başında doğrulandı), istem `kanit_denetimi_v2`, `temperature=0`, `seed=0`, `num_ctx=2048`, `num_predict=256`, en fazla 5 aday, embedding `bge-m3`.
- **Ham veriler:**
  - `docs/veri/asama8_d1_aday_tamamlama.json`
  - `docs/veri/asama8_d0_d2_oracle.json`
  - **Betikler:** `scripts/rag_d1_aday_tamamlama.py`, `scripts/rag_d2_oracle.py`.
  - Eski ham kayıtlar (`asama8_kanit_hatti_dev*.json`) değiştirilmedi.

## 1. Özet: sorun nerede yoğunlaşıyor?

| Katman | Kanıt | Değerlendirme |
|---|---|---|
| **Alıntı üretimi** | Yalnız gold verildiğinde (K1) model 24/30 vakada "destekleniyor" diyor, ama **19'unda hiç alıntı vermiyor** (boş alıntı oranı 0,633). Mevcut adaylarla (K3) boş alıntı 7/30 | **Birincil darboğaz.** R4 tetiklendi |
| **Model kararı** | K1'de bile 6/30 vakada "desteklenmiyor" (C001, C006, C057, E003, E006, E012). Destek oranı 0,80 < 0,90 | **İkincil sorun.** R1 tetiklendi. Bir kısmı taslak gold hatası olabilir; insan incelemesi gerekir |
| **Retrieval ve aday havuzu** | Mevcut 5 adayda gold yalnız 1/30 vakada yok (E001). Tamamlama ya da `parcalama_v2` ile Recall@10 = 1,0 | **Küçük ve giderilebilir.** R3 tetiklenmedi (K2 ve K3 destek oranı aynı: 0,70) |
| **Karıştırıcı etkisi** | K1 → K2 destek oranı 0,80 → 0,70 (fark tam 0,10) | R2 tetiklenmedi (kural K1 ≥ 0,90 şartı arar) |
| **Değerlendirme** | Cevaplananların çoğu gold'a değiyor: K1 5/5, K2 11/13, K3 12/14. Gold'a değmeyen cevaplar C003, E005, E011. Anlamsal doğruluk ölçülemiyor (insan etiketi yok) | R5 **biçimsel olarak** tetiklendi. Ama fark boş alıntıdan geliyor (§4.3). Gerçek katkı küçük ama çözülmemiş |

**Sonuç:** Sorun ağırlıklı olarak **model çıktısında**:
- **Önce alıntı üretimi:** "destekleniyor" deyip alıntı vermeme.
- **Sonra kanıt kararı:** gold karşısında bile ret.

Retrieval kaybı tek vakada (E001); D1 bunu gideriyor. Değerlendirme katmanı insan incelemesi bekleyen ikincil bir belirsizlik.

**Uyarı:** Aynı yapılandırma iki ayrı koşuda 4 vakada farklı sonuç verdi (§5). Birkaç vakalık farklar gürültü sınırındadır.

## 2. D0: ölçüm sözleşmesi

**Uygulama:**
- **`src/medakis/rag/evaluation/contract.py`:**
  - Ayrık sınıflar: `TEKNIK_HATA`, `GUVENLIK_REDDI`, `KAYNAK_CATISMASI`, `BILINCLI_KACINMA`, `YANLIS_KABUL`, `YANLIS_RET` (alt nedenli), `CEVAP_INCELEME_BEKLIYOR`, `DOGRU_CEVAP` ve `YANLIS_KANITLI_CEVAP` (yalnız insan etiketiyle).
  - Anlamsal doğruluk insan etiketi olmadan `None` döner ve gerekçesini kaydeder.
  - Eski ölçüler ayrı alanlarda korunur.
- **Yanlış ret alt neden önceliği:** eşik altı → adaylarda gold yok → boş alıntı → sahte alıntı → kanıt yetersiz → diğer. Bilgi yoksa `kayitta_yok`.
- **`GroundedResult.decision` (`ModelDecision`):** modelin ham kararı (`destekleniyor`, etiketten çevrilmiş alıntı önerileri, `eksik`) ve doğrulama sorunları.
  - Karar mantığı değişmedi; yalnız kayıt eklendi.
  - Kullanıcıya gösterilmez.
- **Yeni değerlendirme kaydı (vaka başına):** sınıf ve alt neden, ham karar, doğrulanmış alıntı metni ve belge aralığı, isteme giren aday sıralaması (belge, bölüm, uzaklık, gold mu), uyarılar.

**D0 yeniden koşusu (mevcut yapılandırma `aday_deneyi`, v1, tamamlama yok; 60 vaka):**

| Ölçü | Tümü | Seçim (41) | Teyit (19) |
|---|---|---|---|
| Bilinçli kaçınma, karar verilen kaynak dışı | 26/28 = **0,929** | 0,909 | 1,000 |
| Bilinçli kaçınma, bütün kaynak dışı | 26/30 = 0,867 | — | — |
| Yanlış kabul | 2/30 = 0,067 (V003, V005) | 0,091 | 0,000 |
| Teknik hata | 2/60 (V012, V023: `cikti_kesildi`) | 0 | 0,105 |
| Güvenlik reddi / kaynak çatışması | 0 / 0 | — | — |
| Yanlış ret | 16/30 = **0,533** | 0,579 | 0,455 |
| Yanlış ret alt nedenleri | kanıt yetersiz 8, boş alıntı 7, adaylarda gold yok 1 (E001) | | |
| Cevap, inceleme bekliyor | 14 (**doğru sayılmadı**) | 8 | 6 |
| Anlamsal doğruluk | **hesaplanmadı** (insan onaylı iddia yok) | | |
| Eski ölçüler (karşılaştırma için) | doğru kaçınma 0,933 / yanlış kaçınma 0,533 | | |

**Eski kayıtların yeniden puanlanması (alıntı, ham karar ve aday listesi eski kayıtta YOK → `kayitta_yok`; tahmin yapılmadı):**

| Kayıt | Bilinçli kaçınma (karar verilen) | Teknik hata | Yanlış kabul | Yanlış ret | Alt nedenler | İnceleme bekleyen |
|---|---|---|---|---|---|---|
| P1 (`esik`, CV) | 30/30 = 1,000 | 0 | 0 | 23 (0,767) | boş alıntı 11, kanıt yetersiz 7, eşik altı 5 | 7 |
| P2 (`aday_deneyi`) | 26/28 = 0,929 | 2 | 2 | 14 (0,467) | kanıt yetersiz 10, boş alıntı 4 | 16 |

Eski kayıtlarda "adaylarda gold yok" alt nedeni ayırt edilemez (aday listesi kaydedilmemişti).

## 3. D1: aday havuzu tamamlama (bge-m3, eşiksiz, model çağrısı yok)

**Yöntem:**
- `RetrievalConfig.refill` varsayılan **kapalı** ve yalnız değerlendirme kipinde çalışır.
- Ayıklamadan sonra tekil aday 10'un altındaysa istek iki katına çıkar (en fazla 80 ya da koleksiyon büyüklüğü).
- Her turda bütün sonuçlar baştan aynı denetimlerden geçer: indeks eşlemesi, hash, metin aralığı, onay filtresi, sürüm, kural çatışması. Tek bir bütünlük ihlali bütün sorguyu `unavailable` yapar (testle doğrulandı).

| Yöntem | Recall@5 | Recall@10 | MRR@10 | Tekil aday en az | 10'dan az aday dönen vaka (60) | Gecikme medyan / p95 (ms) |
|---|---|---|---|---|---|---|
| v1 (mevcut) | 0,9667 | 0,9667 | 0,9233 | 1 | 11 | 232,6 / 471,1 |
| `parcalama_v2` | 0,9833 | 1,0000 | 0,9567 | 10 | 0 | 239,6 / 313,1 |
| v1 + tamamlama | 0,9833 | 1,0000 | 0,9400 | 10 | 0 | 244,5 / 295,8 |
| `parcalama_v2` + tamamlama | 0,9833 | 1,0000 | 0,9567 | 10 | 0 | 241,0 / 334,4 |

**Seçim ve teyit kümeleri:**
- Seçim: v1 R@5 0,947 → tamamlama 0,974 (MRR 0,947 → 0,974).
- Teyit: dört yöntemde aynı (R@5 1,0; MRR 0,882).

**Önceden sabit kabul kuralı:**
- **v1 + tamamlama — KABUL.**
  - Hiçbir ölçü kötüleşmedi.
  - 10'dan az aday dönen vaka 11 → 0.
  - Gecikme medyanı ×1,05.
- **`parcalama_v2` + tamamlama — KABUL EDİLMEDİ.**
  - Kural "10'dan az aday dönen vaka azalır" şartını arıyor; v2'de bu sayı zaten 0.
  - Tamamlama v2'de hiç devreye girmedi; bütün ölçüler v2 ile aynı.
- Tamamlamanın devreye girdiği vakalar: C006, C016, C050, E001, E003, E005 ve kaynak dışı U003, V012, V014, V021, V024. Bu vakaların hiçbirinde ilk sıralar değişmedi.

**E001 analizi (tek vakaya özel ayar yapılmadı):**
- v1'de Chroma'nın ilk 10 sonucunun 9'u, farklı belgelerdeki **birebir aynı "Giriş" şablon metni**. Metin özetiyle ayıklanınca havuzda 1 parça kaldı: `SYN-PRC-DEVIR@1.0 / Giriş`, uzaklık 0,464, gold değil.
- Gold `SYN-PRC-DEVIR / Amaç` (0,565) havuz dışında kaldı.
- **Tamamlama:** n = 40'ta 10 tekil aday; gold 2. sırada.
- **`parcalama_v2`:** Giriş parçaları indekste yok; gold 1. sırada.
- E001'in iki gold parçası var. Hiçbir yöntemde ikisi birden ilk 5'te değil; Recall@5 kaybı bundan geliyor.
- **Genel mekanizma:** şablon tekrarı ve ayıklama sonrası eksik havuz. Aynı mekanizma 11 vakada havuzu daraltıyordu; tamamlama hepsine genel olarak uygulanıyor.
- **Gerçek depo:** ölçümden önce ve sonra hazır indeksler, manifest özetleri ve eşikler (`null`) aynı.

## 4. D2: gold kanıt oracle (30 cevaplanabilir vaka)

**Koşullar:**
- **K1:** yalnız gold parçalar (v1 parçalaması; 1–2 parça).
- **K2:** gold ve mevcut retrieval'ın gold olmayan en üst adayları (toplam 5; konum sabit tohumla karıştırıldı).
- **K3:** mevcut retrieval ilk 5 (D0 yeniden koşusunun cevaplanabilir alt kümesi).

Gold parça metinleri gerçek corpus'tan kuruldu; parça hash'leri manifestle denetlendi. İstem uzaklık göstermez.

| Ölçü | K1 yalnız gold | K2 gold + karıştırıcı | K3 mevcut adaylar |
|---|---|---|---|
| Teknik hata | 0 | 0 | 0 |
| Model "destekleniyor" | 24/30 = **0,800** | 21/30 = 0,700 | 21/30 = 0,700 |
| Boş alıntı ("destekleniyor" + alıntı yok) | 19/30 = **0,633** | 6/30 = 0,200 | 7/30 = 0,233 |
| Alıntılı destekte birebir doğrulanan | 5/5 = 1,000 | 13/15 = 0,867 | 14/14 = 1,000 |
| Sahte alıntılı karar (doğrulamada reddedildi) | 0 | 2 (C003, C034) | 0 |
| Cevaplanan (`answered`) | 5 (0,167) | 13 (0,433) | 14 (0,467) |
| Gold alıntı isabeti (taslak gold) | 5 (0,167) | 11 (0,367) | 12 (0,400) |
| Gold'a değmeyen cevap | — | E005, E011 | C003, E011 |
| "Desteklenmiyor" vakaları | C001, C006, C057, E003, E006, E012 | C006, C016, C024, C057, E001, E003, E006, E008, E012 | — |

**Seçim ve teyit ayrımı (destek oranı / boş alıntı oranı):**

| Küme | K1 | K2 | K3 |
|---|---|---|---|
| Seçim (19) | 0,684 / 0,474 | 0,579 / 0,158 | 0,632 / 0,211 |
| Teyit (11) | 1,000 / 0,909 | 0,909 / 0,273 | 0,818 / 0,273 |

Küçük örneklemde iki küme arasındaki fark büyük. Bu çalışmada bir seçim yapılmadığı için teyit kümesi "harcanmadı"; ama artık D0–D2 için ölçülmüş durumda.

### 4.1 Önceden sabit yorum kuralları

| Kural | Sonuç | Değer |
|---|---|---|
| R1: model kararı (K1 destek < 0,90) | **Tetiklendi** | 0,800 |
| R2: karıştırıcı etkisi (K1 ≥ 0,90 ve K2 < K1 − 0,10) | Tetiklenmedi | K1 0,90 altında |
| R3: retrieval ya da aday havuzu (K1, K2 ≥ 0,90 ve K3 belirgin düşük) | Tetiklenmedi | K3 = K2 = 0,700 |
| R4: alıntı üretimi (boş alıntı ≥ 0,10 ya da sahte > 0) | **Tetiklendi** | Boş alıntı en fazla 0,633; sahte 2 |
| R5: değerlendirme (destek − gold isabeti ≥ 0,10) | **Biçimsel olarak tetiklendi** | En fazla fark 0,633 (§4.3) |

R3'teki "belirgin" eşiği (0,10) ön kayıtta sayı olarak yoktu. D2 koşusundan önce betikte sabitlendi ve sonuç bu eşikten bağımsız: K3 ile K2 eşit.

### 4.2 Keşif (ön kayıtta yoktu)

- **Az bağlam, daha çok boş alıntı.** Boş alıntı en sık **yalnız gold** verildiğinde görülüyor: K1 0,633, K2 0,200, K3 0,233.
  - Olası açıklama (test edilmedi): model tek ve açıkça ilgili bir parçada kararı verip alıntıyı gereksiz görüyor.
  - Boş alıntıların hepsinde `eksik` alanı boş.
  - Bu, D3'teki "destekleniyor = true iken en az 1 alıntı" şema önerisinin en büyük etkiyi yapabilecek değişiklik olduğunu düşündürüyor.
  - **Uygulanmadı:** istem ve şema değişikliği ayrı onaya bağlı.
- **Sahte alıntı yalnız K2'de.** İki karar (C003, C034) yakın ama birebir olmayan metin üretti. Birebir doğrulama ikisini de reddetti; güvenlik sınırı çalışıyor.
- **Sürekli ret.** C006, C057, E003, E006 ve E012 her üç koşulda da cevaplanamadı. Gold verilse bile "desteklenmiyor" ya da kanıt yetersiz. Bunlar ya model kararı sorunu ya da taslak gold hatası.
  - C057 ve E006 insan inceleme listesinin öncelikli vakaları arasında.
- **E001.** Mevcut retrieval'da gold yok, model reddediyor; K1'de gold verilince cevaplanıyor. Retrieval kaybının gerçek sonuca etkisi tek vakayla sınırlı.

### 4.3 R5'in yorumu

- R5 farkı ("destekleniyor" oranı − gold alıntı isabeti) bu koşuda neredeyse tamamen **boş alıntıdan** geliyor. Alıntı yoksa gold'a değen alıntı da olamaz.
- **Değerlendirme katmanını doğrudan gösteren ölçü** (önceden kayıtlı değil; keşif): cevaplanan vakalarda gold isabeti K1 5/5, K2 11/13, K3 12/14.
- Gold'a değmeyen cevaplar (C003, E005, E011; **E011 iki koşulda**) ya yanlış kanıtlı cevap ya da eksik gold. İnsan incelemesi olmadan ayırt edilemez.
- Ayrıca `expected_claims` boş olduğu için anlamsal doğruluk hiçbir koşulda hesaplanamıyor.
- **Sonuç:** değerlendirme katmanı açık bir belirsizlik kaynağı, ama gözlenen kayıpların ana nedeni değil.

## 5. Tekrarlanabilirlik uyarısı

- D0 yeniden koşusu ile eski P2 kaydı aynı yapılandırmada: aynı digest, istem, ayarlar, v1 indeksi ve `aday_deneyi`.
- Buna rağmen 4 vaka farklı:
  - C001, C013, C054: eski koşuda `answered`, yeni koşuda boş alıntı nedeniyle `no_source`.
  - E008: eski koşuda kanıt yetersiz, yeni koşuda `answered`.
- **Net etki:** yanlış ret 14 → 16. `temperature=0` ve `seed=0` modelin çıktısını koşular arasında birebir sabitlemiyor.
  - İki koşuda da üretici sıcak tutuldu (eski `keep_alive=10m`, yeni `5m`).
  - Olası nedenler (doğrulanmadı): GPU hesaplama sırası, Ollama'nın bellek ve toplu işleme durumu.
- **Sonuç:** 2–4 vakalık farklar gürültü sınırında. D3 ve sonrasında karşılaştırmalar ya tekrarlı koşu ya da aynı oturumda eşleştirilmiş koşu gerektirir.

## 6. Testler ve doğrulama

- **Yeni testler — `tests/test_rag_d0_d1.py` (20 test):**
  - Sınıfların ayrıklığı; inceleme bekleyen cevabın doğru sayılmaması; alt neden önceliği.
  - Teknik hatanın kaçınma paydasından çıkarılması; anlamsal doğruluğun hesaplanmaması.
  - Tamamlama: varsayılanın kapalı olması; normal kipte hiç çalışmaması; üst sınırda durması; eklenen sonuçlarda bütünlük ihlalinin bütün sorguyu durdurması; onay filtresinin korunması.
  - Ham karar kaydı (cevaplanan ve boş alıntı); model çağrılmadığında kaydın boş kalması.
- **Regresyon, entegrasyon ve altın özetler:** §7'de.

## 7. Durum

- **Varsayılan paket:** **1348 geçti** (önceki 1328 + 20 yeni), 522 seçim dışı (PostgreSQL/Docker 510 + gerçek model 12), 0 başarısız.
- **Altın özetler ve özgün paket sağlama özeti:** geçti. Aşama 1–6 kodu değişmedi.
- **Gerçek model entegrasyonu** (`-m integration tests/test_rag_generation_integration.py`): 11 geçti, 1 xfail.
  - Xfail testi `test_direct_evidence_call_respects_budget`: bilinen boş alıntı davranışı; D2 bunu nicel olarak doğruladı.
- **Koşu sonrası:** Ollama'da yüklü model yok (boşaltma doğrulandı).
- **Gerçek depo:** eşikler `null`; hazır indeksler ve manifestler değişmedi.
- **Dokunulmayanlar:** PostgreSQL, Docker, migration, seed ve `main`.
- **Rapor dosyaları eklendikten sonra:** sunum (marka ve yerel yol) taraması, altın özet testleri ve yeni testler yeniden koşuldu: 300 geçti.

**Sıradaki kararlar proje sahibinde (bu çalışmada yapılmadı):**
- D3: istem ya da şema; özellikle "destekleniyor = true ⇒ en az 1 alıntı".
- D4 ve D5: model indirme ve kurulum onayı gerektiriyor.
- Tamamlamanın v1 için kalıcı varsayılan olması.
- İnsan incelemesi: gold teyidi ve `expected_claims`.
- Aşama 8.7.
