# MedAkis teslim durumu (BTK teslimi, 24.11.2026)

Bu belge, teslim edilen kodda **gerçekten çalışan** bileşenleri ve henüz uygulanmamış bileşenleri ayırır. Çalışmayan özellik çalışıyormuş gibi gösterilmez.

**Kategoriler:**
- **Çalışıyor ve doğrulandı:** otomatik testleri geçer, arayüzde ya da komutla çalıştırıldı.
- **Çalışan prototip:** çalışır ama sınırlı ya da deneysel.
- **Geliştirme aşamasında:** kısmen var, kullanıcıya açık değil.
- **Henüz uygulanmadı.**

## Şartname aşamaları (MedAkis_Proje_Sartnamesi.md §8)

| Aşama | Kapsam | Durum |
|---|---|---|
| 1 | Klasör yapısı, kilitli bağımlılıklar, Türkçe Streamlit ekranı | **Çalışıyor ve doğrulandı** |
| 2 | Şema (34 tablo), iş kuralları, deterministik sentetik veri | **Çalışıyor ve doğrulandı**. PostgreSQL ile çalışan testler bu teslimde koşulmadı |
| 3 | Görev, kapasite, gözlem hesapları; servis paneli | **Çalışıyor ve doğrulandı** (veritabanlı mod) |
| 4 | İş yükü tahmini: baseline, Random Forest, zamansal değerlendirme (30/60 dk) | **Çalışıyor ve doğrulandı** (makine öğrenmesi) |
| 5 | CP-SAT personel optimizasyonu ve bağımsız atama doğrulayıcısı | **Çalışıyor ve doğrulandı** (OR-Tools CP-SAT) |
| 6 | Yönetici onayı, plan SHA-256 özeti, özet zincirli denetim izi, dışa aktarım | **Çalışıyor ve doğrulandı** (Sentetik Demo modunda) |
| 7 | Kontrollü belge corpus'u, BGE-M3 indeks, kaynak doğrulamalı retrieval | **Çalışan prototip.** Normal kullanıcıya kapalı; yalnız geliştirme/test kipi |
| 8 | Yerel kaynaklı cevap (Gemma 3 4B), reranker kapıları (LAMAR, ModernBERT-TR), donanım profilleri | **Geliştirme aşamasında.** Deneyler tamamlandı; **A6 kabul kriteri KARŞILANMADI**. Normal RAG kapalı |
| 9 | Kör test, RAG karşılaştırması, uçtan uca teslim testi | **Henüz uygulanmadı** (kör test kullanılmadı) |

## Öncelikli özellikler (teslim gecesi değerlendirmesi)

| Özellik | Durum | Not |
|---|---|---|
| Kullanıcı arayüzü (Streamlit) | **Çalışıyor ve doğrulandı** | Komuta merkezi (`dashboard.py`): 9 sayfa, koyu tema. Klasik inceleme ekranı (`app.py`) korunur |
| Makine öğrenmesi ile iş yükü tahmini | **Çalışıyor ve doğrulandı** | Random Forest; sızıntı testleri |
| CP-SAT ile personel optimizasyonu | **Çalışıyor ve doğrulandı** | Rol, yetkinlik, vardiya, mola, eşzamanlı atama kısıtları; bağımsız doğrulayıcı |
| İnsan onaylı öneri akışı | **Çalışıyor ve doğrulandı** | Sistem hiçbir planı uygulamaz; onay izlenebilir kayıttır |
| Denetim kayıtları | **Çalışıyor ve doğrulandı** | Özet zincirli, yalnız ekleme yapılan iz (tek yazıcılı prototip) |
| Telegram bildirim altyapısı | **Çalışan prototip** (bu teslimde eklendi) | Varsayılan **dry-run**; yalnız geçerli onaylı öneri için; tekrar engeli; başarısızlık ayrı kaydedilir; token yalnız ortam değişkeninden. Canlı gönderim test edilmedi (token yenilenmedi) |
| Kaynaklı RAG altyapısı | **Çalışan prototip / geliştirme** | Arayüzde: BM25 anahtar kelime kaynak araması (belge, sürüm, birebir alıntı; kaynak yoksa kaçınır; embedding/LLM yok). BGE-M3 + reranker + Gemma hattı geliştirme kipinde; A6 karşılanmadı, normal kullanıcıya kapalı |
| Kamera tabanlı yoğunluk analizi | **Çalışan prototip** | Programatik sentetik kamera simülasyonu (5 alan, hareketli anonim figürler, senaryolar, başlat/durdur); sentetik karede renk tabanlı sayım. Gerçek kamera ve gerçek insan tespiti YOK |
| Hizmet alanı ısı haritası | **Çalışıyor ve doğrulandı** | Kat planı üzerinde SVG ısı haritası (yeşil/sarı/kırmızı), simülasyondan canlı |
| Yoğunluğa duyarlı personel optimizasyonu | **Çalışan prototip** | CP-SAT geçici görevlendirme modeli + bağımsız doğrulayıcı (rol/yetkinlik, vardiya, aktif görev, asgari kapasite, tek atama). Sentetik kadro. Yoğunluk tek başına gerekçe değil |
| Yönetici ile etkileşen AI Agent | **Çalışan prototip** | Kural tabanlı araç yönlendirmeli asistan (dil modeli DEĞİL): yoğunluk, personel, tahmin projeksiyonu, optimizasyon, kaynak arama, öneri oluşturma, onay durumu. Onay veremez. MCP entegrasyonu yok |

## Doğrulama (teslim gecesi)

- Kabul senaryosu tarayıcıda çalıştırıldı: simülasyon başlatıldı → acil servis kritik → ısı haritası kırmızı → CP-SAT önerisi (doğrulayıcı: ihlal yok) → öneri onaya gönderildi → onaylandı → dry-run bildirim hazırlandı. Asistan ve kaynak araması `tests/test_urun_prototipi.py` ile doğrulandı.

- Uygulama Sentetik Demo modunda başlatıldı. Tarayıcıda ana ekran, sekmeler ve inceleme senaryosu yüklemesi doğrulandı.
- Öneri → onay → dry-run bildirim → bildirim izi akışı servis düzeyinde uçtan uca testle doğrulandı (`tests/test_notifications.py`).
- Test sonuçları: README "Test sonuçları" bölümü.
