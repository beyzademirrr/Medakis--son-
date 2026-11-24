"""D1 deneyi: ayıklama sonrası aday havuzu tamamlama (ön kayıt: docs/raporlar/ASAMA_8_D0_D2_DENEY_KAYDI.md §3).

    .\\.venv\\Scripts\\python.exe scripts\\rag_d1_aday_tamamlama.py

- Dört yöntem (bge-m3, değerlendirme kipi, eşiksiz): v1, parcalama_v2, v1 + tamamlama, parcalama_v2 + tamamlama.
- v1: gerçek depodaki HAZIR indeks yalnız okunur. v2: S3 deney deposundaki mevcut indeks (yeniden kurulmaz).
- Ölçüler (30 cevaplanabilir; gold her indeksin kendi parça eşlemesiyle): Recall@5, Recall@10, MRR@10; tekil aday
  sayısı (60 vaka); sıcak getirme gecikmesi (ısınma sorgusu sonrası); seçim/teyit ayrı.
- Model çağrısı yok; eşik yazılmaz; gerçek indeks değişmez (önce/sonra karşılaştırılır).
Çıktı: docs/veri/asama8_d1_aday_tamamlama.json
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_rag_profiles, load_settings  # noqa: E402
from medakis.rag.chunking.markdown_sections import INTRO  # noqa: E402
from medakis.rag.embeddings.ollama_client import OllamaEmbedder  # noqa: E402
from medakis.rag.evaluation.eval_set import gold_chunk_ids, load_dev_set  # noqa: E402
from medakis.rag.evaluation.metrics import recall_at_k, reciprocal_rank  # noqa: E402
from medakis.rag.metadata.file_store import FileMetadataStore  # noqa: E402
from medakis.rag.retrieval.retriever import RetrievalConfig, Retriever  # noqa: E402
from medakis.rag.vector_store.chroma_store import ChromaVectorStore  # noqa: E402
from medakis.rag.vector_store.location import check_store_dir  # noqa: E402

EVAL = ROOT / "evaluation" / "asama7"
CORPUS = ROOT / "corpus" / "kontrollu"
OUT = ROOT / "docs" / "veri" / "asama8_d1_aday_tamamlama.json"
MODEL = "bge-m3"


def split_of(group: str) -> str:
    return "teyit" if int(hashlib.sha256(("secim_teyit_v1:" + group).encode()).hexdigest()[:8], 16) % 3 == 0 else "secim"


def load_cases():
    return [c for n in ("dev.jsonl", "dev_ek.jsonl") for c in load_dev_set(EVAL / n).cases]


def registry_state(store: Path) -> dict:
    reg = FileMetadataStore(CORPUS, store).registry()
    return {k: {"model": reg.manifest(k)["embedding"]["model"], "esik": reg.threshold(k),
                "manifest_sha256": hashlib.sha256(json.dumps(reg.manifest(k), sort_keys=True).encode()).hexdigest()}
            for k in reg.ready_keys()}  # fmt: skip


def agg(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["ans"]]
    uniq = [r["tekil_aday"] for r in rows]
    lat = sorted(r["sure_ms"] for r in rows)
    return {
        "recall_at_5": round(statistics.mean(r["r5"] for r in ans), 4), "recall_at_10": round(statistics.mean(r["r10"] for r in ans), 4),
        "mrr_at_10": round(statistics.mean(r["mrr"] for r in ans), 4),
        "tekil_aday_en_az": min(uniq), "tekil_aday_medyan": statistics.median(uniq), "on_alti_vaka": sum(u < 10 for u in uniq),
        "on_alti_cevaplanabilir": sum(r["tekil_aday"] < 10 for r in ans),
        "sure_medyan_ms": round(statistics.median(lat), 1), "sure_p95_ms": round(lat[min(len(lat) - 1, int(0.95 * (len(lat) - 1) + 0.5))], 1),
        "gold_ilk5_disi": sorted(r["vaka"] for r in ans if r["r5"] < 1), "gold_ilk10_disi": sorted(r["vaka"] for r in ans if r["r10"] < 1),
    }  # fmt: skip


def measure(ret: Retriever, parts: list[dict], cases, intro: set[str]) -> list[dict]:
    ret.retrieve("Isınma sorgusu: yetkinlik nedir?", evaluation=True)  # sıcak ölçüm için
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        res = ret.retrieve(c.question, as_of=c.as_of, evaluation=True)
        ms = (time.perf_counter() - t0) * 1000
        ranked = [ch.chunk_id for ch in res.chunks]
        gold = gold_chunk_ids(c.gold, parts)
        first = next((i + 1 for i, x in enumerate(ranked) if x in set(gold)), None)
        rows.append({"vaka": c.case_key, "grup": c.paraphrase_group, "kume": split_of(c.paraphrase_group), "ans": c.answerable,
                     "sonuc": res.outcome.value, "tekil_aday": len(ranked), "ilk_gold": first, "gold_parca": len(gold),
                     "r5": recall_at_k(ranked, gold, 5) if gold else None, "r10": recall_at_k(ranked, gold, 10) if gold else None,
                     "mrr": reciprocal_rank(ranked, gold, 10) if gold else None, "sure_ms": round(ms, 1),
                     "tamamlama": next((w for w in res.warnings if w.startswith("aday_tamamlama")), None),
                     "ilk5": [{"parca": ch.chunk_id[:12], "belge": f"{ch.source_key}@{ch.version}", "bolum": ch.section,
                               "uzaklik": round(ch.distance, 4), "giris": ch.chunk_id in intro, "gold": ch.chunk_id in set(gold)}
                              for ch in res.chunks[:5]]})  # fmt: skip
    return rows


def decide(base: dict, new: dict) -> dict:
    worse = [k for k in ("recall_at_5", "recall_at_10", "mrr_at_10") if new[k] < base[k] - 1e-9]
    fewer = new["on_alti_vaka"] < base["on_alti_vaka"]
    lat_ok = new["sure_medyan_ms"] <= 2 * base["sure_medyan_ms"]
    return {"kotulesen": worse, "on_alti_azaldi": fewer, "gecikme_2x_icinde": lat_ok, "kabul": not worse and fewer and lat_ok}


def main() -> int:
    settings = load_settings(env={})
    real = check_store_dir(settings.rag_store_dir, min_free_gb=settings.rag_min_free_gb)
    exp = check_store_dir(ROOT / "data" / "rag_deney_s3", min_free_gb=settings.rag_min_free_gb)
    before = registry_state(real)
    common = load_rag_profiles(settings)["common"]
    emb = OllamaEmbedder(MODEL, common["ollama_base_url"], 2048, float(common["timeout_seconds"]), keep_alive="5m")
    cases = load_cases()
    out = {"on_kayit": "docs/raporlar/ASAMA_8_D0_D2_DENEY_KAYDI.md §3", "model": MODEL, "vaka": len(cases),
           "kume_sayilari": {k: sum(split_of(c.paraphrase_group) == k for c in cases) for k in ("secim", "teyit")}, "yontemler": {}}  # fmt: skip
    rows_all = {}
    for name, store, refill in (("v1", real, False), ("parcalama_v2", exp, False), ("v1_tamamlama", real, True), ("parcalama_v2_tamamlama", exp, True)):
        meta, vec = FileMetadataStore(CORPUS, store), ChromaVectorStore(store)
        key = meta.registry().ready_keys(MODEL)[0]
        parts = meta.registry().manifest(key)["parcalar"]
        intro = {p["chunk_id"] for p in parts if p["section"] == INTRO}
        rows = measure(Retriever(meta, vec, emb, MODEL, config=RetrievalConfig(refill=refill)), parts, cases, intro)
        vec.close()
        rows_all[name] = rows
        out["yontemler"][name] = {"indeks": key, "parca": len(parts), "tamamlama": refill, "tum": agg(rows),
                                  **{k: agg([r for r in rows if r["kume"] == k]) for k in ("secim", "teyit")}, "vakalar": rows}  # fmt: skip
        s = out["yontemler"][name]["tum"]
        print(name, {k: s[k] for k in ("recall_at_5", "recall_at_10", "mrr_at_10", "tekil_aday_en_az", "on_alti_vaka", "sure_medyan_ms", "sure_p95_ms")}, flush=True)
    Y = out["yontemler"]
    out["karar"] = {"v1_tamamlama_vs_v1": decide(Y["v1"]["tum"], Y["v1_tamamlama"]["tum"]),
                    "parcalama_v2_tamamlama_vs_parcalama_v2": decide(Y["parcalama_v2"]["tum"], Y["parcalama_v2_tamamlama"]["tum"])}  # fmt: skip
    out["e001"] = {n: next(r for r in rows_all[n] if r["vaka"] == "E001") for n in rows_all}
    OllamaEmbedder(MODEL, common["ollama_base_url"], keep_alive=0).unload()
    after = registry_state(real)
    out["gercek_depo"] = {"once": before, "sonra": after, "degismedi": before == after}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("karar:", json.dumps(out["karar"], ensure_ascii=False))
    print("gercek depo degismedi:", out["gercek_depo"]["degismedi"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
