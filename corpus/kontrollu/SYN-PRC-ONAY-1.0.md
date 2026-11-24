# Sentetik plan inceleme ve onay prosedürü

Bu belge sentetiktir; klinik prosedür ya da kurum kuralı değildir. Yalnız MedAkis prototipinin davranışını anlatır.

## Öneri kaydı

Optimizasyon sonucu önce bağımsız doğrulayıcıdan geçer. Yalnız doğrulanmış ve yayımlanabilir öneri kayda alınabilir. Deneysel mod sonucu öneri olarak gösterilemez ve kayda alınamaz.

## İnceleme ve onay

Yönetici kayıtlı öneriyi inceler ve onay ya da ret kararı verir. Karar, ekranda gösterilen planın SHA-256 özetine bağlıdır. Kimlik serbest metinden değil, kullanıcı arayüzünde açılan oturumdan alınır.

## Yetki alanı

Yalnız bir servis için yetkili kişi, birden çok servisi etkileyen planı onaylayamaz. Yetki ve süre politikası sentetik prototip politikasıdır; yetkili kurum kararı değildir.

## Eskime ve yeniden inceleme

Onaydan sonra güvenlik girdileri değişirse, örneğin yeni bir devamsızlık bildirilirse veya yeni bir görev gelirse, onay artık geçerli plan sayılmaz. Bu durumda plan yeniden incelenmelidir.

## Geri alma

Onaylanan karar gerekçesiyle geri alınabilir. Geri alınan onayın kilidi geri alma anından itibaren etkin değildir. Bütün kararlar ekleme-yalnız ve özet zincirli denetim izine yazılır.
