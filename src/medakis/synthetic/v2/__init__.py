"""Sentetik veri üreticisi 2.0.0: üç senaryo, talep ve kapasiteli yürütme ayrı katmanlar.

1.0.0 üreticisi (`medakis.synthetic.generator`) dokunulmadan korunur; bu paket ondan bağımsızdır.
Saf fonksiyonlardır: veritabanına bağlanmaz, gerçek saati okumaz, küresel rastgelelik kullanmaz.
Üretici bir optimizasyon motoru değildir: kadro ve vardiya planı dışsal girdidir.
"""

from medakis.synthetic.v2.generator import GENERATOR_VERSION, generate_v2
from medakis.synthetic.v2.profiles import ParamsV2

__all__ = ["GENERATOR_VERSION", "ParamsV2", "generate_v2"]
