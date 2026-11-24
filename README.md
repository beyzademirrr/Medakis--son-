# MedAkis

**Yapay Zekâ Destekli Sağlık Tesisi İş Yükü Tahmini ve Personel Kaynak Optimizasyon Sistemi**

Hastane yöneticisinin **iş yükünü önceden görmesine**, **personeli kısıtlara uygun biçimde planlamasına** ve her kararı
**insan onayıyla, izlenebilir biçimde** vermesine yardım eden yapay zekâ destekli bir **idari karar destek prototipi**.
Yalnız **sentetik verilerle** çalışır; klinik ürün değildir; gerçek hasta ya da personel verisi kullanılmaz.

## Çözülen problem

Servislerde iş yükü vardiya içinde hızla değişir. Yönetici; bekleyen görevleri, kullanılabilir personeli, rol ve
yetkinlikleri aynı anda değerlendirip yeniden dağılım yapmak zorundadır. MedAkis bu kararı **tahmin + optimizasyon +
insan onayı** zinciriyle destekler: sistem öneri üretir, **hiçbir planı kendisi uygulamaz**.

## Neler çalışıyor (teslim sürümü)

| Bileşen | Ne yapar | Durum |
|---|---|---|
| İş yükü tahmini (makine öğrenmesi) | 30/60 dk ufuklu Random Forest ve baseline; zamansal değerlendirme ve sızıntı testleri | Çalışıyor |
| Personel optimizasyonu (OR-Tools CP-SAT) | Rol, yetkinlik geçerliliği, vardiya, mola ve eşzamanlı atama kısıtlarıyla kadro ve görev önerisi; bağımsız doğrulayıcı ve güvenli ret | Çalışıyor |
| İnsan onaylı öneri akışı | Plan SHA-256 özetine bağlı onay, ret, iptal, geri alma; tazelik denetimi; JSON/CSV dışa aktarım | Çalışıyor |
| Denetim kayıtları | Özet zincirli, yalnız ekleme yapılan olay izi; bütünlük denetimi | Çalışıyor |
| Telegram onay bildirimi | Yalnız geçerli onaylı öneri için; varsayılan **dry-run**; tekrar engeli; başarısızlık ayrı kaydedilir | Prototip (bu sürümde eklendi) |
| Kaynaklı RAG | Onaylı belge corpus'u, BGE-M3 vektör arama, kaynak/sürüm/alıntı aralığı doğrulaması, reranker kapıları (LAMAR-600m, ModernBERT-TR), yerel Gemma 3 4B | Prototip; **normal kullanıcıya kapalı** (A6 kabul kriteri karşılanmadı) |

Ayrıntılı ve dürüst durum tablosu: **`docs/TESLIM_DURUMU.md`**. Kamera tabanlı yoğunluk analizi, ısı haritası,
yoğunluğa duyarlı optimizasyon ve AI Agent **henüz uygulanmadı** (yol haritası aşağıda).

## Mimari

```
 Sentetik veri üreticisi ──► İş yükü hesapları ──► ML tahmini (RF, 30/60 dk)
                                                      │
                                                      ▼
   Personel, rol, yetkinlik, vardiya ──────► CP-SAT optimizasyonu ──► Bağımsız doğrulayıcı
                                                      │
                                                      ▼
                                   Öneri + plan SHA-256 ──► Yönetici incelemesi ve onayı
                                                      │                    │
                                                      ▼                    ▼
                                   Özet zincirli denetim izi     Telegram bildirimi (dry-run varsayılan)

 Kaynaklı RAG (geliştirme kipi): onaylı corpus → BGE-M3 → reranker kapısı → doğrulanmış alıntı / kaynak sunumu
```

## Yenilikçi yönler

- **Güvenli ret:** optimizasyon uygun çözüm bulamazsa ya da girdi geçersizse öneri üretmez; açığı görünür kılar.
- **Plan özetine bağlı onay:** onay, ekranda gösterilen planın SHA-256 özetine bağlanır; girdi değişirse onay geçersizleşir.
- **Kaynak doğrulamalı RAG:** cevap yalnız birebir doğrulanmış alıntılardan oluşur; kaynak yoksa sistem açıkça kaçınır.
  Kabul ölçütleri önceden kaydedilen deneylerle ölçüldü ve karşılanmadığı açıkça raporlandı.
- **Tamamen yerel çalışma:** modeller yerel (Ollama ve izole deney ortamı); bulut API'si gerekmez.

## Örnek demo senaryosu (Sentetik Demo modu)

1. `.\.venv\Scripts\python.exe -m streamlit run app.py -- --demo`
2. **Öneri inceleme ve onay** sekmesi → *İnceleme senaryosunu yükle* (ilk açılış ~30 sn; yerel model dosyaları gerekir).
3. Oturum aç (prototip aktörü), karar anı seç, **Değerlendir** → CP-SAT önerisi ve doğrulayıcı sonucu.
4. Öneriyi incelemeye kaydet → yönetici oturumuyla **onayla**.
5. Onaylı öneride **Bildirimi hazırla (dry-run)** → hazırlanan mesaj ve bildirim izi görünür.
6. **Denetim geçmişi**nde özet zinciri doğrulanmış olay listesi görünür.

## Güvenlik ve veri gizliliği

- Yalnız sentetik veri; yüz tanıma, kimlik tespiti ya da kişisel sağlık verisi işleme yoktur.
- Sistem hiçbir personel atamasını uygulamaz; her öneri yönetici onayı ister.
- Gizli bilgiler koda ve depoya yazılmaz: veritabanı parolaları Windows Credential Manager'da, Telegram token'ı yalnız
  ortam değişkeninde (`.env.example` boş değerler içerir; `.env` git dışındadır).
- Telegram bildirimi tek bir demo sohbetine gider; personelin kişisel hesaplarına bildirim gönderilmez.
- RAG çıktısı operasyonel karar için kullanılmaz; normal kullanıcıya kapalıdır.

## Bilinen sınırlılıklar

- RAG kabul kriteri (A6) karşılanmadı; insan incelemesi ve bağımsız test soruları bekleniyor.
- Denetim izi tek yazıcılı prototiptir; eşzamanlı yazıcılara karşı güvence iddiası yoktur.
- Telegram canlı gönderimi bu teslimde test edilmedi (dry-run doğrulandı).
- Kamera, ısı haritası ve AI Agent henüz yoktur.

## Yol haritası (2 Aralık sunumuna kadar)

1. Sentetik kamera görüntüsü üreticisi ve açıkça "simülasyon" etiketli yoğunluk modülü; hizmet alanı ısı haritası.
2. Yoğunluğa duyarlı geçici görevlendirme önerisi (mevcut CP-SAT kısıtları korunarak; yoğunluk tek başına gerekçe değil).
3. Yöneticiyle konuşan agent: mevcut tahmin/optimizasyon fonksiyonlarını araç olarak çağıran, öneri üretip onaya sunan
   tool-calling katmanı (MCP entegrasyonu ayrı iş).
4. RAG: insan incelemesi, bağımsız soru seti ve uçtan uca mimari kararı.

> Geliştirme geçmişi ve deney raporları: `docs/raporlar/`. Paketle gelen özgün README `docs/paket_v2/README_PAKET_v2.md`.

## Gereksinimler

- Windows 10/11 x64
- Python **3.14** (kilit dosyası Python 3.14.6 ile oluşturuldu). Python Launcher (`py`) ile:
  `py -0p` komutu kurulu sürümleri listeler.
- İnternet bağlantısı yalnız ilk paket kurulumunda gerekir (temiz ortamda ~7 dk, 119 paket).

Not: Bilgisayarda Python 3.10 da kurulu ve `python` komutu onu açıyor. Python 3.10'un
destek süresi Ekim 2026'da bitti; bu yüzden sanal ortam açıkça `py -3.14` ile kurulur.
Sürüm seçiminin gerekçesi ve 3.13 karşılaştırması: `docs/PYTHON_SURUM_DEGERLENDIRMESI.md`.
Uyumluluk yoklaması: `.\.venv\Scripts\python.exe scripts\compat_probe.py`.

## Kurulum (PowerShell, proje kökünde)

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt`, `pip freeze` ile üretilmiş tam kilittir (119 paket, hepsi `==` ile
sabit). Doğrudan bağımlılıklar `pyproject.toml` içindedir: streamlit, pandas, numpy,
scikit-learn, ortools, chromadb, httpx, tzdata, psycopg, sqlalchemy, alembic, keyring ve
geliştirme için pytest, pyyaml.

## Çalıştırma

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Tarayıcıda `http://localhost:8501` açılır. Sunucu yalnız bu bilgisayardan erişilebilir
(`.streamlit/config.toml`, `address = "localhost"`) ve kullanım istatistiği gönderilmez.
Durdurmak için terminalde `Ctrl+C`.

Beklenen ekran: başlık, sarı "Prototip — yalnız sentetik veri" uyarısı, kenar çubuğunda **Çalışma modu**
seçicisi ve beş sekme: **Genel bakış**, **Servis paneli**, **Öneri inceleme ve onay**, **Mimari ve aşamalar**,
**Yapılandırma**. Bu komut **Normal (veritabanlı)** modda açar: Servis paneli PostgreSQL'e bağlanmayı dener
(çalışmıyorsa nedenini yazar). Veritabanı olmadan sunum için aşağıdaki Sentetik Demo komutunu kullanın.

**Servis paneli** (Aşama 3) servis, yerel tarih, vardiya ve 15 dakikalık değerlendirme anı seçtirir;
hasta sayısı, bekleyen iş, kullanılabilir personel (toplam ve rol bazında; yataklı serviste hemşire
ayrıca), gerçekleşen iş kartlarını, 15 dakikalık grafiği ve kapasite/bekleyen görev tablolarını
gösterir. Birim personel-dakikasıdır. Panel yalnız `medakis_app` rolüyle ve READ ONLY işlemle okur.
Eksik veri "—", gerçek sıfır "0" ve nedeniyle, kısmi kapsam "(kısmi)" olarak gösterilir. 30/60
dakikalık tahmin Aşama 4'tedir; panelde tahmin sayısı yoktur. PostgreSQL çalışmıyorsa veya veri
yoksa panel nedenini yazar.

Hesap kuralları ve sınırlamalar: `docs/raporlar/ASAMA_3_RAPORU.md`. Yalnız okuyan uçtan uca
doğrulama (servis sonuçları ile bağımsız SQL karşılaştırması):

```powershell
.\.venv\Scripts\python.exe scripts\asama3_uctan_uca.py
```

## Sentetik Demo (veritabanısız sunum)

PostgreSQL, Docker, migration veya seed gerekmez. Kurulumdan sonra proje kökünde:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py -- --demo
```

Demo modu ayrıca `http://localhost:8501/?mod=demo`, `MEDAKIS_MODE=demo` ortam değişkeni ya da kenar
çubuğundaki **Çalışma modu → Sentetik Demo (veritabanısız)** ile seçilebilir. Demo modunda Servis paneli
çizilmez ve veritabanına hiçbir bağlantı denenmez; ekranın üstünde bunu ve kimlik/yetkilerin `test_sentetik`
olduğunu söyleyen bir uyarı görünür.

**Gerekli model dosyaları** (Git dışı, `artifacts/`). Öneri inceleme ekranı gerçek sentetik senaryoyu
(üretici 2.1 + Aşama 4 Random Forest + U1 bilgi baseline'ı ve kalibrasyonu) bu dosyalarla çalıştırır:

| Yol | Üreten betik |
|---|---|
| `artifacts/forecasting/asama4_seed42/random_forest/` | `scripts\asama4_egit.py --cikti artifacts\forecasting\asama4_seed42` |
| `artifacts/forecasting/asama5_u1/baseline_bilgi/` | `scripts\asama5_u1_kalibrasyon.py` (önce Aşama 4 modeli gerekir) |
| `artifacts/forecasting/asama5_u1/kalibrasyon.json` | `scripts\asama5_u1_kalibrasyon.py` |
| `docs/veri/asama5_guvenli_duzeltme2.json` (karar anları) | depoda var |

```powershell
.\.venv\Scripts\python.exe scripts\asama4_egit.py --cikti artifacts\forecasting\asama4_seed42
.\.venv\Scripts\python.exe scripts\asama5_u1_kalibrasyon.py
```

Temiz ortamda doğrulandı (`MEDAKIS_SON_TESLIM_DOGRULAMASI.md`): iki betik toplam ~10 dk sürer (Aşama 4
~5 dk, U1 ~5 dk; ağ gerekmez) ve model dosyaları baytı baytına aynı üretilir (RF `bd3e4b25…`, bilgi
baseline'ı `2527e87d…`, kalibrasyon `02fbfbe3…`). Aşama 4 betiği demo için gerekmeyen `baseline/`,
`random_forest_gerceklesen/` ve `sonuclar.json` dosyalarını da yazar (toplam ~64 MB). U1 betiği depodaki
`docs/veri/asama5_u1_kalibrasyon.json` özetini yeniden yazar; fark yalnız süre alanı ve ölçülerin son
basamağıdır (≤ 2e-15). İstenirse `git checkout -- docs/veri/asama5_u1_kalibrasyon.json` ile geri alınır.
Model dosyalarını kopyalayarak taşımak için yalnız tablodaki üç yol yeterlidir (~37 MB).

Senaryo yüklenirken model dosyaları ve özetler denetlenir: U1 kalibrasyonu `02fbfbe3…`, üretici 2.0.0
`2a1fdfb0…`, plan 2.1 `09c0315d…`. Dosya yoksa, bozuksa ya da özet uyuşmazsa ekran açık bir hata ve bu
çözüm komutlarını gösterir; **sahte model çıktısı üretilmez**. İlk yükleme 30–45 sn sürer.

**Sunum akışı** (Öneri inceleme ve onay sekmesi):
1. **İnceleme senaryosunu yükle**.
2. **Oturum:** `demo_yonetici` seç → **Oturum aç**. Bu yerel prototip oturumudur; doğrulanmış kimlik değildir.
3. **Karar anı:** `2027-01-19 09:30` ya da `2027-01-22 15:15` (varsayılan politikada yayımlanabilir öneri
   üreten iki an) → **Değerlendir**. Diğer anlarda güvenli ret ve nedenleri görünür.
4. **Katman A önerisini incelemeye kaydet** → plan SHA-256 ve kalemler.
5. Karar formunda **Onayla** → "Onay şu an geçerli". İkinci onay denemesi `zaten_onaylandi` ile reddedilir.
6. **Onayı geri al** (gerekçe zorunlu) → durum "Geri alındı".
7. **Denetim geçmişi:** bütün olaylar ve reddedilen denemeler; özet zinciri doğrulaması.

Onay yalnız insan kararının izlenebilir kaydıdır; sistem hiçbir planı uygulamaz ve gerçek atama yapmaz.
Denetim izi `data/onay_izi/` altına yazılır (Git dışı, tek yazıcılı yerel prototip).

## VS Code ile çalışma

1. VS Code'da **File → Open Folder** ile proje kökünü açın.
2. Önerilen eklentileri kurun (Python, Python Debugger; `.vscode/extensions.json`).
3. Yorumlayıcı otomatik olarak `.venv\Scripts\python.exe` seçilir (`.vscode/settings.json`).
   Seçilmezse: `Ctrl+Shift+P` → **Python: Select Interpreter** → `.venv`.
4. Uygulamayı başlatmak için **Run and Debug** panelinden **MedAkis: Streamlit**,
   testler için **Testing** paneli veya **MedAkis: pytest** yapılandırması.

`.venv` git'e gönderilmez; yeni bir bilgisayarda klonladıktan sonra Kurulum adımlarını çalıştırın.

## PostgreSQL (Docker Compose)

Docker Desktop çalışıyor olmalıdır. Servis yalnız `127.0.0.1:5432` adresinden erişilebilir.
Komutlar proje kökünde çalıştırılır. Parola hiçbir dosyaya yazılmaz: `compose.yaml` ve
`.env` parola içermez; `.env` isteğe bağlıdır (`.env.example` örneğine bakın).

**İlk kurulum** (yönetici ve üç rol parolasını gizli girişle sorar; tekrar çalıştırılabilir,
veriyi silmez):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\db\setup.ps1
```

Parolaları bir parola yöneticisinde saklayın.

| Veritabanı | Amaç | | Rol | Yetki |
|---|---|---|---|---|
| `medakis_dev` | Geliştirme | | `medakis_owner` | Tablo sahibi; yalnız migration |
| `medakis_test` | Otomatik testler | | `medakis_app` | İş tabloları: okuma/ekleme; atama, onay, denetim yalnız ekleme; silme yok |
| `medakis_demo` | Sunum | | `medakis_rag` | Yalnız `rag_*`: belge okuma, sorgu/değerlendirme kaydı; iş tablolarına erişim yok |
| | | | `medakis_seed` | Yalnız `medakis_dev`/`medakis_test`; 17 operasyonel tabloda ekleme/silme; tarihsel tablolar salt okunur, denetim yalnız ekleme |

Tablo bazında yetkiler: `src/medakis/repositories/schema.py` (`PRIVILEGES`).

**Parolaları Windows Credential Manager'a kaydetme** (her parola önce gerçek girişle sınanır):

```powershell
.\.venv\Scripts\python.exe scripts\db\store_credentials.py
.\.venv\Scripts\python.exe scripts\db\store_credentials.py --kontrol
.\.venv\Scripts\python.exe scripts\db\store_credentials.py --degistir
```

Uygulama parolayı buradan okur. Depoya ulaşılamazsa veya kayıt yoksa, terminalde çalışan araçlar
gizli giriş ister; terminali olmayan süreçler açık bir hata verir. Parola ortam değişkeninden
veya dosyadan okunmaz.

**Şema (Alembic):** migration yalnız `medakis_owner` rolüyle çalışır, hedef veritabanı zorunludur.

```powershell
.\.venv\Scripts\python.exe -m alembic -x db=medakis_test upgrade head
.\.venv\Scripts\python.exe -m alembic -x db=medakis_dev upgrade 0003_seed_write_window
.\.venv\Scripts\python.exe -m alembic -x db=medakis_dev current
.\.venv\Scripts\python.exe -m alembic -x db=medakis_dev downgrade base
```

Revizyon 0004 (üretici 2.0.0 kolonları) `medakis_dev` ve `medakis_demo`'ya tasarım gereği uygulanmaz;
bu iki veritabanında `head` hedefi `-x sema_0004_onayi=<veritabanı>` olmadan reddedilir.

`downgrade base` bütün tabloları ve içindeki verileri siler. `medakis_dev`'de ayrıca `-x onay=medakis_dev`
ister; önce yedek alın. `medakis_demo` için her Alembic komutu `-x demo_onayi=medakis_demo` ister.

**Sentetik veri (seed=42)** — yalnız `medakis_dev` ve `medakis_test`; `medakis_demo` reddedilir.

```powershell
.\.venv\Scripts\python.exe scripts\db\seed.py status --database medakis_test
.\.venv\Scripts\python.exe scripts\db\seed.py apply --database medakis_test
.\.venv\Scripts\python.exe scripts\db\seed.py apply --database medakis_test --sifirla
```

- Aynı veri zaten yüklüyse hiçbir şey yazılmaz; farklı veri varsa yalnız `--sifirla` ve veritabanı
  adının yazılmasıyla değişir.
- `medakis_dev`'e her yazma yazılı onay ister; sıfırlamadan önce otomatik yedek alınır.
- Sıfırlama yalnız 17 operasyonel tabloyu siler (TRUNCATE kullanılmaz). Model, tahmin, optimizasyon,
  atama, açık ve onay tablolarında kayıt varsa durur; `audit_events` hiç silinmez, yalnız eklenir.
- Silme, yükleme, sayaç ve denetim kaydı tek işlemdedir; doğrulama başarısızsa hepsi geri alınır.

**Başlatma, durdurma, durum:**

```powershell
docker compose up -d --wait
docker compose ps
docker compose logs --tail 50 postgres
docker compose stop
docker compose start
docker compose down
```

`docker compose down` konteyneri kaldırır, veriyi korur.

> **Uyarı:** `docker compose down -v` ve `docker volume rm medakis_pgdata` bütün
> veritabanlarını **geri dönüşsüz siler**. Önce yedek alın.

**Yönetici konsolu ve parola değiştirme** (konteyner içi, parola sormadan bağlanır;
Docker erişimi olan herkes yöneticidir):

```powershell
docker compose exec postgres psql -U postgres -d medakis_dev
docker compose exec postgres psql -U postgres -d postgres -c "\password medakis_app"
```

**Yedekleme** (`backups\` klasörüne, SHA-256 dosyasıyla; bu klasör git dışındadır):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\db\backup.ps1 -Database medakis_demo
```

Windows PowerShell 5.1'de `pg_dump ... > dosya` kullanmayın: yönlendirme ikili yedeği bozar.

**Geri yükleme** (seçilen veritabanının içeriğini değiştirir; `-Onayla` zorunludur. SHA-256
kontrol edilir, önce güvenlik yedeği alınır, hata olursa işlem geri alınır):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\db\restore.ps1 -Database medakis_test -File backups\DOSYA.dump -Onayla
```

`medakis_demo` için ayrıca `-DemoOnayi medakis_demo` yazılmalıdır:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\db\restore.ps1 -Database medakis_demo -File backups\DOSYA.dump -Onayla -DemoOnayi medakis_demo
```

## Testler

```powershell
.\.venv\Scripts\python.exe -m pytest
```

PostgreSQL testleri çalışan konteyner ister ve varsayılan çalıştırmada yer almaz:

```powershell
.\.venv\Scripts\python.exe -m pytest -m postgres
.\.venv\Scripts\python.exe -m pytest -m postgres_lifecycle
```

`postgres` testleri rol parolalarını Credential Manager'dan okur (önce `store_credentials.py`).
Yazma denemeleri yalnız `medakis_test` içinde yapılır. `postgres_lifecycle` konteyneri durdurup
yeniden başlatır (veri korunur).

Varsayılan çalıştırma ağ gerektirmez. `integration` işaretli testler (gerçek Ollama/Chroma,
Aşama 7+) varsayılan olarak atlanır; çalıştırmak için `-m integration`.

Paketle gelen ağsız doğrulayıcılar ayrıca da çalıştırılabilir:

```powershell
.\.venv\Scripts\python.exe verify_schema.py
.\.venv\Scripts\python.exe evaluation\verify_metrics.py
```

## Yapılandırma

Ayarlar `src/medakis/config.py` içinde merkezidir (saat dilimi Europe/Istanbul, seed 42,
15 dakikalık gözlem, 30/60 dakika ufuk, çözücü süre sınırı). Ortam değişkenleri:

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `PGHOST` | `127.0.0.1` | PostgreSQL adresi; yalnız yerel adres kabul edilir |
| `PGPORT` | `5432` | PostgreSQL portu |
| `PGDATABASE` | `medakis_dev` | `medakis_dev`, `medakis_test` veya `medakis_demo` |
| `MEDAKIS_RAG_PROFILE` | `primary` | `config/rag_profiles.json` içindeki profil adı |

Kullanıcı adı ve parola ayarlardan okunmaz: rolü kod seçer, parola Credential Manager'dan gelir.

## Klasör yapısı

```text
app.py                      Streamlit giriş noktası (src/ yolunu ekler, ui.home.main çağırır)
pyproject.toml              Proje meta verisi, doğrudan bağımlılıklar, pytest ayarı
requirements.txt            Tam bağımlılık kilidi (Python 3.14.6, Windows x64)
compose.yaml                PostgreSQL 18 servisi (yalnız 127.0.0.1)
.env.example                Parolasız yerel ayar örneği
docker/postgres/            Roller ve veritabanları (provision.sql)
scripts/db/                 Kurulum, parola kaydı, yedekleme, geri yükleme
alembic.ini, migrations_pg/ PostgreSQL migration (Alembic)
.python-version             Hedef Python sürümü
.streamlit/config.toml      Yerel sunucu ayarları
src/medakis/
  config.py                 Merkezi yapılandırma
  ui/                       Streamlit ekranları (SQL/model/çözücü yok)
  services/                 Uygulama servisleri (system_info: Aşama 1 durum bilgisi)
  domain/                   Alan modeli ve saf kurallar            (Aşama 2-3)
  repositories/             PostgreSQL: parola, motor, şema        (Aşama 2)
  synthetic/                Deterministik sentetik veri            (Aşama 2)
  forecasting/              Baseline ve Random Forest              (Aşama 4)
  optimization/             CP-SAT ve bağımsız doğrulayıcı         (Aşama 5)
  approvals/                Yönetici onayı ve plan hash'i          (Aşama 6)
  rag/                      Kaynaklı RAG                           (Aşama 7-9)
    contracts/              RagAnswer/Citation DTO'ları ve sağlayıcı arayüzleri (taslak, hazır)
    ingestion, chunking, embeddings, vector_store, retrieval,
    generation, citations, evaluation/   yer tutucu paketler
tests/                      pytest
data/, artifacts/           Çalışma verisi ve model çıktıları (git dışında)
config/, corpus/, evaluation/, migrations/   Paketle gelen girdiler (değiştirilmedi)
docs/paket_v2/              Özgün paket README kopyası
```

Mimari sınırlar testle korunur (`tests/test_structure.py`): optimization, approvals,
repositories ve domain `medakis.rag`'i içe aktaramaz; rag paketi optimization, approvals,
repositories, services, `sqlite3` ve `subprocess` içe aktaramaz; ui `sqlite3`, scikit-learn,
OR-Tools veya Chroma içe aktaramaz.

## Proje belgeleri

`docs/DEVELOPMENT_RULES.md` (geliştirme kuralları), `MedAkis_Proje_Sartnamesi.docx/.md`,
`RAG_MIMARISI_VE_DEGERLENDIRME.md`, `SEMA_DEGISIKLIKLERI.md`, `KAPSAM_VE_KARARLAR.md`,
`src/medakis/repositories/schema.py` ve `migrations_pg/` (güncel PostgreSQL şeması).
`medakis_schema.sql` ve `migrations/001_to_002.sql` SQLite v2 tarihsel referansıdır; `verify_schema.py` bunları doğrular.

## Sınırlamalar

- Ollama ve model indirme Aşama 1'de yapılmadı; RAG profilleri bu cihazda ölçülmedi.
- Bütün sonuçlar sentetik veriyle elde edilmiştir; gerçek hastane güvenliği doğrulanmamıştır. Asgari personel,
  destek kuralları, yetki politikası ve onay süresi `test_sentetik` prototip değerleridir, yetkili karar değildir.
- Kimlik doğrulama yoktur (yerel prototip oturumu). Onay kalıcılığı dosya tabanlıdır; veritabanı tasarımı
  `docs/raporlar/ASAMA_5_U7_RAPORU.md` §6'dadır (migration ayrı onay).
- Gerçekleşen iş yükü, `[tamamlanma − gerçekleşen süre, tamamlanma)` simülasyon varsayımıyla hesaplanır;
  iptal zamanı kaydedilmediği için iptal görevlerin geçmiş durumu kesin değildir (Aşama 3 raporu).

## Dosya bütünlüğü (SHA-256)

- `docs/paket_v2/SHA256SUMS_PAKET_v2.txt`: paket teslimindeki özgün kayıt; hiç değiştirilmez.
  Güncel belgeler MedAkis adıyla düzenlendiği için özgün baytlar, paketin depoya ilk alındığı
  git commit'inde (`5f89cb8`) doğrulanır. Git geçmişi olmayan kopyada bu kontrol atlanır.
- `SHA256SUMS.txt`: aynı 20 paket dosyasının güncel hâli, güncel adlarıyla
  (ör. `MedAkis_Proje_Sartnamesi.md`, `medakis_schema.sql`).

```powershell
.\.venv\Scripts\python.exe scripts\checksums.py check
.\.venv\Scripts\python.exe scripts\checksums.py update
```

`check` her iki manifesti doğrular (pytest de çalıştırır). Bir paket dosyası (ör. bu README)
bilerek değiştirildiğinde `update` ile güncel manifest yeniden hesaplanır.
