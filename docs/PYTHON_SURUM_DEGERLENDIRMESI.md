# Python sürümü değerlendirmesi (FAZ 1.1 kapanışı)

Tarih: 4 Kasım 2026 · Karar: **Python 3.14.6 ile devam** (değişiklik yapılmadı)

## Bilgisayardaki durum

| Sürüm | Konum | Not |
|---|---|---|
| 3.14.6 | `py -3.14` | `.venv` bu sürümle kurulu |
| 3.10.11 | `python` (PATH) | Ekim 2026'da destek süresi bitti; kullanılmıyor |
| 3.13 | kurulu değil | — |

## Ölçülen kanıtlar (gerçekten çalıştırıldı)

1. **Kurulum dosyaları:** Kilitteki 108 paketin tamamı `--only-binary=:all:` ile hazır wheel olarak kuruldu; hiçbir paket kaynak koddan derlenmedi. `pip check`: "No broken requirements found".
2. **Paket meta verisi** (kurulu dağıtımlardan okundu):

| Paket | Sürüm | Wheel etiketi | Bildirilen Python sınıflandırması |
|---|---|---|---|
| streamlit | 1.66.1 | py3-none-any | 3.11–3.15 |
| pandas | 3.1.1 | cp314-win_amd64 | 3.11–3.15 |
| numpy | 2.5.4 | cp314-win_amd64 | 3.12–3.15 |
| scikit-learn | 1.9.1 | cp314-win_amd64 | 3.11–3.14 |
| ortools | 9.15.6755 | cp314-win_amd64 | 3.10–3.14 |
| scipy | 1.18.1 | cp314-win_amd64 | 3.12–3.15 |
| onnxruntime | 1.31.1 | cp314-win_amd64 | 3.11–3.14 |
| pyarrow | 26.0.1 | cp314-win_amd64 | 3.11–3.15 |
| chromadb | 1.5.9 | cp39-abi3-win_amd64 | sınıflandırma yok (abi3: 3.9+ tek wheel) |
| httpx | 0.28.1 | py3-none-any | 3.10–3.12 bildirir; saf Python, `>=3.8` |
| pytest | 9.2.0 | py3-none-any | 3.10–3.15 |

   Derlenmiş bileşen içeren ana paketlerin (pandas, numpy, scikit-learn, ortools, scipy, onnxruntime) 3.14'e özel resmi wheel'i var.

3. **Çalışma zamanı yoklaması** — `scripts/compat_probe.py` (ağsız):
   RandomForest eğitimi/tahmini, pandas 15→30 dk örnekleme, CP-SAT NoOverlap (`OPTIMAL`),
   bellek içi Chroma cosine sorgusu (açık vektörlerle, doğru en yakın sonuç). Hepsi hatasız.
4. **Test paketi:** pytest 3.14.6 üzerinde tam geçti (sayı FAZ_1_1_RAPORU.md kapanış bölümünde).
5. **Uyarı taraması** (`-W default`): tek uyarı chromadb'den geliyor —
   `asyncio.iscoroutinefunction` 3.14'te kullanımdan kalkmış, **3.16'da kaldırılacak**.
   Bugün işlevi etkilemiyor; yalnız Python 3.16'ya geçişte chromadb güncellemesi gerekir.

## Python 3.13 daha güvenilir olur muydu?

- **Lehine:** 3.13 ekosistemde bir yıl daha uzun süredir kullanılıyor; az bilinen paket
  hatalarına rastlama olasılığı biraz daha düşük. Aynı kilit dosyası 3.13 için de
  çözülüyor: `pip install --dry-run --python-version 3.13 --platform win_amd64
  --only-binary=:all: -r requirements.txt` → 108 paketin tamamı wheel ile çözüldü.
- **Aleyhine / gerek yok:** 3.13 bu bilgisayarda kurulu değil; geçiş yeni kurulum, yeni
  `.venv` ve testlerin yeniden yapılması demek. 3.14'te ölçülmüş bir sorun yok. Destek
  süresi 3.14'te daha uzun (3.13: ~Ekim 2029, 3.14: ~Ekim 2030).
- **Sonuç:** Şu an 3.13'e geçmek için ölçülmüş bir gerekçe yok; çalışan ortam bozulmadı.

## Açık risk ve geri dönüş planı

- En büyük belirsizlik **gerçek Chroma kalıcı depo + Ollama entegrasyonu** (Aşama 7-8);
  bu yoklama yalnız bellek içi Chroma'yı sınadı. Aşama 7'de entegrasyon testleri 3.14'te
  başarısız olursa geri dönüş: `py install 3.13` (veya python.org kurulum dosyası) →
  `py -3.13 -m venv .venv313` → aynı `requirements.txt` → testler. Kilit 3.13 için
  çözüldüğünden kod değişikliği beklenmez.
- `pyproject.toml` `requires-python = ">=3.12"` der; kilit yalnız 3.14.6 / Windows x64
  üzerinde test edilmiştir.
