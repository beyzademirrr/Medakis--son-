"""Aşama 8.6: donanım profilleri, model yaşam döngüsü ve retrieval-only geri dönüşü (ağsız; sahte istemciler)."""

import json
from dataclasses import replace

import httpx
import pytest
import rag_helpers as H
import test_rag_generation as G

from medakis.rag.contracts.dto import RagOutcome
from medakis.rag.contracts.generation import GroundedResult
from medakis.rag.contracts.retrieval import RetrievalOutcome, RetrievalResult
from medakis.rag.embeddings.ollama_client import OllamaEmbedder
from medakis.rag.generation.lifecycle import LifecycleError, ModelLifecycle
from medakis.rag.generation.mode import GenerationMode
from medakis.rag.generation.ollama_generator import GEMMA3_4B_DIGEST, GenerationUnavailable, OllamaGenerator
from medakis.rag.generation.pipeline import POLICY_CANDIDATES, RETRIEVAL_ONLY_FALLBACK, EvidencePipeline
from medakis.rag.generation.profiles import PROFILES, HardwareSnapshot, KeepAlivePolicy, select_profile

TEST = GenerationMode.TEST
SNAP = HardwareSnapshot(15.8, 4.4, "NVIDIA GeForce GTX 1660 Ti", 6144, 0, "572.60", {"C": 12.0, "D": 197.7}, "0.40.17",
                        {"bge-m3:latest": "7907", "gemma3:4b": GEMMA3_4B_DIGEST}, (), "D:", "D:")  # fmt: skip


# ---- Profil seçimi -----------------------------------------------------------------------------------------------


def test_measured_device_selects_gpu_profile():
    sel = select_profile(SNAP)
    assert sel.profile.name == "gpu_standart" and sel.profile.generator_model == "gemma3:4b" and sel.reasons == () and sel.warnings == ()


def test_low_vram_falls_back_through_cpu_to_retrieval_only_with_reasons():
    sel = select_profile(replace(SNAP, vram_used_mib=4000))  # 2144 MiB boş < 3500
    assert sel.profile.name == "retrieval_only" and sel.profile.retrieval_only
    assert sel.reasons[0].startswith("gpu_standart:vram_yetersiz") and "model_kurulu_degil:gemma3:1b" in sel.reasons[1]


def test_cpu_profile_needs_installed_light_model_never_downloads():
    assert select_profile(SNAP, "cpu_yedek").profile.name == "retrieval_only"  # gemma3:1b kurulu değil
    with_1b = replace(SNAP, installed={**SNAP.installed, "gemma3:1b": "x"})
    assert select_profile(with_1b, "cpu_yedek").profile.name == "cpu_yedek"


@pytest.mark.parametrize("change, reason", [
    ({"ollama_version": None}, "ollama_erisilemiyor"),
    ({"ram_free_gb": 0.2}, "ram_yetersiz"),
    ({"disk_free_gb": {"C": 12.0, "D": 10.0}}, "depo_bos_alan"),
    ({"store_drive": "E:"}, "depo_bos_alan"),
    ({"installed": {}}, "model_kurulu_degil:bge-m3"),
])  # fmt: skip
def test_nothing_suitable_is_reported(change, reason):
    sel = select_profile(replace(SNAP, **change))
    assert sel.reasons[-1] == "hicbir_profil_uygun_degil" and any(reason in r for r in sel.reasons)


@pytest.mark.parametrize("change, reason", [
    ({"models_drive": "C:"}, "modeller_sistem_surucusunde"),
    ({"models_drive": None}, "model_konumu_bilinmiyor"),
    ({"store_drive": "C:"}, "depo_sistem_surucusunde_ya_da_bilinmiyor"),
])  # fmt: skip
def test_system_drive_storage_blocks_every_profile(change, reason):
    sel = select_profile(replace(SNAP, **change))  # KM31: D: zorunluluğu uyarı değil, engel
    assert sel.reasons[-1] == "hicbir_profil_uygun_degil" and all(reason in r for r in sel.reasons[:-1])


def test_unknown_profile_rejected():
    with pytest.raises(ValueError):
        select_profile(SNAP, "her_sey")


def test_keep_alive_policies():
    assert KeepAlivePolicy.HER_SORGUDA_BOSALT.values() == (0, 0)
    assert KeepAlivePolicy.URETICI_SICAK.values() == (0, "5m") and KeepAlivePolicy.IKISI_SICAK.values("2m") == ("2m", "2m")
    assert PROFILES["gpu_standart"].keep_alive is KeepAlivePolicy.HER_SORGUDA_BOSALT  # varsayılan en az bellek


# ---- Yaşam döngüsü -----------------------------------------------------------------------------------------------


class FakeModel:
    def __init__(self, name, loaded):
        self.model, self._loaded, self.unloads = name, loaded, 0

    def unload(self):
        self.unloads += 1
        self._loaded.discard(self.model)


class FakeGen(FakeModel):
    def loaded_models(self):
        return [{"name": n} for n in sorted(self._loaded)]


def test_embedding_unloaded_and_verified_before_generation():
    loaded = {"bge-m3", "gemma3:4b"}
    emb, gen = FakeModel("bge-m3", loaded), FakeGen("gemma3:4b", loaded)
    life = ModelLifecycle(emb, gen, sleep=lambda s: None)
    life.before_generation()
    assert emb.unloads == 1 and "bge-m3" not in loaded and life.events == ["embedding_bosaltildi"]


def test_both_warm_policy_needs_explicit_dev_mode():
    loaded = {"bge-m3", "gemma3:4b"}
    for mode in (None, GenerationMode.KAPALI):
        with pytest.raises(LifecycleError, match="politika_yalniz_gelistirme_test"):
            ModelLifecycle(FakeModel("bge-m3", loaded), FakeGen("gemma3:4b", loaded), KeepAlivePolicy.IKISI_SICAK, mode=mode)


@pytest.mark.parametrize("probe, kept, why", [
    (lambda: (5.9, 1700), True, None),
    (lambda: (1.0, 1700), False, "ram_yetersiz"),
    (lambda: (5.9, 300), False, "vram_yetersiz"),
    (lambda: (None, None), False, "ram_yetersiz"),
    (None, False, "kaynak_olcumu_yok"),
])  # fmt: skip
def test_both_warm_checks_resources_before_each_generation(probe, kept, why):
    loaded = {"bge-m3:latest", "gemma3:4b"}
    emb, gen = FakeModel("bge-m3:latest", loaded), FakeGen("gemma3:4b", loaded)
    life = ModelLifecycle(emb, gen, KeepAlivePolicy.IKISI_SICAK, sleep=lambda s: None, mode=TEST, resources=probe)
    life.before_generation()
    assert (emb.unloads == 0) is kept
    if not kept:
        assert life.events[-1].startswith("embedding_bosaltildi:" + why) and "bge-m3:latest" not in loaded


def test_session_end_unloads_and_verifies_even_on_error():
    loaded = {"bge-m3:latest", "gemma3:4b"}
    emb, gen = FakeModel("bge-m3:latest", loaded), FakeGen("gemma3:4b", loaded)
    life = ModelLifecycle(emb, gen, KeepAlivePolicy.IKISI_SICAK, sleep=lambda s: None, mode=TEST, resources=lambda: (6.0, 2000))
    with pytest.raises(RuntimeError):
        with life.session():
            raise RuntimeError("oturum içinde hata")
    assert loaded == set() and life.events[-2:] == ["tumu_bosaltildi", "oturum_sonu_dogrulandi"]


def test_unverifiable_unload_raises():
    class Stuck(FakeModel):
        def unload(self):
            self.unloads += 1  # bellekten çıkmıyor

    loaded = {"bge-m3"}
    life = ModelLifecycle(Stuck("bge-m3", loaded), FakeGen("gemma3:4b", loaded), wait_s=0.01, sleep=lambda s: None)
    with pytest.raises(LifecycleError, match="bosaltma_dogrulanamadi"):
        life.before_generation()


# ---- İstemci boşaltma çağrıları ----------------------------------------------------------------------------------


def test_unload_requests_are_non_generating():
    seen = []

    def handler(request):
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "gemma3:4b"}]})
        return httpx.Response(200, json={"done_reason": "unload", "embeddings": []})

    t = httpx.MockTransport(handler)
    OllamaGenerator(TEST, transport=t).unload()
    OllamaEmbedder("bge-m3", transport=t).unload()
    assert OllamaGenerator(TEST, transport=t).loaded_models() == [{"name": "gemma3:4b"}]
    assert seen[0] == ("POST", "/api/chat", {"model": "gemma3:4b", "messages": [], "keep_alive": 0})
    assert seen[1] == ("POST", "/api/embed", {"model": "bge-m3", "input": [], "keep_alive": 0})
    assert seen[2][:2] == ("GET", "/api/ps")


# ---- Retrieval-only geri dönüşü --------------------------------------------------------------------------------------


FOUND = RetrievalResult(RetrievalOutcome.FOUND, "x", (G.C1, G.C2), evaluation=True)


def test_retrieval_only_profile_returns_labelled_sources_without_model():
    r = EvidencePipeline(object(), None, TEST).decide("Rezerv nasıl hesaplanır?", FOUND, POLICY_CANDIDATES)
    assert r.outcome is RagOutcome.UNAVAILABLE and r.model_calls == 0 and r.answer is None and r.quotes == ()
    assert r.fallback == (G.C1, G.C2) and r.fallback_label == RETRIEVAL_ONLY_FALLBACK and r.warnings == ("profil:retrieval_only",)


@pytest.mark.parametrize("code", ["bellek_yetersiz", "zaman_asimi", "ollama_kapali", "model_yok", "model_digest_uyusmuyor"])
def test_generator_failures_fall_back_to_retrieval_only(code):
    class Failing:
        def chat_json(self, messages, schema):
            raise GenerationUnavailable(code)

    r = EvidencePipeline(object(), Failing(), TEST).decide("Rezerv nasıl hesaplanır?", FOUND, POLICY_CANDIDATES)
    assert r.outcome is RagOutcome.UNAVAILABLE and r.answer is None and r.warnings == (f"uretici:{code}",) and r.fallback


def test_lifecycle_failure_prevents_generation_and_falls_back():
    class Life:
        def before_generation(self):
            raise LifecycleError("bosaltma_dogrulanamadi")

    gen = G.FakeChat()
    calls = []

    class Recording:
        def chat_json(self, messages, schema):
            calls.append(1)

    r = EvidencePipeline(object(), Recording(), TEST, lifecycle=Life()).decide("Rezerv?", FOUND, POLICY_CANDIDATES)
    assert calls == [] and r.model_calls == 0 and r.warnings == ("yasam_dongusu:bosaltma_dogrulanamadi",) and r.fallback
    assert gen  # sahte sunucu kullanılmadı


@pytest.mark.parametrize("outcome", [RetrievalOutcome.NO_SOURCE, RetrievalOutcome.CONFLICTING_SOURCES, RetrievalOutcome.REJECTED,
                                     RetrievalOutcome.UNAVAILABLE])  # fmt: skip
def test_security_outcomes_never_get_fallback_sources(outcome):
    r = EvidencePipeline(object(), None, TEST).decide("Rezerv?", RetrievalResult(outcome, "x"), POLICY_CANDIDATES)
    assert r.fallback == () and r.outcome.value == outcome.value  # çatışma/ret/kaynak yokta parça gösterilmez


def test_fallback_contract_only_on_labelled_unavailable():
    with pytest.raises(ValueError):
        GroundedResult(RagOutcome.NO_SOURCE, "x", fallback=(G.C1,), fallback_label="etiket")
    with pytest.raises(ValueError):
        GroundedResult(RagOutcome.UNAVAILABLE, "x", fallback=(G.C1,))  # etiketsiz


def test_startup_check_is_read_only():
    src = (H.ROOT / "scripts" / "rag_baslangic_denetimi.py").read_text(encoding="utf-8")
    assert "/api/pull" not in src and "/api/chat" not in src and "/api/embed" not in src and "keep_alive" not in src



# ---- KM31: yeniden deneme DAHİL toplam işlem bütçesi -----------------------------------------------------------------


class Clock:
    def __init__(self, steps):
        self.values = list(steps)

    def __call__(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_generator_retry_cannot_exceed_total_budget(monkeypatch):
    import medakis.rag.generation.ollama_generator as OG

    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma3:4b", "digest": GEMMA3_4B_DIGEST}]})
        raise httpx.ReadTimeout("t", request=request)

    # deadline=0+120; tags isteği t=0; sohbet 1. deneme t=1 (zaman aşımı); 2. deneme öncesi t=130 → bütçe bitti
    monkeypatch.setattr(OG.time, "monotonic", Clock([0, 0, 1, 130]))
    gen = OllamaGenerator(TEST, transport=httpx.MockTransport(handler))
    with pytest.raises(GenerationUnavailable, match="zaman_asimi"):
        gen.chat_json([{"role": "user", "content": "x"}], {})
    assert seen.count("/api/chat") == 1  # ikinci deneme yapılmadı


def test_generator_respects_pipeline_remaining_budget():
    seen = []
    t = httpx.MockTransport(lambda r: seen.append(r.url.path) or httpx.Response(200, json={}))
    with pytest.raises(GenerationUnavailable, match="zaman_asimi"):
        OllamaGenerator(TEST, transport=t).chat_json([{"role": "user", "content": "x"}], {}, budget_s=0)
    assert seen == []  # bütçe yoksa istek yok


def test_embedder_total_budget_includes_retries(monkeypatch):
    import medakis.rag.embeddings.ollama_client as OC

    seen = []

    def handler(request):
        seen.append(1)
        raise httpx.ReadTimeout("t", request=request)

    monkeypatch.setattr(OC.time, "monotonic", Clock([0, 0, 130]))
    with pytest.raises(EmbeddingUnavailableType, match="zaman_asimi"):
        OllamaEmbedder("bge-m3", transport=httpx.MockTransport(handler)).version()
    assert len(seen) == 1


def test_pipeline_budget_exhausted_before_generation_falls_back_without_call():
    calls = []

    class Gen:
        supports_budget = True

        def chat_json(self, messages, schema, budget_s=None):
            calls.append(budget_s)

    clock = Clock([0, 125])  # başlangıç 0; üretimden önce 125 sn geçmiş
    r = EvidencePipeline(object(), Gen(), TEST, clock=clock).decide("Rezerv?", FOUND, POLICY_CANDIDATES)
    assert calls == [] and r.warnings == ("butce_asildi",) and r.outcome is RagOutcome.UNAVAILABLE and r.fallback


def test_pipeline_passes_remaining_budget_to_generator():
    seen = []

    class Gen:
        supports_budget = True

        def chat_json(self, messages, schema, budget_s=None):
            seen.append(budget_s)
            raise GenerationUnavailable("zaman_asimi")

    EvidencePipeline(object(), Gen(), TEST, clock=Clock([0, 30])).decide("Rezerv?", FOUND, POLICY_CANDIDATES)
    assert seen == [90]  # 120 - 30


from medakis.rag.errors import EmbeddingUnavailable as EmbeddingUnavailableType  # noqa: E402
