"""Chroma yerel vektör deposu sarmalayıcısı (Aşama 7.2).

- `PersistentClient`, yalnız denetlenmiş mutlak depo yolunda (`location.check_store_dir`) açılır.
- Telemetri kapalı. Koleksiyonlar `embedding_function=None` ile açılır: Chroma'nın varsayılan modeli hiçbir zaman
  indirilmez veya çalıştırılmaz; vektörler her zaman açıkça verilir.
- Uzaklık cosine (`hnsw.space`). Açılan koleksiyonun yapılandırması doğrulanır.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from medakis.rag.errors import IndexError_

CHROMA_DIR = "chroma"


@dataclass(frozen=True)
class Hit:
    vector_id: str
    distance: float
    document: str
    metadata: dict[str, Any]


class ChromaVectorStore:
    def __init__(self, store_dir: Path) -> None:
        path = Path(store_dir) / CHROMA_DIR
        path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._client = chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False))

    def _check(self, col: Any, key: str) -> Any:
        cfg = col.configuration or {}
        if (cfg.get("hnsw") or {}).get("space") != "cosine" or cfg.get("embedding_function") is not None:
            raise IndexError_("koleksiyon_yapilandirmasi_gecersiz", col.name)
        if (col.metadata or {}).get("index_key") != key:
            raise IndexError_("koleksiyon_kimligi_uyusmuyor", col.name)
        return col

    def open(self, name: str, key: str, create: bool = False) -> Any:
        names = self.names()
        if name in names:
            return self._check(self._client.get_collection(name, embedding_function=None), key)
        if not create:
            raise IndexError_("koleksiyon_yok", name)
        col = self._client.create_collection(name, embedding_function=None, configuration={"hnsw": {"space": "cosine"}},
                                             metadata={"index_key": key})  # fmt: skip
        return self._check(col, key)

    def close(self) -> None:
        """İstemciyi kapatır (Windows'ta SQLite dosya kilitleri bırakılır)."""
        self._client.close()

    def names(self) -> list[str]:
        return sorted(c.name for c in self._client.list_collections())

    def delete(self, name: str) -> None:
        if name in self.names():
            self._client.delete_collection(name)

    @staticmethod
    def upsert(col: Any, ids: list[str], vectors: list[list[float]], documents: list[str], metadatas: list[dict]) -> None:
        col.upsert(ids=ids, embeddings=vectors, documents=documents, metadatas=metadatas)

    @staticmethod
    def delete_ids(col: Any, ids: list[str]) -> None:
        col.delete(ids=list(ids))

    @staticmethod
    def snapshot(col: Any, with_vectors: bool = False) -> dict[str, dict[str, Any]]:
        include = ["documents", "metadatas"] + (["embeddings"] if with_vectors else [])
        got = col.get(include=include)
        out = {}
        for i, vid in enumerate(got["ids"]):
            out[vid] = {"document": got["documents"][i], "metadata": dict(got["metadatas"][i] or {})}
            if with_vectors:
                out[vid]["dims"] = len(got["embeddings"][i])
        return out

    @staticmethod
    def count(col: Any) -> int:
        return int(col.count())

    @staticmethod
    def query(col: Any, vector: list[float], n: int, doc_uids: list[str]) -> list[Hit]:
        if not doc_uids:
            return []
        res = col.query(query_embeddings=[vector], n_results=n, where={"doc_uid": {"$in": list(doc_uids)}},
                        include=["documents", "metadatas", "distances"])  # fmt: skip
        return [Hit(vid, float(d), doc, dict(md or {})) for vid, d, doc, md
                in zip(res["ids"][0], res["distances"][0], res["documents"][0], res["metadatas"][0])]  # fmt: skip
