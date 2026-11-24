# MedAkis Proje Şartnamesi

Sürüm 2.0 • 4 Kasım 2026

## 1 Amaç ve kullanım sınırı

### Projenin amacı

MedAkis, sağlık tesisinde gelecek iş yükünü personel dakikası cinsinden tahmin eden ve uygun personel için uygulanabilir görev dağılımı öneren idari karar destek prototipidir. Birincil senaryo yataklı serviste gece vardiyasındaki hemşire iş yüküdür. Acil servis ve ayaktan tedavi aynı mimarinin sentetik senaryo gösterimleridir. Amaç hasta sayısına bakarak sabit oran uygulamak değil, bakım ve işlem sürelerini görünür kılmaktır.

### Kullanıcılar ve sınırlar

Vardiya sorumlusu servis iş yükünü ve açıkları inceler; planlama sorumlusu simülasyon varsayımlarını ve vardiya kapasitesini düzenler; geliştirici modelleri ve çözüm sonuçlarını karşılaştırır. İlk sürüm yerel ve tek kullanıcılıdır. Gerçek klinik karar, otomatik personel emri, gerçek hasta verisi, tanı önerisi, bordro veya hastane bilgi sistemi entegrasyonu kapsam dışıdır.

### Kapsamdaki çıktılar

Sistem sentetik veri üretimi, servis görünümü, iş yükü hesabı, 30 ve 60 dakikalık tahmin, baseline ve Random Forest karşılaştırması, CP-SAT ataması, personel açığı açıklaması ve sentetik sonuç dışa aktarımını kapsar. Arayüz Türkçe olur. Klinik süre ve oranlar doğrulanmış standartlar gibi gösterilmez; varsayımların sentetik olduğu bütün ana ekranlarda belirtilir.

### Başarı tanımı

Başarı, daha düşük hata elde etme iddiasına bağlanmaz. Başarı; zaman sızıntısı olmadan ölçülmüş tahminler, zorunlu kuralları koruyan atamalar, yetersizlikte açık raporu, tekrarlanabilir sentetik demo ve test edilen uçtan uca akıştır. Gerçek yaşamda kullanım için gerekli klinik, kurumsal ve hukuki değerlendirmeler bu geliştirme paketinin sonucu değildir.

## 2 Kullanıcı akışları ve ekranlar

### Ana gösterge paneli

Kullanıcı tesisi, servisi, senaryo tarihini ve vardiyayı seçer. Panel mevcut sentetik hasta sayısını, bekleyen iş dakikasını, kullanılabilir personel dakikasını ve 30/60 dakika tahminini ayrı kartlarla gösterir. Zaman damgası, veri kaynağı, kullanılan model ve son güncelleme görünür olmalıdır. Boş veri durumunda sıfır tahmin uydurulmaz; veri üretme veya uygun tarih seçme yönlendirmesi sunulur.

### Görev ve personel ekranı

Görev listesinde işlem türü, son tarih, sentetik bakım gereksinimi, tahmini dakika ve durum gösterilir. Personel görünümü rolü, yetkinlikleri, vardiyayı ve uygunsuzluk aralıklarını gösterir. Kullanıcı görev süresi varsayımını değiştirdiğinde mevcut görevlerin anlık tahminleri kendiliğinden geriye dönük değişmez; yeniden hesaplama açık bir işlem olmalıdır.

### Tahmin ve değerlendirme ekranı

Her ufuk için baseline ile Random Forest MAE/RMSE değerleri, kronolojik test tarihleri ve örnek sayısı karşılaştırılır. Grafik tahmin anını ve hedef aralığını belirtir. Eksik veri veya yalnız eğitim verisi olduğunda değerlendirme eksikliği açıklanır. RF üstünlüğü varsayılmaz. Birimler bütün grafiklerde personel dakikasıdır.

### Planlama ve açık ekranı

Kullanıcı zaman penceresi ve çözücü sınırını seçer. Sonuçta seçili çalışma kimliği, çözüm durumu, görev atamaları, kişi bazında yük ve atanmayan işler görünür. Personel yokluğu, yetkinlik eksikliği, vardiya dışı süre, izin veya zaman çakışması açıklanır. Tahmin rezervi, somut atanamayan işlerden ayrı sunulur. Dışa aktarım seçili çalışmaya aittir; önceki alternatifler aktif planla birleştirilmez.

## 3 Mimari ve çalışma ortamı

### Modüler monolit

Tek Python uygulaması içinde arayüz, servis, alan modeli, veri erişimi, sentetik üretim, tahmin ve optimizasyon modülleri ayrılır. Streamlit yalnız sunum ve kullanıcı etkileşimini yönetir. İş kuralları servis/alan katmanında, SQL repository katmanında yer alır. UI dosyalarından doğrudan model eğitimi veya karmaşık SQL çalıştırılmaz.

### Teknoloji ve yapılandırma

Python, Streamlit, PostgreSQL 18 (Docker Compose), SQLAlchemy Core, Alembic, pandas/numpy, scikit-learn ve Google OR-Tools CP-SAT kullanılır. pytest doğrulama aracıdır. İlk aşamada uyumlu sürümler çözülür, Python sürümü belgelenir ve bağımlılıklar kilitlenir. Veritabanı adresi, saat dilimi, seed, eğitim sınırları ve çözücü süre sınırı merkezi yapılandırmada tutulur; gizli sabitler UI içine gömülmez.

### Veri erişimi ve kayıt

Uygulama PostgreSQL'e yalnız düşük yetkili rollerle bağlanır (medakis_app, medakis_rag); şema değişikliği yalnız medakis_owner rolüyle Alembic üzerinden yapılır. Parolalar kaynak kodda, dosyada veya günlükte tutulmaz: Windows Credential Manager'dan okunur, erişilemezse terminalde gizli giriş istenir. Yazma işlemleri transaction ile yapılır; yarım kalan model/atama kayıtları geri alınır. Parametreli sorgular kullanılır. Testler ayrı medakis_test veritabanında çalışır.

### Depolama ve hata yönetimi

Olay tarihleri TIMESTAMPTZ olarak saklanır ve oturum saat dilimi UTC'dir; yerel gösterim tesis saat dilimindedir. Aralıklar başlangıç dahil bitiş hariçtir. Model dosyaları artifacts altında, veritabanı adlandırılmış Docker volume'unda (medakis_pgdata), yedekler backups altında tutulur ve sürüm kontrolüne alınmaz. Model dosyası yükleme güvenilir yerel çıktılarla sınırlıdır. Hatalar kullanıcıya anlaşılır mesajla, geliştiriciye hassas veri içermeyen işlem iziyle aktarılır.

## 4 Sentetik veri ve iş yükü hesabı

### Üretim sözleşmesi

Varsayılan seed 42 ile bir yataklı servis, bir acil ve bir ayaktan birim üretilir. Eğitim demosu için en az 28 gün önerilir; miktar ayarlanabilir. Yataklı senaryoda gece vardiyası yerel 20.00 ile ertesi gün 08.00 olarak üretilebilir; bu bir demo varsayımıdır. İsim, kimlik numarası veya gerçek tanı yerine SYN ve STAFF kodları kullanılır. Tekrar üretim aynı seed ile aynı içerikleri sağlamalı; veri ekleme işlemi çoğaltma yapmamalıdır.

### Görev ve bakım gereksinimi

Her task_type pozitif base_minutes ve gerekli role sahiptir. Bir veya daha çok yetkinlik şartı olabilir; hepsi gereklidir. İş yaratılırken en son bilinen bakım gereksinimi katsayısı ile süre hesaplanır ve estimated_minutes olarak saklanır. Örneğin 10 sentetik dakika × 1,2 katsayı = 12 personel dakikasıdır. Bu değer klinik standart değildir; klinik doğrulama iddiası olmadan değiştirilebilir.

### Gerçekleşen iş yükü

Gözlemler 15 dakikalık kesintisiz pencerelerdir. Tamamlanan görevin actual_minutes değeri gerçekleşme zaman aralığına orantılı dağıtılır; pencere toplamları görev toplamını korur. İlk uygulama gerçekleşme başlangıcını completed_at eksi actual_minutes olarak türetebilir ve bu simülasyon varsayımını belgelemelidir. İptal işler iş yüküne katılmaz. Hasta sayısı bir açıklayıcı değişkendir, tek başına iş yükü ölçüsü değildir.

### Kapasite ve çifte sayım

Kapasite vardiya ile plan penceresi kesişiminden izin ve gerçek mola aralıkları çıkarılarak hesaplanır. break_minutes özet alanıdır; molalar staff_unavailability içinde somutlaştırılır ve iki kez düşülmez. Örneğin 2 kişi × 30 dakika eksi toplam 10 dakika mola = 50 personel dakikası kapasitedir. Tahmin 65 dakika ise toplam talep açığı 15 dakikadır; uygunluk ve görev zamanları somut açıkları artırabilir.

## 5 Tahmin yöntemi ve değerlendirme

### Hedefler ve erişilebilirlik

Tahmin anı t için 30 ve 60 dakika ayrı hedeflerdir. Hedef, t ile t+h aralığındaki gerçekleşen personel dakikalarının toplamıdır. Yalnız t anında bilinen kayıtlar kullanılabilir. Gözlemin window_end ve available_at alanları t sonrasında ise özellik olarak alınmaz. Gelecekte tamamlanan görev süreleri, sonradan güncellenen bakım skorları ve gelecek hasta sayısı eğitim girdisine sızmamalıdır.

### Baseline ve Random Forest

Baseline, son dört erişilebilir 15 dakikalık pencerenin ortalama dakika hızını seçili ufka ölçekler. Yetersiz geçmişte tahmin üretilmez. Random Forest için gecikmeli iş yükü, geçmiş kayan ortalamalar, o an bilinen bekleyen iş/hasta sayısı, saat, hafta günü ve servis bilgisi kullanılabilir. 30 ve 60 dakika için ayrı modeller ve model_runs kayıtları oluşturulur. Ön işleme yalnız eğitim verisinde fit edilir.

### Kronolojik deney

Veri zaman sırasıyla eğitim, doğrulama ve test olarak ayrılır. Eğitim örneklerinin etiket bitişleri test tahmin anlarını geçemez; ufka göre gap/purge uygulanır. Hiperparametreler doğrulamada seçilir, test yalnız son değerlendirmedir. Özelliklerin geçmişi kullanması serbesttir; etiket bilgisinin geleceğe taşınması yasaktır. Sabit seed, veri parametreleri, özellik listesi ve model dosya yolu saklanır.

### Ölçüm ve yorum

MAE=ortalama mutlak hata, RMSE=kare hataların ortalamasının kareköküdür; ikisinin birimi personel dakikasıdır. Metrikler ufuk ve servis bazında, aynı test örneklerinde raporlanır. Örnek sayısı ve veri aralığı belirtilir. Random Forest baseline değerinden kötü ise sonuç dürüstçe gösterilir. Sentetik veride iyi sonuç gerçek klinik performans kanıtı değildir. Tahminler sıfırın altında olamaz; eksik model çıktısı açık hata durumudur.

## 6 Personel ataması ve açık yönetimi

### Karar modeli

CP-SAT her görev için aday personel seçer ve pencere içinde uygun başlangıç zamanı belirler. Görev süresi tam sayıya yukarı yuvarlanır. İşin sonu vardiya ve plan penceresini aşamaz; son tarih kısıtı sağlanır. İlk sürüm tek personelli, bölünemeyen ve tek servisteki işler içindir. Çoklu personelli işlemler, servisler arası transfer ve fazla mesai kapsam dışıdır.

### Zorunlu kurallar

Bir görev en fazla bir personele atanır. Personel aktif olmalı, rolü eşleşmeli, bütün yetkinlikleri işin tüm süresinde geçerli olmalıdır. Atama uygun servis vardiyasında kalır; izin, mola ve başka görevlerle çakışmaz. CP-SAT interval ve NoOverlap kısıtları kullanılabilir. Görev, çalışma ve tahmin servisleri eşleşmelidir. Çözümü kaydetmeden bağımsız doğrulayıcı aynı kuralları yeniden kontrol eder.

### Amaç ve rezerv

Birinci amaç atanmayan görev dakikasını minimize etmek, ikinci amaç uygun çözümler arasında yük farkını azaltmaktır. Öncelikler iki çözüm aşamasıyla veya matematiksel olarak baskın ağırlıkla uygulanır. Somut planlı işlerin toplamı tahminin içindedir; üzerine bir kez daha eklenmez. Tahminden planlı dakika çıkarılarak kalan pozitif fark rezerv talep olarak raporlanır; kimliği olmayan rezerv işler atanmış gibi gösterilmez.

### Sonuç durumları

OPTIMAL optimal kanıtlanmış çözümü, FEASIBLE bulunan uygun çözümü belirtir. Süre sınırında UNKNOWN sonucu plan üretmez. INFEASIBLE veya MODEL_INVALID durumlarında neden ve hata bilgisi gösterilir. Atanmamış her somut görev için açık dakikası ve açıklama kaydedilir. Uygun personel yokken kural gevşetilmez. Somut görev açığı ile rezerv açığı ayrı kalemlerdir; toplamda aynı talep iki kez sayılmaz. Karar her zaman kullanıcı tarafından incelenir.

## 7 Veri modeli ve uygulama bütünlüğü

### İlişki grupları

Tesis → servis → oda → yatak fiziksel yapıyı oluşturur. Sentetik hasta → başvuru → bakım gereksinimi ve görevler bakım akışını temsil eder. Personel → yetkinlik/gerçek vardiya/uygunsuzluk kapasiteyi belirler. Gözlem → model çalışması → tahmin → optimizasyon çalışması zinciri sonuçların kaynağını izler. Atamalar ve açıklar belirli bir optimizasyon çalışmasına bağlıdır.

### SQL güvenceleri

İlk şema 23 iş tablosudur; v2 ekleriyle toplam 34 iş tablosu vardır. Foreign key, UNIQUE, CHECK ve EXCLUDE kısıtları temel bütünlüğü sağlar. Tüm ondalık sayılar sonlu olmak zorundadır (NaN ve sonsuz reddedilir). Başvuru ve görev servisi birleşik foreign key ile eşleşir. Süreler pozitif, gözlem ve tahmin değerleri negatif olmayan sayılardır. Tamamlanmış görevde tamamlanma zamanı ve gerçek süre zorunludur. Şema başlangıç işlemi tek transaction içindedir ve sürüm alembic_version tablosunda izlenir.

### Uygulamada zorunlu ek kontroller

Veritabanı kısıtları tek başına yatak ile başvuru servisi ilişkisini ve personel atamalarının yetkinlik/izin uygunluğunu garanti etmez. Aynı yatağın örtüşen yatışları, personelin örtüşen vardiyaları, aynı çalışmada personelin örtüşen atamaları ve aynı kaynağın örtüşen onaylı sürümleri ayrıca EXCLUDE kısıtıyla da reddedilir; bu kısıtlar uygulama doğrulamasının yerine geçmez. Uygulama bunları transaction içinde kontrol etmelidir. Model/tahmin ufku, tahmin/çalışma servisi ve atama/görev servisi eşleştirilir. Mola süresi vardiyadan uzun olamaz; personelin örtüşen vardiyaları reddedilir. Geçersiz tarihler ve sonlu olmayan sayılar veritabanında da reddedilir.

### Geçmiş ve silme

Bağlı kayıtları silme varsayılan FK davranışıyla engellenir. Personel ve yatak aktif alanıyla kullanım dışı bırakılır. Bir görevin sonradan değiştirilmesi önceki planı sessizce güncellemez; yeni çalışma oluşturulur ve seçili sonuç değişimi görünür olur. İlk sürümde audit_events uygulama işlem izidir; değiştirilemez güvenlik kaydı iddiası yoktur. Veri temizleme yalnız açık kullanıcı işlemiyle ve yedek alınarak yapılır.

## 8 Geliştirme aşamaları ve kabul kriterleri

### Aşama 1 ve 2

Aşama 1: klasör yapısı, sanal ortam, kilitli bağımlılıklar, boş Türkçe Streamlit ekranı ve görünür prototip açıklaması. Kabul: belgelenen komutla uygulama açılır. Kullanıcı onayından sonra Aşama 2: repository, şema kurulumu, iş kuralları ve deterministik sentetik veri. Kabul: SQL doğrulaması, servis izolasyonu, tarih ve tekrar üretim testleri geçer; gerçek veri içermez.

### Aşama 3 ve 4

Aşama 3: görev, kapasite, gözlem hesapları ve servis paneli. Kabul: elle hesaplanan küçük örnekte dakika toplamları ve gece yarısı aralıkları doğrudur; boş veri ekranı çalışır. Onaydan sonra Aşama 4: baseline, Random Forest ve zamansal değerlendirme. Kabul: 30/60 dakika ayrı sonuç, ortak test dilimi, MAE/RMSE ve sızıntı testleri; yetersiz geçmiş açıklaması bulunur.

### Aşama 5 ve 6

Aşama 5: CP-SAT ve bağımsız atama doğrulayıcısı. Kabul: sıfır personelde atama yok, açık var; eksik yetkinlikte ihlal yok; mola ve gece yarısı doğru; timeout durumları açık. Onaydan sonra Aşama 6: yönetici onayı, plan hash ve denetim akışı. Aşama 7: kontrollü belge, indeks ve retrieval. Aşama 8: yerel kaynaklı cevap ve donanım yedekleri. Aşama 9: RAG karşılaştırması, uçtan uca sentetik demo, dışa aktarım ve temiz ortam teslim testleri. Her aşama sonunda gerçek test sonucu açıklanır ve kullanıcıdan sonraki aşama onayı beklenir.

### Teslim standardı

Geliştirici değişen dosyaları, uygulamanın çalıştırma komutlarını, test çıktılarını ve bilinen sınırlamaları raporlar. Çalıştırılmayan test başarılı sayılmaz. Proje sonunda tek komutla yerel uygulama açılmalı; seed ve parametrelerle demo tekrar üretilebilmelidir. Bu aktarım paketinin doğrulanması, gelecekte yazılacak uygulamanın test edildiği anlamına gelmez. Ayrıntılı geliştirme kuralları docs/DEVELOPMENT_RULES.md içindedir.

## 9 Tablo sözlüğü

### 1 facilities

Sağlık tesisi ve yerel saat dilimi. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 2 units

Servis ve senaryo türü. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 3 rooms

Servis içindeki oda. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 4 beds

Oda içindeki yatak. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 5 patients

Yalnızca sentetik hasta kimliği. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 6 encounters

Sentetik başvuru ve servis yatışı. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 7 acuity_assessments

Bakım gereksinimi değerlendirme geçmişi. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 8 staff

Sentetik personel ve görev rolü. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 9 skills

Yetkinlik sözlüğü. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 10 staff_skills

Personel yetkinliği ve geçerlilik aralığı. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 11 shift_templates

Yerel saatle vardiya şablonu. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 12 staff_shifts

UTC başlangıç bitiş ile gerçekleşecek vardiya. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 13 staff_unavailability

İzin mola veya diğer uygunsuzluk aralığı. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 14 task_types

İşlem türü ve değiştirilebilir sentetik süre. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 15 task_skill_requirements

İşlem türünün tümü gerekli yetkinlikleri. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 16 care_tasks

Planlanmış veya tamamlanan bakım işleri. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 17 workload_observations

Servis bazında 15 dakikalık gerçekleşen iş yükü. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 18 model_runs

Model sürümü eğitim sınırları ve test metrikleri. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 19 workload_forecasts

Tahmin anı ve ufku başına toplam personel dakikası. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 20 optimization_runs

Çözücü çalıştırması ve açıklanabilir sonuç. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 21 assignments

Bir çözüm içindeki iş personel ataması. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 22 staffing_gaps

Atanmayan iş ve açık personel dakikası. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

### 23 audit_events

Hassas veri içermeyen uygulama işlem izi. Ayrıntılı kolon ve ilişki tanımları src/medakis/repositories/schema.py içindedir.

## 10 RAG sistemi ve kabul planı

### Amaç ve değişmez sınır
RAG, sentetik prosedürler, personel görev tanımları, yetkinlik kuralları ve operasyonel iş akışlarından kaynaklı açıklama getirir. İş yükü tahmini ve CP-SAT ayrı deterministik servislerdir. RAG'nin SQL çalıştırma, görev atama, yetkinlik güncelleme, onay verme veya dosya/araç çalıştırma yetkisi yoktur. LLM açıklaması planın uygulanabilirlik kanıtı değildir. Yetkinlik hakkında belgedeki bir ifade, sürümlü yapılandırılmış kuralı değiştirmez. Çelişki görüldüğünde açıklama durur, çatışma ve kaynaklar gösterilir; yönetici manuel incelemeye yönlendirilir. Kural değişikliği ayrı kullanıcı işlemi, test ve denetim kaydı gerektirir.

### Modüller ve arayüzler
src/medakis/rag/ altında ingestion, chunking, embeddings, vector_store, retrieval, generation, citations, evaluation ve contracts modülleri bulunur. read-only operational_context servisi yalnız seçili çalışmanın doğrulanmış toplu sonuçlarını RAG'ye verir. ApprovalService ve OptimizationService rag paketini bağımlılık olarak almaz. RAG cevabı yalnız DTO döndürür: outcome, answer, citations, query_id, timings, warnings. Citation: source_key, source_name, section, version, valid_from, valid_until, chunk_id, alıntı aralığı. Bu DTO hiçbir atama komutuna dönüştürülmez.

### Kontrollü belge yaşam döngüsü
Yalnız izinli yerel corpus klasörü, sentetik işaretli Markdown/JSON belgeleri kabul edilir. İlk sürüm PDF, OCR, internetten otomatik tarama veya gerçek hastane yüklemesi içermez. Yönetici kaynak adını, source_key, tür, sürüm, geçerlilik başlangıcı/bitişi ve onayı girer. draft belgeler sorguya giremez. Approved sürümler değiştirilemez; düzeltme yeni sürümdür. Geçerlilik [valid_from,valid_until) UTC aralığıdır. Aynı source_key için örtüşen approved sürümler uygulamada reddedilir; çatışma varsa sorgu fail-closed olur. Sorgu varsayılanı bugündür, geçmiş sorgu as_of ile açıkça seçilir. Süresi dolmuş veya retired belge güncel cevapta kaynak olamaz; tarihsel sorguda da retired belge kullanılmaz. Mevcut eski approved sürüm kendi tarih aralığında tarihsel sorguda kullanılabilir.

### Parçalama ve indeks
Başlangıç ortak chunk ayarı yaklaşık 320 model tokenı, en fazla 40 token örtüşme; başlık/bölüm sınırlarını koru. Hafif 512 bağlamlı embedding yedeğinde başlık ve sorgu önekleri dahil limit aşılmasın. Kesin token sayısını ilgili tokenizer ile ölç; sessiz truncate yerine hata ve yeniden parçalama uygula. Metni ve metadata'yı SHA-256 ile izle. Ortak corpus/chunk snapshot iki embedding adayı için aynıdır; modelin önerdiği sorgu öneki ayrı config olarak kaydedilir. qwen için retrieval talimatı+query biçimi model kartından sürüm sabitlenerek uygulanır. BGE-M3'ün bu entegrasyonu yalnız dense embedding kullanır; sparse veya çok vektörlü arama uygulanmış sayılmaz.

Vektörler Chroma PersistentClient ile yerel diskte tutulur, PostgreSQL ana veritabanına kopyalanmaz. pgvector ileride ayrı bir deney olarak değerlendirilebilir. Model etiketi/digest, çıktı boyutu, cosine uzaklık metriği, corpus hash ve chunk config bir indeks sürümünün kimliğidir. Aynı boyut olsa bile farklı embedding modelleri aynı koleksiyona karıştırılmaz. Boyut API sonucundan doğrulanır. Embedding API /api/embed olur; truncate=false kullanılır. Chroma'nın otomatik varsayılan embedding indirmesi kapatılır; açıkça üretilen vektörler gönderilir.

PostgreSQL metadata ve Chroma arasında atomik transaction yoktur. Bu nedenle indeks building oluşturulur, vektörler idempotent kimliklerle upsert edilir, sayım/hash/eşleme kontrol edilir, ardından ready yayımlanır. Başarısız indeks sorgulanamaz. Sorgu tek bir ready snapshot'a bağlanır. Silme/retire sonrası SQL metadata kontrolü her sorguda tekrarlanır; eski Chroma satırı geçersiz kaynağı sızdıramaz. Yeniden oluşturma yeni koleksiyonla yapılır, eski hazır koleksiyon doğrulanmadan kapatılmaz. Yedek PostgreSQL+corpus+Chroma manifestiyle alınır; tutarsız vektör deposu corpus üzerinden yeniden kurulabilir.

### Retrieval ve kaynaklı cevap
Sorgu uzunluk sınırı, yalnız metin ve yerel kullanım doğrulanır. Onaylı/geçerli belge kimlikleri SQL'den bulunur, Chroma metadata filtresine yansıtılır ve dönen sonuçlar SQL'de yeniden doğrulanır. İlk sürüm cosine dense top-10 alır; yanıt bağlamına en fazla 5 uygun ve tekrarsız parça girer. Hatalı metadata, eksik bölüm, kaynak hash uyuşmazlığı veya geçersiz indeks durumunda cevap üretilmez. Benzerlik eşiği evrensel sabit değildir: model bazında yalnız dev kümesinde kalibre edilir, testte dondurulur. Yeterli kanıt yoksa 'Kontrollü kaynaklarda bu soruyu yanıtlayacak bilgi bulunamadı' denir. Sıfır sonuç durumunda LLM çağrılmaz.

Birbirine aykırı kaynaklar veya kural-belge çatışmasında outcome=conflicting_sources olur. LLM'ye belgelerin güvenilmeyen veri olduğu, içlerindeki talimatları izlememesi ve yalnız sağlanan kaynaklara dayanması sistem mesajıyla bildirilir. Araç çağrısı ve SQL aracı tanımlanmaz. İstek kaynak kimlikleriyle sınırlandırılır; cevapta her operasyonel iddia parça referansı taşır. Son işlem referansın gerçekten getirilen, halen geçerli parçaya ait olduğunu ve alıntının metinde bulunduğunu kontrol eder. Bu kontrol anlamsal sadakati kanıtlamaz; insan etiketli iddia incelemesi ayrıca yapılır. Geçersiz kaynaklı cevap yayımlanmaz, kaynak özeti veya kaçınma gösterilir.

Üretim bağlam bütçesi: 2048 tokenlık profilde sistem mesajı, soru, kaynak başlıkları ve parçaların toplamını üreticinin tokenizerı ile ölç; başlangıç giriş bütçesi en fazla 1536 tokendır, 256 çıktı tokenı ve emniyet payı ayır. En fazla 5 parça bir üst sınırdır, hepsinin sığacağı varsayılmaz. Düşük sıralı parçaları çıkarırken zorunlu kanıt kaybolursa bağlamı sessiz kesmek yerine kaynak özeti veya kaçınma kullan. 4096 bağlama geçiş yalnız bellek ölçümünden sonra yapılır.

### Donanıma göre seçim ve bellek bütçesi
Hedef Windows, i7-10750H, 16 GB RAM, GTX 1660 Ti 6 GB VRAM, yaklaşık 220 GB boş disk. Başlangıç embedding adayı Ollama qwen3-embedding:0.6b; karşılaştırma bge-m3; yerel cevap adayı gemma3:4b. Bunlar ölçülmüş kazananlar değildir. Resmi kayıtların 4 Kasım 2026 görünümünde model dosyaları sırasıyla yaklaşık 639 MB, 1,2 GB ve 3,4 GB'dir. Dosya boyutu çalışma RAM/VRAM ihtiyacı değildir; bağlam belleği, GPU sürücüsü, çalışma tamponları ve Windows ekran kullanımı eklenir. Gemma 4B etiketi o anda Q4_K_M gösterir; etiket değişebileceğinden digest sabitlenir. Hiçbiri bu kullanıcı cihazında çalıştırılmış değildir.

İlk profil: tek kullanıcı, eşzamanlı istek 1, embedding batch 1–8, yanıt num_ctx=2048, üst sınır 4096 yalnız ölçümden sonra, num_predict=256, temperature=0. İndeksleme çevrimdışı/sıralıdır. Sorguda embedding sonucu RAM'e alındıktan sonra embedding modeli keep_alive=0 ile boşaltılır; sonra üretici yüklenir. Ollama tek yüklenen model ve paralellik 1 hedefiyle başlatılır. BGE karşılaştırması Qwen ile aynı anda çalışmaz. Random Forest eğitimi, indeksleme ve üretim eşzamanlı yapılmaz; başlangıçta RF n_jobs=2 ve CPU thread üst sınırı 4 ölçülür. VRAM veya RAM yetmezse batch/bağlam azaltılır; retry en fazla bir kez, ardından CPU profili veya üretimsiz kaynak gösterimi. Tam GPU yerleşimi ya da sabit yanıt süresi vaat edilmez.

CPU yedeği: ayrı Ollama oturumunu CUDA_VISIBLE_DEVICES=-1 ile başlat, Windows tepsi uygulamasının önce kapandığından emin ol; aynı portta ikinci sunucu açma. ollama ps ve nvidia-smi ile gerçek CPU/GPU yerleşimini doğrula. Hafif embedding alternatifi paraphrase-multilingual:278m (katalogda yaklaşık 563 MB, kısa bağlam); hafif üretici gemma3:1b (yaklaşık 806 MB). Türkçe kalite düşüşü ölçülmeden otomatik sessiz değişim yoktur. Embedding değişirse yeni koleksiyon ve tam yeniden indeks zorunlu; üretici değişimi sorgu kaydında görünür. En güvenli kaynak kısıtlı kip LLM olmadan ilgili kaynak parçalarını gösterir. Bu kip retrieval-only olarak etiketlenir.

220 GB'nin tamamı kullanılmaz: başlangıç corpus+indeks kotası 5 GB, model/indirilen alternatifler 15 GB plan bütçesi, en az 30 GB serbest disk koruma eşiği önerilir. Bunlar tasarım bütçeleridir; gerçek boyut raporlanır. Donanım başlangıç kontrolü RAM, VRAM, boş disk, Ollama sürümü ve sürücüyü kaydeder. OOM, zaman aşımı, Ollama kapalı ve Chroma bozuk durumları ana tahmin/optimizasyon uygulamasını durdurmamalıdır.

### Değerlendirme veri seti
İlk mini corpus ve 12 soru yalnız smoke test fikstürüdür; model başarısı kanıtı değildir. Kabul için en az 80 bağımsız Türkçe soru hazırlanır: 60 cevaplanabilir, 20 kaynak dışı/kaçınma; dört belge türünün her birinden en az 10 cevaplanabilir soru. Bunun dışında en az 10 injection/çatışma/sona ermiş sürüm güvenlik vakası tutulur. Yaklaşık yüzde 30 dev, yüzde 70 test; aynı sorunun paraphrase'leri aynı splitte tutulur. Test soruları eşik/parametre ayarında kullanılmaz. Gold ilgili chunk kimlikleri, beklenen iddialar ve as_of insan tarafından etiketlenip dondurulur. Çok bölümlü soruların bütün ilgili parçaları işaretlenir. Corpus değişince set sürümü ve gold eşlemeleri gözden geçirilir.

### Metrikler ve hesaplama
Recall@5(q)=ilk 5 tekrarsız sonuçta bulunan gold parçaların sayısı / bütün gold parçaların sayısı. MRR@10(q)=ilk ilgili parçanın sırası r<=10 ise 1/r, yoksa 0. Makro ortalama yalnız answerable=1 ve gold kümesi boş olmayan sorularda hesaplanır. Kaynaksız sorular bu iki metriğe sıfır eklenmez; ayrı kaçınma başarısına girer. Geçersiz/filtrelenmiş parçalar başarı sayılmaz. Citation accuracy=iddiasını gerçekten destekleyen ve geçerli metadata taşıyan referansların / bütün verilen referansların oranı. Referanssız cevap bu oranda NA olur; ayrıca citation coverage zorunlu iddiaların ne kadarının kaynaklandığını ölçer. Faithfulness=insan incelemesinde getirilen metinle desteklenen atomik olgusal iddialar / bütün atomik olgusal iddialar. Hiç iddia yoksa NA; kaçınma doğruluğu ayrı ölçülür. LLM hakem tek başına nihai karar vermez.

Kaçınma recall'u=doğru kaçınılan kaynak dışı sorular / tüm kaynak dışı sorular; cevaplanabilir sorulardaki yanlış kaçınma oranı ayrıca raporlanır. Süre perf_counter ile sorgu girişinden tamamlanmış/kaçınılmış yanıta kadar duvar saati, model yükleme dahil ölçülür. Retrieval, generation, toplam ms, soğuk/sıcak koşu, hata oranı, RAM/VRAM tepe kullanımı raporlanır. Aynı corpus, aynı sorular ve aynı donanımda Qwen ve BGE sırayla ölçülür; üretici sabit kalır. En az 3 soğuk yükleme ve 30 sıcak sorgu ile p50/p95 raporlanır. Deney sıra etkisi için A/B sırası dönüşümlü yapılır.

### Kabul kriterleri ve karar
Başlangıç proje hedefleri (ölçülmüş sonuç değil): test Recall@5>=0,80; MRR@10>=0,75; citation accuracy>=0,95; citation coverage>=0,95; faithfulness>=0,90; kaynak dışı sorularda kaçınma>=0,95 ve yanlış kaçınma<=0,15. Metadata/sona ermiş kaynak testleri ve kaynak yokken LLM çağrılmaması yüzde 100 geçmelidir. Deterministik atama kuralları, yönetici onayı ve denetim kaydı hiçbir injection testinde değişmemelidir. Hedef gecikme sıcak retrieval p95<=5 s, sıcak uçtan uca p95<=30 s; CPU için p95<=90 s ilk kabul hedefidir. Soğuk yükleme ayrıca raporlanır; timeout 120 s sonrası açıklamalı unavailable/retrieval-only. Bu hedefler cihazda ölçülmeden sağlandı denmez. Sağlanmazsa üreten kip kabul edilmez; retrieval-only kip açık sınırlamalarla ayrıca değerlendirilebilir. İki embedding adayı bu kapılara göre karşılaştırılır; fark küçükse daha az bellek ve süre kullanan aday tercih edilir.

### Geliştirme ve test aşamaları
Mevcut aşamalar 1–5 korunur: iskelet, veri, iş yükü, tahmin, CP-SAT. Aşama 6 plan snapshot hash'i, yönetici onayı ve audit izi; Aşama 7 kontrollü corpus ve RAG indeksleme/retrieval; Aşama 8 yerel kaynaklı cevap ve donanım profilleri; Aşama 9 kör test, karşılaştırma ve uçtan uca teslim. Her aşamanın sonunda test kanıtı ve kullanıcı onayı gerekir. Kullanıcı daha önce aşama 1'i tamamladıysa tekrar üretmek yerine durumu incele ve ilk eksik aşamada dur.

Testler: indeksleme idempotency, içerik hash'i, metadata zorunluluğu, sürüm çakışması, geçerlilik sınır anı, draft/retired filtreleme, model/boyut uyumsuzluğu, SQL-Chroma yarım yazım ve eski vektör temizliği; Recall/MRR elle hesaplama; kaynak yok, zayıf benzerlik, çelişki, sahte citation; prompt injection; Ollama kapalı/timeout/OOM/CPU; seçili plan değiştirmeden açıklama; onay hash'i değişince geçersizleşme; gerçek kullanıcı işlemi olmadan approved olmama; reddetme/iptal ve audit tutarlılığı. Birim testleri sahte sağlayıcıyla ağsız, entegrasyon testleri yerel gerçek Ollama/Chroma ile ayrı işaretlenir. Sahte sağlayıcı testi gerçek model kalitesini kanıtlamaz.


## 11 SQL genişlemesi ve yönetici onayı

### Korunan ve eklenen yapı
v1'in 23 tablosu, kolonları, kısıtları ve indeksleri aynen korunmuştur. v2, 11 tablo ekleyerek toplam 34 iş tablosuna çıkar. 23 sayısı tasarım kısıtı değildir. RAG vektörleri ayrı yerel Chroma deposundadır; PostgreSQL belge doğruluğu, sürüm, sorgu ve değerlendirme izlerinin otoritesidir.

- plan_approvals: yönetici kararını run, plan hash'i ve zamanıyla kaydeder. Önceki audit_events tek başına onay durumu için yeterli yapılandırılmış veri sağlamıyordu.
- rag_documents: kaynak adı/türü, sürüm, geçerlilik, sentetiklik, yönetici onayı ve içerik hash'i.
- rag_chunks: bölüm adı, parça metni, sıra, token sayısı ve hash.
- rag_indexes: koleksiyon, embedding digest/boyutu, corpus hash ve yayımlama durumu.
- rag_index_chunks: SQL parçası ile Chroma vektör kimliği ve indeks snapshot eşlemesi.
- rag_queries: soru, sorgu tarihi/as_of, cevap/kaçınma, üretici ve süreler.
- rag_retrieval_hits: sıralama, cosine uzaklık, kullanılan citation işareti.
- rag_eval_sets: dondurulmuş gold set sürümü.
- rag_eval_cases: soru, dev/test ayrımı, gold parçalar ve beklenen iddialar.
- rag_eval_runs: indeks ve sabit deney yapılandırması ile toplu metrikler.
- rag_eval_results: soru bazında Recall, RR, kaynak/sadakat puanları ve insan incelemesi.

### SQL dışında uygulanacak kurallar
JSON kolonları uygulamada şemaya göre doğrulanır; SQL JSON1 eklentisi zorunlu değildir. Gold kimliklerinin ilgili snapshot'ta varlığı, eval_result set/run tutarlılığı, hit chunk'ın query indeksinde bulunması, tüm kaynak metadata'sı, UTC biçimi, hash'in gerçek içerikle eşleşmesi ve aynı source_key'in geçerlilik çakışması servis katmanında kontrol edilir. FK ve CHECK bunların tamamını garanti etmez. Approved belge içeriği uygulamada immutable olur. Chroma ile dağıtık atomik commit yoktur; building/ready protokolü gerekir.

Yönetici onayı: görev kimliği, personel kimliği, başlangıç/bitiş, run config ve çözüm sürümü sıralı canonical JSON'a dönüştürülür, SHA-256 alınır. Karar aynı hash'e bağlıdır. En son decided_at,id kararı approved olmalı ve güncel hash aynı kalmalıdır. Değişiklik, rejected veya revoked kararı planı yeniden inceleme gerektirir. Alternatif run'ların planı birleştirilmez. Onay ve audit_events yazımı aynı transaction içindedir; kimlik gerçek UI oturumundan alınır, RAG metninden alınmaz. Yerel prototipte aktör adı doğrulanmış kurumsal kimlik değildir; üretim kimlik doğrulaması kapsam dışıdır.

### Kurulum ve migration
Veritabanı PostgreSQL 18'dir ve Docker Compose ile yalnız 127.0.0.1 üzerinde çalışır. Şema SQLAlchemy Core ile src/medakis/repositories/schema.py içinde tanımlanır; kurulum ve değişiklik Alembic ile yapılır (migrations_pg/, başlangıç revizyonu 0001_v2_baseline). Migration yalnız medakis_owner rolüyle ve açık hedef veritabanıyla çalışır; gerçek veritabanında önce yedek alınır. Geri alma alembic downgrade veya scripts/db/restore.ps1 ile yapılır. medakis_schema.sql ve migrations/001_to_002.sql SQLite v2 tarihsel referansı olarak korunur; çalışma zamanında kullanılmaz.

verify_schema.py SQLite referansının temiz kurulum ve v1→v2 migration'ını bellek içinde çalıştırır; PostgreSQL kısıtları tests/test_pg_schema_live.py ile gerçek sunucuda sınanır. SQL testleri uygulama katmanının RAG/CP-SAT güvenliğini test etmiş sayılmaz.

## 12 Teknik kaynaklar

### Kaynaklar ve doğrulama sınırı
Resmi teknik kaynaklar 4 Kasım 2026 tarihinde kontrol edildi. Model dosya boyutları ve API bilgisi bu sayfalara dayanır; donanım profilleri bu projeye ait ölçülmesi gereken mühendislik önerileridir.
- https://ollama.com/library/qwen3-embedding
- https://ollama.com/library/bge-m3
- https://ollama.com/library/gemma3:4b
- https://ollama.com/library/gemma3:1b
- https://ollama.com/library/paraphrase-multilingual
- https://docs.ollama.com/api/embed
- https://docs.ollama.com/faq
- https://docs.ollama.com/gpu
- https://docs.trychroma.com/docs/collections/configure
