"""Aşama 8.6: donanım anlık görüntüsü, çalışma profilleri ve keep_alive politikaları (saf mantık; sistem çağrısı yok).

Sistem bilgisi (RAM, VRAM, disk, GPU, sürücü, Ollama sürümü, kurulu ve yüklü modeller) `scripts/rag_baslangic_denetimi.py`
tarafından toplanır ve burada `HardwareSnapshot` olarak değerlendirilir. Seçim deterministiktir ve nedenleriyle döner:
istenen profil koşullarını sağlamıyorsa sıradaki daha hafif profile geçilir; en sonda her zaman `retrieval_only` vardır.

Profil eşikleri ÖLÇÜME dayanır (2026-11-24, GTX 1660 Ti 6 GB): gemma3:4b num_ctx=2048 ile ~2.750 MiB VRAM
(`/api/ps` size_vram 2.880.385.842 B), GPU toplam tepe ~3,7 GB; bge-m3 ~657 MiB. Bunlar bu cihaza özgüdür.
Model indirme yapılmaz: kurulu olmayan model gerektiren profil seçilemez.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

GEMMA_4B = "gemma3:4b"
GEMMA_1B = "gemma3:1b"
EMBED = "bge-m3"  # geçici birincil embedding adayı (KM30)


@dataclass(frozen=True)
class HardwareSnapshot:
    ram_total_gb: float
    ram_free_gb: float
    gpu_name: str | None
    vram_total_mib: int | None
    vram_used_mib: int | None
    driver: str | None
    disk_free_gb: dict[str, float]
    ollama_version: str | None
    installed: dict[str, str] = field(default_factory=dict)  # model adı → digest
    loaded: tuple[str, ...] = ()
    models_drive: str | None = None  # OLLAMA_MODELS sürücüsü (ör. "D:")
    store_drive: str | None = None  # RAG deposu sürücüsü
    system_drive: str = "C:"  # Windows %SystemDrive%; modeller ve depo burada OLAMAZ (KM31: uyarı değil, engel)

    @property
    def vram_free_mib(self) -> int | None:
        if self.vram_total_mib is None or self.vram_used_mib is None:
            return None
        return self.vram_total_mib - self.vram_used_mib

    def has(self, model: str) -> bool:
        return model in self.installed or f"{model}:latest" in self.installed


class KeepAlivePolicy(str, Enum):
    HER_SORGUDA_BOSALT = "her_sorguda_bosalt"  # embedding 0, üretici 0: en az bellek, her sorgu soğuk
    URETICI_SICAK = "uretici_sicak"  # embedding 0, üretici kısa süre yüklü
    IKISI_SICAK = "ikisi_sicak"  # ikisi kısa süre yüklü (VRAM'e birlikte sığarsa)

    def values(self, warm: str = "5m") -> tuple[int | str, int | str]:
        return {"her_sorguda_bosalt": (0, 0), "uretici_sicak": (0, warm), "ikisi_sicak": (warm, warm)}[self.value]


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    device: str  # gpu | cpu | yok
    embed_model: str
    generator_model: str | None
    num_ctx: int = 2048
    num_predict: int = 256
    min_vram_free_mib: int = 0
    min_ram_free_gb: float = 1.0
    timeout_s: float = 120.0
    keep_alive: KeepAlivePolicy = KeepAlivePolicy.HER_SORGUDA_BOSALT

    @property
    def retrieval_only(self) -> bool:
        return self.generator_model is None


PROFILES: dict[str, RuntimeProfile] = {
    "gpu_standart": RuntimeProfile("gpu_standart", "gpu", EMBED, GEMMA_4B, min_vram_free_mib=3500, min_ram_free_gb=2.0),
    # CPU yedeği: ayrı, kullanıcının başlattığı CUDA_VISIBLE_DEVICES=-1 Ollama oturumu ve gemma3:1b gerektirir.
    # gemma3:1b KURULU DEĞİL; indirme proje sahibinin iznine bağlı. Kurulu değilken bu profil seçilemez.
    "cpu_yedek": RuntimeProfile("cpu_yedek", "cpu", EMBED, GEMMA_1B, min_ram_free_gb=3.0, timeout_s=120.0),
    "retrieval_only": RuntimeProfile("retrieval_only", "yok", EMBED, None, min_ram_free_gb=0.5),
}
ORDER = ("gpu_standart", "cpu_yedek", "retrieval_only")
MIN_STORE_FREE_GB = 30.0


@dataclass(frozen=True)
class Selection:
    profile: RuntimeProfile
    requested: str
    reasons: tuple[str, ...]  # elenen her profil için neden; seçilen profil koşulları sağlar
    warnings: tuple[str, ...] = ()


def problems_for(profile: RuntimeProfile, snap: HardwareSnapshot) -> list[str]:
    out: list[str] = []
    sysd = snap.system_drive.rstrip(":").upper()
    models = (snap.models_drive or "").rstrip(":").upper()
    store = (snap.store_drive or "").rstrip(":").upper()
    if not models:
        out.append("model_konumu_bilinmiyor")  # OLLAMA_MODELS tanımsızsa Ollama kullanıcı klasörünü (C:) kullanır
    elif models == sysd:
        out.append("modeller_sistem_surucusunde")
    if not store or store == sysd:
        out.append("depo_sistem_surucusunde_ya_da_bilinmiyor")
    if snap.ollama_version is None:
        out.append("ollama_erisilemiyor")
    if not snap.has(profile.embed_model):
        out.append(f"model_kurulu_degil:{profile.embed_model}")
    if profile.generator_model and not snap.has(profile.generator_model):
        out.append(f"model_kurulu_degil:{profile.generator_model}")
    if profile.device == "gpu":
        if snap.vram_total_mib is None:
            out.append("gpu_yok")
        elif (snap.vram_free_mib or 0) < profile.min_vram_free_mib:
            out.append(f"vram_yetersiz:{snap.vram_free_mib}<{profile.min_vram_free_mib}")
    if snap.ram_free_gb < profile.min_ram_free_gb:
        out.append(f"ram_yetersiz:{snap.ram_free_gb:.1f}<{profile.min_ram_free_gb}")
    store_free = snap.disk_free_gb.get((snap.store_drive or "").rstrip(":").upper())
    if store_free is None or store_free < MIN_STORE_FREE_GB:
        out.append(f"depo_bos_alan:{store_free}")
    return out


def select_profile(snap: HardwareSnapshot, requested: str = "gpu_standart") -> Selection:
    if requested not in PROFILES:
        raise ValueError(f"bilinmeyen profil: {requested}")
    warnings: list[str] = []  # D: politikası artık uyarı değil: problems_for içinde ENGEL (hiçbir profil seçilemez)
    reasons = []
    for name in ORDER[ORDER.index(requested):]:
        issues = problems_for(PROFILES[name], snap)
        if not issues:
            return Selection(PROFILES[name], requested, tuple(reasons), tuple(warnings))
        reasons.append(f"{name}:" + ",".join(issues))
    # retrieval_only da sağlanamıyorsa (ör. Ollama kapalı, depo yok): hiçbir profil — çağıran 'unavailable' döner
    return Selection(PROFILES["retrieval_only"], requested, tuple(reasons) + ("hicbir_profil_uygun_degil",), tuple(warnings))
