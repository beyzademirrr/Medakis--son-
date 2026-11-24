# Sentetik sonuç dışa aktarım prosedürü

Bu belge sentetiktir; klinik prosedür ya da kurum kuralı değildir. Yalnız MedAkis prototipinin davranışını anlatır.

## Kapsam

Yalnız kayıtlı öneriler JSON ya da CSV olarak dışa aktarılabilir. Kayıtlı olmayan bir sonuç dışa aktarılamaz.

## Etiketler

Her dışa aktarım bir etiket taşır: geçerli onaylı plan, onaylı ama artık geçersiz plan veya geçerli plan değil. Bekleyen, reddedilen ve geri alınan öneriler geçerli plan olarak etiketlenmez.

## Bütünlük

Dışa aktarılan dosya planın SHA-256 özetini, durumunu ve geçerlilik etiketini taşır. Dosyanın kendi SHA-256 özeti dosya oluşturulduktan sonra baytları üzerinden ayrıca hesaplanır ve arayüzde gösterilir; bu özet dosyanın içinde yer almaz. Denetim izi bozuksa veya plan özeti uyuşmuyorsa dışa aktarım reddedilir.
