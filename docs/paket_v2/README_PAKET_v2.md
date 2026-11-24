# FLOWCARE AI RAG aktarım paketi v2

Yapay zekâ destekli sağlık tesisi iş yükü tahmini ve personel kaynak optimizasyonu için geliştirme girdileri. Yalnız sentetik veri kullanan idari karar destek prototipi. Bu ZIP çalışan uygulama değildir.

## Dosyalar
- FLOWCARE_AI_Proje_Sartnamesi.docx: Türkçe ayrıntılı şartname.
- FLOWCARE_AI_Proje_Sartnamesi.md: aynı metnin Claude Code tarafından okunabilir kopyası.
- RAG_MIMARISI_VE_DEGERLENDIRME.md: RAG mimarisi, model/donanım kararı ve kabul planı.
- SEMA_DEGISIKLIKLERI.md: 23→34 tablo gerekçesi ve migration.
- migrations/001_to_002.sql: mevcut v1 verisini koruyan ekleme migrationı.
- corpus/: dört kontrollü sentetik belge taslağı.
- evaluation/: 12 soruluk mini smoke seti ve ağsız metrik doğrulayıcı.
- config/rag_profiles.json: başlangıç ve yedek çalışma profilleri.
- flowcare_schema.sql: 34 iş tablosu, ilişkiler, kontroller ve indeksler.
- CLAUDE.md: mimari, veri, tahmin, optimizasyon ve aşama kuralları.
- BASLANGIC_TALIMATLARI.md: ilk mesaj ve kullanım sırası.
- KAPSAM_VE_KARARLAR.md: geçmiş kararları ve yeniden tasarlanan ayrıntılar.
- verify_schema.py: yalnız Python standart kütüphanesiyle tekrarlanabilir SQL testi.
- DOGRULAMA_RAPORU.json: teslim anındaki gerçek kontroller.
- SHA256SUMS.txt: diğer dosyaların bütünlük değerleri.

## Hemen yapılabilecek doğrulama
Python 3 kurulu ortamda, bu klasörde:
```powershell
python verify_schema.py
```
Araç bellek içi geçici SQLite veritabanı kullanır; dosyalarınıza dokunmaz. Tam 34 tablo, bütünlük, FK, başarılı ilişkili kayıtlar ve reddedilmesi gereken örnekleri sınar.

Boş geliştirme veritabanı oluşturmak için SQLite CLI kuruluysa:
```text
sqlite3 flowcare.db
.read flowcare_schema.sql
PRAGMA foreign_keys;
PRAGMA integrity_check;
PRAGMA foreign_key_check;
.quit
```
İlk sorgu 1, ikinci ok, üçüncü sıfır satır vermelidir. SQL yalnız yeni boş veritabanına uygulanır. Mevcut veritabanını silmeyin; ikinci uygulama tablo var hatası verir. Her yeni bağlantı FK'yi ayrıca etkinleştirmelidir. İş kurallarının tamamı SQL'de değildir; CLAUDE.md'deki uygulama doğrulamaları zorunludur.

## Claude Code ile geliştirme
BASLANGIC_TALIMATLARI.md içindeki mesajı kullanın. Aşama 1 tamamlandığında Claude Code aşağıdaki hedefleri gerçek dosyalarla sağlamalıdır. Bunlar bu pakette henüz bulunmayan uygulama dosyalarına ait hedef komutlardır:
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
.\.venv\Scripts\python.exe -m pytest
```
macOS/Linux karşılığı `.venv/bin/python` olur. requirements.txt ilk aşamada uyumlu sürümlerle oluşturulup kilitlenecektir. Hedef bağımlılıklar: streamlit, pandas, numpy, scikit-learn, ortools, pytest; sqlite3 Python ile gelir. DOCX üretme kütüphanesi uygulamanın çalışması için gerekli değildir.

## Hedef klasörler
```text
app.py
src/flowcare/{ui,domain,services,repositories,synthetic,forecasting,optimization,approvals}/
src/flowcare/rag/{ingestion,chunking,embeddings,vector_store,retrieval,generation,citations,evaluation,contracts}/
tests/
data/             # git dışında, yalnız sentetik
artifacts/        # git dışında, model ve çalışma çıktıları
```
Veritabanı yolu ayarlardan alınmalı, varsayılan data/flowcare.db olmalıdır. Dosya yolu hataları sessiz boş veritabanı açılmasına yol açmamalıdır. CSV dışa aktarımı yalnız sentetik/toplu sonuç içermelidir. Kimlik doğrulama ve çok kullanıcılı üretim dağıtımı bu prototipin kapsamı dışındadır; varsayılan çalışma yerel olmalıdır.

## Doğrulama sınırı
Teslimde test edilen SQL ve dosya paketidir. Eğitilmiş model, CP-SAT uygulaması veya klinik doğruluk test edilmiş değildir. DOCX görsel kontrolünün durumu teslim raporunda açıklanır. Kapsamın geçmişe uygunluğu için KAPSAM_VE_KARARLAR.md okunmalıdır.

## RAG yerel kurulum hedefi
İlk ortam çözümlemesinde uygulama bağımlılıklarına chromadb ve HTTP/Ollama istemcisi eklenir, sürümler kilitlenir. Kullanıcı bilgisayarına otomatik kurulum veya model indirme bu paket oluşturulurken yapılmadı. Yerel Ollama kurulduktan sonra model geliştirme aşamasında sırayla:
```powershell
ollama pull qwen3-embedding:0.6b
ollama pull bge-m3
ollama pull gemma3:4b
ollama list
ollama ps
```
Model dosyası boyutu GPU bellek ihtiyacı değildir. Önce embedding, ardından boşaltma ve üretim kullanılır; karşılaştırma modelleri ayrı koşar. API örnekleri güncel Ollama sürümünde doğrulanmalı ve digest kaydedilmelidir.

Windows'ta CPU deneme profili: önce açık Ollama tepsi uygulamasını kapat, sonra ayrı PowerShell'de aşağıdaki sunucuyu başlat. İkinci sunucuyu aynı porta başlatma. Bu değişkenler yalnız o süreç/alt süreç için ayarlanır.
```powershell
$env:CUDA_VISIBLE_DEVICES='-1'
$env:OLLAMA_MAX_LOADED_MODELS='1'
$env:OLLAMA_NUM_PARALLEL='1'
ollama serve
```
GPU kipine dönmek için bu sunucuyu durdurup yeni oturumda GPU görünürlüğü değişkeni olmadan başlat. ollama ps ile gerçek yerleşimi kontrol et. Hafif alternatifler ihtiyaç halinde ayrıca indirilir: paraphrase-multilingual:278m ve gemma3:1b. Kalite ölçülmeden sessiz geçiş yapma. API keep_alive=0 yükleme sonrası boşaltmayı ister; sıradaki model öncesinde boşaldığını doğrula. Parametre ve başlangıç limitleri config/rag_profiles.json içindedir.

## Ek ağsız kontroller
```powershell
python evaluation/verify_metrics.py
```
Bu araç Recall@5/MRR@10 formüllerini elle bilinen örneklerle ve mini corpus/gold kimlik eşleşmelerini kontrol eder; embedding veya üretici çalıştırmaz. evaluation/mini_gold.jsonl sadece smoke fikstürüdür; 80 soruluk bağımsız kör kabul setinin yerini tutmaz. Corpus belgeleri draft gelir; yönetici açıkça onaylamadan indekslenmez. Kontrol aracı draft içeriği dosya bütünlüğü için okur, retrieval yetkisi vermez.

## Plan onayı
Seçili çözüm bağımsız doğrulayıcıdan geçer, yönetici arayüzünde incelenir ve canonical snapshot hash'iyle onaylanır. Onay plan_approvals ve audit_events'e aynı transaction'da yazılır. Değişiklik/iptal önceki onayı geçersiz kılar. RAG cevabı açıklamadır, onay veya atama değildir. Gerçek uygulama bu akışı henüz geliştirmemiştir.

## Sürüm notu
Önceki 23 tablo korunmuştur; 11 yeni tabloyla toplam 34. Temiz kurulum user_version=2; mevcut v1 için SEMA_DEGISIKLIKLERI.md ve migration dosyasını izle. Model kalitesi, GPU yerleşimi ve süreler bu paketin tesliminde ölçülmüş değildir. Teknik kaynak bağlantıları RAG belgesindedir.
