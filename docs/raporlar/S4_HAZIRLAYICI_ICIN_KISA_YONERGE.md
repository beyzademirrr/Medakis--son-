# MedAkis: kaynak dışı soru hazırlama (kısa yönerge)

Bu yönerge, soruları hazırlayacak kişiye iletilmek içindir. Ayrıntılı sürüm: `ASAMA_7_S4_BAGIMSIZ_SORU_YONERGESI.md`.

## Ne istiyoruz?

**En az 30 Türkçe soru.** Her sorunun cevabı aşağıdaki 13 belgede **bulunmamalı**. Sistemin bu sorularda "kaynakta bilgi yok" deyip demediğini ölçeceğiz.

Okunacak belgeler (`corpus/kontrollu/` klasörü):

`SYN-PRC-DEVIR`, `SYN-PRC-ONAY`, `SYN-PRC-DISA`, `SYN-ROL-HEMSIRE`, `SYN-ROL-SORUMLU`, `SYN-ROL-DESTEK`, `SYN-YET-GECERLILIK-2.0`, `SYN-YET-SERVIS`, `SYN-YET-ACIK`, `SYN-AKS-TAHMIN`, `SYN-AKS-KADRO`, `SYN-AKS-GOREV`, `SYN-AKS-KAYNAK`.

`SYN-FKS-*` ve `SYN-YET-GECERLILIK-1.0` dosyalarını dikkate almayın.

## Kurallar

1. **En az 15 soru alana yakın olsun.** Belgelerdeki sözcükleri kullansın (servis, vardiya, hemşire, yetkinlik, onay, tahmin, rezerv…), ama sorulan bilgi belgelerde olmasın. Örnek durum: belgede bir terim geçiyor ama sayısı, listesi ya da tarihi yok.
2. **En fazla 15 soru alana uzak olsun** (hastane işletmesi, genel sağlık, kişisel konular).
3. **Soru biçimlerini çeşitlendirin:** "kaç", "nedir", "nasıl", "kim", "hangi", "mümkün mü"… Hep aynı kalıbı kullanmayın.
4. Her soru tek cümle olsun, 10–500 karakter.
5. **Yazmayın:**
   - gerçek kişi, hastane ya da hasta bilgisi;
   - telefon, e-posta ya da kimlik numarası;
   - dosyalardaki mevcut soruların kopyası.
6. Her soru için **neden cevabının belgelerde olmadığını** tek cümleyle yazın.
7. `evaluation/asama7/muhurlu_test.jsonl` dosyasına bakmayın.
8. Soruları yazan ile kontrol eden **farklı kişiler** olsun. Kontrol eden kişi, her sorunun cevabının 13 belgede gerçekten olmadığını doğrular.

## Biçim

`evaluation/asama7/bagimsiz_kaynak_disi_sablon.jsonl` dosyasını kopyalayın (ör. `bagimsiz_kaynak_disi.jsonl`) ve her satırda yalnız şu alanları doldurun:

| Alan | Değer |
|---|---|
| `question` | Soru metni |
| `yakinlik` | `alana_yakin` ya da `alana_uzak` |
| `neden_kaynak_disi` | Tek cümle gerekçe |
| `hazirlayan_rolu` | Ör. `bagimsiz_hazirlayici_1` (ad yazmayın) |
| `kontrol_eden_rolu` | Hazırlayandan farklı rol, ör. `proje_sahibi` |
| `etiket_durumu` | Kontrol bitince `bagimsiz_kontrol_edildi` |

Diğer alanlara (`case_key`, `split`, `answerable`, `gold`, `as_of`, `paraphrase_group`) dokunmayın. 30'dan fazla soru için `B031`, `B032`… ile yeni satır ekleyin.

### Doldurulmuş satır biçimi (yalnız biçim örneği; soru metni yer tutucudur)

```json
{"answerable": false, "as_of": "2026-11-23T12:00:00Z", "case_key": "B001", "category": "no_source", "etiket_durumu": "bagimsiz_kontrol_edildi", "gold": [], "hazirlayan_rolu": "bagimsiz_hazirlayici_1", "kontrol_eden_rolu": "proje_sahibi", "neden_kaynak_disi": "<belgelerde neden bulunmadığını anlatan tek cümle>", "paraphrase_group": "B001", "question": "<ÖRNEK: buraya sizin sorunuz gelir>", "split": "dev", "yakinlik": "alana_yakin"}
```

Doğrulama aracı `<…>` yer tutucularını ve "ÖRNEK" sözcüğünü reddeder. Bu satır gerçek veri olarak kabul edilmez.

## Kontrol

```powershell
.\.venv\Scripts\python.exe scripts\rag_bagimsiz_sorular_dogrula.py evaluation\asama7\bagimsiz_kaynak_disi.jsonl
```

Araç yalnız sayıları ve sorun kodlarını gösterir, soruları göstermez. "gecti": true çıkınca dosyayı proje sahibine teslim edin. Dev kümesine ekleme ayrıca kararlaştırılır.
