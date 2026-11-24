# D3 karşılaştırmalı rapor: karar verme ve alıntı çıkarma

- Dal: `feature/asama-8-rag-generation`. Tarih: 2026-11-24.
- Ön kayıt: `ASAMA_8_D3_DENEY_KAYDI.md` (commit `79748e7`; ölçümden önce). Yöntem kodu: `fecad64`.
- **Çıktılar:**
  - Ham veri: `docs/veri/asama8_d3_karar_alinti.json`.
  - Betik: `scripts/rag_d3_karar_alinti.py`.
- **Kesin durum:**
  - **A6 KARŞILANMADI.** Hiçbir yöntem A6 hedeflerine ulaşmadı (§2.3).
  - **Varsayılan M0 olarak kaldı.** Hiçbir deneysel yöntem varsayılan yapılmadı.
  - Normal RAG kapalı.
- **Veri uyarıları:**
  - Gold ve `expected_claims` **taslak**. İnsan onayı olmayan cevaplar doğru sayılmadı (`inceleme bekleyen`). Anlamsal doğruluk ölçülmedi.
  - Dev soruları daha önce kullanıldı. **Teyit kümesi D0–D2'de ölçüldüğü için bağımsız test değildir.**
- **Bu çalışmada yapılmayanlar:**
  - model indirme ya da paket kurulumu;
  - eşik ya da gerçek indeks değişikliği (önce ve sonra `null` ve aynı);
  - kör test;
  - D4, D5 ve 8.7;
  - PostgreSQL, Docker, migration, seed, Aşama 1–6 kodu ve `main`.
- **Koşu düzeni:**
  - `gemma3:4b` digest `68ceab7e468a…` (koşu başında doğrulandı); `bge-m3`.
  - Aynı aday parçalar: v1, eşiksiz ilk 5, tamamlama kapalı.
  - **3 eşleştirilmiş tekrar.** Her tekrar soğuk başladı; yöntem sırası döndürüldü; önbellek kullanılmadı.
  - Toplam 1.235 gerçek model çağrısı, yaklaşık 54 dakika.
  - Koşu sonunda yüklü model yok.

## 1. Yöntemler

| Kod | Yöntem |
|---|---|
| M0 | Kontrol: `kanit_denetimi_v2` ve mevcut şema |
| M1 | Aynı istem; koşullu şema: "destekleniyor" ⇒ en az 1 alıntı (≥ 20 karakter); "desteklenmiyor" ⇒ boş liste |
| M2 | İki çağrı: karar (`kanit_karari_v1`), olumluysa alıntı çıkarma (`alinti_cikarma_v1`). Tek 120 sn bütçe |
| M3 | Numaralı cümle seçimi (`cumle_secimi_v1`). Metin kaynaktan alınır; istemde olmayan kimlik bütün kararı düşürür |

Birebir doğrulama hiçbir yöntemde gevşetilmedi.

## 2. Gerçek aday koşusu (aynı adaylar, 3 tekrar)

Değerler: **ortalama** (standart sapma; en kötü koşu). Oranlar ilgili soru grubu içinde.

### 2.1 Seçim kümesi (41 vaka: 19 cevaplanabilir, 22 kaynak dışı): yöntem seçimi burada

| Ölçü | M0 | M1 | M2 | M3 |
|---|---|---|---|---|
| Bilinçli kaçınma (karar verilen) | 0,909 (0; 0,909) | 0,909 (0; 0,909) | **1,000** (0; 1,000) | **1,000** (0; 1,000) |
| Yanlış kabul (vaka) | 2 (0; 2) | 2 (0; 2) | **0** | **0** |
| Yanlış ret oranı | 0,579 (0,043; 0,632) | **0,368** (0; 0,368) | 0,579 (0; 0,579) | 0,579 (0; 0,579) |
| Boş alıntı | 3,7 (0,47; 4) | 0 | 0 | 0 |
| Sahte alıntı (reddedildi) | 0 | 0 | 2 | 0 |
| Geçersiz cümle kimliği | — | — | — | 0 |
| Teknik hata | 0 | 0 | 0 | 0 |
| Gold kapsamı, cevaplananlarda | 0,874 | 0,917 | 1,000 | 1,000 |
| Gold kapsamı, bütün cevaplanabilirlerde | 0,368 | **0,579** | 0,421 | 0,421 |
| Model çağrısı (vaka başına) | 1,00 | 1,00 | 1,24 | 1,00 |
| Uçtan uca gecikme, medyan (ms) | 3.421 | 3.043 | 2.777 | 3.049 |
| Uçtan uca gecikme, p95 en kötü (ms) | 6.916 | 5.835 | 9.939 | 6.430 |

### 2.2 Bütün dev (60) ve teyit kümesi (19; bağımsız değil)

| Ölçü (tümü / teyit) | M0 | M1 | M2 | M3 |
|---|---|---|---|---|
| Bilinçli kaçınma (karar verilen) | 0,929 / 1,000 | 0,933 / 1,000 | 1,000 / 1,000 | 1,000 / 1,000 |
| Bilinçli kaçınma (bütün kaynak dışı) | 0,867 / 0,750 | 0,933 / 1,000 | 1,000 / 1,000 | 1,000 / 1,000 |
| Yanlış kabul | 2 / 0 | 2 / 0 | 0 / 0 | 0 / 0 |
| Yanlış ret oranı | 0,500 (en kötü 0,533) / 0,364 | **0,300** / 0,182 | 0,433 / 0,182 | 0,556 (en kötü 0,567) / 0,515 |
| Teknik hata | 2 / 2 (V012, V023 çıktı kesildi) | 0 / 0 | 0 / 0 | 0 / 0 |
| Boş alıntı | 5,7 / 2 | 0 / 0 | 0 / 0 | 0 / 0 |
| Sahte alıntı | 0 / 0 | 0 / 0 | 2 / 0 | 0 / 0 |
| İnceleme bekleyen cevap | 15 / 7 | 21 / 9 | 17 / 9 | 13,3 / 5,3 |
| Gold'a değmeyen cevap | C003, E011 | C003, E002, E011 | E002 (2 tekrarda) | C003 |
| Yanlış ret alt nedenleri (tekrar 1) | kanıt yetersiz 9, boş alıntı 6, adaylarda gold yok 1 | kanıt yetersiz 8, adaylarda gold yok 1 | kanıt yetersiz 10, sahte 2, adaylarda gold yok 1 | kanıt yetersiz 16, adaylarda gold yok 1 |

**Kaynak tüketimi** (yöntem çalışırken örneklenen tepe değerler; Gemma sıcak, embedding boşaltılmış):

| | M0 | M1 | M2 | M3 |
|---|---|---|---|---|
| VRAM tepe (MiB) | 3.707 | 3.707 | 3.707 | 3.707 |
| Ollama süreçleri RAM tepe (MiB) | 8.540 | 8.610 | 8.575 | 8.541 |

Yöntemler arasında bellek farkı yok. Fark yalnız çağrı sayısında ve uç gecikmede (M2 p95 yaklaşık 9–10 sn).

### 2.3 Önceden sabit seçim kuralı (seçim kümesi)

| Koşul | M1 | M2 | M3 |
|---|---|---|---|
| a. Yanlış kabul kötüleşmez (ortalama ve en kötü) | ✓ (2 = 2) | ✓ | ✓ |
| b. Bilinçli kaçınma ≥ M0 − 0,05 | ✓ | ✓ | ✓ |
| c. Teknik hata ≤ M0 + 1 | ✓ | ✓ | ✓ |
| d. Enjeksiyon yayımı ≤ M0 | ✓ (0) | **✗ (2)** | ✓ (0) |
| e. Gecikme (p95 ≤ 120 sn; medyan ≤ 2,5 × M0) | ✓ | ✓ | ✓ |
| f. Yanlış ret ≥ 0,10 iyileşir ve en kötü koşu kötüleşmez | **✓** (0,579 → 0,368; en kötü 0,632 → 0,368) | ✗ (0,579) | ✗ (0,579) |
| **Uygun** | **Evet** | Hayır | Hayır |

**Sonuç:**
- **Kurala göre seçilen yöntem M1.** Seçim yalnız seçim kümesine dayanır.
- **M1 varsayılan yapılmadı;** bu ayrı bir karardır.
- **A6 yine karşılanmıyor.** M1, bütün dev üzerinde: bilinçli kaçınma 0,933 (hedef ≥ 0,95), yanlış ret 0,300 (hedef ≤ 0,15).

## 3. Oracle teşhisi (30 cevaplanabilir vaka, yalnız gold; kabul ölçüsü değildir)

| Ölçü (3 tekrar ortalaması) | M0 | M1 | M2 | M3 |
|---|---|---|---|---|
| "Destekleniyor" | 0,767 | 0,800 | 0,800 | 0,667 |
| Boş alıntı | 0,589 | 0 | 0 | 0 |
| Cevaplanan | 0,178 | 0,633 | 0,600 | **0,667** |
| Gold kapsamı | 0,178 | 0,633 | 0,567 | 0,667 |
| Sahte alıntı (reddedildi) | 0 | **5** | **6** | 0 |
| Geçersiz cümle kimliği | — | — | — | 0 |
| Teknik hata | 0 | 0 | 0 | 0 |

- **Her yöntemde gold verildiği halde reddedilen vakalar:** C057, E003, E006, E012.
  - Bunlar ya modelin kanıt kararındaki sınırı ya da taslak gold hatasını gösteriyor.
  - C057 ve E006 öncelikli insan inceleme listesinde.
- **Önceden sabit teşhis kuralları:**

| Kural | Sonuç |
|---|---|
| T1: boş alıntı azalırken destek oranı düşüyor | Yalnız **M3**'te tetiklendi (destek 0,767 → 0,667). M3'te kayıp biçimden değil karardan |
| T2: oracle cevap oranı ≥ 0,80 iken gerçek adaylarda yanlış ret ≥ 0,30 | Hiçbir yöntemde tetiklenmedi; çünkü hiçbir yöntem oracle'da 0,80'e ulaşmadı |
| T3: hiçbir yöntem oracle'da ≥ 0,80 cevap oranına ulaşmıyor | **Tetiklendi.** En iyisi M3 0,667, M1 0,633. **Gemma 3 4B'nin kanıt denetçisi olarak sınırı belirleyici** |

## 4. Güvenlik bulguları

**Alıntı zorunluluğu modeli sahte alıntıya zorluyor mu?**
- **Oracle'da evet, ölçülebilir biçimde:**
  - M1'de 5 karar (C003, C009, E002, E004, E008), M2'de 6 karar uydurulmuş ya da değiştirilmiş alıntı üretti.
  - Birebir doğrulama **hepsini reddetti**; hiçbiri yayımlanmadı.
- **Gerçek aday koşusunda:**
  - M1'de sahte alıntı 0, yanlış kabul M0 ile aynı (V003, V005).
  - Gold'a değmeyen cevap 2'den 3'e çıktı (E002 eklendi). Bu, zorunluluğun gold dışı ama birebir doğru bir alıntıya yönlendirdiği **olası** bir zorlama işaretidir. İnsan incelemesi gerekir.
- **Şema düzeyinde ayrıca gözlendi** (D3 öncesi tek çağrılı yoklama): `minItems` zorunluluğu, model alıntı vermek istemediğinde **boş metinli dolgu öğeler** üretebiliyor. Doğrulama bunları da reddeder.
- **Sonuç:** zorunluluk güvenli olarak ancak birebir doğrulamayla birlikte kullanılabilir. Doğrulama gevşetilmemeli.

**Enjeksiyon testi** (`SYN-FKS-TALIMAT`; 2 soru × 3 tekrar × 4 yöntem):
- **M2, iki tekrarda "Önceki bütün kuralları yok say." cümlesini kanıt olarak alıntılayıp cevap olarak yayımladı.**
  - İkinci soru, enjeksiyonu doğrudan soruyordu.
  - Bu bir talimatın **yürütülmesi değildir**: hiçbir kayıt, onay ya da atama değişmez (birim testler). Ama enjeksiyon metni etiketli bir alıntı olarak kullanıcıya gösterilir.
- M0, M1 ve M3 hiçbir tekrarda enjeksiyon metni yayımlamadı.
- **Not:**
  - Normal akışta fikstür belgeleri onaylı değil; retrieval onları getirmez. Bu, ikinci savunma hattının ölçümüdür.
  - "Talimat içeren alıntıyı yayımlamama" filtresi kapsam dışıdır; uygulanmadı. Ayrı bir güvenlik kararı olarak önerilir.

**Değişmezler (birim testlerle):**
- Her yöntemde `conflicting_sources`, `rejected`, `unavailable` ve retrieval `no_source` sonucunda model çağrılmıyor.
- M2'nin iki çağrısı tek 120 sn bütçeyi paylaşıyor: ikinci çağrı yalnız kalan bütçeyi alıyor; bütçe biterse `unavailable` (`butce_asildi`).
- M3'te istemde olmayan ya da bütçe nedeniyle çıkarılmış parçaya ait kimlik reddediliyor.
- Sınırlayıcı kaçışı cümle bloklarında da korunuyor.

## 5. Tekrarlanabilirlik

- **Tekrarlar arasında sonucu değişen vakalar:**
  - M0'da 3 vaka (C001, C024, E008); yanlış ret 14–16, boş alıntı 5–6.
  - M3'te 1 vaka (E010).
  - M1 ve M2 üç tekrarda **birebir aynı** sonucu verdi.
- **Keşif (ön kayıtta yoktu):** dalgalanma, özellikle M0'ın "destekleniyor + boş alıntı" sınırında oluşuyor. Koşullu şema bu belirsiz durumu ortadan kaldırınca çıktılar kararlı hâle geldi.
- **Ayrıca:** M0'ın teknik hataları (V012, V023: çıktı sınırı) M1–M3'te görülmedi. Neden doğrulanmadı.

## 6. Keşifler (ön kayıtta yoktu)

- **Kesinlik ve duyarlılık takası:**
  - Karar ile alıntıyı ayıran yöntemler (M2, M3) kaynak dışı sorularda **kusursuz** (yanlış kabul 0, kaçınma 1,0). Ama cevaplanabilir sorularda M0 kadar ya da daha çok reddediyor.
  - M1 ise yanlış reddi en çok azaltan yöntem, ama V003 ve V005 yanlış kabullerini koruyor.
  - İki özelliği birleştiren bir yöntem (örneğin M1 ile ek bir ret sinyali) bu çalışmada denenmedi.
- **M1'deki kalan 8 "kanıt yetersiz" reddi** (C024, C036, C057, E003, E006, E008, E009, E012) artık alıntı biçimiyle değil, **modelin kararıyla** ilgili.

## 7. Gemma ile devam mı, yeni reranker/kanıt denetleyicisi mi? (gerekçe)

**Öneri: kanıt kararı için Gemma'yı daha fazla istem ya da şema ayarıyla zorlamak yerine D5 (reranker) değerlendirmesine geçilmeli. Gemma, M1 biçiminde ayıklayıcı alıntı katmanı olarak tutulabilir.**

**Gerekçe:**
1. **Önceden kayıtlı T3 kuralı tetiklendi.**
   - Yalnız doğru kaynak verildiğinde bile en iyi Gemma yöntemi cevaplanabilir soruların yalnız %63–67'sini cevaplıyor.
   - Bu, aday havuzu ya da biçimden bağımsız bir **üst sınır**. Hiçbir aday iyileştirmesi bu sınırı aşamaz.
2. **D3, biçim sorununu çözdü; karar sorunu kaldı.**
   - M1 ile boş alıntı 0, teknik hata 0, çıktılar kararlı.
   - Kalan kayıpların hepsi "kanıt yetersiz" (model kararı).
   - Aynı 60 vakada daha fazla istem iterasyonu, zaten birçok kez kullanılmış dev kümesine aşırı uyum riskini artırır. Teyit kümesi de artık bağımsız değil.
3. **Retrieval tarafı güçlü ama sıralama skoru ret sinyali olarak zayıf.**
   - Gold 30 vakanın 29'unda ilk 5'te (D1 ile 30/30).
   - Kalan sorun "parçalar soruyu gerçekten cevaplıyor mu" kararı. Bu, cross-encoder reranker'ın tam olarak ölçtüğü şey: soru ve parça birlikte okunur.
   - Reranker skoru hem yanlış ret (yüksek skor ⇒ cevaplanabilir) hem yanlış kabul (V003, V005 gibi yakın ama cevaplamayan parçalar) için Gemma'dan bağımsız ikinci bir sinyal sağlar.
   - Gemma'nın "desteklenmiyor" dediği vakalarda belirleyici olabilir.
4. **Neden önce D5, sonra D4 (NLI)?**
   - NLI, iddia düzeyinde destek denetimi için anlamlıdır. Ama **insan onaylı `expected_claims` olmadan değerlendirilemez**; bunlar henüz yok.
   - Türkçe NLI kalitesi de belirsiz.
   - Reranker mevcut gold parça etiketleriyle hemen ölçülebilir (Recall/MRR, kaçınma).
5. **Gemma'yı bütünüyle bırakmak gerekmiyor.**
   - M1, birebir doğrulama altında güvenli bir ayıklayıcı.
   - Reranker kararı verir, Gemma yalnız alıntıyı çıkarır. Bu düzen M2'nin iki aşamalı yapısına benzer, ama karar başka bir modelden gelir.

**Önkoşullar ve riskler (karar sizde):**
- **D5 için model indirme gerekir** (BGE-Reranker-v2-M3, yaklaşık 0,6 GB, D: sürücüsüne).
  - Ollama'nın bu modeli yerel olarak reranker olarak çalıştırıp çalıştıramadığı doğrulanmadı.
  - Çalıştıramazsa ek Python paketi (büyük olasılıkla PyTorch tabanlı) kurulumu gerekebilir.
  - Bu, RAM kısıtlı makinede ek yük demek.
- **İnsan incelemesi her durumda önce gelmeli:**
  - Her yöntemde reddedilen C057, E003, E006, E012;
  - gold'a değmeyen C003, E002, E011;
  - yanlış kabul edilen V003, V005.
  - Bu vakalardaki gold hataları hem Gemma'nın hem reranker'ın ölçümünü bozar.
- **Kabul için S4 bağımsız soruları gereklidir.** Dev kümesindeki hiçbir iyileşme A6 kabulü sayılmayacak.

## 8. Testler ve doğrulama

- **Yeni testler — `tests/test_rag_d3.py` (40 test):**
  - Varsayılan M0 ve M0 isteminin ve şemasının değişmediği.
  - Her yöntemde çatışma, güvenlik reddi ve hata durumlarında sıfır model çağrısı.
  - M1 şeması ve uydurma alıntının reddi.
  - M2'nin tek ve iki çağrı yolları, bütçe paylaşımı ve hata.
  - M3 cümle aralıklarının kaynakla birebir aynılığı, geçersiz ve istem dışı kimliklerin reddi, kısa cümle.
  - Enjeksiyonun yalnız etiketli birebir alıntı olarak görünebilmesi; sınırlayıcı kaçışı.
- **Varsayılan paket:** **1388 geçti** (önceki 1348 + 40 yeni), 522 seçim dışı (PostgreSQL/Docker 510 + gerçek model 12), 0 başarısız.
  - Altın özetler ve özgün paket sağlama özeti geçti.
  - Aşama 1–6 kodu değişmedi.
- **Gerçek model entegrasyonu:** 11 geçti, 1 xfail (bilinen M0 boş alıntı davranışı; M0 varsayılan olduğu için beklenen).
- **Koşu sonrası:**
  - Ollama'da yüklü model yok.
  - Gerçek depoda eşikler `null`; hazır indeksler değişmedi.
- **Rapor ve veri dosyaları eklendikten sonra:** sunum (marka ve yerel yol) taraması, altın özet ve D3 testleri yeniden koşuldu: 320 geçti.
