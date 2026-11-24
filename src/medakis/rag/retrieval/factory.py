"""RAG retrieval yığınını güvenle açan tek giriş noktası (Aşama 7.5).

Sıra: depo konumu denetimi (mutlak, sistem sürücüsü değil, yazılabilir, boş alan) → Chroma → dosya metadata
deposu → profil embedding istemcisi. Denetim geçmezse StoreUnavailable; başka konuma sessiz geçiş yoktur.
Ağ çağrısı yapmaz; Ollama'ya ilk bağlantı ilk sorguda olur. Model indirmez.
"""

from __future__ import annotations

from medakis.config import Settings, load_rag_profiles
from medakis.rag.contracts.providers import MeasuredEmbedder
from medakis.rag.embeddings.factory import embedder_for_profile
from medakis.rag.metadata.file_store import FileMetadataStore
from medakis.rag.retrieval.retriever import Retriever
from medakis.rag.vector_store.chroma_store import ChromaVectorStore
from medakis.rag.vector_store.location import check_store_dir

CONTROLLED_CORPUS = ("corpus", "kontrollu")


def open_retriever(settings: Settings, embedder: MeasuredEmbedder | None = None, model: str | None = None) -> Retriever:
    store = check_store_dir(settings.rag_store_dir, min_free_gb=settings.rag_min_free_gb)
    profiles = load_rag_profiles(settings)
    model = model or profiles["profiles"][settings.rag_profile]["embed"]
    emb = embedder or embedder_for_profile(profiles, settings.rag_profile)
    corpus = settings.project_root.joinpath(*CONTROLLED_CORPUS)
    return Retriever(FileMetadataStore(corpus, store), ChromaVectorStore(store), emb, model)
