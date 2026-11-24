# A6 bağımsız denetim bulgularının doğrulanması ve iyileştirme planı

- Dal: `feature/asama-8-rag-generation`. Tarih: 2026-11-24. İncelenen commit: `0876f9dc68ca9233bf1a73de4c713fac1b1fd9cc`.
- **A6 KARŞILANMADI.** Bu çalışmada:
  - deney, eşik ayarı ve istem değişikliği yapılmadı;
  - model indirilmedi, paket kurulmadı;
  - normal RAG kapalı; kör test kullanılmadı; 8.7'ye geçilmedi.
- Doğrulama yalnız mevcut kod ve ham kayıtlarla yapıldı:
  - `docs/veri/asama8_kanit_hatti_dev.json` (v2);
  - `asama8_kanit_hatti_dev_v1.json`;
  - `asama7_dev_getirme_dokumu.json`;
  - `src/medakis/rag/**`, `scripts/rag_kanit_hatti_degerlendirme.py`.

## 1. Bulguların doğrulanması

| # | Bulgu | Sonuç | Kanıt |
|---|---|---|---|
| 1 | `verify_quotes()` alıntının gerçek olduğunu doğruluyor, soruyu cevapladığını doğrulamıyor; V003 ve V005 yanlış kabul edildi | **Doğrulandı** | `quotes.py` yalnız birebir eşleşme ve aralık denetimi yapıyor. P2 (v2): V003 ve V005 `answered`, gold yok (kaynak dışı) |
| 2 | C036, C046, C057'de doğru kaynak 1. sırada ve bütçede, Gemma yanlış reddediyor | **Doğrulandı** | Döküm: üç vakada gold 1. sıra. 5 parçanın hepsi isteme giriyor (tahmini 876–1.073 token; çıkarılan yok). P1 ve P2: C036 ve C057 `kanit_yetersiz`, C046 `alinti_yok` |
| 3 | `EVIDENCE_SCHEMA` `destekleniyor=true` ile boş alıntıya izin veriyor; P1'de 11, P2'de 4 cevaplanabilir kayıp | **Doğrulandı** | Şemada `alintilar` için `minItems` ya da koşullu kural yok. v2: P1'de 11, P2'de 4 `sahte_alinti:alinti_yok` (yalnız cevaplanabilir) |
| 4 | Tekrar ayıklamadan sonra eksilen adaylar tamamlanmıyor; E001'de doğru kaynak havuza ulaşamıyor | **Doğrulandı** | `retriever.py`: Chroma'dan `top_k=10` isteniyor, `seen_ids` ve `seen_text` ile ayıklandıktan sonra yeniden istek yok. bge dökümünde 60 sorgunun 11'i 10'dan az aday döndürdü. **E001: 1 aday** (özdeş "Giriş" şablon parçaları tek parçaya indi; gold yok) |
| 5 | 8.5 raporunda hata nedenleri yanlış: token kesilmeleri V012 ve V023'te (kaynak dışı); P2'deki 14 yanlış ret = 10 kanıt yetersiz + 4 boş alıntı | **Doğrulandı; rapor düzeltildi** | v2 P2: `unavailable` yalnız V012 ve V023 (`cikti_kesildi`); yanlış ret sayımı 10 + 4. `ASAMA_8_5_ARA_RAPOR.md` §3, §4 ve §7 düzeltildi |
| 6 | `unavailable` doğru kaçınmaya sayılıyor | **Doğrulandı** | `summarize()`: `kacindi = sonuç != answered`. P2 doğru kaçınma 0,933 iki teknik hatayı içeriyor; bilinçli ret 26/30 = 0,867. Eski metrik korunuyor |
| 7 | `gold_hit()` her aralık kesişmesini yeterli sayıyor; C003, E002, E011 incelenmeli; `expected_claims` boş | **Doğrulandı** | `gold_hit` ifadesi: `q.doc_start < g.char_end and g.char_start < q.doc_end`. `expected_claims` 60/60 boş. Gold'a değmeyen cevaplar: C003 ve E002 (P1), C003 ve E011 (P2). **Ek eksik:** değerlendirme kaydı doğrulanmış alıntı metinlerini saklamıyor; bu yüzden bu cevapların neye dayandığı kayıttan görülemiyor |
| 8 | Doğrudan CV sızıntısı yok; ama aynı dev kümesinde istem ve yöntem seçimi bağımsız başarı kanıtı değil | **Doğrulandı ve kabul ediliyor** | Eşik yalnız eğitim katlarında seçiliyor. v1→v2 istem değişikliği ve politika karşılaştırması aynı 60 vakada yapıldı. Ayrıca kaynak dışı soruların hepsini geliştirici yazdı |

## 2. Önerilen ölçüm sözleşmesi (uygulama onaya bağlı)

Her vaka **tek ve ayrık** bir sınıfa düşer. Teknik hata ile karar hatası karıştırılmaz.

| Sınıf | Tanım |
|---|---|
| `TEKNIK_HATA` | `unavailable`: üretici, yaşam döngüsü, bütçe ya da istem hatası. Karar verilemedi |
| `BILINCLI_KACINMA` | Kaynak dışı soruda `no_source` |
| `YANLIS_KABUL` | Kaynak dışı soruda `answered` |
| `YANLIS_RET` | Cevaplanabilir soruda `no_source`. Alt nedenler: `aday_havuzunda_gold_yok`, `esik_alti`, `kanit_yetersiz`, `bos_alinti`, `sahte_alinti` |
| `DOGRU_CEVAP` | Cevaplanabilir soruda `answered` **ve** doğrulanmış alıntılar soruyu cevaplayan `expected_claims` iddialarını destekliyor (insan etiketli) |
| `YANLIS_KANITLI_CEVAP` | Cevaplanabilir soruda `answered`, ama alıntılar beklenen iddiayı desteklemiyor (ör. başka bir formül) |

**Raporlanacak ölçüler:**
- **Bilinçli kaçınma oranı:** `BILINCLI_KACINMA / (kaynak dışı − TEKNIK_HATA)`. Ayrıca bütün kaynak dışı sorulara göre.
- **Teknik hata oranı:** ayrı raporlanır; kaçınma sayılmaz.
- **Yanlış kabul oranı:** kaynak dışı sorular içinde.
- **Yanlış ret oranı:** cevaplanabilir sorular içinde, alt nedenlere göre dökümüyle.
- **Anlamsal cevap doğruluğu:** `DOGRU_CEVAP / (DOGRU_CEVAP + YANLIS_KANITLI_CEVAP)`. **İnsan incelemesi olmadan hesaplanmaz.**
- **Aday havuzu kapsamı:** gold parça ilk k adayda mı ve havuzda kaç tekil aday var.
- **Eski ölçüler** (doğru kaçınma, yanlış kaçınma, gold isabeti) geriye dönük karşılaştırma için ayrı sütunda korunur.

**Değerlendirme kayıtlarına eklenecekler:** doğrulanmış alıntı metinleri ve aralıkları, modelin ham kararı, istemdeki aday listesi ve sırası, teknik hata kodu.

## 3. İnsan incelemesi

`ASAMA_8_A6_INSAN_INCELEME_LISTESI.md` dosyası şunları içeriyor:
- **30 cevaplanabilir vaka:** soru, gold kaynağı, gold alıntısı, gold alıntısından türetilmiş **taslak** iddia; "gold doğru mu", "iddia" ve "not" sütunları.
- **12 öncelikli vaka:** C003, E002, E011, C036, C046, C057, E001, C024, E006, C001, C026, C031.
- **7 kaynak dışı etiket teyidi:** V003, V005, V012, V023, V006, V013, V020.

Taslak iddialar insan onayı **değildir**. Anlamsal doğruluk ölçüsü yalnız onaylanmış iddialarla hesaplanacak.

## 4. Kontrollü deney planı (önerilen sıra; her biri ayrı onay)

Bütün deneylerde aynı kurallar geçerli:
- Yalnız dev verisi kullanılır. Kabul kuralı deneyden önce yazılır ve dondurulur. Teknik hata ayrı raporlanır.
- Ayarlanan parametre varsa grup bazlı CV kullanılır.
- Seçim yanlılığına karşı dev, paraphrase grubuna göre iki kümeye bölünür: **seçim** (yaklaşık 2/3) ve **teyit** (yaklaşık 1/3). Yöntem ve istem seçimi yalnız seçim kümesinde yapılır; teyit kümesi bir kez ölçülür.
- S4 bağımsız kaynak dışı soruları geldiğinde ayrıca raporlanır. Nihai kabul Aşama 9 kör testindedir.

| Sıra | Deney | Amaç | Değişen | Gerekenler |
|---|---|---|---|---|
| D0 | **Ölçüm sözleşmesini uygulama** ve mevcut yapılandırmayı yeniden puanlama | Bulgu 5–7'yi ölçüm düzeyinde gidermek; kayıtlarda alıntı metni | Yalnız değerlendirme betiği ve kayıt biçimi; model, istem ve eşik aynı | İnsan etiketli `expected_claims` (§3) anlamsal ölçü için |
| D1 | **Aday havuzu tamamlama** | Bulgu 4: ayıklamadan sonra tekil aday sayısı k'ya ulaşana kadar ek sonuç istemek; `parcalama_v2` ile birlikte ve ayrı ayrı | Retrieval aday toplama (hata düzeltmesi); güvenlik denetimleri aynı | Yok (model çağrısı olmadan havuz kapsamı ölçülebilir) |
| D2 | **Gold kanıt kehaneti (oracle)** | Bulgu 2: Gemma'ya yalnız gold parça (ve ayrıca gold + karıştırıcılar) verilir. Denetçinin üst sınırı ve yanlış ret oranı, retrieval'dan bağımsız ölçülür | Yalnız denetçinin gördüğü girdi | Yok |
| D3 | **Karar ile alıntının ayrılması** | Bulgu 2 ve 3: (a) önce "parçalar soruyu cevaplıyor mu" kararı, sonra ayrı alıntı çıkarma; (b) şemada `destekleniyor=true` iken `alintilar` en az 1 | İstem ve şema | **İstem değişikliği onayı** |
| D4 | **Cevap-iddia denetimi** | Bulgu 1: alıntının istenen bilgiyi içerdiğini denetlemek. Seçenekler: insan etiketli iddialarla kural tabanlı eşleme (yalnız değerlendirmede) ya da NLI modeli | Kanıt kabul kuralı | NLI için **model indirme ve olası paket kurulumu onayı** |
| D5 | **Reranker karşılaştırması** | Cosine sıralaması ile BGE-Reranker-v2-M3; aday sıralaması ve kaçınma sinyali olarak | Aday sıralaması ve skoru | **Model indirme (yaklaşık 0,6 GB) ve büyük olasılıkla paket kurulumu onayı**; Ollama'da çalışıp çalışmadığı doğrulanmalı |

**Sonraki deney adayları (bağımsız denetim önerisi; kaydedildi, uygulanmadı):**
- BGE-Reranker-v2-M3 (D5).
- Çok dilli NLI modeli, kanıt-iddia uyumu için (D4).

Her ikisi de D: depolama, indirme ve kurulum onayı ile Aşama 1–6 ve güvenlik sınırları korunarak değerlendirilecek.

**Önerilen sıra ve gerekçe:** D0 → D1 → D2 → D3 → D4/D5.
- D0 ve D1 ölçümü ve aday toplamayı düzeltir; model davranışını değiştirmez.
- D2, sorunun retrieval'da mı denetçide mi olduğunu ayırır.
- D3–D5 ancak bu ayrım yapıldıktan sonra anlamlı biçimde karşılaştırılabilir.

## 5. Proje sahibinin kararı gerekenler

1. §2 ölçüm sözleşmesinin onayı (D0).
2. §3 insan incelemesi: gold teyidi ve `expected_claims` etiketlemesini kim yapacak?
3. D1 ve D2'nin başlatılması (model ya da istem değişikliği yok).
4. D3 için istem ve şema değişikliği izni.
5. D4 ve D5 için NLI ile reranker modellerinin indirilmesi ve gerekirse paket kurulumu izni.
6. Dev kümesinin seçim ve teyit olarak bölünmesi (§4).
