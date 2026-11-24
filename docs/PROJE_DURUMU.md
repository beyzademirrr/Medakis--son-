# MedAkis proje durumu (bütünleşik ürün sürümü, 25.11.2026)

Bu belge, koddaki **gerçekten çalışan** bileşenleri ve henüz uygulanmamış bileşenleri ayırır. Çalışmayan
özellik çalışıyormuş gibi gösterilmez. Arayüzde aşama numarası kullanılmaz; bu tablo yalnız teknik izlenebilirlik içindir.

**Kategoriler:** **Çalışıyor ve doğrulandı** (otomatik test + arayüz/komut) · **Çalışan prototip** (çalışır, sınırlı) ·
**Geliştiriliyor — Prototip** (kısmen var, normal kullanıcıya kapalı ya da yalnız etiketli) · **Henüz uygulanmadı**.

## 12 aşama

| Aşama | Kapsam | Durum |
|---|---|---|
| 1 | Klasör yapısı, kilitli bağımlılıklar, Türkçe Streamlit arayüzü | **Çalışıyor ve doğrulandı** |
| 2 | Şema (34 tablo), iş kuralları, deterministik sentetik veri (üretici 2.1) | **Çalışıyor ve doğrulandı**. PostgreSQL ile çalışan testler bu sürümde koşulmadı (sunucu yok) |
| 3 | Görev, kapasite, gözlem hesapları | **Çalışıyor ve doğrulandı** |
| 4 | İş yükü tahmini: baseline, Random Forest, 30/60 dk, zamansal değerlendirme | **Çalışıyor ve doğrulandı** |
| 5 | CP-SAT personel optimizasyonu ve bağımsız doğrulayıcı | **Çalışıyor ve doğrulandı** |
| 6 | Yönetici onayı, plan SHA-256, güncellik, özet zincirli denetim izi, dışa aktarım | **Çalışıyor ve doğrulandı** |
| 7 | Kontrollü belge corpus'u, BGE-M3 indeks, kaynak doğrulamalı retrieval | **Çalışan prototip**. Arayüzde BM25 kaynaklı arama; gömme tabanlı hat geliştirme kipinde |
| 8 | Yerel kaynaklı cevap (Gemma 3 4B), reranker kapıları (LAMAR, ModernBERT-TR), donanım profilleri | **Geliştiriliyor — Prototip.** Deneyler tamamlandı; **A6 kabul kriteri KARŞILANMADI**. Normal RAG kapalı |
| 9 | Dijital Hastane İkizi: ortak veri sağlayıcı, merkezi saat, senaryolar, what-if, onaylı etkinin simülasyonu | **Çalışan prototip** |
| 10 | Sentetik kamera, yoğunluk, uzamsal ve zamansal ısı haritaları, yoğunluğa duyarlı CP-SAT | **Çalışan prototip** (sentetik tespit; gerçek kamera yok) |
| 11 | Araç tabanlı AI Agent, otomatik taslak, talepler, izleme; LangGraph/MCP | Agent ve talepler **çalışan prototip**; LangGraph ve MCP **Geliştiriliyor — Prototip** (paket kurulu değil, MCP çağrısı/kaydı yok) |
| 12 | Telegram bildirimi, n8n, bütünleşik denetim/açıklanabilirlik, README ve dokümantasyon | Telegram **çalışan prototip (dry-run doğrulandı; canlı gönderim test edilmedi)**; n8n **Geliştiriliyor**; denetim ve README **tamamlandı**. Kör test ve bağımsız RAG kabulü **henüz uygulanmadı** |

## Bileşen ayrıntıları

| Bileşen | Durum | Not |
|---|---|---|
| Ortak veri sağlayıcı | Çalışan prototip | Varsayılan **Sentetik Demo** (veritabanı gerekmez). Doğrulanmış PostgreSQL şema ve depoları korunur; bütünleşik arayüz bu sürümde veritabanlı kipe bağlanmadı |
| Dijital ikiz saati | Çalışan prototip | 104 değerlendirilmiş karar anı × 60 dk pencere; oynat/durdur/hız; bütün ekranlar aynı saat |
| Sentetik kamera | Çalışan prototip | Koddan üretilen kare, bağlı bileşen sayımı (0–48 figürde tespit = simülasyon, testli). Gerçek insan tespiti değildir |
| Isı haritaları | Çalışıyor ve doğrulandı | Uzamsal: kamera doluluğu; zamansal: görev dakikası / etkin kapasite. İki ölçü ayrı |
| Tahmin | Çalışıyor ve doğrulandı | Mevcut RF + U1 tampon; 30 dk satırları yalnız gösterim, optimizasyon 60 dk kullanır |
| Yoğunluğa duyarlı CP-SAT | Çalışan prototip | Mevcut Katman A; kısıt gevşetilmez; kamera sinyali etiketli ek talep; doğrulayıcı her sonuçta çalışır |
| What-if | Çalışan prototip | Taşıma, izin, ek personel, yoğunluk; rol/yetkinlik/kapsam/asgari kişi/kaynak açığı kontrolleri |
| AI Agent | Çalışan prototip | 15 araç (13 salt okunur, 2 yalnız taslak); onay/atama/bildirim aracı yok; araç süreleri gerçek ölçüm. Gemma 3 4B isteğe bağlı ve sayı denetimli; bu makinede doğrulama sırasında Ollama yanıt vermedi, agent deterministik kipte doğrulandı |
| Kaynaklı arama | Çalışan prototip | BM25; belge, sürüm, skor, belgede doğrulanmış alıntı; bulunamazsa ret. Dil modeli cevabı değildir |
| İnsan onayı | Çalışıyor ve doğrulandı | Agent oturumu yalnız kayda alabilir; onay yönetici oturumunda; özet ve güncellik kontrolü |
| Telegram | Çalışan prototip | Varsayılan dry-run; yalnız ortam değişkenleri; onaysız/özeti uyuşmayan/mükerrer bildirim engelli. Daha önce paylaşılan token kullanılmadı; canlı gönderim test edilmedi |
| Talepler ve izleme | Çalışan prototip | Ek personel, geçici görevlendirme, alternatif talepleri; metrikler yalnız oturum izinden |
| Denetim ve açıklanabilirlik | Çalışıyor ve doğrulandı | Operasyon, onay, bildirim ve talep izleri özet zincirli; kullanılan veri, kurallar, fayda/risk, elenen adaylar, kaynak belgeler |

## Doğrulama

- `tests/test_urun_prototipi.py` (24 test, bildirim testleriyle birlikte) geçti; tam test paketi sonucu aşağıdaki "Test sonuçları" bölümündedir.
- Gerçek tarayıcıda: Kontrol Merkezi → Dijital İkiz "Acil kritik yoğunluk (+%150)" → Acil 25/22 Kritik, ek sinyal 18 dk →
  Kamera ekranı (kutular + ısı) → Optimizasyon (OPTIMAL, doğrulayıcı geçti, 3 taşıma) → onaya gönderildi → Onaylandı
  ("Plan hâlâ geçerli") → Emirler ve Bildirimler dry-run ("ağ isteği yapılmadı") → Agent otomatik taslağı (aynı plan
  kayıtlı olduğu için ikinci kez kaydedilmedi) → zamansal ısı haritası.
- Çalıştırılmayan: PostgreSQL entegrasyon testleri, canlı Telegram gönderimi, Gemma ile agent yeniden ifadesi, LangGraph/MCP.

## Test sonuçları (25.11.2026)

- Tam varsayılan paket: 1438 geçti, 2 başarısız (arayüzün RAG paketini doğrudan içe aktarması ve şartname .md/.docx
  eşleşmesi); ikisi düzeltildi ve ilgili 113 test ile ürün testleri (24) yeniden çalıştırılıp geçti. 522 test işaretle
  dışarıda bırakılır (PostgreSQL ve entegrasyon testleri; bu makinede sunucu yok).
- Tarayıcı: 15 ekranın hepsi hatasız açıldı; kritik senaryo → CP-SAT → onay → dry-run bildirim → mükerrer engeli →
  agent → denetim zinciri → ikizde simüle etki → saat oynatma uçtan uca denendi.

## 2 Aralık'a kadar kalan işler

1. LangGraph orkestrasyonu ve salt okunur/taslak MCP araç sunucusu (paket kurulumu proje sahibinin onayıyla).
2. Ollama açıkken Gemma 3 4B yeniden ifade akışının uçtan uca doğrulanması.
3. Bütünleşik arayüzün doğrulanmış PostgreSQL kipine bağlanması (veri sağlayıcı seçimi).
4. Yeni token ile tek demo sohbetine canlı Telegram testi; n8n akışı.
5. Kaynak doğrulamalı RAG'in bağımsız soru setiyle kabulü (A6) ve kör test.
