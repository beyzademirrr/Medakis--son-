"""A6 analizi: yalnız cosine eşiği ile cosine + kanıt kapsaması karşılaştırması (yalnız dev; grup bazlı 5 katlı CV).

    .\\.venv\\Scripts\\python.exe scripts\\rag_kanit_denetimi_analizi.py

- Girdi: docs/veri/asama7_dev_getirme_dokumu.json (dev getirme dökümü) ve denetimle düzeltilmiş dev etiketleri.
- Yöntemler: M0 yalnız cosine; M1 yalnız kapsama; M2 cosine VE kapsama; M3 cosine VE cevap türü uyumu (sayı soran
  soruda ilk 3 parçadan biri sayı içermeli). M3'te ayarlanan tek eşik cosine'dir.
- Kapsama parametreleri ÖNCEDEN sabit (genel terim eşiği 0,3; ilk 3 parça). Eşikler yalnız eğitim katlarında seçilir:
  yanlış kaçınma ≤ 0,15 koşulunda kaçınma recall'u en yüksek; eşitlikte yanlış kaçınması düşük, sonra en temkinli.
  Duyarlılık tablosu (başka parametreler) yalnız bilgi içindir, seçimde kullanılmaz.
- Test verisi okunmaz. Eşik yazılmaz; normal sorgu kapalı kalır.
Çıktı: docs/veri/asama7_kanit_denetimi_analizi.json
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.rag.chunking.chunker import chunk_corpus  # noqa: E402
from medakis.rag.evaluation.eval_set import gold_chunk_ids, load_dev_set  # noqa: E402
from medakis.rag.evaluation.metrics import reciprocal_rank, recall_at_k  # noqa: E402
from medakis.rag.ingestion.corpus_loader import load_corpus  # noqa: E402
from medakis.rag.retrieval.support import TermStats, answer_type_ok, coverage  # noqa: E402

EVAL = ROOT / "evaluation" / "asama7"
DUMP = ROOT / "docs" / "veri" / "asama7_dev_getirme_dokumu.json"
OUT = ROOT / "docs" / "veri" / "asama7_kanit_denetimi_analizi.json"
MAX_FA, FOLDS = 0.15, 5
FIXED = {"max_df": 0.3, "k": 3}
TC_GRID = [round(x / 10, 1) for x in range(11)]


def rates(rows, answer):  # answer: satır → cevap verilir mi
    unans = [not answer(r) for r in rows if not r["ans"]]
    ans = [not answer(r) for r in rows if r["ans"]]
    return (sum(unans) / len(unans) if unans else None, sum(ans) / len(ans) if ans else None)


def choose(rows, method):
    """Eğitim satırlarında eşik(ler) seçimi."""
    tds = sorted({r["d1"] for r in rows if r["d1"] is not None}) + [math.inf]
    tcs = TC_GRID if method in ("M1", "M2") else [0.0]
    if method == "M1":
        tds = [math.inf]
    best = None
    for td in tds:
        for tc in tcs:
            rule = lambda r, td=td, tc=tc: _ok(r, method, td, tc)  # noqa: E731
            rec, fa = rates(rows, rule)
            if fa is None or rec is None or fa > MAX_FA:
                continue
            key = (-rec, fa, td, -tc)
            if best is None or key < best[0]:
                best = (key, td, tc)
    return None if best is None else (best[1], best[2])


def apply(r, method, params):
    if params is None:
        return False  # uygun eşik yok → kaçın (fail-closed)
    return _ok(r, method, *params)


def _ok(r, method, td, tc):
    if r["d1"] is None or r["d1"] > td:
        return False
    if method in ("M1", "M2"):
        return r["cov"] >= tc
    if method == "M3":
        return r["tur_uyumu"]
    return True


def cv(rows, method):
    fold = lambda r: int(hashlib.sha256(r["grup"].encode()).hexdigest()[:8], 16) % FOLDS  # noqa: E731
    flags, chosen = [], []
    for f in range(FOLDS):
        params = choose([r for r in rows if fold(r) != f], method)
        chosen.append(None if params is None else [None if math.isinf(params[0]) else round(params[0], 4), params[1]])
        flags += [(r["ans"], apply(r, method, params), r["vaka"]) for r in rows if fold(r) == f]
    unans = [not a for ans, a, _ in flags if not ans]
    ans = [not a for ans, a, _ in flags if ans]
    return {"kacinma_recall": round(sum(unans) / len(unans), 3), "yanlis_kacinma": round(sum(ans) / len(ans), 3),
            "kacinilan_cevaplanabilir": sorted(v for ans, a, v in flags if ans and not a),
            "cevaplanan_kaynak_disi": sorted(v for ans, a, v in flags if not ans and a), "kat_parametreleri": chosen}  # fmt: skip


def main() -> int:
    dump = json.loads(DUMP.read_text(encoding="utf-8"))
    cases = {c.case_key: c for n in ("dev.jsonl", "dev_ek.jsonl") for c in load_dev_set(EVAL / n).cases}
    chunks = chunk_corpus(load_corpus(ROOT / "corpus" / "kontrollu").documents)
    stats = TermStats.from_chunks(f"{c.section} {c.text}" for c in chunks)
    parts = [c.map_entry() for c in chunks]
    out = {"sabit_parametreler": FIXED, "yanlis_kaçınma_siniri": MAX_FA, "katlar": FOLDS, "adaylar": {}}
    for model, data in dump["adaylar"].items():
        rows = []
        for v in data["vakalar"]:
            c = cases[v["vaka"]]
            res = v["sonuclar"]
            gold = gold_chunk_ids(c.gold, parts)
            ranked = [s["parca"] for s in res]
            row = {"vaka": c.case_key, "grup": c.paraphrase_group, "ans": c.answerable, "d1": res[0]["uzaklik"] if res else None,
                   "recall5": recall_at_k(ranked, gold) if c.answerable else None, "rr10": reciprocal_rank(ranked, gold) if c.answerable else None}  # fmt: skip
            for mdf in (0.2, 0.3, 0.5):
                for k in (1, 3, 5):
                    row[f"cov_{mdf}_{k}"] = coverage(c.question, [f"{s['bolum']} {s['metin']}" for s in res[:k]], stats, mdf)
            row["cov"] = row[f"cov_{FIXED['max_df']}_{FIXED['k']}"]
            row["tur_uyumu"] = answer_type_ok(c.question, [s["metin"] for s in res[:FIXED["k"]]])
            rows.append(row)
        ans = [r for r in rows if r["ans"]]
        res_model = {
            "recall_at_5": round(sum(r["recall5"] for r in ans) / len(ans), 3), "mrr_at_10": round(sum(r["rr10"] for r in ans) / len(ans), 3),
            "yontemler": {m: {"cv": cv(rows, m), "orneklem_ici_parametre": choose(rows, m)} for m in ("M0", "M1", "M2", "M3")},
            "duyarlilik_bilgi_amacli": {},
            "vakalar": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if not k.startswith("cov_")} for r in rows],
        }  # fmt: skip
        for mdf in (0.2, 0.3, 0.5):
            for k in (1, 3, 5):
                tmp = [{**r, "cov": r[f"cov_{mdf}_{k}"]} for r in rows]
                c2 = cv(tmp, "M2")
                res_model["duyarlilik_bilgi_amacli"][f"max_df={mdf},k={k}"] = [c2["kacinma_recall"], c2["yanlis_kacinma"]]
        out["adaylar"][model] = res_model
        print(model, "recall", res_model["recall_at_5"], "mrr", res_model["mrr_at_10"])
        for m, r in res_model["yontemler"].items():
            print("  ", m, "CV kacinma", r["cv"]["kacinma_recall"], "yanlis", r["cv"]["yanlis_kacinma"],
                  "| kacan cevap.", r["cv"]["kacinilan_cevaplanabilir"], "| gecen kaynak disi", r["cv"]["cevaplanan_kaynak_disi"])
        print("   duyarlilik M2:", res_model["duyarlilik_bilgi_amacli"])
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
