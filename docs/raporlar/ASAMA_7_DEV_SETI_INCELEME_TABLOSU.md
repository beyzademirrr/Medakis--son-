# Aşama 7 dev kümesi inceleme tablosu (taslak, denetimden sonra)

- Kaynak: `evaluation/asama7/dev.jsonl` + `dev_ek.jsonl` = **60 dev vakası** (30 cevaplanabilir, 30 kaynak dışı).
- Test verisi bu tabloya girmez. Gold alıntısı, belgedeki karakter aralığının birebir metnidir.
- "Denetim" sütunu bağımsız etiket denetiminin sonucudur (bkz. ASAMA_7_TEKNIK_KARAR_RAPORU.md §1). "İnceleme" sütunu proje sahibi içindir.

## Cevaplanabilir vakalar

| Vaka | Grup | Soru | Gold belge | Bölüm | Aralık | Gold alıntı | Denetim | İnceleme |
|---|---|---|---|---|---|---|---|---|
| C001 | C001 | Vardiya devri sırasında sistem bir planı uygular mı? | `SYN-PRC-DEVIR@1.0` | Amaç | 361–441 | Devir, sistemin bir planı uyguladığı an değildir; sistem hiçbir planı uygulamaz. | eksik_gold: SORUMLU/Sınırlar da sistemin planı uygulamadığını söylüyor | |
|  |  | ↳ ek gold | `SYN-ROL-SORUMLU@1.0` | Sınırlar | 542–603 | Onay yalnız seçilen planı kayıt altına alır; planı uygulamaz. |  | |
| C003 | C003 | Bir görevin tahmini süresi nasıl hesaplanır? | `SYN-PRC-DEVIR@1.0` | Bekleyen görevler | 566–697 | Görevin tahmini süresi, görev türünün temel süresi ile hastanın görev oluşturulduğu anda bilinen son bakım katsayısının çarpımıdır. | uygun | |
| C006 | C006 | Deneysel mod sonucu öneri olarak kaydedilebilir mi? | `SYN-PRC-ONAY@1.0` | Öneri kaydı | 294–358 | Deneysel mod sonucu öneri olarak gösterilemez ve kayda alınamaz. | uygun | |
|  |  | ↳ ek gold | `SYN-AKS-KADRO@1.0` | Varsayılan politika | 617–665 | Deneysel mod sonuçları öneri olarak gösterilmez. |  | |
| C009 | C009 | Onay veren kişinin kimliği nereden alınır? | `SYN-PRC-ONAY@1.0` | İnceleme ve onay | 505–581 | Kimlik serbest metinden değil, kullanıcı arayüzünde açılan oturumdan alınır. | uygun | |
| C013 | C013 | Hangi sonuçlar JSON veya CSV olarak dışa aktarılabilir? | `SYN-PRC-DISA@1.0` | Kapsam | 169–234 | Yalnız kayıtlı öneriler JSON ya da CSV olarak dışa aktarılabilir. | uygun | |
| C016 | C016 | Hemşirenin rolü hangi kaynaktan okunur? | `SYN-ROL-HEMSIRE@1.0` | Rol | 214–301 | Rol, personel kaydındaki yapılandırılmış rol alanından okunur; belge metninden okunmaz. | uygun | |
| C021 | C021 | Vardiya sorumlusu hangi bilgileri inceler? | `SYN-ROL-SORUMLU@1.0` | Rol | 168–235 | Vardiya sorumlusu servis iş yükünü, kapasiteyi ve açıkları inceler. | HATA: incelenen bilgilerin asıl listesi İnceleme bölümünde; gold yalnız Rol cümlesiydi | |
|  |  | ↳ ek gold | `SYN-ROL-SORUMLU@1.0` | İnceleme | 302–403 | Sorumlu, öneriyi onaylamadan önce ihtiyaç ve kapasite tablosunu, atanmayan işleri ve rezervi inceler. |  | |
| C024 | C024 | İhtiyaç ve kapasite tablosu ekrandan değiştirilebilir mi? | `SYN-ROL-SORUMLU@1.0` | İnceleme | 404–466 | Bu bilgiler salt okunur gösterilir ve ekrandan değiştirilemez. | uygun | |
| C026 | C026 | Destek personeli hangi görevlere aday olabilir? | `SYN-ROL-DESTEK@1.0` | Rol | 167–236 | Destek personeli yalnız destek rolü gerektiren görevler için adaydır. | eksik_gold: adaylığın sınırı (hemşire görevine atanmaz) Görev sınırı bölümünde | |
|  |  | ↳ ek gold | `SYN-ROL-DESTEK@1.0` | Görev sınırı | 255–311 | Destek personeli hemşire rolü gerektiren göreve atanmaz. |  | |
| C031 | C031 | Yetkinliğin göreve göre ne kadar süre geçerli olması gerekir? | `SYN-YET-GECERLILIK@2.0` | Geçerlilik | 175–240 | Yetkinlik, atanan görevin bütün süresi boyunca geçerli olmalıdır. | eksik_gold: aynı kural hemşire adaylığı bölümünde de yazılı | |
|  |  | ↳ ek gold | `SYN-ROL-HEMSIRE@1.0` | Görev adaylığı | 532–618 | görevin gerektirdiği bütün yetkinliklerin görev süresi boyunca geçerli olması gerekir. |  | |
| C034 | C034 | Eksik yetkinlik sistem tarafından otomatik verilir mi? | `SYN-YET-GECERLILIK@2.0` | Eksik yetkinlik | 460–538 | Eksik yetkinlik otomatik olarak verilmez; atanmayan iş açık olarak gösterilir. | uygun | |
| C036 | C036 | Yataklı servislerde hemşireden hangi yetkinlikler beklenir? | `SYN-YET-SERVIS@1.0` | Yataklı servisler | 183–303 | Prototip inceleme senaryosundaki iki yataklı serviste hemşire için monitör takibi ve ilaç uygulama yetkinlikleri aranır. | uygun | |
| C041 | C041 | Katman B'de rezerv nasıl hesaplanır? | `SYN-YET-ACIK@1.0` | Rezerv | 481–589 | Katman B'de rezerv, servis ve rol için max(0, tahmin edilen talep − somut görev dakikası) olarak hesaplanır. | uygun | |
| C042 | C042 | Somut görev açığı ile rezerv arasındaki fark nedir? | `SYN-YET-ACIK@1.0` | Açık | 310–468 | Bu somut görev açığı rezervden ayrıdır: açık bilinen bir görevin karşılanamayan dakikasıdır, rezerv ise tahmine dayalı ve henüz görev olarak bilinmeyen iştir. | eksik_gold: farkın ikinci yarısı (rezervin tanımı) Rezerv bölümünde | |
|  |  | ↳ ek gold | `SYN-YET-ACIK@1.0` | Rezerv | 813–884 | Rezerv, henüz görev olarak bilinmeyen beklenen iş için ayrılan süredir; |  | |
| C046 | C046 | Gerçekleşen iş hangi zaman aralıklarında ölçülür? | `SYN-AKS-TAHMIN@1.0` | Pencereler | 166–245 | Gerçekleşen iş, on beş dakikalık pencerelerde personel dakikası olarak ölçülür. | uygun | |
| C050 | C050 | Katman A hangi girdileri kullanır? | `SYN-AKS-KADRO@1.0` | Girdi | 175–299 | Katman A, karar anında bilinen planlı kadroyu, servis takvimini, yetkinlikleri ve tahmine dayalı ihtiyaç tablosunu kullanır. | uygun | |
| C054 | C054 | Katman B görevleri hangi zaman penceresinde denetler? | `SYN-AKS-GOREV@1.0` | Pencere | 162–281 | Katman B, karar anından başlayan altmış dakikalık pencerede son tarihi pencere içinde olan bekleyen görevleri denetler. | uygun | |
| C057 | C057 | Çözücü süre sınırı kullanıcı tarafından değiştirilebilir mi? | `SYN-AKS-GOREV@1.0` | Çözücü süre sınırı | 586–706 | İnceleme senaryosunda çözücü süre sınırı merkezi yapılandırmadan okunur ve kullanıcı tarafından ekrandan değiştirilemez. | uygun | |
| E001 | C001 | Devir anında MedAkis seçilen planı kendisi uygular mı? | `SYN-PRC-DEVIR@1.0` | Amaç | 361–441 | Devir, sistemin bir planı uyguladığı an değildir; sistem hiçbir planı uygulamaz. | eksik_gold: SORUMLU/Sınırlar da sistemin planı uygulamadığını söylüyor | |
|  |  | ↳ ek gold | `SYN-ROL-SORUMLU@1.0` | Sınırlar | 542–603 | Onay yalnız seçilen planı kayıt altına alır; planı uygulamaz. |  | |
| E002 | C003 | Görev için öngörülen dakika hangi iki değerin çarpımıyla bulunur? | `SYN-PRC-DEVIR@1.0` | Bekleyen görevler | 566–697 | Görevin tahmini süresi, görev türünün temel süresi ile hastanın görev oluşturulduğu anda bilinen son bakım katsayısının çarpımıdır. | uygun | |
| E003 | C006 | Deneysel çözüm modu bir öneri olarak kayda geçirilebilir mi? | `SYN-PRC-ONAY@1.0` | Öneri kaydı | 294–358 | Deneysel mod sonucu öneri olarak gösterilemez ve kayda alınamaz. | uygun | |
|  |  | ↳ ek gold | `SYN-AKS-KADRO@1.0` | Varsayılan politika | 617–665 | Deneysel mod sonuçları öneri olarak gösterilmez. |  | |
| E004 | C009 | Onaylayan kişinin kimlik bilgisi serbest metinden mi alınır? | `SYN-PRC-ONAY@1.0` | İnceleme ve onay | 505–581 | Kimlik serbest metinden değil, kullanıcı arayüzünde açılan oturumdan alınır. | uygun | |
| E005 | C016 | Bir personelin hemşire olduğu bilgisi nereden okunur? | `SYN-ROL-HEMSIRE@1.0` | Rol | 214–301 | Rol, personel kaydındaki yapılandırılmış rol alanından okunur; belge metninden okunmaz. | uygun | |
| E006 | C024 | İnceleme ekranındaki ihtiyaç ve kapasite bilgileri düzenlenebilir mi? | `SYN-ROL-SORUMLU@1.0` | İnceleme | 404–466 | Bu bilgiler salt okunur gösterilir ve ekrandan değiştirilemez. | uygun | |
| E007 | C026 | Destek rolündeki personel hangi tür işlere aday gösterilir? | `SYN-ROL-DESTEK@1.0` | Rol | 167–236 | Destek personeli yalnız destek rolü gerektiren görevler için adaydır. | eksik_gold: adaylığın sınırı (hemşire görevine atanmaz) Görev sınırı bölümünde | |
|  |  | ↳ ek gold | `SYN-ROL-DESTEK@1.0` | Görev sınırı | 255–311 | Destek personeli hemşire rolü gerektiren göreve atanmaz. |  | |
| E008 | C034 | Personelde eksik olan yetkinlik sistem tarafından tamamlanır mı? | `SYN-YET-GECERLILIK@2.0` | Eksik yetkinlik | 460–538 | Eksik yetkinlik otomatik olarak verilmez; atanmayan iş açık olarak gösterilir. | uygun | |
| E009 | C036 | Yataklı servislerdeki hemşirelerde hangi beceriler aranır? | `SYN-YET-SERVIS@1.0` | Yataklı servisler | 183–303 | Prototip inceleme senaryosundaki iki yataklı serviste hemşire için monitör takibi ve ilaç uygulama yetkinlikleri aranır. | uygun | |
| E010 | C041 | Rezerv dakikası hangi formülle bulunur? | `SYN-YET-ACIK@1.0` | Rezerv | 481–589 | Katman B'de rezerv, servis ve rol için max(0, tahmin edilen talep − somut görev dakikası) olarak hesaplanır. | uygun | |
| E011 | C046 | İş yükü ölçümünde kullanılan pencere uzunluğu nedir? | `SYN-AKS-TAHMIN@1.0` | Pencereler | 166–245 | Gerçekleşen iş, on beş dakikalık pencerelerde personel dakikası olarak ölçülür. | uygun | |
| E012 | C057 | Çözücünün süre sınırını ekrandan değiştirebilir miyim? | `SYN-AKS-GOREV@1.0` | Çözücü süre sınırı | 586–706 | İnceleme senaryosunda çözücü süre sınırı merkezi yapılandırmadan okunur ve kullanıcı tarafından ekrandan değiştirilemez. | uygun | |

## Kaynak dışı vakalar

| Vaka | Soru | Not | Denetim | İnceleme |
|---|---|---|---|---|
| U001 | Bir hastaya parasetamol dozu ne kadar verilmelidir? |  | uygun | |
| U002 | Hemşirelerin aylık maaşı ne kadardır? |  | uygun | |
| U003 | Hastanenin otopark ücreti nedir? |  | uygun | |
| U004 | Grip aşısı kaç doz uygulanır? |  | uygun | |
| U005 | Yoğun bakımda kaç yatak vardır? |  | uygun | |
| U006 | Hemşirelerin yıllık izin hakkı kaç gündür? | alana yakın (bilerek zor) | uygun | |
| V001 | Gece vardiyasında bir serviste en az kaç hemşire bulunmalıdır? | alana yakın (bilerek zor) | uygun | |
| V002 | Hemşire başına düşen hasta sayısı en fazla kaç olmalıdır? | alana yakın (bilerek zor) | uygun | |
| V003 | Fazla mesai ücreti nasıl hesaplanır? |  | uygun | |
| V004 | Vardiya değişim talebi hangi yönetici tarafından onaylanır? | alana yakın (bilerek zor) | uygun | |
| V005 | İzin talebi sisteme nasıl girilir? |  | uygun | |
| V006 | Random Forest modeli kaç karar ağacı kullanır? | alana yakın (bilerek zor) | alana yakın: Random Forest adı geçiyor, ağaç sayısı yok; kaynak dışı etiketi korundu | |
| V007 | Hangi serviste kaç yatak bulunur? |  | uygun | |
| V008 | Yoğun bakım hemşiresi hangi sertifikalara sahip olmalıdır? |  | uygun | |
| V009 | Personelin yıllık performans değerlendirmesi nasıl yapılır? |  | uygun | |
| V010 | Sistem şifremi unutursam ne yapmalıyım? |  | uygun | |
| V011 | Hastanın düşme riski nasıl değerlendirilir? |  | uygun | |
| V012 | Kan transfüzyonu hangi adımlarla yapılır? |  | uygun | |
| V013 | Acil serviste triaj kategorileri nelerdir? | alana yakın (bilerek zor) | alana yakın: triaj yetkinliği geçiyor, kategoriler yok; kaynak dışı etiketi korundu | |
| V014 | Hastanenin başhekimi kimdir? |  | uygun | |
| V015 | Vardiya çizelgesi kaç hafta önceden yayınlanır? |  | uygun | |
| V016 | Görev atamaları cep telefonuma bildirim olarak gelir mi? |  | uygun | |
| V017 | Personel kaç yaşında emekli olur? |  | uygun | |
| V018 | Gece vardiyasından sonra hemşire kaç saat dinlenmelidir? | alana yakın (bilerek zor) | uygun | |
| V019 | Enfeksiyon kontrol komitesi ne sıklıkla toplanır? |  | uygun | |
| V020 | Tahmin modelinin doğruluk yüzdesi kaçtır? | alana yakın (bilerek zor) | tartışmalı: corpus ölçü adlarını (MAE, RMSE) veriyor ama istenen yüzde değeri yok; kaynak dışı etiketi korundu | |
| V021 | Hasta memnuniyet anketi sonuçları nerede görülür? |  | uygun | |
| V022 | Çalışanlar sendikaya nasıl üye olur? |  | uygun | |
| V023 | Serviste hangi ilaçlar stokta tutulur? |  | uygun | |
| V024 | Bakım katsayısı değerlerini hangi klinik ölçek belirler? | alana yakın (bilerek zor) | uygun | |
