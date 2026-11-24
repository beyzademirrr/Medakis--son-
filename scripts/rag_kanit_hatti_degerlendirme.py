"""Aşama 8.5 dev ön değerlendirmesi: cosine eşiği ile kanıt denetimli hattın A6 karşılaştırması (yalnız dev; TEST kipi).

    .\\.venv\\Scripts\\python.exe scripts\\rag_kanit_hatti_degerlendirme.py

- Veri: dev.jsonl + dev_ek.jsonl (60 vaka). Kör test ve 56 vakalık ayrılmış küme okunmaz.
- Sıra: önce 60 sorunun retrieval'ı (BGE-M3, değerlendirme kipi), ardından embedding boşaltılır; sonra Gemma 3 4B.
- Politikalar:
  M0 yalnız cosine (eşik grup CV eğitim katlarında);
  P1 `esik` + kanıt denetimi (eşik aynı CV eğitim katlarında; denetçi yalnız eşik altı adayları görür);
  P2 `aday_deneyi` + kanıt denetimi (eşik yok; ayarlanan parametre yok → tek ölçüm, CV gerekmez).
- Ölçüler: doğru kaçınma (kaynak dışı), yanlış kaçınma (cevaplanabilir), cevaplanan kaynak dışı sorular, gold isabeti,
  sahte alıntı, hata (unavailable), model çağrısı, Gemma süresi, VRAM tepe.
- Eşik kayda yazılmaz; normal sorgu kapalı kalır. Çıktı: docs/veri/asama8_kanit_hatti_dev.json
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_settings  # noqa: E402
from medakis.rag.contracts.dto import RagOutcome  # noqa: E402
from medakis.rag.embeddings.ollama_client import OllamaEmbedder  # noqa: E402
from medakis.rag.evaluation.eval_set import load_dev_set  # noqa: E402
from medakis.rag.generation.mode import GenerationMode  # noqa: E402
from medakis.rag.generation.ollama_generator import OllamaGenerator  # noqa: E402
from medakis.rag.generation.pipeline import POLICY_CANDIDATES, POLICY_THRESHOLD, EvidencePipeline  # noqa: E402
from medakis.rag.retrieval.factory import open_retriever  # noqa: E402
from medakis.rag.retrieval.thresholds import DevObservation, calibrate  # noqa: E402

EVAL = ROOT / "evaluation" / "asama7"
OUT = ROOT / "docs" / "veri" / "asama8_kanit_hatti_dev.json"
FOLDS, MODEL = 5, "bge-m3"


class CachingGenerator:
    """Aynı istem için Gemma bir kez çağrılır (deterministik ayarlar); süre ve VRAM kaydedilir."""

    def __init__(self, inner) -> None:
        self.inner, self.cache, self.durations, self.vram = inner, {}, [], []

    def chat_json(self, messages, schema):
        key = hashlib.sha256(json.dumps(list(messages), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key not in self.cache:
            t0 = time.perf_counter()
            self.cache[key] = self.inner.chat_json(messages, schema)
            self.durations.append((time.perf_counter() - t0) * 1000)
            v = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
            if v.isdigit():
                self.vram.append(int(v))
        return self.cache[key]


def pct(values, q):
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def fold_of(group: str) -> int:
    return int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % FOLDS


def gold_hit(result, case) -> bool:
    return any(q.source_key + "@" + q.version == g.doc_uid and q.doc_start < g.char_end and g.char_start < q.doc_end
               for q in result.quotes for g in case.gold)  # fmt: skip


def summarize(rows: list[dict]) -> dict:
    oos = [r for r in rows if not r["ans"]]
    ans = [r for r in rows if r["ans"]]
    answered_ans = [r for r in ans if not r["kacindi"]]
    return {
        "dogru_kacinma": round(sum(r["kacindi"] for r in oos) / len(oos), 3),
        "yanlis_kacinma": round(sum(r["kacindi"] for r in ans) / len(ans), 3),
        "cevaplanan_kaynak_disi": sorted(r["vaka"] for r in oos if not r["kacindi"]),
        "kacinilan_cevaplanabilir": sorted(r["vaka"] for r in ans if r["kacindi"]),
        "gold_isabeti": round(sum(r["gold"] for r in answered_ans) / len(answered_ans), 3) if answered_ans else None,
        "sahte_alinti": sum(r["sahte"] for r in rows), "hata_unavailable": sum(r["sonuc"] == "unavailable" for r in rows),
        "model_cagrisi": sum(r["cagri"] for r in rows),
    }  # fmt: skip


def row(case, result) -> dict:
    return {"vaka": case.case_key, "grup": case.paraphrase_group, "ans": case.answerable, "sonuc": result.outcome.value,
            "kacindi": result.outcome is not RagOutcome.ANSWERED, "gold": gold_hit(result, case),
            "sahte": any(w.startswith("sahte_alinti:") for w in result.warnings), "cagri": result.model_calls,
            "uyari": list(result.warnings)[:2]}  # fmt: skip


def main() -> int:
    settings = load_settings(env={})
    cases = [c for n in ("dev.jsonl", "dev_ek.jsonl") for c in load_dev_set(EVAL / n).cases]
    retriever = open_retriever(settings, embedder=OllamaEmbedder(MODEL, keep_alive="5m"), model=MODEL)
    retrievals = {c.case_key: retriever.retrieve(c.question, c.as_of, evaluation=True) for c in cases}
    retriever.vectors.close()
    OllamaEmbedder(MODEL, keep_alive=0).embed_one("boşaltma")  # sıralı yükleme: embedding boşalır, sonra üretici
    gen = CachingGenerator(OllamaGenerator(GenerationMode.TEST, keep_alive="10m"))
    pipe = EvidencePipeline(retriever, gen, GenerationMode.TEST)
    d1 = {k: (r.chunks[0].distance if r.chunks else None) for k, r in retrievals.items()}

    # M0 ve P1: eşik grup CV eğitim katlarında
    m0_rows, p1_rows, taus = [], [], []
    for f in range(FOLDS):
        train = [DevObservation(c.case_key, "dev", c.answerable, d1[c.case_key]) for c in cases if fold_of(c.paraphrase_group) != f]
        tau = calibrate(train).max_distance
        taus.append(None if tau is None else round(tau, 4))
        for c in (c for c in cases if fold_of(c.paraphrase_group) == f):
            abst = d1[c.case_key] is None or tau is None or d1[c.case_key] > tau
            m0_rows.append({"vaka": c.case_key, "grup": c.paraphrase_group, "ans": c.answerable, "sonuc": "no_source" if abst else "found",
                            "kacindi": abst, "gold": False, "sahte": False, "cagri": 0})  # fmt: skip
            p1_rows.append(row(c, pipe.decide(c.question, retrievals[c.case_key], POLICY_THRESHOLD, tau)))
    # P2: eşiksiz aday deneyi (ayarlanan parametre yok)
    p2_rows = [row(c, pipe.decide(c.question, retrievals[c.case_key], POLICY_CANDIDATES)) for c in cases]
    out = {
        "dev_vaka": len(cases), "model": {"embedding": MODEL, "uretici": "gemma3:4b"}, "cv_kat_esikleri": taus,
        "M0_yalniz_cosine_cv": summarize(m0_rows), "P1_esik_kanit_cv": summarize(p1_rows), "P2_aday_kanit": summarize(p2_rows),
        "gemma": {"benzersiz_cagri": len(gen.durations), "sure_ms_p50": pct(gen.durations, 0.5), "sure_ms_p95": pct(gen.durations, 0.95),
                  "sure_ms_en_fazla": round(max(gen.durations), 1) if gen.durations else None, "vram_tepe_mib": max(gen.vram) if gen.vram else None},
        "vakalar": {"P1": p1_rows, "P2": p2_rows},
    }  # fmt: skip
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k in ("M0_yalniz_cosine_cv", "P1_esik_kanit_cv", "P2_aday_kanit"):
        print(k, json.dumps(out[k], ensure_ascii=False))
    print("gemma", out["gemma"])
    OllamaGenerator(GenerationMode.TEST, keep_alive=0).describe()
    return 0


if __name__ == "__main__":
    sys.exit(main())
