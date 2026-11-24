# D5 öncesi model seçimi araştırması: reranker ve kanıt denetleyicisi

- Dal: `feature/asama-8-rag-generation`. Araştırma tarihi: 2026-11-24.
- **Bu bir masa başı araştırmadır.** Model indirilmedi, paket kurulmadı, kod değişmedi, ölçüm yapılmadı.
- **BGE-Reranker-v2-M3'ün öncelikli aday olması kararı geri çekildi.** Bu rapor seçimi kanıta dayandırmak içindir.
- **Proje sahibinin kararı bekleniyor.**
- A6 KARŞILANMADI; bu rapor hiçbir kabul hedefini değiştirmez.
- **İşaretler:**
  - **[D]** doğrulanmış (kaynakta okundu);
  - **[Ö]** üreticinin kendi beyanı (bağımsız değil);
  - **[H]** hesap ya da tahmin (bizim);
  - **[?]** doğrulanamadı.

## 1. Temel bulgular (özet)

1. **Türkçe reranking için yayımlanmış bir benchmark bulunamadı.**
   - TR-MTEB (EMNLP 2025 Findings) bir **embedding** benchmark'ıdır [D]. Cross-encoder reranker değerlendirdiğini doğrulayamadım; PDF okunamadı [?].
   - MIRACL'in 18 dili arasında **Türkçe yok** [D: Horizon-Labs kartı ve LAMAR makalesi].
   - Bu yüzden yaygın "çok dilli" reranker skorları Türkçe için doğrudan kanıt değildir.
2. **Türkçe içeren tek bağımsız karşılaştırma yanıltıcı olabilir.**
   - LAMAR makalesinin XQuAD-tr tablosu [D, ek tablo yalnız özetten okundu: [?]] **dil tutarlılığını** ölçer: aday kümesinde aynı metnin farklı dillerdeki çevirileri var; doğru cevap sorguyla aynı dildeki belgedir.
   - Bu test **Türkçe ilgililik kalitesini ölçmez.**
   - Qwen3-Reranker-4B'nin buradaki düşük skoru (Türkçe nDCG@1 43,03) yalnız Türkçe olan MedAkis corpus'u için büyük ölçüde ilgisizdir.
3. **"Cevaplanabilirlik" ile "ilgililik" farklı şeylerdir.**
   - Reranker'lar ilgililik sıralaması için eğitilir; skorları **kalibre edilmiş bir cevaplanabilirlik olasılığı değildir**.
   - Cevaplanabilirlik için bulduğum tek doğrudan kanıt İngilizce: SQuAD 2.0 ve NewsQA'da ince ayarlı encoder'lar sıfır-atışlı büyük modellerle yarışıyor [D: arXiv 2506.21288].
   - Türkçe cevaplanabilirlik sınıflandırıcısı ya da benchmark'ı bulunamadı.
4. **Bizim D1 ölçümümüz sıralamanın zaten iyi olduğunu gösteriyor.**
   - Recall@5 0,967–0,983; tamamlamayla Recall@10 1,0.
   - Reranker'dan beklenen asıl katkı **sıralama değil, ret ya da kabul sinyalidir.**
   - Bu, MedAkis'te ancak dev verisinde eşik kalibrasyonuyla ölçülebilir; literatürde karşılığı yok.
5. **Donanım ve çalışma ortamı kısıtları belirleyici:**
   - **Ollama'nın yerel rerank API'si yok** [D: açık issue #10989, #3368].
   - **GTX 1660 Ti'de fp16 NaN sorunu raporlanmış** [D: PyTorch #58123].
   - 4B ve üzeri modeller 6 GB VRAM'e ancak **kuantize GGUF ile llama.cpp üzerinden** sığar. Topluluk GGUF'larının çoğu bozuk dönüştürülmüş [D].

## 2. Aday reranker'lar

### 2.1 Temel bilgiler

| Model | Yayın | Parametre | Mimari / taban | Lisans | Türkçe eğitimde? | Bağlam |
|---|---|---|---|---|---|---|
| Qwen3-Reranker-0.6B | Haziran 2025 (makale 5 Haz 2025) [D] | 0,6B [D] | Qwen3 LLM, evet/hayır logit [D] | Apache-2.0 [D] | "100+ dil" [D]; Türkçe adıyla yok | 32K [D] |
| Qwen3-Reranker-4B | Haziran 2025 [D] | 4B [D] | Qwen3-4B-Base [D] | Apache-2.0 [D] | aynı | 32K [D] |
| Qwen3-Reranker-8B (referans) | Haziran 2025 [D] | 8B [D] | Qwen3 | Apache-2.0 [D] | aynı | 32K [D] |
| BGE-Reranker-v2-M3 | 2024 [D: ilgili makale Şub 2024; kart tarihi yok] | 568M [D] | bge-m3 (XLM-R) cross-encoder [D] | Apache-2.0 [D] | "Multilingual" [D]; MIRACL eğitim verisiyle eğitilmiş [D] | örnekte 512 [D] |
| Jina-Reranker-v2-Base-Multilingual | 2024 [?] | 278M [D] | cross-encoder | **CC-BY-NC-4.0** [D] | çok dilli [D] | [?] |
| Jina-Reranker-v3.5 | Tem–Ağu 2026 [D, kaynaklar arası çelişkili] | 0,6B [D] | listwise | **Ticari olmayan** [D] | çok dilli | 131K [D] |
| LAMAR-600m | Tem 2026 (arXiv 2607.22042) [D] | 0,6B [D] | bge-m3-retromae cross-encoder; Qwen3-Reranker-4B'den damıtma [D] | MIT [D, model kartı] | **Evet, `tr` açıkça listelenmiş** [D] | 8.192 [D] |
| Querit-Reranker-4B | Haz 2026 (arXiv 2606.19037) [D] | 4,02B [D] | Qwen3-Embedding-4B'den [D] | Apache-2.0 [D] | Listelenen 8 dilde Türkçe **yok** [D] | 128K [D] |
| Querit-Reranker (A0.4B, MoE) | Haz 2026 [D] | 4,92B toplam / 0,43B aktif [D] | şirket içi MoE, `trust_remote_code` [D] | Apache-2.0 [D] | Türkçe yok [D] | 128K [D] |
| Yuanbao-Reranker-0.6B (4B, 8B ailesi) | 2026 [D] | 0,6B [D] | Qwen3-0.6B [D] | Apache-2.0 [D] | "multilingual" [D]; Türkçe adıyla yok | 8.192 [D] |
| mxbai-rerank-large-v2 | 2025 (ProRank makalesi Haz 2025) [D] | yaklaşık 1,5B [D] | qwen2 mimarisi [D] | Apache-2.0 [D] | "100+ dil" [D] | [?] |
| gte-multilingual-reranker-base | 2024 (mGTE) [D] | yaklaşık 306M [?] | encoder | Apache-2.0 [D] | çok dilli [D] | [?] |
| llama-nemotron-rerank-1b-v2 | [?] | 1B | Llama 3.2 | NVIDIA Open Model License + Llama 3.2 [D] | 26 dil [?]; Türkçe doğrulanamadı | [?] |
| zerank-2 | 2025–2026 [?] | 4B [D] | Qwen3-4B [D] | **Çelişkili:** kartta CC-BY-NC-4.0, Apache PR'ı [D] | "çok dilli" iddiası; HF etiketi yalnız `en` [D] | 32K [D] |
| Horizon-Labs multilingual-reranker-base | v1.1: 2026-10-16 [D] | 308M [D] | mmBERT-base; Qwen3-Reranker-4B'den damıtma [D] | Apache-2.0 [D] | **Türkçe verisi yok** [D] | [?] |
| ytu-ce-cosmos/modernbert-tr-reranker | [?] (yıl belirtilmemiş) | yaklaşık 150M [D] | ModernBERT-TR; Qwen3-Reranker-8B'den damıtma [D] | Apache-2.0 [D] | **Yalnız Türkçe** [D] | 512 (değerlendirme) [D] |

### 2.2 Genel retrieval ve reranking başarısı (Türkçe değil)

| Model | Üretici beyanı [Ö] | Üçüncü taraf: LAMAR Tablo 4, MIRACL ort. nDCG@10 [D] | LAMAR Tablo 5, 5 MTEB reranking ort. [D] | Horizon kartı, ort. (MIRACL / Wiki / diğer) [Ö'ye yakın] |
|---|---|---|---|---|
| Qwen3-Reranker-0.6B | MMTEB-R 66,36 | 64,9 | 85,91 | 0,845 |
| Qwen3-Reranker-4B | MMTEB-R 72,74 | 68,9 | **87,34** (en yüksek) | **0,875** (en yüksek) |
| Qwen3-Reranker-8B | MMTEB-R 72,94 | — | — | — |
| BGE-Reranker-v2-M3 | (kart grafikleri okunamadı) | 69,1 (MIRACL'de eğitilmiş) | 86,31 | 0,866 (MIRACL'de eğitilmiş) |
| LAMAR-600m | — | 69,5 | 86,84 | — |
| jina-reranker-v3 | — | 68,6 | 86,46 | — |
| jina-v2-base-multi | — | 67,9 | 83,63 | — |
| gte-multi-reranker-base | — | 67,3 | 85,75 | — |
| llama-nemotron-rerank-1b-v2 | — | 69,2 | 86,82 | — |
| zerank-2 (4B) | — | 63,3 | 86,00 | — |
| Querit-Reranker-4B | MTEB Multilingual v2 reranking ort. 71,09 ("29 Ağu 2026 itibarıyla en iyi") | — | — | — |
| Yuanbao-Reranker-0.6B | 5 panel ort. 70,96 (Qwen3-0.6B 67,32; bge-v2-m3 57,33) | — | — | — |
| jina-reranker-v3.5 | MIRACL 74,11 (Qwen3-4B 76,56) | — | — | — |
| mxbai-rerank-large-v2 | BEIR 57,49; "Multilingual" 29,79 | — | — | — |

**Yorum [H]:**
- Bağımsıza en yakın iki kaynakta (LAMAR makalesi ve Horizon kartı) ilk sıradaki 6–8 modelin farkı **1–2 puan**.
- Bu fark, MIRACL'de eğitilmiş modellerin avantajı ve değerlendirme düzeni farkı içinde kalıyor.
- Üreticilerin kendi tabloları birbiriyle çelişiyor. Örneğin bge-v2-m3, Yuanbao tablosunda 57,33, LAMAR'da 86,31 (farklı panel ve aday havuzu).
- **Genel retrieval tablolarından kesin bir "en iyi model" çıkmıyor.** Yalnız Qwen3-Reranker-4B birden çok bağımsız tabloda tutarlı olarak en üstte ya da üst grupta.

### 2.3 Türkçe kanıtı

| Kaynak | Ne ölçüyor | Türkçe sonuç |
|---|---|---|
| LAMAR Tablo 11–13, XQuAD-tr [D tablo var; değerler ek özetten [?]] | **Dil tutarlılığı:** paralel çeviriler arasında sorguyla aynı dildeki belgeyi 1. sıraya koymak | nDCG@1: LAMAR 98,40; nemotron-1b 97,90; bge-v2-m3 96,56; jina-v3 90,17; gte 85,29; jina-v2 82,86; Qwen3-0.6B 76,98; zerank-2 60,84; Qwen3-4B 43,03 |
| ytu-ce-cosmos/modernbert-tr-reranker kartı [Ö] | Türkçe retrieval'ı yeniden sıralama, ilk 100 (kendi embedding'iyle), nDCG@10 | Ortalama +9,20 puan (TQuad 87,48 → 94,00; SquadTR 75,94 → 90,11). **Başka bir reranker'la karşılaştırma yok** |
| TR-MTEB [D varlığı; içerik [?]] | Türkçe embedding: 26 veri seti, reranking görevleri dahil | Cross-encoder reranker skoru bulunamadı |

**Sonuç:**
- **Türkçeye özgü, bağımsız bir reranker kalite karşılaştırması yok.**
- Türkçe kararı ancak MedAkis dev verisi, S4 soruları ve isteğe bağlı TR-MTEB'in Türkçe retrieval setleriyle (SquadTR, TQuad, XQuAD-tr) **kendi ölçümümüzle** verilebilir. Veri seti indirmesi ayrı izin gerektirir.

## 3. Cevaplanabilirlik ve kanıt denetimi alternatifleri

| Model | Görev | Boyut | Lisans | Türkçe | Kanıt |
|---|---|---|---|---|---|
| mDeBERTa-v3-base-xnli-multilingual-nli-2mil7 | NLI (gerektirme, nötr, çelişki) | 0,3B [D] | MIT [D] | **Eğitimde var; XNLI-tr doğruluk 0,792** [D] | İddia ve kanıt uyumu için uygun; soru ve parça cevaplanabilirliği için hipotez kalıbı gerekir, doğrulanmadı [H] |
| bge-m3-zeroshot-v2.0 | NLI tabanlı sıfır-atış sınıflandırma | 0,6B [D] | MIT taban; ticari olmayan veri içerir [D] | Belirtilmemiş [D] | Yalnız İngilizce ölçüm [D] |
| Granite Guardian 3.3-8B | Groundedness, bağlam ilgililiği, cevap ilgililiği | 8B [D] | Apache-2.0 [D] | **"Yalnız İngilizce eğitilip test edildi"** [D] | Uygun değil (dil ve boyut) |
| HHEM-2.1-Open | Olgusal tutarlılık | 0,1B [D] | Apache-2.0 [D] | İngilizce [D] | Uygun değil |
| LettuceDetect (mmBERT, EuroBERT, Qwen-2B) | Span düzeyinde halüsinasyon | 0,2–2B [D] | MIT [D] | Listelenen dillerde **yok** [D] | Uygun değil |
| İnce ayarlı encoder cevaplanabilirlik sınıflandırıcısı | Soru ve bağlam ⇒ cevaplanabilir mi | 0,1–0,4B | — | Türkçe hazır model bulunamadı | İngilizcede SQuAD 2.0 için %90,2 [D]. Türkçe için eğitim verisi gerekir; 60 dev vakası yetmez [H] |

**Değerlendirme [H]:**
- A6'nın kalan hatası "parçalar soruyu cevaplıyor mu" kararı. Hazır Türkçe destekli bir answerability modeli yok.
- **NLI (mDeBERTa-xnli)** Türkçe kanıtı olan tek denetleyici. Ama asıl işi **iddia ile kanıt** uyumudur; bu da insan onaylı `expected_claims` olmadan ölçülemez. Bu yüzden D4'e (iddia denetimi) uygun, D5'in (cevaplanabilirlik ve sıralama) yerine geçmez.
- **Talimatla çalışan reranker'lar** (Qwen3, Querit, Yuanbao, zerank-2) "Belge soruyu cevaplıyor mu?" biçiminde bir talimatla cevaplanabilirliğe yaklaştırılabilir. Ama bu, **istem tasarımıdır**: dev verisinde seçilir, aşırı uyum riski taşır, kanıtlanmış değildir.

## 4. Donanım ve Windows uyumluluğu (GTX 1660 Ti 6 GB, 16 GB RAM)

**Ağırlık belleği [H: parametre × bayt; aktivasyon ve KV hariç]:**

| Model | fp32 | fp16 / bf16 | Q8 GGUF | Q4_K_M GGUF | 6 GB VRAM'e sığar mı? |
|---|---|---|---|---|---|
| 0,15–0,3B encoder'lar | 0,6–1,2 GB | 0,3–0,6 GB | — | — | Evet (CPU'da da çalışır) |
| bge-v2-m3, LAMAR (0,57–0,6B) | 2,3 GB | 1,1 GB | — | — | Evet, fp32 ile |
| Qwen3/Yuanbao-0.6B | 2,4 GB | 1,2 GB | yaklaşık 0,65 GB | yaklaşık 0,4 GB | Evet |
| mxbai-large-v2 (1,5B) | 6 GB | 3 GB | yaklaşık 1,6 GB | yaklaşık 1 GB | fp16 ya da GGUF ile |
| Qwen3/Querit-4B | 16 GB | 8 GB | yaklaşık 4,3 GB | yaklaşık 2,5 GB | **Yalnız GGUF**; Gemma boşaltılmışken (sıralı yükleme) |
| Qwen3-8B | 32 GB | 16 GB | yaklaşık 8,7 GB | yaklaşık 5 GB | **Pratikte hayır** (Q4 sınırda; kısmi CPU yükü yavaş) |
| Querit A0.4B MoE (toplam 4,92B) | — | yaklaşık 9,8 GB | [?] | [?] | Hayır (toplam ağırlık bellekte olmalı); özel kod |

**Doğrulanmış kısıtlar:**
- **Ollama:** yerel rerank uç noktası yok. Ollama kütüphanesindeki bazı Qwen3-Reranker yüklemelerinde sınıflandırma başı eksik; skorları anlamsız [D]. Mevcut Ollama altyapımızla reranker **çalıştırılamaz**.
- **llama.cpp (`llama-server --reranking --pooling rank`):** Qwen3-Reranker ve bge-reranker GGUF'larını sunabilir [D].
  - Topluluk Qwen3-Reranker GGUF'larının çoğu `cls.output.weight` ve `pooling_type=RANK` eksik dönüştürülmüş; **hata vermeden sabite yakın skor üretir** [D].
  - Özgün ağırlıklardan güncel `convert_hf_to_gguf.py` ile yeniden dönüştürme gerekir [D].
  - Yeni bir yürütülebilir dosya (Windows CUDA derlemesi) indirmeyi gerektirir.
- **PyTorch yolu (transformers ya da sentence-transformers):**
  - Turing (sm_75) güncel CUDA 13.x derlemelerinde destekleniyor; Python 3.14 PyTorch 2.15'te korunuyor [D].
  - Windows ve Python 3.14 için resmi CUDA wheel'ini **doğrudan doğrulayamadım** [?].
  - Kurulum büyük (PyTorch ve CUDA, birkaç GB). D: sürücüsü için `HF_HOME` ve pip önbelleği ayarı gerekir.
- **fp16 riski:** GTX 1660 / 1660 Ti'de yarı duyarlıkta NaN çıktısı raporlanmış [D]. Encoder'lar fp32 çalıştırılmalı (bge ve LAMAR için 2,3 GB, sorun değil). bf16 bu kartta donanımsal olarak yok [H].
- **Bellek:** Gemma (yaklaşık 3,7 GB VRAM, D3 ölçümü) ile 4B reranker birlikte sığmaz. 8.6'daki sıralı yükleme ve boşaltma düzeni zorunlu [H]. RAM tarafında Ollama süreçleri D3'te yaklaşık 8,5 GB gördü [D: bizim ölçüm].

## 5. Karar matrisi

**Puanlama [H]:** 1 = zayıf, 5 = güçlü; kanıt türü parantez içinde. Türkçe sütunu dil tutarlılığı testini **dışarıda bırakır**; o test MedAkis için ilgisiz.

| Model | Genel kalite (bağımsıza yakın) | Türkçe kanıtı | Talimatla cevaplanabilirliğe uyarlanabilirlik | Donanım uygunluğu (6 GB) | Windows ve çalışma yolu riski | Lisans | Olgunluk / bağımsız tekrar |
|---|---|---|---|---|---|---|---|
| Qwen3-Reranker-4B | **5** (birden çok tabloda en üstte) | 2 (yalnız "100+ dil") | **5** (talimat destekli) | 2 (yalnız Q4/Q8 GGUF) | 2 (GGUF dönüşüm tuzağı; llama.cpp) | 5 | 4 |
| Qwen3-Reranker-8B | 5 [Ö] | 2 | 5 | 1 | 1 | 5 | 4 |
| Qwen3-Reranker-0.6B | 3 | 2 | 4 | 4 (fp32 2,4 GB) | 3 | 5 | 4 |
| Querit-Reranker-4B | 5 [Ö], bağımsız tekrar yok | 1 (Türkçe listede yok) | 4 [?] | 2 | 2 | 5 | 2 |
| Yuanbao-Reranker-0.6B / 4B | 4 [Ö] | 2 | 4 | 4 / 2 | 3 | 5 | 2 |
| BGE-Reranker-v2-M3 | 4 (MIRACL avantajı var) | 2 | 2 (talimat yok) | **5** (fp32) | **4** (olgun; sentence-transformers) | 5 | **5** |
| LAMAR-600m | 4 (MIRACL 69,5; ort. 86,84) | **3** (Türkçe eğitimde; dil tutarlılığı güçlü) | 2 | **5** | 4 | 5 (MIT) | 2 (tek makale, yeni) |
| jina-v2-base-multi / v3.5 | 3 / 4 | 2 | 2 / 3 | 5 / 4 | 3 | **1 (ticari değil)** | 4 / 3 |
| mxbai-rerank-large-v2 | 3 ("Multilingual" 29,79) | 1 | 3 | 3 | 3 | 5 | 3 |
| gte-multi-reranker-base | 3 | 2 | 2 | 5 | 4 | 5 | 4 |
| llama-nemotron-rerank-1b-v2 | 4 | ? | 2 | 4 | 3 | 3 (NVIDIA lisansı) | 3 |
| zerank-2 (4B) | 3–4 | 1 | 4 | 2 | 2 | **2 (çelişkili)** | 2 |
| modernbert-tr-reranker | ? (karşılaştırma yok) | **4** (Türkçe eğitim ve ölçüm, kendi beyanı) | 1 | **5** (150M) | 4 | 5 | 1 |
| mDeBERTa-xnli-2mil7 (NLI; D4) | — | **4** (XNLI-tr 0,792) | 3 (iddia denetimi) | 5 | 4 | 5 | 4 |

## 6. Öneriler (karar sizde)

Seçim marka, parametre sayısı, popülerlik ya da embedding ailesi üzerinden yapılmadı. Türkçeye özgü reranker benchmark'ı olmadığı için **hiçbir öneri ölçümsüz kabul edilmemeli.** D5 ölçümü bu adaylar arasında karar verecek.

### 6.1 En güçlü kalite adayı: **Qwen3-Reranker-4B** (Q8_0 GGUF, llama.cpp; Gemma ile sıralı yükleme)

**Gerekçe:**
- Bağımsıza en yakın iki karşılaştırmada genel ortalaması en yüksek: LAMAR Tablo 5'te 87,34, Horizon kartında 0,875.
- Üreticinin MMTEB-R skoru 72,74; 8B'ye çok yakın (72,94). Yani 8B'nin kalite artısı küçük, donanım maliyeti büyük.
- **Talimat desteği**, A6'nın asıl sorunu olan "parça soruyu cevaplıyor mu" kararına uyarlanabilir.
- Apache-2.0 lisanslı.

**Riskler:**
- Türkçe kanıtı yok.
- Dil tutarlılığı testinde zayıf; Türkçe corpus için önemsiz [H].
- Kuantizasyonun reranker kalitesine etkisi ölçülmemiş [?].
- Ollama'da çalışmaz; llama.cpp ve doğru GGUF dönüşümü gerekir; dönüşüm hatası **sessizdir**. Kurulumdan sonra skorların sabite yakın olmadığı mutlaka doğrulanmalı.
- 4B'nin Q4_K_M'si (yaklaşık 2,5 GB) daha güvenli sığar; Q8_0 (yaklaşık 4,3 GB) kalite için tercih edilir, Gemma boşaltılmışken.

**Alternatif:** Querit-Reranker-4B. Bugün açık modeller arasında en yüksek MTEB Multilingual v2 reranking iddiasına sahip, ama Türkçe listesinde yok ve bağımsız tekrarı yok. İkinci kalite adayı olarak aynı ölçümde denenebilir.

### 6.2 En iyi kalite / donanım dengesi: **BGE-Reranker-v2-M3 ve LAMAR-600m birlikte ölçülsün**

İkisi aynı mimari ailede (XLM-R tabanlı cross-encoder). Aynı kurulumla, aynı bellekle (fp32 yaklaşık 2,3 GB) ek maliyet olmadan birlikte ölçülebilir.

**BGE-Reranker-v2-M3:**
- Bağımsız tablolarda Qwen3-4B'ye 1 puan yakın (86,31 / 0,866).
- En olgun araç desteği; fp32 ile 1660 Ti fp16 sorununu tamamen atlar; CPU'da da çalışır.
- Talimat desteği yok; MIRACL skorları eğitim verisi nedeniyle şişkin olabilir.

**LAMAR-600m:**
- Türkçe **açıkça eğitimde**; MIT lisanslı; genel ortalaması bge'ye eşit ya da biraz üstünde (86,84 / MIRACL 69,5).
- Çok yeni (Temmuz 2026); tek makale; bağımsız tekrar yok.

**Neden 0.6B Qwen3 değil?** Bağımsız tablolarda bge ve LAMAR'ın gerisinde (MIRACL 64,9; ort. 85,91 / 0,845). Ayrıca bf16 bu kartta yok, fp16 riskli; fp32'ye zorlanır.

### 6.3 En hafif alternatif: **ytu-ce-cosmos/modernbert-tr-reranker** (yaklaşık 150M); yedek: **gte-multilingual-reranker-base**

**modernbert-tr-reranker:**
- Bulunan **tek Türkçeye özel reranker**: Türkçe veriyle eğitilmiş, Qwen3-Reranker-8B'den damıtılmış, Apache-2.0.
- Türkçe retrieval setlerinde kendi beyanına göre ortalama +9,2 nDCG@10.
- CPU'da bile hızlı.
- **Riskler:** başka bir reranker'la karşılaştırması yok; tarih ve sürüm bilgisi eksik; tek ekip.
- Bu yüzden "hafif ve Türkçe" seçenek olarak **ölçülmeden** önerilemez.

**gte-multilingual-reranker-base:**
- Olgun, Apache-2.0, 0,3B; bağımsız tablolarda bge'nin 0,5–2 puan gerisinde.

### 6.4 Cevaplanabilirlik ve kanıt denetimi (D4) için

- **mDeBERTa-v3-base-xnli-multilingual-nli-2mil7** (0,3B, MIT, XNLI-tr 0,792) Türkçe kanıtı olan tek denetleyici.
- Ancak **insan onaylı `expected_claims`** gelmeden değerlendirilemez. D5'ten sonra ve insan incelemesinden sonra ele alınmalı.
- Granite Guardian, HHEM ve LettuceDetect Türkçe desteklemediği için önerilmiyor.

### 6.5 Önerilen D5 ölçüm düzeni (onaydan sonra; bu çalışmada yapılmadı)

- **Aynı aday parçalar** (D3 ile aynı), aynı seçim/teyit ayrımı, 3 tekrar.
- Adaylar: Qwen3-Reranker-4B (Q8_0 ve Q4_K_M ayrı), BGE-v2-M3, LAMAR-600m, isteğe bağlı modernbert-tr-reranker.
- **Ölçüler:**
  - sıralama (Recall, MRR);
  - **ret ve kabul sinyali olarak** grup bazlı CV ile kalibre edilmiş eşik;
  - M1 ile birlikte uçtan uca A6 ölçüleri;
  - VRAM, RAM ve gecikme.
- **Kurulum seçimi (ayrı onay):** encoder'lar için PyTorch ile sentence-transformers, ya da hepsi için llama.cpp (Windows CUDA derlemesi). Her ikisi de indirme ve D: depolama gerektirir.
- **İsteğe bağlı:** TR-MTEB Türkçe retrieval setleriyle dış Türkçe kontrolü (veri indirme onayı gerekir).

## 7. Kaynaklar

- **Qwen3-Reranker:**
  - Qwen3 Embedding makalesi: https://arxiv.org/pdf/2506.05176
  - Model kartı: https://huggingface.co/Qwen/Qwen3-Reranker-4B
  - Blog: https://qwenlm.github.io/blog/qwen3-embedding
- **BGE-Reranker-v2-M3:** https://huggingface.co/BAAI/bge-reranker-v2-m3
- **Jina:**
  - v2-base-multilingual: https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual/raw/main/README.md ve https://jina.ai/models/jina-reranker-v2-base-multilingual/
  - v3.5: https://jina.ai/models/jina-reranker-v3.5/ ve https://huggingface.co/jinaai/jina-reranker-v3.5
- **LAMAR:** https://arxiv.org/html/2607.22042 ve https://huggingface.co/nlpai-lab/LAMAR-600m
- **Querit-Reranker:** https://huggingface.co/papers/2606.19037, https://huggingface.co/Querit/Querit-4B ve https://huggingface.co/Querit/Querit
- **Yuanbao-Reranker:** https://huggingface.co/Yuanbao-Search/Yuanbao-Reranker-0.6B
- **mxbai-rerank-large-v2:** https://huggingface.co/mixedbread-ai/mxbai-rerank-large-v2
- **gte-multilingual-reranker-base:** https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base ve mGTE makalesi https://arxiv.org/html/2407.19669v2
- **llama-nemotron-rerank-1b-v2:** https://build.nvidia.com/nvidia/llama-nemotron-rerank-1b-v2/modelcard
- **zerank-2:** https://huggingface.co/zeroentropy/zerank-2
- **Horizon-Labs multilingual-reranker-base:** https://huggingface.co/Horizon-Labs/multilingual-reranker-base
- **Türkçe kaynaklar:**
  - modernbert-tr-reranker: https://huggingface.co/ytu-ce-cosmos/modernbert-tr-reranker
  - TR-MTEB: https://aclanthology.org/2025.findings-emnlp.471/ ve https://github.com/selmanbaysan/mteb_tr
- **NLI ve kanıt denetimi:**
  - mDeBERTa-xnli-2mil7: https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7
  - bge-m3-zeroshot-v2.0: https://huggingface.co/MoritzLaurer/bge-m3-zeroshot-v2.0
  - Granite Guardian 3.3-8B: https://huggingface.co/ibm-granite/granite-guardian-3.3-8b
  - HHEM-2.1-Open: https://huggingface.co/vectara/hallucination_evaluation_model
  - LettuceDetect: https://github.com/KRLabsOrg/LettuceDetect
  - Cevaplanabilirlik için küçük encoder'lar: https://arxiv.org/html/2506.21288v1
- **Çalışma ortamı:**
  - Ollama reranker desteği: https://github.com/ollama/ollama/issues/10989 ve https://github.com/ollama/ollama/issues/16076
  - Qwen3-Reranker GGUF dönüşüm sorunu: https://huggingface.co/cicaba/Qwen3-Reranker-0.6B-GGUF-llamacpp-fixed ve https://huggingface.co/ethicalabs/Qwen3-Reranker-8B-GGUF
  - Ollama yanına rerank sunucusu: https://github.com/rushlabs-ai/rerankd
  - PyTorch CUDA ve Turing desteği: https://dev-discuss.pytorch.org/t/notice-cuda-12-6-wheels-will-no-longer-be-published-from-pytorch-2-15-drops-maxwell-pascal-volta/3432
  - PyTorch Python 3.10 kaldırma notu: https://dev-discuss.pytorch.org/t/notice-python-3-10-support-is-being-removed-from-pytorch-2-15-2-14-is-the-last-release-with-3-10-wheels/3440
  - GTX 1660 fp16 NaN: https://github.com/pytorch/pytorch/issues/58123
