# Sentetik açık ve rezerv gösterimi

Bu belge sentetiktir; klinik prosedür ya da kurum kuralı değildir. Yalnız MedAkis prototipinin davranışını anlatır.

## Açık

Zorunlu kurallar sağlanamadığında sistem kurala aykırı atama yapmaz. Karşılanamayan somut görev, nedeni ve rolüyle atanmayan iş olarak listelenir. Bu somut görev açığı rezervden ayrıdır: açık bilinen bir görevin karşılanamayan dakikasıdır, rezerv ise tahmine dayalı ve henüz görev olarak bilinmeyen iştir.

## Rezerv

Katman B'de rezerv, servis ve rol için max(0, tahmin edilen talep − somut görev dakikası) olarak hesaplanır. Tahmin edilen talep, karar anından sonraki altmış dakika için iş yükü tahminidir; somut görev dakikası, karar anında bekleyen ve son tarihi bu pencere içinde olan görevlerin dakikaya yukarı yuvarlanmış süreleri toplamıdır. Rezerv, henüz görev olarak bilinmeyen beklenen iş için ayrılan süredir; kimliği olmayan bu iş hiçbir personele atanmış gösterilmez. Tahmin yoksa rezerv hesaplanmaz.

## Güvenli ret

Girdi eksik, belirsiz veya güvenilmez olduğunda öneri yayımlanmaz ve nedenleri gösterilir.
