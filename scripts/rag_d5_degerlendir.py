"""D5 değerlendirmesi (ön kayıt: docs/raporlar/ASAMA_8_D5_DENEY_KAYDI.md). Model çalıştırmaz; skor dosyalarını okur.

    .\\.venv\\Scripts\\python.exe scripts\\rag_d5_degerlendir.py

- Girdi: D:\\medakis-deney\\d5\\sonuclar\\aday_havuzu.json ve havuz_*_t{1,2,3}.json (izole ortamda üretilen skorlar).
- Sıralama (30 cevaplanabilir), AUROC, grup bazlı CV ile kabul/ret, kararlılık, maliyet; tüm / seçim / teyit.
  * tüm: 60 vakada grup CV; seçim: CV yalnız 41 seçim vakası içinde (seçim kuralı bunu kullanır);
  * teyit: eşik bütün seçim kümesinde kalibre edilip 19 teyit vakasına uygulanır (bağımsız DEĞİL; tutarlılık).
- Reranker kabul/ret başarısı uçtan uca kaynaklı cevap başarısı DEĞİLDİR. Gemma kullanılmaz. Hiçbir eşik yazılmaz.
Çıktı: docs/veri/asama8_d5_sonuclar.json
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.rag.evaluation.answerability import CvCase, accepts, auroc, group_cv, to_distance  # noqa: E402
from medakis.rag.evaluation.metrics import recall_at_k, reciprocal_rank  # noqa: E402
from medakis.rag.retrieval.lexical import BM25, LexDoc, first_sentence, gate, rrf, rule_conflict_in  # noqa: E402
from medakis.rag.retrieval.thresholds import DevObservation, calibrate  # noqa: E402

D5 = Path(r"D:\medakis-deney\d5\sonuclar")
OUT = ROOT / "docs" / "veri" / "asama8_d5_sonuclar.json"
POOLS = ("H10", "H10+", "H20", "H20+")
RERANKERS = {"R2": ("modernbert", "yok"), "R3": ("lamar", "yok"), "R4": ("qwen06", "varsayilan"), "R5": ("qwen06", "cevap"),
             "R6": ("bge", "yok")}  # fmt: skip
COSINE = {"R0": "H10", "R0+": "H10+", "R1-": "H20", "R1": "H20+"}
NO_RERANKER = ("R0", "R0+", "R1-", "R1", "B2")  # ek reranker yok; BGE-M3 embedding modeli GEREKİR (B2 cosine sırasını kullanır)
MODEL_FREE = ("B1", "B1k", "B3")  # tamamen modelsiz (yalnız BM25)
REPS = (1, 2, 3)


def method_outputs(data: dict, scores: dict | None = None, kind: str = "cos", pool: str = "H20+", key: str = "skorlar") -> dict:
    """Her vaka için {sonuc, sira (parça listesi), s}. Retrieval sonucu found değilse aynen geçer (model/sıralama yok)."""
    parts = {p["parca"]: p for p in data["parcalar"]}
    out = {}
    cache: dict = {}
    for c in data["vakalar"]:
        h = c["havuzlar"]["H20+" if kind in ("B2",) else pool] if kind in ("cos", "rr", "B2") else None
        if kind in ("cos", "rr") and h["sonuc"] != "found":
            out[c["vaka"]] = {"sonuc": h["sonuc"], "sira": [], "s": None}
            continue
        if kind == "cos":
            cands = sorted(h["adaylar"], key=lambda a: a["sira"])
            out[c["vaka"]] = {"sonuc": "found", "sira": [a["parca"] for a in cands], "s": -cands[0]["uzaklik"] if cands else None}
        elif kind == "rr":
            sc = scores[c["vaka"]]
            cands = sorted(h["adaylar"], key=lambda a: (-sc[a["parca"]], a["sira"]))
            vals = [sc[a["parca"]] for a in cands]
            bad = any(v != v or v in (float("inf"), float("-inf")) for v in vals)
            out[c["vaka"]] = {"sonuc": "teknik_hata" if bad else "found", "sira": [a["parca"] for a in cands], "s": None if bad or not vals else vals[0]}
        else:  # modelsiz: B1, B1k, B2, B3
            g = gate(data["parcalar"], c["izinli_belgeler"], c["catismalar"])
            if g.outcome != "found":
                out[c["vaka"]] = {"sonuc": g.outcome, "sira": [], "s": None}
                continue
            ck = (kind, tuple(c["izinli_belgeler"]))
            if ck not in cache:
                if kind == "B3":
                    sections: dict = {}
                    for p in g.allowed_parts:
                        sections.setdefault((p["belge"], p["bolum"]), []).append(p)
                    recs = [LexDoc(f"{b}§{s}", f"{ps[0]['kaynak_adi']} {s} {first_sentence(' '.join(x['metin'] for x in ps))}")
                            for (b, s), ps in sections.items()]  # fmt: skip
                    cache[ck] = (BM25(recs), sections)
                else:
                    cache[ck] = (BM25([LexDoc(p["parca"], p["metin"]) for p in g.allowed_parts], prefix=5 if kind == "B1k" else None), None)
            bm, sections = cache[ck]
            ranked = bm.scores(c["soru"])
            if kind == "B3":
                order = [p["parca"] for key_, _ in ranked for p in sections[tuple(key_.split("§", 1))]]
                s = ranked[0][1] if ranked else None
            elif kind == "B2":
                fused = rrf([[a["parca"] for a in sorted(h["adaylar"], key=lambda a: a["sira"])], [k for k, _ in ranked]])
                order, s = [k for k, _ in fused], (fused[0][1] if fused else None)
            else:
                order, s = [k for k, _ in ranked], (ranked[0][1] if ranked else None)
            res = "conflicting_sources" if rule_conflict_in(order[:10], parts) else "found"
            out[c["vaka"]] = {"sonuc": res, "sira": order[:20], "s": s if res == "found" else None}
    return out


def evaluate(data: dict, mo: dict, subset: str) -> dict:
    cases = [c for c in data["vakalar"] if subset == "tum" or c["kume"] == subset]
    ans = [c for c in cases if c["ans"]]
    # Sıralama
    r5 = [recall_at_k(mo[c["vaka"]]["sira"], c["gold_parcalar"], 5) for c in ans]
    r10 = [recall_at_k(mo[c["vaka"]]["sira"], c["gold_parcalar"], 10) for c in ans]
    mrr = [reciprocal_rank(mo[c["vaka"]]["sira"], c["gold_parcalar"], 10) for c in ans]
    top1 = [bool(mo[c["vaka"]]["sira"]) and mo[c["vaka"]]["sira"][0] in set(c["gold_parcalar"]) for c in ans]
    # Ayırma gücü (s yoksa en düşük)
    lo = -1e18
    pos = [mo[c["vaka"]]["s"] if mo[c["vaka"]]["s"] is not None else lo for c in ans]
    neg = [mo[c["vaka"]]["s"] if mo[c["vaka"]]["s"] is not None else lo for c in cases if not c["ans"]]
    # Kabul/ret
    tech = {c["vaka"] for c in cases if mo[c["vaka"]]["sonuc"] == "teknik_hata"}
    sec = {c["vaka"] for c in cases if mo[c["vaka"]]["sonuc"] in ("rejected", "conflicting_sources")}
    if subset == "teyit":
        train = [c for c in data["vakalar"] if c["kume"] == "secim"]
        tau = calibrate([DevObservation(c["vaka"], "dev", c["ans"], to_distance(mo[c["vaka"]]["s"], True)) for c in train]).max_distance
        acc = {c["vaka"]: accepts(mo[c["vaka"]]["s"], tau, True) for c in cases}
        taus = [tau]
    else:
        acc, taus = group_cv([CvCase(c["vaka"], c["grup"], c["ans"], mo[c["vaka"]]["s"]) for c in cases], higher_is_better=True)
    dec = [c for c in cases if c["vaka"] not in tech | sec]
    oos_all = [c for c in cases if not c["ans"]]
    oos_dec = [c for c in dec if not c["ans"]]
    ans_dec = [c for c in dec if c["ans"]]
    bk = sum(not acc[c["vaka"]] for c in oos_dec)
    yk = sum(acc[c["vaka"]] for c in oos_dec)
    yr = sum(not acc[c["vaka"]] for c in ans_dec)
    rate = lambda a, b: None if b == 0 else round(a / b, 4)  # noqa: E731
    kac_k, ykac_k = rate(bk, len(oos_dec)), rate(yr, len(ans_dec))
    d = None if kac_k is None or ykac_k is None else round(max(0, 0.95 - kac_k) + max(0, ykac_k - 0.15), 4)
    accepted_ans = [c for c in ans_dec if acc[c["vaka"]]]
    return {
        "recall_at_5": round(statistics.mean(r5), 4), "recall_at_10": round(statistics.mean(r10), 4), "mrr_at_10": round(statistics.mean(mrr), 4),
        "gold_ilk1": round(sum(top1) / len(top1), 4), "auroc": None if auroc(pos, neg) is None else round(auroc(pos, neg), 4),
        "dogru_kacinma_karar": kac_k, "dogru_kacinma_tum": rate(bk, len(oos_all)), "yanlis_kabul": yk, "yanlis_kabul_orani": rate(yk, len(oos_all)),
        "yanlis_kacinma_karar": ykac_k, "yanlis_kacinma_tum": rate(len(ans) - len(accepted_ans), len(ans)), "yanlis_ret": yr,
        "teknik_hata": len(tech), "guvenlik_reddi": sum(mo[c["vaka"]]["sonuc"] == "rejected" for c in cases),
        "kaynak_catismasi": sum(mo[c["vaka"]]["sonuc"] == "conflicting_sources" for c in cases),
        "kabul_edilen_cevaplanabilirde_gold_ilk1": rate(sum(mo[c["vaka"]]["sira"][:1] and mo[c["vaka"]]["sira"][0] in set(c["gold_parcalar"]) for c in accepted_ans), len(accepted_ans)),
        "a6_uzaklik": d, "a6_karsilandi": d == 0.0, "esikler": taus,
        "kabul": {c["vaka"]: acc[c["vaka"]] for c in cases},
    }  # fmt: skip


def spread(vals: list) -> dict:
    v = [x for x in vals if x is not None]
    if not v:
        return {"ort": None, "ss": None, "en_kotu": None, "tekrarlar": vals}
    return {"ort": round(statistics.mean(v), 4), "ss": round(statistics.pstdev(v), 4), "min": min(v), "max": max(v), "tekrarlar": vals}


def main() -> int:
    data = json.loads((D5 / "aday_havuzu.json").read_text(encoding="utf-8"))
    runs = {}
    for code, (model, tal) in RERANKERS.items():
        runs[code] = {t: json.loads((D5 / f"havuz_{model}_{tal}_cuda_t{t}.json").read_text(encoding="utf-8")) for t in REPS}
    smoke = {}
    for model, tal in set(RERANKERS.values()):
        f = D5 / f"smoke_{model}_{tal}_cuda_t1.json"
        smoke[f"{model}/{tal}"] = json.loads(f.read_text(encoding="utf-8"))["gecti"] if f.exists() else None
    methods: dict = {}
    for code, pool in COSINE.items():
        mo = method_outputs(data, kind="cos", pool=pool)
        methods[code] = {"tur": "ek_reranker_yok_bge_m3", "havuz": pool, "tekrarlar": [{s: evaluate(data, mo, s) for s in ("tum", "secim", "teyit")}]}
    for code in ("B1", "B1k", "B2", "B3"):
        mo = method_outputs(data, kind=code)
        methods[code] = {"tur": "ek_reranker_yok_bge_m3" if code == "B2" else "modelsiz", "havuz": "izinli belgeler", "tekrarlar": [{s: evaluate(data, mo, s) for s in ("tum", "secim", "teyit")}]}
    for code, (model, tal) in RERANKERS.items():
        for pool in POOLS:
            name = code if pool == "H20+" else f"{code}@{pool}"
            reps = [{s: evaluate(data, method_outputs(data, runs[code][t]["skorlar"], "rr", pool), s) for s in ("tum", "secim", "teyit")} for t in REPS]
            methods[name] = {"tur": "reranker", "model": model, "talimat": tal, "havuz": pool, "tekrarlar": reps}
        if model == "qwen06":  # keşif: P(yes)'in tekdüze karşılığı log-oran (doyma eşitliklerini çözer)
            reps = [{s: evaluate(data, method_outputs(data, runs[code][t]["skorlar_logoran"], "rr", "H20+"), s) for s in ("tum", "secim", "teyit")} for t in REPS]
            methods[f"{code}_logoran"] = {"tur": "reranker_kesif", "model": model, "talimat": tal, "havuz": "H20+", "tekrarlar": reps}
    keys = ("recall_at_5", "recall_at_10", "mrr_at_10", "gold_ilk1", "auroc", "dogru_kacinma_karar", "dogru_kacinma_tum", "yanlis_kabul",
            "yanlis_kacinma_karar", "yanlis_kacinma_tum", "teknik_hata", "guvenlik_reddi", "kaynak_catismasi", "a6_uzaklik",
            "kabul_edilen_cevaplanabilirde_gold_ilk1")  # fmt: skip
    summary = {m: {s: {k: spread([r[s][k] for r in v["tekrarlar"]]) for k in keys} | {"a6_tum_tekrarlarda": all(r[s]["a6_karsilandi"] for r in v["tekrarlar"])}
                   for s in ("tum", "secim", "teyit")} for m, v in methods.items()}  # fmt: skip
    # Maliyet ve kararlılık (reranker'lar)
    cost = {}
    for code, (model, tal) in RERANKERS.items():
        r = runs[code]
        pairs = [(v, p) for v in r[1]["skorlar"] for p in r[1]["skorlar"][v]]
        maxdiff = max(abs(r[a]["skorlar"][v][p] - r[b]["skorlar"][v][p]) for v, p in pairs for a in REPS for b in REPS)
        h20 = {c["vaka"]: c["havuzlar"]["H20+"]["getirme_ms"] for c in data["vakalar"]}
        e2e = sorted(h20[v] + ms for t in REPS for v, ms in zip(r[t]["skorlar"], r[t]["vaka_ms"]))
        cost[code] = {"soguk_ms": [r[t]["soguk_ms"] for t in REPS], "yukleme_s": [r[t]["meta"]["yukleme_s"] for t in REPS],
                      "sicak_skorlama_ms_medyan": [r[t]["sicak_ms_medyan"] for t in REPS], "sicak_skorlama_ms_p95": [r[t]["sicak_ms_p95"] for t in REPS],
                      "uctan_uca_sicak_ms_medyan": round(statistics.median(e2e), 1), "uctan_uca_sicak_ms_p95": round(e2e[int(0.95 * (len(e2e) - 1))], 1),
                      "vram_tepe_mib": max(r[t]["meta"]["vram_tepe_mib"] or 0 for t in REPS), "ram_tepe_mib": max(r[t]["meta"]["ram_tepe_mib"] for t in REPS),
                      "tepe_ayrilan_mib": round(max(r[t]["meta"]["bosaltma"]["tepe_ayrilan_bayt"] for t in REPS) / 2**20),
                      "tekrarlar_arasi_en_buyuk_fark": maxdiff, "sonlu_olmayan": sum(r[t]["sonlu_olmayan"] for t in REPS),
                      "kesilen_cift": sum(r[t]["kesilen_cift"] for t in REPS), "cift": r[1]["cift"], "batch": r[1]["meta"]["batch"]}  # fmt: skip
    cos_ms = {p: sorted(c["havuzlar"][p]["getirme_ms"] for c in data["vakalar"]) for p in POOLS}
    cost["cosine"] = {p: {"medyan": round(statistics.median(v), 1), "p95": v[int(0.95 * (len(v) - 1))]} for p, v in cos_ms.items()}
    # Seçim kuralları (yalnız seçim kümesi)
    r1_r5 = summary["R1"]["secim"]["recall_at_5"]["ort"]
    elig = {}
    for m in methods:
        if m.endswith("_logoran") or "@" in m:
            continue
        s = summary[m]["secim"]
        ok = s["teknik_hata"]["max"] == 0
        if methods[m]["tur"] == "reranker":
            model, tal = RERANKERS[m]
            ok &= bool(smoke.get(f"{model}/{tal}")) and cost[m]["sonlu_olmayan"] == 0 and s["recall_at_5"]["ort"] >= r1_r5 - 1e-9
        elig[m] = {"uygun": ok, "a6_uzaklik_ort": s["a6_uzaklik"]["ort"], "a6_uzaklik_en_kotu": s["a6_uzaklik"]["max"],
                   "a6_tum_tekrarlarda": s["a6_tum_tekrarlarda"], "auroc": s["auroc"]["ort"],
                   "vram": cost[m]["vram_tepe_mib"] if m in cost else 0, "sicak_ms": cost[m]["uctan_uca_sicak_ms_medyan"] if m in cost else cost["cosine"]["H20+"]["medyan"]}  # fmt: skip
    ok = [m for m in elig if elig[m]["uygun"]]
    best = min(ok, key=lambda m: (elig[m]["a6_uzaklik_ort"], not elig[m]["a6_tum_tekrarlarda"], -(elig[m]["auroc"] or 0)))
    near = [m for m in ok if elig[m]["a6_uzaklik_ort"] <= elig[best]["a6_uzaklik_ort"] + 0.05 + 1e-9]
    balance = min(near, key=lambda m: (elig[m]["vram"], elig[m]["sicak_ms"]))
    rank = lambda m: (elig[m]["a6_uzaklik_ort"], -(elig[m]["auroc"] or 0))  # noqa: E731
    no_rr = min((m for m in NO_RERANKER if elig[m]["uygun"]), key=rank)
    free = min((m for m in MODEL_FREE if elig[m]["uygun"]), key=rank)
    # R5 ve R4 karşılaştırması (seçim; tekrar 1 kabul kararları)
    k4, k5 = methods["R4"]["tekrarlar"][0]["secim"]["kabul"], methods["R5"]["tekrarlar"][0]["secim"]["kabul"]
    ansmap = {c["vaka"]: c["ans"] for c in data["vakalar"]}
    flips = [{"vaka": v, "cevaplanabilir": ansmap[v], "R4_kabul": k4[v], "R5_kabul": k5[v]} for v in k4 if k4[v] != k5[v]]
    out = {"on_kayit": "docs/raporlar/ASAMA_8_D5_DENEY_KAYDI.md", "uyari": "Dev verisi; A6 kabulü değildir; Gemma yok; reranker kabulü cevap başarısı değildir.",
           "smoke": smoke, "ozet": summary, "maliyet": cost, "uygunluk": elig,
           "secim": {"en_basarili": best, "kalite_donanim_dengesi": balance, "en_basarili_ek_reranker_gerektirmeyen": no_rr, "en_basarili_tamamen_modelsiz": free, "yakin_yontemler": near},
           "R5_R4": {"degisen_vakalar": flips}, "yontemler": {m: {k: v for k, v in mv.items() if k != "tekrarlar"} for m, mv in methods.items()},
           "kabul_kararlari_tekrar1": {m: mv["tekrarlar"][0]["tum"]["kabul"] for m, mv in methods.items() if "@" not in m}}  # fmt: skip
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for m in methods:
        if "@" in m:
            continue
        s = summary[m]["secim"]
        t = summary[m]["tum"]
        print(f"{m:12s} secim d={s['a6_uzaklik']['ort']} kac={s['dogru_kacinma_karar']['ort']} ykac={s['yanlis_kacinma_karar']['ort']} auc={s['auroc']['ort']} "
              f"R5={s['recall_at_5']['ort']} | tum d={t['a6_uzaklik']['ort']} kac={t['dogru_kacinma_karar']['ort']} ykac={t['yanlis_kacinma_karar']['ort']} auc={t['auroc']['ort']}")  # fmt: skip
    print("secim:", out["secim"], "R5/R4 degisen:", len(flips))
    return 0


if __name__ == "__main__":
    sys.exit(main())
