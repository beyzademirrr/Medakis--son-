"""D5: doğrulanmış aday havuzlarının BİR KEZ üretilmesi (bütün yöntemler aynı dosyayı okur).

    .\\.venv\\Scripts\\python.exe scripts\\rag_d5_aday_havuzu.py

- Havuzlar (bge-m3 v1, değerlendirme kipi, eşiksiz): H10 (top_k=10), H10+ (top_k=10, tamamlama), H20 (top_k=20),
  H20+ (top_k=20, tamamlama). Her aday retrieval'ın bütün denetimlerinden geçmiştir (indeks eşlemesi, hash, aralık,
  onay, geçerlilik, sürüm, kural çatışması).
- Modelsiz yöntemler için her vakanın izinli belge kümesi (`eligible_documents`, aynı `as_of`) ve manifestle hash'i
  doğrulanmış bütün v1 parçaları kaydedilir. Hash uyuşmazlığında betik durur.
- Dev verisi yalnız; test okunmaz. Eşik yazılmaz; gerçek indeks yalnız okunur. Çıktı:
  D:\\medakis-deney\\d5\\sonuclar\\aday_havuzu.json ve docs/veri/asama8_d5_aday_havuzu.json
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_rag_profiles, load_settings  # noqa: E402
from medakis.rag.chunking.chunker import sha256_text  # noqa: E402
from medakis.rag.embeddings.ollama_client import OllamaEmbedder  # noqa: E402
from medakis.rag.evaluation.eval_set import gold_chunk_ids, load_dev_set  # noqa: E402
from medakis.rag.ingestion.document_policy import eligible_documents  # noqa: E402
from medakis.rag.metadata.file_store import FileMetadataStore  # noqa: E402
from medakis.rag.retrieval.retriever import RetrievalConfig, Retriever  # noqa: E402
from medakis.rag.vector_store.chroma_store import ChromaVectorStore  # noqa: E402
from medakis.rag.vector_store.location import check_store_dir  # noqa: E402

EVAL = ROOT / "evaluation" / "asama7"
CORPUS = ROOT / "corpus" / "kontrollu"
OUT_D = Path(r"D:\medakis-deney\d5\sonuclar\aday_havuzu.json")
OUT_REPO = ROOT / "docs" / "veri" / "asama8_d5_aday_havuzu.json"
MODEL = "bge-m3"
POOLS = {"H10": RetrievalConfig(top_k=10), "H10+": RetrievalConfig(top_k=10, refill=True),
         "H20": RetrievalConfig(top_k=20), "H20+": RetrievalConfig(top_k=20, refill=True)}  # fmt: skip


def split_of(group: str) -> str:
    return "teyit" if int(hashlib.sha256(("secim_teyit_v1:" + group).encode()).hexdigest()[:8], 16) % 3 == 0 else "secim"


def main() -> int:
    settings = load_settings(env={})
    real = check_store_dir(settings.rag_store_dir, min_free_gb=settings.rag_min_free_gb)
    common = load_rag_profiles(settings)["common"]
    cases = [c for n in ("dev.jsonl", "dev_ek.jsonl") for c in load_dev_set(EVAL / n).cases]
    meta, vec = FileMetadataStore(CORPUS, real), ChromaVectorStore(real)
    reg = meta.registry()
    key = reg.ready_keys(MODEL)[0]
    thresholds_before = {k: reg.threshold(k) for k in reg.ready_keys()}
    manifest = reg.manifest(key)
    docs = meta.corpus().by_uid()
    parts = []
    for p in manifest["parcalar"]:
        text = docs[p["doc_uid"]].text[p["char_start"]:p["char_end"]]
        if sha256_text(text) != p["sha256"]:
            raise SystemExit(f"parça hash uyuşmuyor: {p['chunk_id']}")
        rec = docs[p["doc_uid"]].record
        parts.append({"parca": p["chunk_id"], "belge": p["doc_uid"], "kaynak_adi": rec.source_name, "bolum": p["section"],
                      "aralik": [p["char_start"], p["char_end"]], "metin": text, "kural_catismasi": bool(rec.rule_conflict)})  # fmt: skip
    states, problems = meta.document_states()
    emb = OllamaEmbedder(MODEL, common["ollama_base_url"], 2048, float(common["timeout_seconds"]), keep_alive="5m")
    retrievers = {n: Retriever(meta, vec, emb, MODEL, config=cfg) for n, cfg in POOLS.items()}
    retrievers["H10"].retrieve("Isınma sorgusu.", evaluation=True)
    out_cases = []
    for c in cases:
        elig = eligible_documents(states, c.as_of, problems)
        row = {"vaka": c.case_key, "grup": c.paraphrase_group, "kume": split_of(c.paraphrase_group), "ans": c.answerable,
               "soru": c.question, "as_of": c.as_of.isoformat(), "gold_parcalar": list(gold_chunk_ids(c.gold, manifest["parcalar"])),
               "gold_araliklar": [[g.doc_uid, g.char_start, g.char_end] for g in c.gold],
               "izinli_belgeler": sorted(s.record.uid for s in elig.allowed), "catismalar": list(elig.conflicts), "havuzlar": {}}  # fmt: skip
        for name, ret in retrievers.items():
            t0 = time.perf_counter()
            r = ret.retrieve(c.question, as_of=c.as_of, evaluation=True)
            row["havuzlar"][name] = {"sonuc": r.outcome.value, "uyarilar": list(r.warnings), "getirme_ms": round((time.perf_counter() - t0) * 1000, 1),
                                     "adaylar": [{"parca": ch.chunk_id, "sira": ch.rank, "uzaklik": ch.distance} for ch in r.chunks]}  # fmt: skip
        out_cases.append(row)
    vec.close()
    OllamaEmbedder(MODEL, common["ollama_base_url"], keep_alive=0).unload()
    reg_after = FileMetadataStore(CORPUS, real).registry()
    out = {"indeks": key, "model": MODEL, "parcalama": manifest["parcalama"], "parcalar": parts, "vakalar": out_cases,
           "esik_once": thresholds_before, "esik_sonra": {k: reg_after.threshold(k) for k in reg_after.ready_keys()},
           "not": "Dev verisi; kör test değildir. Gold TASLAK."}  # fmt: skip
    text = json.dumps(out, ensure_ascii=False, indent=1)
    OUT_D.parent.mkdir(parents=True, exist_ok=True)
    OUT_D.write_text(text, encoding="utf-8")
    OUT_REPO.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    for n in POOLS:
        sizes = [len(r["havuzlar"][n]["adaylar"]) for r in out_cases]
        print(n, "aday en az/en çok", min(sizes), max(sizes), "sonuçlar", sorted({r["havuzlar"][n]["sonuc"] for r in out_cases}))
    print("parça", len(parts), "sha256", digest, "eşik", out["esik_sonra"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
