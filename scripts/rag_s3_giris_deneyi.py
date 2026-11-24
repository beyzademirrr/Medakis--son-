"""S3 deneyi: "Giriş" şablon parçalarını indeks dışında bırakmak (parcalama_v2) ile mevcut indeksin (v1) karşılaştırması.

    .\\.venv\\Scripts\\python.exe scripts\\rag_s3_giris_deneyi.py

- Yalnız dev (dev.jsonl + dev_ek.jsonl, denetimle düzeltilmiş gold). Test ve ayrılmış küme okunmaz.
- v1: gerçek depodaki HAZIR indeksler (dokunulmaz, yeniden kurulmaz). v2: AYRI deney deposunda (D:) kurulur; gerçek
  depodaki indeksler emekliye ayrılmaz. Deney deposu onay izinin salt kopyasını kullanır.
- Ölçüler: Recall@5, MRR@10 (gold aralık → parça eşlemesi her indeks için ayrı), örneklem içi dev eşiğiyle kaçınma ve
  yanlış kaçınma, grup bazlı 5 katlı CV (yalnız cosine; eşik eğitim katlarında).
- ÖNCEDEN SABİT KABUL KURALI (birincil aday bge-m3): v2 kabul edilir ancak ve ancak CV kaçınma, CV yanlış kaçınma,
  Recall@5 ve MRR@10'un HİÇBİRİ kötüleşmez ve en az biri iyileşir. Aksi halde kabul edilmez. qwen bilgi amaçlıdır.
- Eşik yazılmaz; normal sorgu kapalı kalır. Çıktı: docs/veri/asama7_s3_giris_deneyi.json
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_rag_profiles, load_settings  # noqa: E402
from medakis.rag.chunking.chunker import ChunkConfig, chunk_corpus  # noqa: E402
from medakis.rag.chunking.markdown_sections import INTRO  # noqa: E402
from medakis.rag.embeddings.ollama_client import OllamaEmbedder  # noqa: E402
from medakis.rag.evaluation.eval_set import gold_chunk_ids, load_dev_set  # noqa: E402
from medakis.rag.evaluation.metrics import CaseOutcome, summarize  # noqa: E402
from medakis.rag.metadata.file_store import LEDGER, FileMetadataStore  # noqa: E402
from medakis.rag.retrieval.retriever import Retriever  # noqa: E402
from medakis.rag.retrieval.thresholds import DevObservation, calibrate  # noqa: E402
from medakis.rag.vector_store.chroma_store import ChromaVectorStore  # noqa: E402
from medakis.rag.vector_store.index_builder import build_index  # noqa: E402
from medakis.rag.vector_store.location import check_store_dir  # noqa: E402

EVAL = ROOT / "evaluation" / "asama7"
CORPUS = ROOT / "corpus" / "kontrollu"
OUT = ROOT / "docs" / "veri" / "asama7_s3_giris_deneyi.json"
V2 = ChunkConfig(version="parcalama_v2", exclude_intro=True)
CANDIDATES = ("bge-m3", "qwen3-embedding:0.6b")
FOLDS = 5


def group_cv(rows: list[dict]) -> dict:
    fold = lambda g: int(hashlib.sha256(g.encode()).hexdigest()[:8], 16) % FOLDS  # noqa: E731
    flags, taus = [], []
    for f in range(FOLDS):
        train = [DevObservation(r["vaka"], "dev", r["ans"], r["d1"]) for r in rows if fold(r["grup"]) != f]
        tau = calibrate(train).max_distance
        taus.append(None if tau is None else round(tau, 4))
        flags += [(r["ans"], r["d1"] is None or tau is None or r["d1"] > tau) for r in rows if fold(r["grup"]) == f]
    unans = [a for ans, a in flags if not ans]
    ans = [a for ans, a in flags if ans]
    return {"kacinma_recall": round(sum(unans) / len(unans), 3), "yanlis_kacinma": round(sum(ans) / len(ans), 3), "kat_esikleri": taus}


def measure(ret: Retriever, parts: list[dict], cases: list) -> dict:
    intro_ids = {p["chunk_id"] for p in parts if p["section"] == INTRO}
    rows, outcomes = [], []
    for c in cases:
        res = ret.retrieve(c.question, as_of=c.as_of, evaluation=True)
        ranked = tuple(ch.chunk_id for ch in res.chunks)
        gold = gold_chunk_ids(c.gold, parts)
        d1 = res.chunks[0].distance if res.chunks else None
        rows.append({"vaka": c.case_key, "grup": c.paraphrase_group, "ans": c.answerable, "d1": d1, "ranked": ranked, "gold": gold,
                     "giris_ilk1": bool(ranked) and ranked[0] in intro_ids, "giris_ilk5": sum(x in intro_ids for x in ranked[:5])})  # fmt: skip
    cal = calibrate([DevObservation(r["vaka"], "dev", r["ans"], r["d1"]) for r in rows])
    tau = cal.max_distance
    for r in rows:
        outcomes.append(CaseOutcome(r["vaka"], r["ans"], r["ranked"], r["gold"], r["d1"] is None or tau is None or r["d1"] > tau))
    s = summarize(outcomes)
    return {
        "recall_at_5": round(s.recall_at_5, 4), "mrr_at_10": round(s.mrr_at_10, 4), "esik": None if tau is None else round(tau, 4),
        "orneklem_ici_kacinma": round(s.abstention_recall, 3), "orneklem_ici_yanlis_kacinma": round(s.false_abstention, 3),
        "cv": group_cv(rows), "giris_ilk1_vaka": sorted(r["vaka"] for r in rows if r["giris_ilk1"]),
        "giris_ilk5_toplam": sum(r["giris_ilk5"] for r in rows), "parca": len(parts),
        "vakalar": [{"vaka": r["vaka"], "ans": r["ans"], "d1": None if r["d1"] is None else round(r["d1"], 4),
                     "ilk_gold": next((i + 1 for i, x in enumerate(r["ranked"]) if x in set(r["gold"])), None)} for r in rows],
    }  # fmt: skip


def decide(old: dict, new: dict) -> dict:
    pairs = {"cv_kacinma": (old["cv"]["kacinma_recall"], new["cv"]["kacinma_recall"], +1),
             "cv_yanlis_kacinma": (old["cv"]["yanlis_kacinma"], new["cv"]["yanlis_kacinma"], -1),
             "recall_at_5": (old["recall_at_5"], new["recall_at_5"], +1), "mrr_at_10": (old["mrr_at_10"], new["mrr_at_10"], +1)}  # fmt: skip
    worse = [k for k, (a, b, sign) in pairs.items() if (b - a) * sign < -1e-9]
    better = [k for k, (a, b, sign) in pairs.items() if (b - a) * sign > 1e-9]
    return {"kotulesen": worse, "iyilesen": better, "kabul": not worse and bool(better)}


def main() -> int:
    settings = load_settings(env={})
    real = check_store_dir(settings.rag_store_dir, min_free_gb=settings.rag_min_free_gb)
    exp = check_store_dir(ROOT / "data" / "rag_deney_s3", min_free_gb=settings.rag_min_free_gb)
    shutil.copy2(real / LEDGER, exp / LEDGER)  # onay izinin salt kopyası (zincir aynen)
    common = load_rag_profiles(settings)["common"]
    cases = [c for n in ("dev.jsonl", "dev_ek.jsonl") for c in load_dev_set(EVAL / n).cases]
    out = {"dev_vaka": len(cases), "v2_parcalama": V2.as_dict(), "kabul_kurali": "bge-m3: hiçbir ölçü kötüleşmez ve en az biri iyileşir", "adaylar": {}}
    for model in CANDIDATES:
        emb = OllamaEmbedder(model, common["ollama_base_url"], 2048, float(common["timeout_seconds"]), keep_alive="5m")
        real_meta, real_vec = FileMetadataStore(CORPUS, real), ChromaVectorStore(real)
        key1 = real_meta.registry().ready_keys(model)[0]
        old = measure(Retriever(real_meta, real_vec, emb, model), real_meta.registry().manifest(key1)["parcalar"], cases)
        real_vec.close()
        exp_meta, exp_vec = FileMetadataStore(CORPUS, exp), ChromaVectorStore(exp)
        built = build_index(chunk_corpus(exp_meta.corpus().documents, V2), V2, emb, exp_vec, exp_meta.registry())
        new = measure(Retriever(exp_meta, exp_vec, emb, model), exp_meta.registry().manifest(built.index_key)["parcalar"], cases)
        exp_vec.close()
        OllamaEmbedder(model, common["ollama_base_url"], keep_alive=0).embed_one("boşaltma")
        out["adaylar"][model] = {"v1_indeks": key1, "v2_indeks": built.index_key, "v1": old, "v2": new, "karar": decide(old, new)}
        print(model, json.dumps({k: (old[k], new[k]) for k in ("recall_at_5", "mrr_at_10", "orneklem_ici_kacinma", "orneklem_ici_yanlis_kacinma")}),
              "CV", old["cv"]["kacinma_recall"], old["cv"]["yanlis_kacinma"], "->", new["cv"]["kacinma_recall"], new["cv"]["yanlis_kacinma"],
              "| giris ilk1:", old["giris_ilk1_vaka"], "| karar:", out["adaylar"][model]["karar"])  # fmt: skip
    # Gerçek depo bütünlüğü: hazır indeksler aynen duruyor mu?
    reg = FileMetadataStore(CORPUS, real).registry()
    out["gercek_depo_hazir_indeksler"] = {reg.manifest(k)["embedding"]["model"]: k for k in reg.ready_keys()}
    out["gercek_depo_esik"] = {k: reg.threshold(k) for k in reg.ready_keys()}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("gercek depo:", out["gercek_depo_hazir_indeksler"], out["gercek_depo_esik"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
