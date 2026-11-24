"""Üretim kip kapısı (Aşama 8.1).

A6 (güvenli kaçınma) çözülene kadar normal kullanıcıya RAG cevap üretimi KAPALIDIR (KM30).
- Varsayılan kip `kapali`. Üretim ve kanıt denetimi yalnız çağıran kod açıkça `gelistirme` ya da `test` verdiğinde çalışır.
- Kip ortam değişkeninden, yapılandırma dosyasından ya da kullanıcı girdisinden OKUNMAZ; yalnız parametredir.
  Arayüz ve servis katmanı RAG'yi import etmez (yapı testleri).
- Kapalı kipte model hiçbir zaman çağrılmaz: kapı, istem kurulmadan ve ağ isteği yapılmadan önce denetlenir.
"""

from __future__ import annotations

from enum import Enum

from medakis.rag.errors import RagError

DISABLED_MESSAGE = "Kaynaklı cevap üretimi kapalıdır: güvenli kaçınma (A6) kabul kriteri henüz karşılanmadı."


class GenerationMode(str, Enum):
    KAPALI = "kapali"
    GELISTIRME = "gelistirme"
    TEST = "test"


class GenerationDisabled(RagError):
    """Üretim kapalı kipte çağrıldı."""


def require_enabled(mode: object) -> GenerationMode:
    """Yalnız açıkça verilmiş `gelistirme` ya da `test` kipine izin verir; diğer her değer reddedilir."""
    if not isinstance(mode, GenerationMode) or mode is GenerationMode.KAPALI:
        raise GenerationDisabled("uretim_kapali_a6", DISABLED_MESSAGE)
    return mode
