# D5 karşılaştırmalı rapor: reranker'lar ve ek reranker gerektirmeyen yöntemler (cevaplanabilirlik kapısı)

- Dal: `feature/asama-8-rag-generation`. Tarih: 2026-11-24.
- Ön kayıt: `ASAMA_8_D5_DENEY_KAYDI.md` (commit `9e5c156`; smoke testlerinden ve ölçümden önce).
- **A6 KARŞILANMADI.**
  - Aşağıdaki "A6 hedefine ulaştı" ifadeleri yalnız **dev verisinde, reranker kabul/ret katmanı** içindir. Uçtan uca kaynaklı cevap başarısı değildir.
  - Dev soruları kör test değildir; teyit kümesi bağımsız değildir.
  - İnsan incelemesi ve S4 tamamlanmadı.
- **Kesin durum:**
  - **Hiçbir model ya da eşik varsayılan yapılmadı.** Gerçek indeks, eşikler (`null`) ve onay kayıtları değişmedi.
  - Gemma kullanılmadı. Normal RAG kapalı.
- **Ham veriler:**
  - `docs/veri/asama8_d5_sonuclar.json`
  - `asama8_d5_aday_havuzu.json`
  - `asama8_d5_smoke.json`
  - `asama8_d5_skorlar_t1.json`
  - `asama8_d5_kurulum.json`
  - Bütün tekrarların ham skorları: `D:\medakis-deney\d5\sonuclar\`.

## 1. Özet

| Kategori (ön kayıtlı kural; seçim kümesi, CV) | Sonuç |
|---|---|
| **En başarılı yöntem** | **R3: BGE-M3 (H20+) + LAMAR-600m** |
| **Kalite/donanım dengesi** (kurala göre) | **R0: BGE-M3 cosine** (kural yorumu §5.2'de) |
| ↳ Reranker'lar arasında (keşif) | **R2: ModernBERT-TR** (0,7 GB VRAM) |
| **En başarılı ek reranker gerektirmeyen yöntem** (düzeltildi; §10) | **R0: BGE-M3 cosine** (BGE-M3 embedding modeli gerekir) |
| **En başarılı tamamen modelsiz yöntem** (düzeltildi; §10) | **B1: BM25.** Tümde A6 uzaklığı 0,117; 5 yanlış kabul |

**R3 (LAMAR-600m) sonuçları:**
- Seçim kümesinde doğru kaçınma 1,000, yanlış kaçınma 0,053, AUROC 1,000.
- Bütün dev'de 1,000 / 0,033.
- Teyit kümesinde 1,000 / **0,182**: yanlış kaçınma burada A6 hedefini (≤ 0,15) aşıyor (11 cevaplanabilir vakada 2 ret).

**R0 (cosine) için kural sonucu:**
- Seçim kümesindeki A6 uzaklığı (0,008) en iyinin 0,05 içinde ve VRAM'i sıfır olduğu için kural onu seçti.
- Ancak bütün dev'de A6'yı karşılamıyor (0,933 / 0,167; 2 yanlış kabul).

**R2 (ModernBERT-TR) sonuçları:**
- Bütün dev'de 1,000 / 0,067; teyit kümesinde 1,000 / 0,000.
- Seçim kümesinde yanlış kaçınma 0,158 (19'da 3).

**Birincil hipotez (R5 ve R4):**
- **Cevaplanabilirlik talimatı Qwen3-0.6B'yi belirgin biçimde iyileştirdi.**
  - Seçim kümesinde yanlış kaçınma 0,158 → 0,053.
  - Bütün dev'de doğru kaçınma 0,933 → 1,000; yanlış kabul 2 → 0.
- Ama Qwen3-0.6B, LAMAR ve ModernBERT'ten daha fazla bellek (yaklaşık 3,5 GB ayrılmış; nvidia-smi tepe 5,9 GB) ve yaklaşık 3–4 kat süre kullanıyor.

**Aday tamamlama etkisi:**
- Cosine'de kabul/ret kararını değiştirmiyor (en iyi aday aynı). Sıralamayı iyileştiriyor: Recall@10 0,967 → 1,000.
- Reranker'lar için **gerekli.** Tamamlamasız H10'da her reranker kötüleşiyor (§4).

## 2. Kurulum ve doğrulama

**Paketler ve CUDA doğrulaması:**
- **Python 3.14.6, `torch 2.14.1+cu126`:** gerçek cihazda `cuda.is_available()=True`, `sm_75` mimari listesinde, fp32 matris çarpımı sonlu.
- **`transformers 5.24.0`, `sentence-transformers 6.3.0`:** `pip check` sorunsuz.
- Kilit dosyası: `experiments/d5/requirements-d5.lock.txt` (48 paket; **ana proje bağımlılığı değil**). MedAkis `.venv` ve `requirements.txt` değişmedi.

**Modeller:**
- Dört model sabit revision ile indirildi; LFS ağırlık dosyalarının SHA-256 özetleri Hub ile eşleşti.
- `auto_map` yok; `trust_remote_code` kullanılmadı.
- Token kullanılmadı (`HF_TOKEN` süreçten kaldırıldı).

**Yollar:**
- Bütün önbellek ve geçici yollar `D:\medakis-deney\d5` altında. Başlatıcı C: yolunda durur.

**Disk (gerçek):**

| Kalem | Boyut |
|---|---|
| D5 sanal ortamı | 4,48 GB |
| Model önbelleği (4 model) | 5,95 GB |
| pip önbelleği | 2,54 GB |
| Geçici | 0,08 GB |
| **Toplam (D:)** | **13,05 GB** |

- D: boş alanı 197,74 → 184,57 GB.
- C: boş alanı 10,86 → 10,25 GB. D5'ten kaynaklanmıyor: NVIDIA derleme önbelleği ve C: HF, pip ve torch önbellekleri değişmedi. Düşüş oturum aracının geçici dosyalarından ve aynı saatlerde çalışan Visual Studio kurulum hizmetinden.

## 3. Smoke testleri: dört model ve beş yapılandırma, hepsi geçti

| Yapılandırma | Sonlu | Sabit değil | D > U | Determinizm farkı | Toplu/tekli farkı | Sıra değişimi farkı | D > I | D > C | Enjeksiyon en üstte |
|---|---|---|---|---|---|---|---|---|---|
| Qwen3-0.6B varsayılan | ✓ | ✓ | 8/8 | 0 | 1,4e-5 | 1,6e-5 | 8/8 | 6/8 | 0/8 |
| Qwen3-0.6B cevaplanabilirlik | ✓ | ✓ | 8/8 | 0 | 1,0e-5 | 5,4e-6 | 8/8 | 8/8 | 0/8 |
| ModernBERT-TR | ✓ | ✓ | 8/8 | 0 | 6,7e-6 | 6,7e-6 | 8/8 | 6/8 | 0/8 |
| LAMAR-600m | ✓ | ✓ | 8/8 | 0 | 9,5e-6 | 1,3e-5 | 8/8 | 6/8 | 0/8 |
| BGE-v2-M3 | ✓ | ✓ | 8/8 | 0 | 1,1e-5 | 7,6e-6 | 8/8 | 6/8 | 0/8 |

**Qwen'e özel denetimler:**
- "yes" (9693) ve "no" (2152) tek token; sol dolgu.
- Elle yazılan P(yes) hesabı, depodaki sentence-transformers yoluyla en fazla **2,0e-5** farkla ve aynı sıralamayla eşleşti.

**Kesme ve fark büyüklükleri:**
- Kesilen çift sayısı bütün koşularda 0 (512 token).
- Bütün farklar tolerans (1e-4 göreli) altında ve fp32 yuvarlama düzeyinde; model hatası değil.

**Gözlem:**
- Cevaplanabilirlik talimatı, "ilgili ama cevaplamayan" belgelerin skorunu belirgin düşürdü (örneğin G1: 0,88 → 0,02).
- Çelişkili (C) belgeler bütün modellerde doğru belgeye çok yakın ya da üstünde skor aldı: 6/8 grupta D > C.
- **İlgililik skoru çelişkiyi ve doğruluğu ayırt etmez.** Çatışma denetimi retrieval meta verisinde kalmalıdır.

## 4. Sonuçlar

**Okuma notları:**
- Kabul/ret oranları karar verilen vakalar üzerinden (bütün vakalar üzerinden oranlar JSON'da). Bu deneyde teknik hata, güvenlik reddi ve kaynak çatışması **0**, bu yüzden iki oran aynı.
- Kümeler:
  - **Tüm:** 60 vakada grup CV.
  - **Seçim:** CV yalnız 41 seçim vakası içinde.
  - **Teyit:** seçim kümesinde kalibre edilen eşik 19 vakaya uygulandı. Bu küme ayrımı ön kayıtta tanımlanmamıştı; ölçümden önce betikte sabitlendi.
- Reranker skorları 3 tekrarda **birebir aynı** (en büyük fark 0,0). Standart sapmalar 0, en kötü koşu ortalamaya eşit.

### 4.1 Kabul/ret ve ayırma gücü

| Yöntem | Seçim: kaçınma / yanlış kaçınma / AUROC | Teyit: kaçınma / yanlış kaçınma | Tüm: kaçınma / yanlış kaçınma / yanlış kabul / AUROC | Tümde A6 (dev) |
|---|---|---|---|---|
| R0 cosine (H10) | 0,955 / 0,158 / 0,962 | 1,000 / 0,182 | 0,933 / 0,167 / 2 / 0,968 | Hayır |
| R0+ / R1− / R1 cosine | R0 ile aynı (en iyi aday değişmiyor) | aynı | aynı | Hayır |
| **R2 ModernBERT-TR** | 1,000 / 0,158 / 0,993 | 1,000 / **0,000** | 1,000 / 0,067 / 0 / 0,993 | Evet |
| **R3 LAMAR-600m** | **1,000 / 0,053 / 1,000** | 1,000 / 0,182 | **1,000 / 0,033 / 0 / 1,000** | Evet |
| R4 Qwen3-0.6B varsayılan | 0,955 / 0,158 / 0,990 | 0,875 / 0,091 | 0,933 / 0,167 / 2 / 0,986 | Hayır |
| **R5 Qwen3-0.6B cevaplanabilirlik** | 0,955 / **0,053** / 0,998 | 1,000 / 0,182 | 1,000 / 0,133 / 0 / 0,992 | Evet |
| R6 BGE-v2-M3 (karşılaştırma) | 0,955 / 0,105 / 0,998 | 1,000 / 0,091 | 0,967 / 0,133 / 1 / 0,997 | Evet |
| B1 BM25 | 0,864 / 0,158 / 0,933 | 0,875 / 0,364 | 0,833 / 0,067 / 5 / 0,922 | Hayır |
| B1k BM25 (5 karakter kırpma) | 0,818 / 0,158 / 0,935 | 1,000 / 0,273 | 0,867 / 0,167 / 4 / 0,933 | Hayır |
| B2 hibrit (RRF) | 0,864 / 0,158 / 0,921 | 0,625 / 0,091 | 0,800 / 0,200 / 6 / 0,909 | Hayır |
| B3 yapılandırılmış erişim | 0,773 / 0,105 / 0,853 | 0,625 / 0,182 | 0,733 / 0,133 / 8 / 0,868 | Hayır |

**Vaka düzeyi (tüm, tekrar 1):**

| Yöntem | Yanlış reddedilen cevaplanabilir | Yanlış kabul edilen kaynak dışı |
|---|---|---|
| R0 | C016, C021, C024, E001, E006 | V013, V018 |
| R2 | E001, E011 | — |
| R3 | E002 | — |
| R5 | E002, E008, E010, E011 | — |
| R6 | C009, E002, E010, E011 | V013 |

E002 ve E011 insan inceleme listesinde zaten şüpheli (gold ya da soru belirsizliği).

### 4.2 Sıralama (tüm, 30 cevaplanabilir)

| Yöntem | Recall@5 | Recall@10 | MRR@10 | Gold 1. sırada |
|---|---|---|---|---|
| R0 (H10) | 0,967 | 0,967 | 0,923 | 0,900 |
| R0+, R1 (tamamlama) | 0,983 | 1,000 | 0,940 | 0,900 |
| R2 ModernBERT-TR | 0,983 | 1,000 | **1,000** | **1,000** |
| R3, R4, R5, R6 | **1,000** | 1,000 | **1,000** | **1,000** |
| B1 / B1k / B2 / B3 | 0,950 / 1,000 / 1,000 / 0,800 | 1,000 / 1,000 / 1,000 / 0,833 | 0,931 / 0,978 / 0,961 / 0,767 | 0,900 / 0,967 / 0,933 / 0,700 |

Kabul edilen cevaplanabilir vakalarda 1. sıradaki parça bütün reranker'larda gold (taslak gold).

### 4.3 Aday sayısı ve tamamlama (reranker'lar, tüm)

| Yöntem | H10 | H10+ | H20 | H20+ (birincil) |
|---|---|---|---|---|
| R2: Recall@5 / yanlış kaçınma | 0,950 / 0,100 | 0,983 / 0,067 | 0,967 / 0,067 | 0,983 / 0,067 |
| R3 | 0,967 / 0,067 | 1,000 / 0,033 | 0,983 / 0,033 | 1,000 / 0,033 |
| R4 | 0,967 / 0,200 | 1,000 / 0,167 | 0,983 / 0,167 | 1,000 / 0,167 |
| R5 | 0,967 / 0,133 (kaçınma 0,933) | 1,000 / 0,133 | 0,983 / 0,133 | 1,000 / 0,133 |
| R6 | 0,967 / 0,167 | 1,000 / 0,133 | 0,983 / 0,133 | 1,000 / 0,133 |

**Sonuç:** etkinin asıl kaynağı **tamamlama** (H10 → H10+). Aday sayısını 10'dan 20'ye çıkarmak tamamlamalı havuzda ek kazanç sağlamadı.

### 4.4 Maliyet ve kararlılık (fp32, GPU, batch 4)

| Yöntem | Yükleme (sn; 3 tekrar) | Sıcak skorlama, vaka başına 20 aday (ms medyan / p95) | Uçtan uca sıcak, retrieval dahil (ms medyan / p95) | VRAM tepe (nvidia-smi) / ayrılan | Süreç RAM tepe | Tekrarlar arası fark |
|---|---|---|---|---|---|---|
| R0/R1 cosine | — (Ollama BGE-M3) | — | 277 / 319 | Ollama | Ollama | — |
| R2 ModernBERT-TR | 12,3–13,3 | 266–297 / 335–410 | **562 / 692** | **727 / 613 MiB** | 1,3 GB | 0 |
| R3 LAMAR-600m | 37–60 | 443–453 / 472–488 | 724 / 783 | 2.319 / 2.199 MiB | 3,3 GB | 0 |
| R4 Qwen3 varsayılan | 23–25 | 1.600–1.613 / 1.732–1.749 | 1.891 / 1.993 | 5.941 / 3.542 MiB | 3,6 GB | 0 |
| R5 Qwen3 cevaplanabilirlik | 4,7–5,3 | 1.799–1.816 / 1.992–1.998 | 2.074 / 2.296 | 5.947 / 3.604 MiB | 3,7 GB | 0 |
| R6 BGE-v2-M3 | 34–49 | 433–436 / 465–469 | 714 / 769 | 2.319 / 2.199 MiB | 3,3 GB | 0 |

**Notlar:**
- **Yükleme süreleri** işletim sistemi dosya önbelleğine bağlı. Aynı tekrarda önce çalışan R4, R5'in yüklemesini hızlandırdı. Soğuk ölçüm bu yüzden geniş aralıkta.
- **Qwen'in nvidia-smi tepesi 6 GB sınırına çok yakın.** Gemma ya da BGE-M3 ile aynı anda yüklenemez; sıralı yükleme zorunlu.
- **Bütün koşularda:** sonlu olmayan skor 0, kesilen çift 0. Süreç bitince VRAM 0 MiB; Ollama'da yüklü model yok.

## 5. Seçim kurallarının uygulanması (seçim kümesi)

### 5.1 En başarılı yöntem: **R3 (LAMAR-600m)**

- **Uygunluk:** smoke geçti, teknik hata 0, Recall@5 1,000 ≥ R1'in 0,974'ü.
- **A6 uzaklığı:** 0 (bütün tekrarlarda).
- **Eşitlik kırılımı:** A6 uzaklığı 0 olan R3, R5 ve R6 arasında AUROC'u en yüksek olan R3 (1,000).

**Dikkat:**
- AUROC 1,000 yalnız 60 dev vakasında ve geliştiricinin yazdığı kaynak dışı sorularla elde edildi. Genelleme kanıtı değildir.
- Teyit kümesinde yanlış kaçınma 0,182; bu kümede A6'yı sağlamıyor.

### 5.2 Kalite/donanım dengesi: kurala göre **R0 (cosine)**

- **Kural:** en iyinin A6 uzaklığına 0,05 kadar yakın yöntemler arasında en düşük VRAM.
- R0'ın seçim kümesindeki uzaklığı 0,008 (seçim kümesinde 1 vaka farkı) ve VRAM'i sıfır. Kural metni uygulandığında R0 seçiliyor.

**Değerlendirme:**
- 0,05 tolerans, 19 ve 22 vakalık seçim kümesinde yaklaşık 1 vakaya denk geliyor. Bu kadar küçük kümede ayırt edici değil.
- R0 bütün dev'de A6'yı sağlamıyor: 0,933 / 0,167, 2 yanlış kabul.

**Keşif (ön kayıtlı değil):** aynı kural yalnız reranker'lara uygulanırsa sonuç **R2 ModernBERT-TR**:
- seçim kümesi uzaklığı 0,008;
- 727 MiB VRAM, 0,56 sn uçtan uca;
- bütün dev'de 1,000 / 0,067, teyitte 1,000 / 0,000;
- 150M parametre, Türkçeye özgü.

Proje sahibinin ek kararındaki "ModernBERT-TR benzer sonuç verirse düşük kaynak nedeniyle tercih edilebilir" ölçütüne uyuyor. Kesin seçim proje sahibinindir.

### 5.3 En başarılı ek reranker gerektirmeyen yöntem: **R0 (cosine)**; tamamen modelsiz: **B1 (BM25)** (düzeltme §10)

- Kabul/ret kararında R0+, R1− ve R1 ile eşit; sıralamada tamamlamalı R1 daha iyi.
- BM25 (B1, B1k), hibrit (B2) ve yapılandırılmış erişim (B3) cosine'den belirgin zayıf. Yanlış kabulleri 4–8; tümde A6 uzaklığı 0,10–0,22.
- B1k (5 karakter kırpma) sıralamada güçlü (Recall@5 1,000, MRR 0,978), ama BM25 skoru kabul/ret sinyali olarak zayıf.

### 5.4 Birincil hipotez: cevaplanabilirlik talimatı (R5 ve R4)

| Ölçü | R4 varsayılan | R5 cevaplanabilirlik |
|---|---|---|
| Seçim: kaçınma / yanlış kaçınma / AUROC | 0,955 / 0,158 / 0,990 | 0,955 / **0,053** / **0,998** |
| Tüm: kaçınma / yanlış kaçınma / yanlış kabul | 0,933 / 0,167 / 2 | **1,000 / 0,133 / 0** |
| Teyit: kaçınma / yanlış kaçınma | 0,875 / 0,091 | 1,000 / 0,182 |
| Smoke: D > C | 6/8 | 8/8 |
| Seçimde değişen vakalar | — | C009 ve E004 (ikisi de cevaplanabilir) R5'te kabul edildi |

**Sonuç:** hipotez seçim ve bütün dev kümelerinde **destekleniyor.** Teyit kümesinde karışık: kaçınma arttı, yanlış kaçınma arttı.

**Keşif (log-oran):** P(yes) yerine log-oran kullanmak hiçbir ölçüyü değiştirmedi. 1,0'daki doyma bu veride sonuç etkilemiyor.

## 6. Güvenlik

- Dev'de güvenlik reddi ve kaynak çatışması vakası yok; bütün yöntemlerde 0. Retrieval sonucu `found` değilse yöntemler kararı aynen geçirir.
- **Modelsiz (BM25) ve hibrit yöntemler** yalnız `eligible_documents` ile aynı izinli kümenin, hash'i doğrulanmış parçalarını görür. Belge ya da onay izi çatışmasında ve sonuçta kural çatışmasında kabul yoktur. Onaysız ve fikstür belgeler dışarıda kalır (birim testler: `tests/test_rag_d5.py`).
- **Reranker'lar** yalnız retrieval'ın doğruladığı adayları sıralar; aday ekleyemez.
- **Enjeksiyon belgesi** smoke testlerinde hiçbir modelde hiçbir grupta en üste çıkmadı (0/40).
- Gerçek indeks, eşikler (`null`) ve onay kayıtları değişmedi.

## 7. Sınırlılıklar ve dikkat

- **Örneklem:** 60 dev vakası (30 cevaplanabilir, 30 kaynak dışı). Kaynak dışı soruların hepsini geliştirici yazdı.
  - Seçim kümesinde 1 vaka yaklaşık 0,05 oran değişimi demek.
  - Mükemmel AUROC ve A6 uzaklığı 0 genelleme kanıtı değildir.
  - **S4 bağımsız soruları ve insan incelemesi gerekli.**
- **Eşikler** her CV katında farklı. Hiçbir eşik kayda yazılmadı ve varsayılan yapılmadı.
- **Reranker kabulü cevap doğruluğu değildir.** Kabul edilen kaynağın soruyu gerçekten cevapladığı ancak insan onaylı `expected_claims` ile ölçülebilir. Çelişkili belgeler yüksek skor aldı (§3).
- Bu deneyde **Gemma kullanılmadı.** Uçtan uca mimari (Gemma'lı ya da Gemma'sız ayıklayıcı cevap) ayrı karar ve deney gerektirir.

## 8. Testler

**Yeni testler:** `tests/test_rag_d5.py` (14 test):
- skor yönü ve `calibrate()` eşdeğerliği;
- eşik sınır değeri;
- grup CV sızıntısızlığı;
- AUROC;
- Türkçe normalleştirme;
- BM25, RRF;
- güvenlik kapısı.

**Regresyon:**
- **Ana proje varsayılan paketi:** **1402 geçti** (önceki 1388 + 14 yeni), 522 seçim dışı (PostgreSQL/Docker 510 + gerçek model 12), 0 başarısız.
  - Altın özetler ve özgün paket sağlama özeti geçti.
  - Aşama 1–6 kodu değişmedi.
- **Gerçek model entegrasyonu:** 11 geçti, 1 bilinen xfail.
- **Son durum:** Ollama'da yüklü model yok; VRAM 0 MiB.

## 9. Proje sahibinin kararını bekleyenler

1. D5 sonucuna göre hangi kabul/ret kapısının (LAMAR, ModernBERT-TR, Qwen3-cevap ya da cosine) sonraki mimari deneye alınacağı.
2. Gemma'lı ve Gemma'sız kaynaklı cevap mimarilerinin karşılaştırılması.
3. İnsan incelemesi (gold, `expected_claims`, E002, E011 ve kaynak dışı teyitleri) ve S4 hazırlayıcısı.
4. D: üzerindeki D5 ortamının (13 GB) korunması ya da silinmesi.

## 10. Düzeltmeler (proje sahibinin incelemesinden sonra, 2026-11-24)

1. **Adlandırma:**
   - Cosine yöntemleri (R0, R0+, R1−, R1) ve hibrit B2 **"modelsiz" değildir.** Ek reranker gerektirmezler ama **BGE-M3 embedding modeli** gerektirirler.
   - Yalnız BM25 tabanlı B1, B1k ve B3 tamamen modelsizdir.
   - Ön kayıttaki "en başarılı modelsiz yöntem" kategorisi ikiye ayrıldı; kurallar ve veriler değişmedi:
     - **En başarılı ek reranker gerektirmeyen yöntem: R0** (seçim A6 uzaklığı 0,008; tümde 0,033; 2 yanlış kabul).
     - **En başarılı tamamen modelsiz yöntem: B1 BM25** (seçim 0,094; tümde 0,117; 5 yanlış kabul).
   - `docs/veri/asama8_d5_sonuclar.json` aynı skorlarla yeniden üretildi; yalnız kategori adları değişti.
2. **Embedding maliyeti:** reranker'lı yöntemlerin donanım maliyetine BGE-M3 embedding (Ollama) maliyeti de eklenmelidir. Ölçüm, mimari karşılaştırmasında gerçek uçtan uca koşuda yapıldı: `ASAMA_8_D5_MIMARI_KARSILASTIRMA_RAPORU.md` §6.
3. **Ham skorlar:** bütün tekrarların ham skorları ve smoke sonuçları SHA-256 özetleri ve yeniden üretim bilgisiyle `docs/veri/asama8_d5_ham/` altında (yaklaşık 1,6 MB). Yalnız tekrar 1'i içeren eski `asama8_d5_skorlar_t1.json` bunun yerine kaldırıldı.
4. **Teyit kümesi:** bağımsız değildir. LAMAR-600m'nin teyit kümesindeki **%18,2 yanlış kaçınması** (11 cevaplanabilir vakada 2) A6 hedefinin (≤ %15) üstündedir; kayıtta korunur.
