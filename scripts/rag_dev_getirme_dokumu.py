"""Dev kümesi için iki adayın getirme ayrıntısı (yalnız dev; test verisi okunmaz).

    .\\.venv\\Scripts\\python.exe scripts\\rag_dev_getirme_dokumu.py

Değerlendirme kipinde (eşiksiz) her dev sorusu için ilk 10 doğrulanmış parçanın kimliği, belgesi, bölümü, uzaklığı
ve metni yazılır. Etiket denetimi ve kanıt denetimi analizi bu dökümü kullanır. Eşik yazılmaz; normal sorgu kapalı kalır.
Çıktı: docs/veri/asama7_dev_getirme_dokumu.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_rag_profiles, load_settings  # noqa: E402
from medakis.rag.embeddings.ollama_client import OllamaEmbedder  # noqa: E402
from medakis.rag.evaluation.eval_set import gold_chunk_ids, load_dev_set  # noqa: E402
from medakis.rag.metadata.file_store import FileMetadataStore  # noqa: E402
from medakis.rag.retrieval.retriever import Retriever  # noqa: E402
from medakis.rag.vector_store.chroma_store import ChromaVectorStore  # noqa: E402
from medakis.rag.vector_store.location import check_store_dir  # noqa: E402

EVAL = ROOT / "evaluation" / "asama7"
OUT = ROOT / "docs" / "veri" / "asama7_dev_getirme_dokumu.json"
CANDIDATES = ("bge-m3", "qwen3-embedding:0.6b")


def main() -> int:
    settings = load_settings(env={})
    store = check_store_dir(settings.rag_store_dir, min_free_gb=settings.rag_min_free_gb)
    common = load_rag_profiles(settings)["common"]
    parts = [load_dev_set(EVAL / n) for n in ("dev.jsonl", "dev_ek.jsonl")]
    cases = [c for p in parts for c in p.cases]
    meta = FileMetadataStore(ROOT / "corpus" / "kontrollu", store)
    out = {"dev_sha256": [p.sha256 for p in parts], "adaylar": {}}
    for model in CANDIDATES:
        emb = OllamaEmbedder(model, common["ollama_base_url"], 2048, float(common["timeout_seconds"]), keep_alive="5m")
        vectors = ChromaVectorStore(store)
        ret = Retriever(meta, vectors, emb, model)
        key = meta.registry().ready_keys(model)[0]
        parts_map = meta.registry().manifest(key)["parcalar"]
        rows = []
        for c in cases:
            res = ret.retrieve(c.question, as_of=c.as_of, evaluation=True)
            gold = set(gold_chunk_ids(c.gold, parts_map))
            rows.append({"vaka": c.case_key, "grup": c.paraphrase_group, "cevaplanabilir": c.answerable, "soru": c.question,
                         "gold": sorted(gold), "sonuclar": [{"sira": ch.rank, "parca": ch.chunk_id, "belge": f"{ch.source_key}@{ch.version}",
                         "bolum": ch.section, "uzaklik": round(ch.distance, 4), "gold": ch.chunk_id in gold, "metin": ch.text}
                         for ch in res.chunks]})  # fmt: skip
        out["adaylar"][model] = {"indeks": key, "vakalar": rows}
        vectors.close()
        OllamaEmbedder(model, common["ollama_base_url"], keep_alive=0).embed_one("boşaltma")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("yazildi", OUT, len(cases), "vaka")
    return 0


if __name__ == "__main__":
    sys.exit(main())
