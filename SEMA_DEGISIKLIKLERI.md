# Şema değişiklik gerekçeleri ve yükseltme

Güncel veritabanı PostgreSQL 18'dir; şema src/medakis/repositories/schema.py ve migrations_pg/ içindedir. Bu belgenin "Kurulum ve migration" bölümü SQLite v2 referansını tarihsel kayıt olarak anlatır; PostgreSQL eşlemesi en alttaki bölümdedir.

## Korunan ve eklenen yapı
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

## SQL dışında uygulanacak kurallar
JSON kolonları uygulamada şemaya göre doğrulanır; SQL JSON1 eklentisi zorunlu değildir. Gold kimliklerinin ilgili snapshot'ta varlığı, eval_result set/run tutarlılığı, hit chunk'ın query indeksinde bulunması, tüm kaynak metadata'sı, UTC biçimi, hash'in gerçek içerikle eşleşmesi ve aynı source_key'in geçerlilik çakışması servis katmanında kontrol edilir. FK ve CHECK bunların tamamını garanti etmez. Approved belge içeriği uygulamada immutable olur. Chroma ile dağıtık atomik commit yoktur; building/ready protokolü gerekir.

Yönetici onayı: görev kimliği, personel kimliği, başlangıç/bitiş, run config ve çözüm sürümü sıralı canonical JSON'a dönüştürülür, SHA-256 alınır. Karar aynı hash'e bağlıdır. En son decided_at,id kararı approved olmalı ve güncel hash aynı kalmalıdır. Değişiklik, rejected veya revoked kararı planı yeniden inceleme gerektirir. Alternatif run'ların planı birleştirilmez. Onay ve audit_events yazımı aynı transaction içindedir; kimlik gerçek UI oturumundan alınır, RAG metninden alınmaz. Yerel prototipte aktör adı doğrulanmış kurumsal kimlik değildir; üretim kimlik doğrulaması kapsam dışıdır.

## Kurulum ve migration (SQLite v2 referansı, tarihsel)
Yeni boş DB: medakis_schema.sql; user_version=2. Mevcut v1 DB: önce uygulamayı kapat, dosyayı SQLite backup API ile yedekle, PRAGMA user_version=1 olduğunu doğrula, migrations/001_to_002.sql dosyasını tek bağlantıda uygula. Başka sürümde veya ikinci kez çalıştırma. Hata halinde rollback, migration uygulanmış gibi sürüm yükseltme. user_version kontrolünü migration runner yapar; SQL dosyası tek başına sürüm önkoşulunu kontrol etmez. Veri silme veya yeniden kurulum gerekmez. PRAGMA foreign_key_check ve integrity_check sonrasında doğrulanır. v2'den downgrade otomatik desteklenmez; geri dönüş yedekten yapılır.

verify_schema.py temiz kurulum ve v1→v2 migration'ı bellek içinde çalıştırır, önceki 23 tablo DDL ve satırlarının korunmasını kontrol eder. SQL testleri uygulama katmanının RAG/CP-SAT güvenliğini test etmiş sayılmaz.

## PostgreSQL eşlemesi (FAZ 1.2B)
Tablo, kolon, anahtar ve indeks adları SQLite v2 ile aynıdır (tests/test_pg_schema_static.py). Tip eşlemesi: zaman TIMESTAMPTZ, yerel vardiya saati TIME, bayrak BOOLEAN, *_json JSONB, REAL DOUBLE PRECISION, id BIGINT IDENTITY. Referansa eklenen güvenceler: her ondalık kolonda sonlu kısıtı, SHA-256 kolonlarında 64 onaltılık karakter biçimi, SYN/STAFF kod biçimi ve dört EXCLUDE kısıtı. Sürüm izleme user_version yerine alembic_version tablosundadır.
Kurulum: `.\.venv\Scripts\python.exe -m alembic -x db=medakis_dev upgrade head`. Geri alma: `downgrade`; gerçek veride önce `scripts\db\backup.ps1`.
