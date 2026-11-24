# Altyapı görevi: medakis_dev ve medakis_demo için güvenli geri yükleme

- Durum: **Ertelendi** (FAZ 1.2C kapanış kararı, 8 Kasım 2026). FAZ 1.2C kapsamında değildir.
- Bugünkü davranış:
  - `scripts/db/restore.ps1`, `medakis_dev` ve `medakis_demo` için ilk komut olarak 3 koduyla reddeder.
  - Bu reddin öncesinde hiçbir dosya kontrolü yapılmaz, güvenlik yedeği alınmaz, docker veya veritabanı komutu çalışmaz.
  - `medakis_test` geri yüklemesi çalışmaya devam eder.
- Kaynak bulgular:
  - `docs/reviews/FAZ_1_2C_TAKIP_INCELEMESI_3cec2bd.md`, B1 ve B2 (bağımsız inceleme dalı).
  - `docs/raporlar/FAZ_1_2C_IKINCI_INCELEME_YANITI.md`, R1.

## Çözülmesi gereken sorunlar

1. **B1: Hedef ve arşiv bağlanmalı.**
   - Doğrulama, güvenlik yedeği ve geri yükleme aynı doğrulanmış sunucu ve veritabanı kimliğine bağlı olmalı (`system_identifier:oid`).
   - Bugün `check_dump.py` hedef sürümünü uygulamanın host/portundan okuyor; geri yükleme ise Compose sunucusuna yapılıyor.
   - Arşiv bir kez benzersiz bir yola alınmalı. Özet ve içerik denetimi o kopya üzerinde yapılmalı, geri yüklemede aynı kopya kullanılmalı. Bugün dosya iki kez kopyalanıyor ve arada bir TOCTOU açıklığı var.
2. **B2: Etkin yetkiler doğrulanmalı.**
   - Arşivdeki SQL metnini taramak yeterli değil. Sütun düzeyi yetkiler (`GRANT UPDATE (col) ...`) ve `PUBLIC` üzerinden verilen yetkiler bugünkü taramadan kaçıyor.
   - Seçenekler:
     - Arşiv ACL'lerini korunan hedefe hiç taşımamak (`pg_restore --no-acl`); yetkileri hedefte, depodaki politikadan (Alembic) yeniden kurmak.
     - Arşivi önce izole bir hedefte açıp gerçek etkin tablo, sütun ve sayaç yetkilerini doğrulamak.
3. **Testler:**
   - Hedef kimliği uyuşmazlığı.
   - Orijinal dosyanın denetimden sonra değiştirilmesi.
   - Sütun yetkisi ve `PUBLIC` yetkisi içeren gerçek dump.
   - Katı dev politikasıyla izole hedefe tam veri geri yükleme provası.

## Kabul ölçütü

- Yukarıdaki dört test grubu gerçek PostgreSQL'de ve izole prova kümesinde geçmeli.
- Dev/demo geri yükleme reddi ancak bu görevin ayrı onayıyla kaldırılmalı.
