"""Aşama 7.3: Ollama embedding istemcisi (sahte HTTP; gerçek Ollama ve model indirme yok)."""

import json
from pathlib import Path

import httpx
import pytest

from medakis.config import load_rag_profiles, load_settings
from medakis.rag.embeddings.factory import embedder_for_profile
from medakis.rag.embeddings.ollama_client import OllamaEmbedder, check_base_url
from medakis.rag.embeddings.prefixes import PREFIXES, prefixes_for
from medakis.rag.errors import EmbeddingUnavailable

MODEL = "qwen3-embedding:0.6b"
SRC = Path(__file__).resolve().parents[1] / "src" / "medakis" / "rag"


class FakeOllama:
    """Ölçülmüş Ollama 0.40.17 davranışını taklit eder: toplu istekte prompt_eval_count toplamdır,
    truncate=false bağlam aşımında 400 döner."""

    def __init__(self, dims=8, ctx=32768, installed=True, fail=None):
        self.dims, self.ctx, self.installed, self.fail = dims, ctx, installed, list(fail or [])
        self.requests: list[tuple[str, str, dict | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        if self.fail:
            kind = self.fail.pop(0)
            if kind == "timeout":
                raise httpx.ReadTimeout("zaman", request=request)
            if kind == "connect":
                raise httpx.ConnectError("kapalı", request=request)
            if kind == "oom":
                return httpx.Response(500, text="model requires more system memory than is available")
            if kind == "500":
                return httpx.Response(500, text="beklenmeyen")
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.17"})
        if path == "/api/tags":
            models = [{"name": MODEL, "model": MODEL, "digest": "d" * 64}] if self.installed else []
            return httpx.Response(200, json={"models": models})
        if path == "/api/show":
            return httpx.Response(200, json={"model_info": {"qwen3.embedding_length": self.dims, "qwen3.context_length": self.ctx}})
        if path == "/api/embed":
            texts = body["input"]
            counts = [len(t.split()) + 2 for t in texts]
            if not body.get("truncate", True) and max(counts) > body["options"]["num_ctx"]:
                return httpx.Response(400, json={"error": "the input length exceeds the context length"})
            return httpx.Response(200, json={"embeddings": [[0.5] * self.dims for _ in texts], "prompt_eval_count": sum(counts)})
        return httpx.Response(404, text="not found")


def client(fake, **kw):
    return OllamaEmbedder(MODEL, transport=httpx.MockTransport(fake), **kw)


def test_describe_reads_digest_dimensions_and_caps_context():
    fake = FakeOllama(dims=8, ctx=1024)
    info = client(fake, num_ctx=2048).describe()
    assert (info.model, info.digest, info.dimensions, info.context_length, info.num_ctx) == (MODEL, "d" * 64, 8, 1024, 1024)
    assert info.query_prefix.startswith("Instruct: ") and info.query_prefix.endswith("\nQuery:") and info.doc_prefix == ""


def test_embed_one_sends_single_input_truncate_false_keep_alive_zero():
    fake = FakeOllama()
    vec, count = client(fake).embed_one("Yetkinlik görev süresince geçerli olmalı")
    method, path, body = fake.requests[-1]
    assert (method, path) == ("POST", "/api/embed") and body["input"] == ["Yetkinlik görev süresince geçerli olmalı"]
    assert body["truncate"] is False and body["keep_alive"] == 0 and body["options"]["num_ctx"] == 2048
    assert len(vec) == 8 and count == 7


def test_context_overflow_is_error_not_truncation():
    with pytest.raises(EmbeddingUnavailable, match="baglam_asildi"):
        client(FakeOllama(ctx=16), num_ctx=16).embed_one("kelime " * 50)


def test_batch_requires_matching_total_count():
    c = client(FakeOllama())
    assert len(c.embed_many(["a b", "c d e"], [4, 5])) == 2
    with pytest.raises(EmbeddingUnavailable, match="toplu_sayim_uyusmuyor"):
        c.embed_many(["a b", "c d e"], [4, 4])  # toplam uyuşmazsa sessiz kabul yok


@pytest.mark.parametrize("fail, code, calls", [
    (["connect"], "ollama_kapali", 1),
    (["timeout", "timeout"], "zaman_asimi", 2),
    (["oom", "oom"], "bellek_yetersiz", 2),
    (["500"], "ollama_hatasi", 1),
])  # fmt: skip
def test_failures_map_to_codes_with_at_most_one_retry(fail, code, calls):
    fake = FakeOllama(fail=fail)
    with pytest.raises(EmbeddingUnavailable, match=code):
        client(fake).version()
    assert len(fake.requests) == calls


def test_single_retry_recovers():
    fake = FakeOllama(fail=["timeout"])
    assert client(fake).version() == "0.40.17" and len(fake.requests) == 2


def test_missing_model_is_reported_never_pulled():
    fake = FakeOllama(installed=False)
    with pytest.raises(EmbeddingUnavailable, match="model_yok") as err:
        client(fake).describe()
    assert "onay" in str(err.value) and all(p in ("/api/tags",) for _, p, _ in fake.requests)


@pytest.mark.parametrize("url", ["http://10.0.0.5:11434", "https://127.0.0.1:11434", "http://u:p@127.0.0.1:11434",
                                 "http://ollama.example.com", "http://127.0.0.1:11434/api"])  # fmt: skip
def test_only_loopback_http(url):
    with pytest.raises(EmbeddingUnavailable, match="yerel_degil"):
        check_base_url(url)


def test_proxy_environment_ignored(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://10.9.9.9:3128")
    c = OllamaEmbedder(MODEL)
    assert c._client._trust_env is False


def test_unknown_model_has_no_invented_prefix():
    with pytest.raises(EmbeddingUnavailable, match="onek_yapilandirmasi_yok"):
        prefixes_for("embeddinggemma")  # kurulu ama şartname adayı değil
    assert set(PREFIXES) == {"qwen3-embedding:0.6b", "bge-m3", "paraphrase-multilingual:278m"}


def test_profiles_map_to_candidates_without_network():
    data = load_rag_profiles(load_settings(env={}))
    assert embedder_for_profile(data, "primary").model == "qwen3-embedding:0.6b"
    assert embedder_for_profile(data, "comparison").model == "bge-m3"
    with pytest.raises(EmbeddingUnavailable):
        embedder_for_profile(data, "olmayan")


def test_no_download_or_mutation_endpoints_in_rag_code():
    text = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    for forbidden in ("/api/pull", "/api/create", "/api/delete", "/api/push", "/api/copy", "/api/generate"):
        assert forbidden not in text, forbidden
    # Aşama 8: /api/chat yalnız kip kapılı üretim istemcisinde bulunabilir.
    users = [p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if "/api/chat" in p.read_text(encoding="utf-8")]
    assert users == ["generation/ollama_generator.py"], users


def test_keep_alive_default_zero_and_explicit_override():
    fake = FakeOllama()
    client(fake).embed_one("a b")
    assert fake.requests[-1][2]["keep_alive"] == 0
    client(fake, keep_alive="5m").embed_one("a b")
    assert fake.requests[-1][2]["keep_alive"] == "5m"


def test_loaded_models_is_read_only_get():
    fake = FakeOllama()
    try:
        client(fake).loaded_models()
    except EmbeddingUnavailable:
        pass
    assert fake.requests[-1][:2] == ("GET", "/api/ps")
