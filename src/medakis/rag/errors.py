"""RAG hata türleri. Her hata makinece okunur bir `code` taşır; mesajda gizli bilgi veya belge içeriği yer almaz."""

from __future__ import annotations


class RagError(Exception):
    """RAG katmanının tüm hatalarının tabanı."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


class CorpusError(RagError):
    """Corpus yapısı (manifest) kullanılamaz; hiçbir belge yüklenmez."""


class LedgerError(RagError):
    """Belge onay izi bozuk ya da istenen geçiş kurala aykırı."""


class StoreUnavailable(RagError):
    """RAG deposu güvenli konumda değil ya da erişilemez (başka konuma sessiz geçiş yapılmaz)."""


class ChunkingError(RagError):
    """Belge güvenle parçalanamadı (ör. token sınırı aşıldı); sessiz kesme yapılmaz."""


class EmbeddingUnavailable(RagError):
    """Embedding sağlayıcısı kullanılamıyor (kapalı, model yok, zaman aşımı, bellek, bağlam aşımı)."""


class IndexError_(RagError):  # noqa: N801 - yerleşik IndexError ile karışmasın
    """İndeks oluşturulamadı, doğrulanamadı ya da sorgulanabilir durumda değil."""


class SealedSplitError(RagError):
    """Mühürlü değerlendirme kümesi (test) izinsiz okunmak istendi."""
