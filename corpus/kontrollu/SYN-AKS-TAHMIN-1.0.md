# Sentetik iş yükü tahmini akışı

Bu belge sentetiktir; klinik prosedür ya da kurum kuralı değildir. Yalnız MedAkis prototipinin davranışını anlatır.

## Pencereler

Gerçekleşen iş, on beş dakikalık pencerelerde personel dakikası olarak ölçülür. Tahmin ufukları otuz ve altmış dakikadır.

## Modeller

Basit mevsimsel temel model ile Random Forest modeli zamana dayalı ayrımla karşılaştırılır. Değerlendirme ölçüleri ortalama mutlak hata ve kök ortalama kare hatasıdır. Model seçimi yalnız doğrulama dönemine göre yapılır.

## Belirsizlik tamponu

Tahmine kalibre edilmiş bir belirsizlik tamponu eklenir. Tamponlu talep, kapasiteyle karşılaştırılarak açık ve fazla hesaplanır.

## Sızıntı önleme

Tahmin anında bilinmeyen bilgi modele girmez. Sonradan kaydedilen gözlem veya değerlendirme geçmiş tahmini değiştirmez.
