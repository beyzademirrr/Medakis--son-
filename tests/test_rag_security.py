"""Aşama 7.5: metadata deposu sözleşmesi, güvenlik sınırları ve yalıtım (ağsız, veritabanısız)."""

import ast
import gc
import socket
import uuid
from dataclasses import replace

import pytest
import rag_helpers as H

from medakis.config import load_settings
from medakis.rag.chunking.chunker import ChunkConfig, chunk_corpus
from medakis.rag.contracts.metadata_store import MetadataStore
from medakis.rag.contracts.retrieval import RetrievalOutcome as R
from medakis.rag.embeddings.fake_provider import FakeEmbedder
from medakis.rag.errors import StoreUnavailable
from medakis.rag.ingestion.approval_ledger import APPROVE, RETIRE
from medakis.rag.metadata.file_store import FileMetadataStore
from medakis.rag.retrieval.factory import open_retriever
from medakis.rag.retrieval.retriever import Retriever
from medakis.rag.vector_store.chroma_store import ChromaVectorStore
from medakis.rag.vector_store.index_builder import build_index
from medakis.repositories.schema import PRIVILEGES

RAG = H.ROOT / "src" / "medakis" / "rag"
T0 = H.T0


def _imports(files):
    found = {}
    for py in files:
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for n in names:
                found.setdefault(n, set()).add(py.relative_to(RAG).as_posix())
    return found


IMPORTS = _imports(RAG.rglob("*.py"))


def _users(prefix):
    return {f for name, files in IMPORTS.items() if name == prefix or name.startswith(prefix + ".") for f in files}


# ---- Yalıtım (import sınırları) ------------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden", ["sqlalchemy", "psycopg", "sqlite3", "keyring", "subprocess", "medakis.repositories",
                                       "medakis.approvals", "medakis.optimization", "medakis.services", "medakis.ui",
                                       "medakis.forecasting", "medakis.synthetic", "langgraph", "langchain"])  # fmt: skip
def test_rag_never_imports_sql_decision_or_tool_layers(forbidden):
    assert not _users(forbidden), forbidden  # PostgreSQL adaptörü Aşama 7'de yok (KM29)


def test_network_and_vector_libraries_confined_to_their_modules():
    # Aşama 8: kip kapılı üretim istemcisi de yerel Ollama'ya httpx ile bağlanır (yalnız loopback).
    assert _users("httpx") <= {"embeddings/ollama_client.py", "embeddings/factory.py", "generation/ollama_generator.py"}
    assert _users("chromadb") == {"vector_store/chroma_store.py"}


def test_no_dynamic_execution_or_shell_in_rag():
    text = "\n".join(p.read_text(encoding="utf-8") for p in RAG.rglob("*.py"))
    for token in ("eval(", "exec(", "os.system", "Popen", "__import__", "pickle"):
        assert token not in text, token


def test_rag_role_privileges_are_read_and_append_only():
    privs = PRIVILEGES["medakis_rag"]
    assert all(t.startswith("rag_") for t in privs)  # iş tablolarına erişim yok
    assert not any({"UPDATE", "DELETE", "TRUNCATE"} & set(p) for p in privs.values())
    for t in ("rag_documents", "rag_chunks", "rag_indexes", "rag_index_chunks"):
        assert privs[t] == frozenset({"SELECT"})


def test_retriever_result_has_no_command_surface():
    import medakis.rag.contracts.retrieval as C

    public = {n for n in dir(C.RetrievalResult) if not n.startswith("_")}
    assert not any(w in n for n in public for w in ("assign", "approve", "apply", "write", "execute", "ata", "onayla"))


# ---- Depo konumu ve tek giriş noktası ------------------------------------------------------------------------------


def test_open_retriever_refuses_system_drive_without_fallback(monkeypatch):
    s = replace(load_settings(env={}), rag_store_dir=type(H.ROOT)("C:/medakis_rag_deneme"))
    monkeypatch.setenv("SystemDrive", "C:")
    with pytest.raises(StoreUnavailable, match="sistem_surucusu_yasak"):
        open_retriever(s, embedder=FakeEmbedder())
    assert not type(H.ROOT)("C:/medakis_rag_deneme").exists()  # hiçbir şey oluşturulmadı


def test_open_retriever_refuses_relative_store():
    s = replace(load_settings(env={}), rag_store_dir=type(H.ROOT)("data/rag"))
    with pytest.raises(StoreUnavailable, match="mutlak_degil"):
        open_retriever(s, embedder=FakeEmbedder())


def test_open_retriever_uses_controlled_corpus_and_profile_model():
    path = H.d_tmp()
    try:
        s = replace(load_settings(env={}), rag_store_dir=path)
        ret = open_retriever(s, embedder=FakeEmbedder())
        assert ret.model == "qwen3-embedding:0.6b" and ret.metadata.corpus_dir == H.CORPUS
        res = ret.retrieve("Yetkinlik nedir?")
        assert res.outcome is R.UNAVAILABLE and "hazir_indeks_yok" in res.warnings  # indeks yok: güvenli
        ret.vectors.close()
    finally:
        gc.collect()
        H.remove(path)


# ---- Metadata deposu sözleşmesi ----------------------------------------------------------------------------------------


@pytest.fixture()
def store():
    path = H.d_tmp()
    yield FileMetadataStore(H.CORPUS, path, ledger_name=f"{uuid.uuid4().hex}.jsonl"), path
    gc.collect()
    H.remove(path)


def test_file_store_satisfies_contract_and_rereads_every_call(store):
    meta, _ = store
    assert isinstance(meta, MetadataStore)
    states, problems = meta.document_states()
    assert problems == [] and states["SYN-PRC-ONAY@1.0"].status.value == "draft"
    recs = {d.record.uid: d.record for d in meta.corpus().documents}
    meta.ledger.append(APPROVE, recs["SYN-PRC-ONAY@1.0"], "test-onayi", "t", T0, list(recs.values()))
    assert meta.document_states()[0]["SYN-PRC-ONAY@1.0"].status.value == "approved"  # önbellek yok
    meta.ledger.append(RETIRE, recs["SYN-PRC-ONAY@1.0"], "test-onayi", "t", T0, list(recs.values()))
    assert meta.document_states()[0]["SYN-PRC-ONAY@1.0"].status.value == "retired"


def test_query_log_is_append_only_with_sequence(store):
    meta, _ = store
    ids = [meta.append_query_log({"soru": f"s{i}"}) for i in range(3)]
    log = meta.query_log()
    assert [e["sira"] for e in log] == [0, 1, 2] and [e["sorgu_kimligi"] for e in log] == ids and len(set(ids)) == 3


# ---- Ağ ve yazma yüzeyi ---------------------------------------------------------------------------------------------


def test_retrieval_makes_no_network_connection_and_writes_only_query_log(monkeypatch):
    path = H.d_tmp()
    try:
        vectors = ChromaVectorStore(path)
        meta = FileMetadataStore(H.CORPUS, path)
        res = build_index(chunk_corpus(meta.corpus().documents), ChunkConfig(), FakeEmbedder("m"), vectors, meta.registry())
        meta.registry().set_threshold(res.index_key, 1.0, T0, {"kume": "dev"})
        recs = {d.record.uid: d.record for d in meta.corpus().documents}
        meta.ledger.append(APPROVE, recs["SYN-AKS-KAYNAK@1.0"], "test-onayi", "t", T0, list(recs.values()))
        files_before = {p: p.read_bytes() for p in path.rglob("*") if p.is_file() and "chroma" not in p.parts}

        real_connect = socket.socket.connect

        def guarded(self, address):
            host = address[0] if isinstance(address, tuple) else str(address)
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError(f"dış ağ bağlantısı denendi: {host}")
            return real_connect(self, address)

        monkeypatch.setattr(socket.socket, "connect", guarded)
        out = Retriever(meta, vectors, FakeEmbedder("m"), "m", clock=lambda: T0).retrieve("Kaynaklı açıklama onay yerine geçer mi?")
        assert out.outcome in (R.FOUND, R.NO_SOURCE)
        changed = {p for p in path.rglob("*") if p.is_file() and "chroma" not in p.parts and files_before.get(p) != p.read_bytes()}
        assert changed == {path / "sorgu_kaydi.jsonl"}  # onay izi ve indeks kaydı değişmez
        vectors.close()
    finally:
        gc.collect()
        H.remove(path)
