# Claude Code başlangıç talimatı v2

Tarihsel not: Bu metin ilk paket teslimine aittir. Veritabanı artık PostgreSQL 18'dir; güncel kurallar docs/DEVELOPMENT_RULES.md içindedir.

ZIP'i çıkar, proje klasörünü (paket teslimindeki adı: FLOWCARE_AI_RAG_v2) Claude Code'da aç ve aşağıdaki mesajı ver.

```text
MedAkis projesini bu paketin v2 gereksinimleriyle geliştir.
Önce CLAUDE.md, README.md, KAPSAM_VE_KARARLAR.md, şartnamenin .md kopyası,
RAG_MIMARISI_VE_DEGERLENDIRME.md ve SEMA_DEGISIKLIKLERI.md dosyalarını oku.
medakis_schema.sql ve verify_schema.py dosyalarını incele; doğrulayıcıyı çalıştır.

Mevcut uygulama varsa durumunu incele, üzerine körlemesine yazma.
Yeni projede yalnız Aşama 1'i uygula: Python ortamı, kilitli bağımlılıklar,
modüler klasörler ve açılan Türkçe Streamlit ekranı. RAG klasörleri ve sağlayıcı
arayüz taslaklarını dahil et; henüz model indirme, indeksleme, tahmin ve
optimizasyon uygulama. İş verisi için SQLite, vektörler için yerel Chroma,
embedding için Ollama Qwen3 0.6B ve BGE-M3 karşılaştırması, yanıt için Gemma 3 4B
adayını planla. Donanım 16 GB RAM / GTX 1660 Ti 6 GB; tek model sıralı kullanım,
CPU ve hafif kip zorunlu. RAG atama veya yönetici onayı veremez.

README'yi gerçek uygulama komutlarıyla güncelle, uygulamanın açıldığını test et,
gerçek test sonuçlarını ve sınırlamaları raporla. Kullanıcı onayı olmadan
sonraki aşamaya geçme. Yalnız sentetik veri ve kontrollü sentetik belgeler kullan.
```

Bu paket uygulama veya yüklenmiş model içermez. Eğer önceki aşamalar tamamlandıysa Claude Code ilk eksik aşamayı ve somut değişiklikleri raporlayıp o aşamada çalışmalıdır. Mevcut DB için yedek ve migration gereklidir; DB silme yetkisi verilmiş değildir.
