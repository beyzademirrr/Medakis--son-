"""D5 mimari karşılaştırması — kalite koşusu (ön kayıt: docs/raporlar/ASAMA_8_D5_MIMARI_DENEY_KAYDI.md §2.1).

    .\\.venv\\Scripts\\python.exe scripts\\rag_d5_mimari_kalite.py

A0 (M1, H10), A1 (LAMAR kapısı + M1), A2 (LAMAR kapısı + kaynak sunumu), A3 (ModernBERT kapısı + M1),
A4 (ModernBERT kapısı + kaynak sunumu); 60 dev vakası × 3 eşleştirilmiş tekrar. Kapı skorları D5 fp32 skorları;
eşikler grup CV eğitim katlarından. Gemma her tekrarda soğuk başlar; önbellek yok.
UYARI: Gold TASLAK; kapı kabulü ve kaynak sunumu cevap doğruluğu DEĞİLDİR; A6 kabulü değildir.
Çıktı: docs/veri/asama8_d5_mimari_kalite.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rag_d5_mimari_ortak import (  # noqa: E402
    CORPUS, ROOT, chunk_factory, d5_scores, gate_thresholds, gold_overlap, load_pool, retrieval_for,
)  # fmt: skip

from medakis.rag.evaluation.contract import classify, summarize_contract  # noqa: E402
from medakis.rag.generation import methods as M  # noqa: E402
from medakis.rag.generation.gate import apply_gate, present_sources  # noqa: E402
from medakis.rag.generation.mode import GenerationMode  # noqa: E402
from medakis.rag.generation.ollama_generator import GEMMA3_4B_DIGEST, OllamaGenerator  # noqa: E402
from medakis.rag.generation.pipeline import POLICY_CANDIDATES, EvidencePipeline  # noqa: E402
from medakis.rag.ingestion.corpus_loader import load_corpus  # noqa: E402

OUT = ROOT / "docs" / "veri" / "asama8_d5_mimari_kalite.json"
REPS = 3
GEMMA_ARCH = {"A0": None, "A1": "lamar", "A3": "modernbert"}
PRESENT_ARCH = {"A2": "lamar", "A4": "modernbert"}
DRAFT = "Gold TASLAK; kapı kabulü ve kaynak sunumu cevap doğruluğu değildir; dev verisi; A6 kabulü değildir."


def main() -> int:
    data = load_pool()
    make = chunk_factory(data)
    docs = load_corpus(CORPUS).by_uid()
    thr, thr_info = {}, {}
    for m in ("lamar", "modernbert"):
        thr[m], thr_info[m] = gate_thresholds(data, m)
    scores = {m: d5_scores(m) for m in ("lamar", "modernbert")}
    gen = OllamaGenerator(GenerationMode.TEST, keep_alive="5m")
    digest = gen.describe()
    if digest != GEMMA3_4B_DIGEST:
        raise SystemExit("gemma digest farklı")
    pipe = EvidencePipeline(object(), gen, GenerationMode.TEST, method=M.METHOD_STRICT)
    rows = []

    def published_ok(res) -> bool:  # yayımlanan her alıntı belgede birebir mi (doğrulamanın bağımsız tekrarı)
        return all(docs[f"{q.source_key}@{q.version}"].text[q.doc_start:q.doc_end] == q.text for q in res.quotes)

    def record(arch, rep, c, outcome, warnings, calls, ms, gate=None, res=None, pres=None, cands=()):
        gold = set(c["gold_parcalar"])
        gin = bool(gold & set(cands)) if c["ans"] else None
        k = classify(c["ans"], outcome, warnings, gin)
        d = res.decision if res is not None else None
        probs = list(d.problems) if d else []
        quotes = list(res.quotes) if res is not None else []
        shown = list(pres.chunks) if pres is not None else []
        return {"mimari": arch, "tekrar": rep, "vaka": c["vaka"], "kume": c["kume"], "ans": c["ans"], "sonuc": outcome, "sinif": k.klass.value,
                "alt_neden": k.reason, "uyarilar": list(warnings), "model_cagrisi": calls, "sure_ms": round(ms, 1),
                "kapi": None if gate is None else {"kabul": gate.accepted, "skor": gate.top_score, "esik": thr[GEMMA_ARCH.get(arch) or PRESENT_ARCH.get(arch)][c["vaka"]]},
                "ham_karar": d.as_dict() if d else None,
                "gecersiz_oneri": sum(1 for p in probs if p.split(":")[-1] in ("alinti_parcada_yok", "parca_getirilmedi", "alinti_cok_kisa")),
                "yayimlanan_gecersiz_alinti": 0 if not quotes or published_ok(res) else len(quotes),
                "alintilar": [{"belge": f"{q.source_key}@{q.version}", "aralik": [q.doc_start, q.doc_end], "metin": q.text} for q in quotes],
                "sunulan": [{"parca": ch.chunk_id, "belge": f"{ch.source_key}@{ch.version}", "bolum": ch.section, "aralik": [ch.quote_start, ch.quote_end]} for ch in shown],
                "gold_kapsami": (gold_overlap(quotes, c["gold_araliklar"]) if quotes else gold_overlap(shown, c["gold_araliklar"]) if shown else False) if c["ans"] else None}  # fmt: skip

    for rep in range(1, REPS + 1):
        gen.unload()
        if gen.loaded_models():
            raise SystemExit("Gemma boşaltılamadı")
        t_rep = time.perf_counter()
        for i, c in enumerate(data["vakalar"]):
            r10 = retrieval_for(c, "H10", make)
            r10p = retrieval_for(c, "H10+", make)
            gates = {m: apply_gate(r10p, scores[m][c["vaka"]], thr[m][c["vaka"]], k=5) for m in ("lamar", "modernbert")}
            archs = list(GEMMA_ARCH)
            for arch in archs[(rep + i) % 3:] + archs[:(rep + i) % 3]:
                m = GEMMA_ARCH[arch]
                retr = r10 if m is None else gates[m].retrieval
                t0 = time.perf_counter()
                res = pipe.decide(c["soru"], retr, POLICY_CANDIDATES)
                ms = (time.perf_counter() - t0) * 1000
                cands = res.candidates or tuple(ch.chunk_id for ch in sorted(retr.chunks, key=lambda x: x.rank)[:5])
                rows.append(record(arch, rep, c, res.outcome.value, res.warnings, res.model_calls, ms, None if m is None else gates[m], res, None, cands))
            for arch, m in PRESENT_ARCH.items():
                t0 = time.perf_counter()
                p = present_sources(gates[m])
                ms = (time.perf_counter() - t0) * 1000
                rows.append(record(arch, rep, c, p.outcome, p.warnings, 0, ms, gates[m], None, p, [ch.chunk_id for ch in gates[m].retrieval.chunks]))
        print(f"tekrar {rep}: {round(time.perf_counter() - t_rep)} sn", flush=True)
    gen.unload()
    remaining = [x.get("name") for x in gen.loaded_models()]

    archs = ["A0", "A1", "A2", "A3", "A4"]
    summary = {}
    for a in archs:
        summary[a] = {}
        for sub in ("tum", "secim", "teyit"):
            per = []
            for rep in range(1, REPS + 1):
                rr = [r for r in rows if r["mimari"] == a and r["tekrar"] == rep and (sub == "tum" or r["kume"] == sub)]
                s = summarize_contract(rr)
                ans_acc = [r for r in rr if r["ans"] and r["kapi"] and r["kapi"]["kabul"]]
                oos_acc = [r for r in rr if not r["ans"] and r["kapi"] and r["kapi"]["kabul"]]
                gate = None
                if a != "A0":
                    oos = [r for r in rr if not r["ans"]]
                    ans = [r for r in rr if r["ans"]]
                    gate = {"kacinma": round(sum(not r["kapi"]["kabul"] for r in oos) / len(oos), 4),
                            "yanlis_kacinma": round(sum(not r["kapi"]["kabul"] for r in ans) / len(ans), 4),
                            "yanlis_kabul": sum(r["kapi"]["kabul"] for r in oos)}  # fmt: skip
                answered_ans = [r for r in rr if r["ans"] and r["sonuc"] in ("answered", "kaynak_sunuldu")]
                per.append({**s, "kapi": gate,
                            "gemma_yeniden_ret_cevaplanabilir": sum(r["sonuc"] == "no_source" for r in ans_acc) if a in ("A1", "A3") else None,
                            "gemma_yeniden_ret_alt": dict(sorted({x["alt_neden"]: sum(y["alt_neden"] == x["alt_neden"] for y in ans_acc if y["sonuc"] == "no_source") for x in ans_acc if x["sonuc"] == "no_source"}.items())) if a in ("A1", "A3") else None,
                            "gemma_kurtardigi_yanlis_kabul": sum(r["sonuc"] != "answered" for r in oos_acc) if a in ("A1", "A3") else None,
                            "teknik_hata": sum(r["sinif"] == "TEKNIK_HATA" for r in rr),
                            "gecersiz_oneri": sum(r["gecersiz_oneri"] for r in rr), "yayimlanan_gecersiz_alinti": sum(r["yayimlanan_gecersiz_alinti"] for r in rr),
                            "gold_kapsami_sunulan_cevaplanabilir": None if not answered_ans else round(sum(bool(r["gold_kapsami"]) for r in answered_ans) / len(answered_ans), 4),
                            "gold_disi_sunum": sorted(r["vaka"] for r in answered_ans if not r["gold_kapsami"]),
                            "yanlis_kabul_vakalar": sorted(r["vaka"] for r in rr if r["sinif"] == "YANLIS_KABUL"),
                            "yanlis_ret_vakalar": sorted(r["vaka"] for r in rr if r["sinif"] == "YANLIS_RET"),
                            "model_cagrisi_ort": round(sum(r["model_cagrisi"] for r in rr) / len(rr), 3),
                            "sure_ms_medyan": round(statistics.median(r["sure_ms"] for r in rr), 1)})  # fmt: skip
            summary[a][sub] = per
    out = {"uyari": DRAFT, "on_kayit": "docs/raporlar/ASAMA_8_D5_MIMARI_DENEY_KAYDI.md", "gemma_digest": digest, "tekrar": REPS,
           "kapi_esikleri": thr_info, "ozet": summary, "bosaltma_sonrasi_yuklu": remaining, "vakalar": rows}  # fmt: skip
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for a in archs:
        for sub in ("secim", "tum"):
            p = summary[a][sub]
            pick = lambda k: [x[k] for x in p]  # noqa: E731
            print(a, sub, "kac", pick("bilincli_kacinma_orani_karar_verilen"), "yret", pick("yanlis_ret_orani_karar_verilen"), "yk", pick("yanlis_kabul_orani"),
                  "tek", pick("teknik_hata"), "ince", pick("cevap_inceleme_bekliyor"), pick("kaynak_sunuldu_inceleme_bekliyor"),
                  "gemma_ret", pick("gemma_yeniden_ret_cevaplanabilir"), "yay_gec", pick("yayimlanan_gecersiz_alinti"), "kapi", p[0]["kapi"])  # fmt: skip
    print("yuklu", remaining)
    return 0


if __name__ == "__main__":
    sys.exit(main())
