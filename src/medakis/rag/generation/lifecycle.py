"""Aşama 8.6: embedding ve üretici modellerinin sıralı yüklenmesi, boşaltılması ve oturum sonu temizliği.

- Sıra: retrieval (embedding) → embedding BOŞALTILIR ve boşaldığı `/api/ps` ile doğrulanır → üretici yüklenir.
- Boşaltma istemcilerin `unload()` yöntemleriyle yapılır: embedding için boş girdi, üretici için boş mesaj; ikisinde de
  keep_alive=0. Bu modül doğrudan HTTP çağrısı yapmaz.
- KM31 kararları:
  * Varsayılan politika `her_sorguda_bosalt`. `ikisi_sicak` (5 dk) YALNIZ açıkça verilen geliştirme/test kipinde.
  * `ikisi_sicak` sırasında her üretimden önce anlık RAM ve VRAM yeterliliği denetlenir (çağıranın verdiği ölçüm
    işleviyle; bu paket sistem komutu çalıştırmaz). Ölçüm yoksa ya da yetersizse embedding boşaltılır (güvenli yön).
  * Oturum sonunda iki model de boşaltılır ve boşaltma doğrulanır (`session()` bağlam yöneticisi; hata olsa da).
- Boşaltma doğrulanamazsa üretim çağrılmaz: `LifecycleError` → hat retrieval-only'ye düşer.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from medakis.rag.errors import RagError
from medakis.rag.generation.mode import GenerationMode, require_enabled
from medakis.rag.generation.profiles import KeepAlivePolicy

# Ölçüme dayalı alt sınırlar (8.6: iki model yüklüyken GPU ~4,4/6 GB, ollama süreçleri ~3,1 GB RAM).
MIN_RAM_FREE_GB_WARM = 2.0
MIN_VRAM_FREE_MIB_WARM = 768

ResourceProbe = Callable[[], tuple[float | None, int | None]]  # (boş RAM GB, boş VRAM MiB)


class LifecycleError(RagError):
    """Model yaşam döngüsü güvenle yönetilemedi (boşaltma doğrulanamadı ya da politika izinsiz)."""


def _base(name: str) -> str:
    return name.split(":")[0]


class ModelLifecycle:
    def __init__(self, embedder: Any, generator: Any, policy: KeepAlivePolicy = KeepAlivePolicy.HER_SORGUDA_BOSALT,
                 wait_s: float = 15.0, sleep: Callable[[float], None] = time.sleep, mode: GenerationMode | None = None,
                 resources: ResourceProbe | None = None, min_ram_free_gb: float = MIN_RAM_FREE_GB_WARM,
                 min_vram_free_mib: int = MIN_VRAM_FREE_MIB_WARM) -> None:  # fmt: skip
        if policy is not KeepAlivePolicy.HER_SORGUDA_BOSALT:
            try:
                require_enabled(mode)  # sıcak tutma yalnız açık geliştirme/test kipinde
            except RagError:
                raise LifecycleError("politika_yalniz_gelistirme_test", policy.value) from None
        self.embedder, self.generator, self.policy, self.wait_s, self.sleep = embedder, generator, policy, wait_s, sleep
        self.resources, self.min_ram, self.min_vram = resources, min_ram_free_gb, min_vram_free_mib
        self.events: list[str] = []

    def loaded(self) -> list[str]:
        return [m.get("name", "") for m in self.generator.loaded_models()]

    def _wait_unloaded(self, model: str) -> None:
        end = time.monotonic() + self.wait_s
        while any(_base(n) == _base(model) for n in self.loaded()):
            if time.monotonic() > end:
                raise LifecycleError("bosaltma_dogrulanamadi", model)
            self.sleep(0.25)

    def _unload_embedding(self, event: str) -> None:
        self.embedder.unload()
        self._wait_unloaded(self.embedder.model)
        self.events.append(event)

    def _resources_ok(self) -> tuple[bool, str]:
        if self.resources is None:
            return False, "kaynak_olcumu_yok"
        ram, vram = self.resources()
        if ram is None or ram < self.min_ram:
            return False, f"ram_yetersiz:{ram}"
        if vram is None or vram < self.min_vram:
            return False, f"vram_yetersiz:{vram}"
        return True, "kaynak_yeterli"

    def before_generation(self) -> None:
        """Üretici çağrısından önce: politika ve anlık kaynaklara göre embedding'i boşalt ve doğrula."""
        if self.policy is KeepAlivePolicy.IKISI_SICAK:
            ok, why = self._resources_ok()
            if ok:
                self.events.append("embedding_yuklu_kalabilir")
                return
            self._unload_embedding(f"embedding_bosaltildi:{why}")  # güvenli yön: sıralı yüklemeye dön
            return
        self._unload_embedding("embedding_bosaltildi")

    def unload_all(self) -> None:
        self.embedder.unload()
        self.generator.unload()
        self._wait_unloaded(self.embedder.model)
        self._wait_unloaded(self.generator.model)
        self.events.append("tumu_bosaltildi")

    @contextmanager
    def session(self) -> Iterator[ModelLifecycle]:
        """Oturum: çıkışta (hata olsa da) iki model boşaltılır ve doğrulanır; doğrulanamazsa LifecycleError."""
        try:
            yield self
        finally:
            self.unload_all()
            self.events.append("oturum_sonu_dogrulandi")
