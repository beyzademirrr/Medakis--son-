# S4: Bağımsız kaynak dışı dev soruları yönergesi

- **Amaç:** Kaçınma eşiği ve kanıt denetimi yöntemlerini, geliştiricinin (benim) yazmadığı sorularla sınamak. Mevcut 30 kaynak dışı dev sorusunu aynı kişi yazdı; bu, sonuçlara yazar yanlılığı katıyor.
- **Durum:** Yönerge ve **boş** şablon hazır. **Hiçbir soru yazılmadı.** Bu sorular yazılıp bağımsız olarak kontrol edilene kadar bağımsız değerlendirme yapılmış sayılmaz.
- **Bu set kör test değildir.** Dev verisidir: eşik ve yöntem seçiminde kullanılacak. Aşama 9 kör testi ayrı ve Git dışıdır (`ASAMA_9_KOR_TEST_PROTOKOLU_ONERISI.md`).

## 1. Kim hazırlar?

| Rol | Görev |
|---|---|
| Hazırlayan | Proje sahibi ya da geliştirmeye katılmayan biri. Geliştirici (ben) hazırlamaz, öneri sunmaz, örnek vermez |
| Kontrol eden | Hazırlayandan farklı biri. Her sorunun cevabının 13 onaylı belgede gerçekten olmadığını doğrular |

## 2. Hazırlamadan önce

- Yalnız 13 onaylı güncel belgeyi okuyun (`corpus/kontrollu/`, `SYN-FKS-*` ve `SYN-YET-GECERLILIK-1.0` hariç). Alana yakın soru yazmak için belgeleri bilmek gerekir.
- `evaluation/asama7/muhurlu_test.jsonl` dosyasına (ayrılmış küme) **bakmayın**. Mevcut dev sorularını kopyalamayın; doğrulama komutu birebir aynı soruyu reddeder.

## 3. Soru yazma kuralları

1. **En az 30 soru**, Türkçe, tek cümle, 10–500 karakter.
2. **Cevabı 13 belgede olmamalı.** Belgede kısmen geçen konular (ör. aynı terim) olabilir, ama sorulan bilginin kendisi bulunmamalı.
3. **Dağılım önerisi:** en az 15 **alana yakın** soru (belgelerdeki terimleri kullanır: servis, vardiya, hemşire, yetkinlik, onay, tahmin, rezerv vb.) ve en fazla 15 **alana uzak** soru (hastane işletmesi, klinik bilgi, kişisel konular).
4. **Biçim çeşitliliği:** "kaç", "nedir", "nasıl", "kim", "hangi", "mümkün mü" gibi farklı soru biçimleri kullanın. Aynı kalıbı (ör. hep "kaç …") tekrar etmeyin; bu, önceki setteki yanlılıktır.
5. **Yasaklar:** gerçek kişi, hastane, hasta ya da personel bilgisi; telefon, e-posta, kimlik numarası; klinik doz ya da tanı önerisi isteyen ve gerçek hasta bağlamı taşıyan soru.
6. Her soru için **neden kaynak dışı** olduğunu bir cümleyle yazın (ör. "belgeler ölçü adını veriyor ama sayısal değer yok").

## 4. Şablon ve alanlar

Dosya: `evaluation/asama7/bagimsiz_kaynak_disi_sablon.jsonl` (30 boş satır). Kopyalayıp doldurun (ör. `bagimsiz_kaynak_disi.jsonl`); şablonu değiştirmeyin.

| Alan | Doldurma |
|---|---|
| `case_key` | B001… olduğu gibi kalır; 30'dan fazla soru için B031… ekleyin |
| `question` | Soru metni |
| `yakinlik` | `alana_yakin` ya da `alana_uzak` |
| `neden_kaynak_disi` | Tek cümle gerekçe |
| `hazirlayan_rolu` | Ör. `proje_sahibi`, `bagimsiz_degerlendirici_1` (ad yazmayın) |
| `kontrol_eden_rolu` | Hazırlayandan farklı rol |
| `etiket_durumu` | Kontrol bitince `bagimsiz_kontrol_edildi` |
| Diğerleri | Değiştirmeyin (`answerable: false`, `gold: []`, `split: dev`, `as_of`) |

## 5. Doğrulama ve teslim

```powershell
.\.venv\Scripts\python.exe scripts\rag_bagimsiz_sorular_dogrula.py evaluation\asama7\bagimsiz_kaynak_disi.jsonl
```

- Komut yalnız sayıları ve sorun kodlarını yazar; soru metinlerini yazmaz.
- Geçtikten sonra dosyanın dev kümesine eklenmesi ve yeniden ölçüm **ayrı karar** gerektirir.
- Yeniden ölçümde eşik yalnız dev verisiyle seçilir; grup bazlı CV raporlanır.
