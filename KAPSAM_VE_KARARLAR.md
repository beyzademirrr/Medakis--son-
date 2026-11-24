# Kapsamın kaynağı ve yeniden oluşturma kararları

Kaynak: Bilgi Doğrulama Analizi, konuşma kimliği 6aea3b59-c748-83ed-9b2c-14330139fa4d. Erişilebilen son 5 tur 4 Kasım 2026 tarihinde okundu. Daha eski tur imleci sunulmadı; eski dosyalar alınmadı ve bağlantıları kullanılmadı.

## Geçmişte açıkça bulunan kararlar
Python, Streamlit, SQLite, scikit-learn, Google OR-Tools CP-SAT; modüler monolit; ana senaryo yataklı servis gece vardiyası hemşire iş yükü; acil ve ayaktan tedavide sentetik senaryolar; bakım ve işlem süreleriyle iş yükü; 30/60 dakika tahmin; basit model ile Random Forest karşılaştırması; zamana dayalı ayrım, MAE ve RMSE; yetkinlik, vardiya ve uygunluk zorunlulukları; açık varsa ihlalli atama yerine açık gösterimi; gerçek hasta verisi kullanmama; idari karar destek prototipi; aşama sonunda kullanıcı onayı. DOCX, 23 tabloluk SQL, geliştirme sözleşmesi ve README teslimatı.

## Bu pakette yeni tanımlanan uygulama kararları
Tablo adları ve kolonlar eski SQL'in birebir kopyası değildir. Yeni 23 tabloluk ilişkisel tasarım, 15 dakikalık gözlem aralığı, UTC depolama, dakika bazlı optimizasyon, görev bazlı atama, sentetik üretim sözleşmesi, ekranlar, test senaryoları ve klasör yapısı kapsamı uygulanabilir kılmak için yeniden tasarlanmıştır. Bunlar eski konuşmada onaylanmış ayrıntılar gibi sunulmamalıdır. Klinik eşik, oran veya mevzuat uygunluğu iddiası yoktur.

Bu paket geliştirme girdisidir. Çalışan MedAkis uygulaması, eğitilmiş model veya klinik olarak doğrulanmış bir ürün içermez. requirements dosyası yerine kurulum hedefleri belirtilmiştir; uyumlu bağımlılık sürümleri ilk aşamada çözülüp kilitlenmelidir.

## v2 yeni kullanıcı gereksinimleri
4 Kasım 2026 tarihli bu sohbette RAG modülü, kontrollü sentetik corpus, Ollama Qwen3 0.6B/BGE-M3/Gemma 3 4B adayları, Chroma, Recall@5/MRR@10, kaynak/sadakat/süre ölçümü, sürüm ve geçerlilik metadata'sı, kaynak yoksa kaçınma, RAG'nin deterministik kuralları değiştirememesi ve belirtilen donanım için CPU/hafif kip açıkça istendi. Yönetici onayı ve denetim korunacak. 23 tablo sayısını korumak zorunlu değil.
Bu v2 paketi önceki v1'in yerini alır. İlk 23 tablo aynen korunarak RAG ve yapılandırılmış onay için 11 ek tablo tasarlanmıştır. Başlangıç profil ve metrik eşikleri uygulama kararıdır, kullanıcı cihazında ölçülmüş sonuç değildir.

## Ad değişikliği (5 Kasım 2026)
Proje sahibi ürünün resmî ve tek adını MedAkis olarak belirledi; önceki çalışma adı kullanılmaz (ayrıntı: docs/raporlar/MEDAKIS_YENIDEN_ADLANDIRMA_RAPORU.md). GitHub deposu beyzademirrr/MedAkis'tir. Python paketi `medakis` olarak yeniden adlandırıldı; şartname ve şema dosyaları MedAkis adlarıyla yeniden adlandırıldı. Veritabanı tablo/kolon adları, kısıtlar, migration ve user_version değişmedi. Paket teslimine ait özgün kayıtlar docs/paket_v2/ altında ve git geçmişinde değiştirilmeden korunur.

## Veritabanı ve RAG akışı kararı (5 Kasım 2026)
Proje sahibi SQLite yerine PostgreSQL 18 + Docker Compose kullanılmasına, SQLAlchemy 2 Core + Alembic + psycopg 3 ile çalışılmasına ve parolaların Windows Credential Manager'da tutulmasına karar verdi. RAG akışı Aşama 7-8'de LangGraph ile koordine edilecek; ChromaDB korunur, pgvector ileride değerlendirilir. Yukarıdaki "Geçmişte açıkça bulunan kararlar" bölümündeki SQLite ifadesi o tarihteki kararı anlatır. SQLite şeması ve migration kaydı tarihsel referans olarak silinmez.
