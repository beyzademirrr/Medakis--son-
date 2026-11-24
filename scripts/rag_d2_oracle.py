"""D0 yeniden koşusu ve D2 gold kanıt oracle deneyi (ön kayıt: docs/raporlar/ASAMA_8_D0_D2_DENEY_KAYDI.md §2, §4).

    .\\.venv\\Scripts\\python.exe scripts\\rag_d2_oracle.py

UYARI: Gold etiketleri insan onayı almamış TASLAKTIR. Oracle koşulları denetçinin ÜST SINIRINI ölçer; gerçek sistem
başarısı ya da A6 kabulü DEĞİLDİR. Dev soruları daha önce yöntem seçiminde kullanıldı; bağımsız/kör veri değildir.

- Koşullar (aynı gemma3:4b digest'i, `kanit_denetimi_v2` istemi, temperature=0, seed=0, num_ctx=2048, num_predict=256):
  K3 mevcut retrieval adayları (v1, eşiksiz ilk 5, tamamlama yok) — 60 vaka (D0 yeniden koşusu);
  K1 yalnız gold parçalar (v1 parçalaması) — 30 cevaplanabilir;
  K2 gold + mevcut retrieval'ın gold olmayan en üst adayları (toplam 5; konum vaka anahtarından sabit tohumla).
- Gold parçalar gerçek corpus metninden kurulur ve parça hash'i manifestle denetlenir; istem uzaklık göstermez.
- Aynı istem bir kez çağrılır (deterministik ayarlar); gerçek çağrı sayısı kaydedilir.
- Eski kayıtlar (asama8_kanit_hatti_dev.json, P1/P2) D0 sınıflarıyla yeniden puanlanır; kayıtta olmayan alanlar
  `kayitta_yok` olarak işaretlenir, tahminle doldurulmaz. Eski ham dosyalar değiştirilmez.
- Eşik yazılmaz; normal sorgu kapalı; gerçek indeks yalnız okunur. Çıktı: docs/veri/asama8_d0_d2_oracle.json
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import random
import sys
import time
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_rag_profiles, load_settings  # noqa: E402
from medakis.rag.chunking.chunker import sha256_text  # noqa: E402
from medakis.rag.contracts.retrieval import RetrievalOutcome, RetrievalResult, RetrievedChunk  # noqa: E402
from medakis.rag.embeddings.ollama_client import OllamaEmbedder  # noqa: E402
from medakis.rag.evaluation.contract import classify, summarize_contract  # noqa: E402
from medakis.rag.evaluation.eval_set import gold_chunk_ids, load_dev_set  # noqa: E402
from medakis.rag.generation.mode import GenerationMode  # noqa: E402
from medakis.rag.generation.ollama_generator import GEMMA3_4B_DIGEST, OllamaGenerator  # noqa: E402
from medakis.rag.generation.pipeline import POLICY_CANDIDATES, EvidencePipeline  # noqa: E402
from medakis.rag.generation.prompt import PROMPT_VERSION  # noqa: E402
from medakis.rag.metadata.file_store import FileMetadataStore  # noqa: E402
from medakis.rag.retrieval.retriever import Retriever  # noqa: E402
from medakis.rag.vector_store.chroma_store import ChromaVectorStore  # noqa: E402
from medakis.rag.vector_store.location import check_store_dir  # noqa: E402

EVAL = ROOT / "evaluation" / "asama7"
CORPUS = ROOT / "corpus" / "kontrollu"
OLD = ROOT / "docs" / "veri" / "asama8_kanit_hatti_dev.json"
OUT = ROOT / "docs" / "veri" / "asama8_d0_d2_oracle.json"
MODEL, K = "bge-m3", 5
DRAFT = "Gold etiketleri TASLAK (insan onayı yok). Oracle üst sınırdır; gerçek sistem başarısı ya da A6 kabulü değildir."


def split_of(group: str) -> str:
    return "teyit" if int(hashlib.sha256(("secim_teyit_v1:" + group).encode()).hexdigest()[:8], 16) % 3 == 0 else "secim"


def iso(dt):
    return None if dt is None else dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class CachingGenerator:
    supports_budget = True

    def __init__(self, inner) -> None:
        self.inner, self.cache, self.calls, self.durations = inner, {}, 0, []

    def chat_json(self, messages, schema, budget_s=None):
        key = hashlib.sha256(json.dumps(list(messages), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key not in self.cache:
            t0 = time.perf_counter()
            try:
                self.cache[key] = ("ok", self.inner.chat_json(messages, schema, budget_s=budget_s))
            except Exception as exc:  # teknik hata da önbelleğe alınır: aynı istem aynı koşulda tekrar denenmez
                self.cache[key] = ("hata", exc)
            self.calls += 1
            self.durations.append(round((time.perf_counter() - t0) * 1000))
        kind, val = self.cache[key]
        if kind == "hata":
            raise val
        return val


def gold_hit(result, case) -> bool:
    return any(f"{q.source_key}@{q.version}" == g.doc_uid and q.doc_start < g.char_end and g.char_start < q.doc_end
               for q in result.quotes for g in case.gold)  # fmt: skip


def record(cond: str, case, result, chunks: list[RetrievedChunk], gold: set[str]) -> dict:
    sent = list(result.candidates) or [c.chunk_id for c in sorted(chunks, key=lambda c: c.rank)[:K]]
    gin = bool(gold & set(sent)) if case.answerable else None
    cls = classify(case.answerable, result.outcome.value, result.warnings, gin)
    by_id = {c.chunk_id: c for c in chunks}
    return {
        "kosul": cond, "vaka": case.case_key, "grup": case.paraphrase_group, "kume": split_of(case.paraphrase_group),
        "ans": case.answerable, "sonuc": result.outcome.value, "sinif": cls.klass.value, "alt_neden": cls.reason,
        "uyarilar": list(result.warnings), "model_cagrisi": result.model_calls, "gold_adaylarda": gin,
        "adaylar": [{"sira": i, "parca": cid[:12], "belge": f"{by_id[cid].source_key}@{by_id[cid].version}" if cid in by_id else None,
                     "bolum": by_id[cid].section if cid in by_id else None,
                     "uzaklik": round(by_id[cid].distance, 4) if cid in by_id else None, "gold": cid in gold}
                    for i, cid in enumerate(sent, 1)],
        "ham_karar": result.decision.as_dict() if result.decision else None,
        "dogrulanmis_alintilar": [{"belge": f"{q.source_key}@{q.version}", "bolum": q.section, "aralik": [q.doc_start, q.doc_end],
                                   "metin": q.text} for q in result.quotes],
        "gold_alinti_isabeti": gold_hit(result, case) if case.answerable else None,
    }  # fmt: skip


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    tech = sum(r["sinif"] == "TEKNIK_HATA" for r in rows)
    called = [r for r in rows if r["ham_karar"] is not None]
    sup = [r for r in called if r["ham_karar"]["destekleniyor"]]
    empty = [r for r in sup if r["ham_karar"]["alinti_sayisi"] == 0]
    with_q = [r for r in sup if r["ham_karar"]["alinti_sayisi"] > 0]
    exact = [r for r in with_q if not r["ham_karar"]["sorunlar"]]
    ans = [r for r in rows if r["sonuc"] == "answered"]
    hit = [r for r in ans if r["gold_alinti_isabeti"]]
    rate = lambda a, b: None if b == 0 else round(a / b, 3)  # noqa: E731
    return {
        "vaka": n, "teknik_hata": tech, "ham_karar_yok": n - len(called),
        "destekleniyor": len(sup), "destekleniyor_orani": rate(len(sup), n),
        "destekleniyor_orani_model_cagrilan": rate(len(sup), len(called)),
        "bos_alinti": len(empty), "bos_alinti_orani": rate(len(empty), n),
        "alintili_destek": len(with_q), "alinti_birebir_dogru": len(exact), "alinti_dogruluk_orani": rate(len(exact), len(with_q)),
        "sahte_alintili_karar": len(with_q) - len(exact),
        "sahte_alinti_sorunlari": sorted({p.split(":", 1)[-1] for r in with_q for p in r["ham_karar"]["sorunlar"]}),
        "cevaplandi": len(ans), "cevap_orani": rate(len(ans), n),
        "gold_alinti_isabeti": len(hit), "gold_alinti_isabeti_orani": rate(len(hit), n),
        "gold_degmeyen_cevap": sorted(r["vaka"] for r in ans if not r["gold_alinti_isabeti"]),
        "reddedilen": sorted(r["vaka"] for r in rows if r["sonuc"] != "answered"),
        "sinif_sayilari": dict(sorted({r["sinif"]: sum(x["sinif"] == r["sinif"] for x in rows) for r in rows}.items())),
        "yanlis_ret_alt_nedenleri": dict(sorted({r["alt_neden"]: sum(x["alt_neden"] == r["alt_neden"] for x in rows if x["sinif"] == "YANLIS_RET")
                                                for r in rows if r["sinif"] == "YANLIS_RET"}.items())),
    }  # fmt: skip


def by_split(rows: list[dict]) -> dict:
    return {"tum": metrics(rows), **{k: metrics([r for r in rows if r["kume"] == k]) for k in ("secim", "teyit")}}


def rescore_old() -> dict:
    """Eski P1/P2 kayıtlarının D0 sınıfları. Eski kayıtta alıntı, ham karar ve aday listesi YOK: tahmin edilmez."""
    old = json.loads(OLD.read_text(encoding="utf-8"))
    out = {}
    for pol, rows in old["vakalar"].items():
        new = []
        for r in rows:
            cls = classify(r["ans"], r["sonuc"], r.get("uyari", []), None)
            new.append({"vaka": r["vaka"], "kume": split_of(r["grup"]), "ans": r["ans"], "sonuc": r["sonuc"], "sinif": cls.klass.value,
                        "alt_neden": cls.reason, "eski_gold_isabeti": r.get("gold"), "dogrulanmis_alintilar": "kayitta_yok",
                        "ham_karar": "kayitta_yok", "aday_siralamasi": "kayitta_yok"})  # fmt: skip
        out[pol] = {"ozet": summarize_contract(new), "eski_ozet": old[{"P1": "P1_esik_kanit_cv", "P2": "P2_aday_kanit"}[pol]], "vakalar": new}
    return out


def interpret(m: dict) -> dict:
    k1, k2, k3 = (m[k]["tum"] for k in ("K1", "K2", "K3_cevaplanabilir"))
    s1, s2, s3 = k1["destekleniyor_orani"], k2["destekleniyor_orani"], k3["destekleniyor_orani"]
    empty = max(x["bos_alinti_orani"] for x in (k1, k2, k3))
    fake = sum(x["sahte_alintili_karar"] for x in (k1, k2, k3))
    gap = max(x["destekleniyor_orani"] - x["gold_alinti_isabeti_orani"] for x in (k1, k2))
    return {
        "R1_model_karari": s1 < 0.90, "R2_karistirici_etkisi": s1 >= 0.90 and s2 < s1 - 0.10,
        "R3_retrieval_aday_havuzu": s1 >= 0.90 and s2 >= 0.90 and s3 < s2 - 0.10,  # "belirgin" = 0,10 (koşudan önce sabitlendi)
        "R4_alinti_uretimi": empty >= 0.10 or fake > 0, "R5_degerlendirme_katmani": gap >= 0.10,
        "degerler": {"K1_destek": s1, "K2_destek": s2, "K3_destek": s3, "en_yuksek_bos_alinti_orani": empty, "sahte_alintili_karar": fake,
                     "destek_gold_isabet_farki_en_fazla": round(gap, 3)},
    }  # fmt: skip


def main() -> int:
    settings = load_settings(env={})
    real = check_store_dir(settings.rag_store_dir, min_free_gb=settings.rag_min_free_gb)
    common = load_rag_profiles(settings)["common"]
    cases = [c for n in ("dev.jsonl", "dev_ek.jsonl") for c in load_dev_set(EVAL / n).cases]
    meta, vec = FileMetadataStore(CORPUS, real), ChromaVectorStore(real)
    reg = meta.registry()
    key = reg.ready_keys(MODEL)[0]
    thresholds_before = {k: reg.threshold(k) for k in reg.ready_keys()}
    parts = reg.manifest(key)["parcalar"]
    pmap = {p["chunk_id"]: p for p in parts}
    docs = meta.corpus().by_uid()

    # 1) Retrieval (embedding sıcak), sonra embedding boşaltılır: üretici tek başına yüklenir.
    emb = OllamaEmbedder(MODEL, common["ollama_base_url"], 2048, float(common["timeout_seconds"]), keep_alive="5m")
    ret = Retriever(meta, vec, emb, MODEL)  # mevcut yapılandırma: tamamlama KAPALI
    retrievals = {c.case_key: ret.retrieve(c.question, as_of=c.as_of, evaluation=True) for c in cases}
    vec.close()
    OllamaEmbedder(MODEL, common["ollama_base_url"], keep_alive=0).unload()

    def chunk(cid: str, rank: int, distance: float) -> RetrievedChunk:
        p = pmap[cid]
        doc = docs[p["doc_uid"]]
        text = doc.text[p["char_start"]:p["char_end"]]
        if sha256_text(text) != p["sha256"]:
            raise SystemExit(f"gold parça hash uyuşmuyor: {cid}")
        r = doc.record
        return RetrievedChunk(rank, cid, distance, r.source_key, r.source_name, r.doc_type, r.version, p["section"],
                              iso(r.valid_from), iso(r.valid_until), p["char_start"], p["char_end"], text)  # fmt: skip

    def fake(case, chunks) -> RetrievalResult:
        return RetrievalResult(RetrievalOutcome.FOUND, "oracle", tuple(chunks), case.as_of, key, None, warnings=("oracle_kosulu",), evaluation=True)

    gen = OllamaGenerator(GenerationMode.TEST, keep_alive="5m")
    digest = gen.describe()
    if digest != GEMMA3_4B_DIGEST:
        raise SystemExit("gemma digest beklenenden farklı")
    cg = CachingGenerator(gen)
    pipe = EvidencePipeline(ret, cg, GenerationMode.TEST, candidate_k=K)
    rows: dict[str, list[dict]] = {"K3": [], "K1": [], "K2": []}
    for c in cases:
        r = retrievals[c.case_key]
        gold = set(gold_chunk_ids(c.gold, parts))
        res = pipe.decide(c.question, r, POLICY_CANDIDATES)
        rows["K3"].append(record("K3", c, res, list(r.chunks), gold))
        if not c.answerable:
            continue
        dist = {ch.chunk_id: ch.distance for ch in r.chunks}
        g_chunks = [chunk(cid, i, dist.get(cid, 0.0)) for i, cid in enumerate([p["chunk_id"] for p in parts if p["chunk_id"] in gold], 1)]
        res1 = pipe.decide(c.question, fake(c, g_chunks), POLICY_CANDIDATES)
        rows["K1"].append(record("K1", c, res1, g_chunks, gold) | {"gold_parca": len(g_chunks)})
        distractors = [ch for ch in sorted(r.chunks, key=lambda x: x.rank) if ch.chunk_id not in gold][: max(0, K - len(g_chunks))]
        mix = g_chunks + distractors
        random.Random(int(hashlib.sha256(("d2_konum_v1:" + c.case_key).encode()).hexdigest()[:8], 16)).shuffle(mix)
        mix = [dataclasses.replace(ch, rank=i) for i, ch in enumerate(mix, 1)]
        res2 = pipe.decide(c.question, fake(c, mix), POLICY_CANDIDATES)
        rows["K2"].append(record("K2", c, res2, mix, gold) | {"karistirici": len(distractors),
                                                               "gold_konum": [ch.rank for ch in mix if ch.chunk_id in gold]})  # fmt: skip
        print(c.case_key, res.outcome.value, res1.outcome.value, res2.outcome.value, flush=True)
    loaded_after = [m.get("name") for m in gen.loaded_models()]
    gen.unload()
    remaining = [m.get("name") for m in gen.loaded_models()]

    k3_ans = [r for r in rows["K3"] if r["ans"]]
    m = {"K1": by_split(rows["K1"]), "K2": by_split(rows["K2"]), "K3_cevaplanabilir": by_split(k3_ans)}
    d0_rows = [{"ans": r["ans"], "sonuc": r["sonuc"], "sinif": r["sinif"], "alt_neden": r["alt_neden"], "kume": r["kume"]} for r in rows["K3"]]
    reg_after = FileMetadataStore(CORPUS, real).registry()
    out = {
        "uyari": DRAFT, "on_kayit": "docs/raporlar/ASAMA_8_D0_D2_DENEY_KAYDI.md",
        "sabitler": {"uretici": "gemma3:4b", "digest": digest, "istem": PROMPT_VERSION, "temperature": 0, "seed": 0, "num_ctx": gen.num_ctx,
                     "num_predict": gen.num_predict, "aday_sayisi": K, "embedding": MODEL, "indeks": key, "tamamlama": False},
        "gemma": {"gercek_cagri": cg.calls, "sure_ms_medyan": sorted(cg.durations)[len(cg.durations) // 2] if cg.durations else None,
                  "sure_ms_en_fazla": max(cg.durations, default=None), "kosu_sonu_yuklu": loaded_after, "bosaltma_sonrasi_yuklu": remaining},
        "D0_yeni_kosu": {"tum": summarize_contract(d0_rows), **{k: summarize_contract([r for r in d0_rows if r["kume"] == k]) for k in ("secim", "teyit")}},
        "D0_eski_kayitlar_yeniden_puanlama": rescore_old(),
        "D2": m, "yorum_kurallari": interpret(m),
        "gercek_depo": {"esik_once": thresholds_before, "esik_sonra": {k: reg_after.threshold(k) for k in reg_after.ready_keys()}},
        "vakalar": rows,
    }  # fmt: skip
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in m.items():
        t = v["tum"]
        print(k, {x: t[x] for x in ("vaka", "teknik_hata", "destekleniyor_orani", "bos_alinti_orani", "alinti_dogruluk_orani", "sahte_alintili_karar",
                                   "cevap_orani", "gold_alinti_isabeti_orani")})  # fmt: skip
    print("D0", json.dumps(out["D0_yeni_kosu"]["tum"], ensure_ascii=False))
    print("yorum", json.dumps(out["yorum_kurallari"], ensure_ascii=False))
    print("gemma", out["gemma"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
