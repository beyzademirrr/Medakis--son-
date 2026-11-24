# Aşama 7 koşullu kapanış raporu

- Dal: `feature/asama-7-rag-retrieval`. Tarih: 2026-11-24.
- **Karar (proje sahibi, KM30):**
  - Aşama 7 retrieval altyapısı **koşullu olarak kapatıldı**.
  - **A6 kabul kriteri KARŞILANMADI** ve başarılı gösterilmez; çözümü sonraki bir iyileştirme çalışmasına ertelendi.
  - BGE-M3 geçici embedding adayı olarak kalır.
  - Mevcut indeksler ve onay kayıtları korunur.
  - Aşama 8'e geçişe izin verildi.
- **Koşul:** A6 çözülene kadar normal kullanıcıya RAG sorgusu ve cevap üretimi **kapalıdır**. Eşik yazılmadı; gerçek sorgu `unavailable / esik_kalibre_edilmedi` döner.

## 1. Kapanan kapsam (retrieval altyapısı)

| Kriter | Sonuç |
|---|---|
| A1 metadata, sürüm, sınır anı, `draft`/`retired`, hash, yarım yazım, eski vektör, sıfır kaynakta gömme yok | Karşılandı |
| A2 güvenlik vakaları | 13/13 (bge-m3 ve qwen3) |
| A3 idempotency | Karşılandı |
| A7 sıcak retrieval p95 ≤ 5 sn | Karşılandı: bge 310 ms, qwen 273 ms. Soğuk sorgu 3,6–4,6 sn |
| A9 ağ ve depolama | Karşılandı: yalnız loopback; C:'ye yazım yok; varsayılan embedding çalışmıyor |
| A4 Recall@5 / A5 MRR@10 | **Yalnız dev göstergesi:** bge v1 0,967 / 0,923; bge `parcalama_v2` 0,983 / 0,957. Kör testle doğrulanmadı |

## 2. Açık kalan: A6 güvenli kaçınma (KARŞILANMADI)

**Hedef:** kaçınma ≥ 0,95 ve yanlış kaçınma ≤ 0,15.

**Mevcut değerler (60 dev vakası, grup bazlı 5 katlı CV):**

| Yöntem | bge-m3 | qwen3 |
|---|---|---|
| Yalnız cosine eşiği | 0,933 / 0,167 | 0,867 / 0,100 |
| Cosine + kanıt kapsaması | 0,933 / 0,233 | 0,867 / 0,133 |
| Cosine + cevap türü uyumu | 0,933 / 0,167 | 0,933 / 0,133 |
| `parcalama_v2` + cosine | 0,933 / 0,167 | 0,867 / 0,133 |

- **Kök neden:** Bi-encoder benzerliği konu yakınlığını ölçer, cevaplanabilirliği ölçmez. Alana yakın kaynak dışı sorular cevaplanabilir sorulardan daha yakın düşebiliyor.
- **Kayıtlar:** `docs/veri/asama7_dev_degerlendirme_v2.json`, `asama7_kanit_denetimi_analizi.json`, `asama7_s3_giris_deneyi.json`.

## 3. Ertelenen işler

1. **Bağımsız kaynak dışı sorular (S4):** yönerge, boş şablon ve doğrulayıcı hazır. **Sorular yazılmadı; bağımsız inceleme yapılmadı.** Mevcut kaynak dışı soruların hepsini geliştirici yazdı (yazar yanlılığı).
2. **Dev setinin insan incelemesi ve dondurulması:** tartışmalı vakalar `ASAMA_7_KARAR_RAPORU.md` §2'de. **Set dondurulmadı.**
3. **Kör test (Aşama 9):** Git dışı protokol önerildi. Git'teki 56 vakalık küme kör test sayılmaz.
4. **`parcalama_v2`:** teknik olarak kabul edildi; deney deposunda duruyor. Gerçek indekse taşınması ayrı karar.
5. **Model ve eşik kesinleştirme:** BGE-M3 geçici. Eşik A6 çözülene kadar yazılmaz.

## 4. Sonraki iyileştirme seçenekleri

| # | Seçenek | Durum |
|---|---|---|
| S1 | Gemma 3 4B ile alıntıya dayalı, fail-closed kanıt denetimi | Aşama 8'de **yalnız geliştirme/test kipinde** uygulanacak; dev ve grup CV ile kanıtlanmadan kabul edilmez |
| S2 | Çapraz kodlayıcı (reranker) | İndirme ve paket kurulumu için ayrı izin gerekir |
| S4 | Bağımsız kaynak dışı sorular | Proje sahibinin hazırlayıcı belirlemesi bekleniyor |
| — | Dev setinin genişletilmesi ve dondurulması | İnsan incelemesi bekliyor |

## 5. Korunanlar (doğrulandı)

- **Onaylar:** 13 belge onayı, 13 olay, zincir geçerli, içerik özetleri aynı.
- **Gerçek indeksler:** bge `8d505c0775bf…` ve qwen `679b1f6d69cd…` hazır; eşik yok.
- **Deney indeksleri (`parcalama_v2`):** bge `3183f148adca…`, qwen `3d22667eb391…`.
- **Testler ve kod:** 1222 test geçti. Altın özetler aynı; Aşama 1–6 kodu değişmedi.
