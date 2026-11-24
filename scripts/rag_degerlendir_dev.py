"""Aşama 7.6: iki embedding adayının YALNIZ DEV kümesinde sıralı değerlendirmesi ve güvenlik vakaları.

    .\\.venv\\Scripts\\python.exe scripts\\rag_degerlendir_dev.py

- Mühürlü test kümesi (evaluation/asama7/muhurlu_test.jsonl) OKUNMAZ; yalnız baytlarının SHA-256'sı mühürle
  karşılaştırılır (dosya değişmemiş olmalı).
- Gerçek indeksler RAG deposunda (merkezi yapılandırma, D:) adaylar için ayrı ayrı kurulur. Eşik indeks kaydına
  YAZILMAZ: yalnız öneri olarak raporlanır (seçim proje sahibinindir; normal sorgu eşik olmadan fail-closed kalır).
- Adaylar sırayla çalışır, aynı anda yüklenmez. Gecikme blokları A/B sırası dönüşümlü ölçülür.
- Güvenlik vakaları geçici bir depoda (D:), geçici onay iziyle çalışır; gerçek onay izine dokunulmaz.
- Çıktı: docs/veri/asama7_dev_degerlendirme.json
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_rag_profiles, load_settings  # noqa: E402
from medakis.rag.chunking.chunker import ChunkConfig, chunk_corpus  # noqa: E402
from medakis.rag.contracts.retrieval import RetrievalOutcome  # noqa: E402
from medakis.rag.embeddings.ollama_client import OllamaEmbedder  # noqa: E402
from medakis.rag.errors import LedgerError  # noqa: E402
from medakis.rag.evaluation.eval_set import gold_chunk_ids, load_dev_set  # noqa: E402
from medakis.rag.evaluation.metrics import CaseOutcome, summarize  # noqa: E402
from medakis.rag.ingestion.approval_ledger import APPROVE, RETIRE, ApprovalLedger, effective_states  # noqa: E402
from medakis.rag.ingestion.document_policy import eligible_documents  # noqa: E402
from medakis.rag.ingestion.manifest import parse_utc  # noqa: E402
from medakis.rag.metadata.file_store import FileMetadataStore  # noqa: E402
from medakis.rag.retrieval.retriever import Retriever  # noqa: E402
from medakis.rag.retrieval.thresholds import DevObservation, calibrate  # noqa: E402
from medakis.rag.vector_store.chroma_store import ChromaVectorStore  # noqa: E402
from medakis.rag.vector_store.index_builder import build_index  # noqa: E402
from medakis.rag.vector_store.location import check_store_dir  # noqa: E402

EVAL = ROOT / "evaluation" / "asama7"
CORPUS = ROOT / "corpus" / "kontrollu"
OUT = ROOT / "docs" / "veri" / "asama7_dev_degerlendirme_v2.json"
DEV_FILES = ("dev.jsonl", "dev_ek.jsonl")  # genişletilmiş dev (60 vaka); test verisi yok
OLLAMA_MODELS = Path(__import__("os").environ.get("OLLAMA_MODELS", r"D:\ollama\models"))
RUNNER_IMAGES = {"ollama.exe", "llama-server.exe"}  # model, llama-server.exe sürecinde yüklenir
CANDIDATES = ("qwen3-embedding:0.6b", "bge-m3")
WARM_KEEP = "10m"
WARM_N, COLD_N = 30, 10
BOOT_N, CV_FOLDS = 2000, 5


def now() -> datetime:
    return datetime.now(UTC)


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


# ---- Donanım ve kaynak örnekleme -------------------------------------------------------------------------------


def run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace").stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"hata:{type(exc).__name__}"


def hardware() -> dict:
    gpu = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"])
    mem = run(["powershell", "-NoProfile", "-Command",
               "$o=Get-CimInstance Win32_OperatingSystem; '{0};{1}' -f $o.TotalVisibleMemorySize,$o.FreePhysicalMemory"])  # fmt: skip
    total_kb, free_kb = (int(x) for x in mem.split(";")) if ";" in mem else (0, 0)
    return {"gpu": gpu, "ram_toplam_gb": round(total_kb / 1048576, 1), "ram_bos_gb": round(free_kb / 1048576, 1),
            "disk_bos_gb": {d: round(shutil.disk_usage(f"{d}:\\").free / 1024**3, 1) for d in ("C", "D")},
            "python": sys.version.split()[0]}  # fmt: skip


class Sampler:
    """VRAM (nvidia-smi) ve ollama süreçlerinin bellek (tasklist) tepe değerleri."""

    def __init__(self) -> None:
        self.vram, self.rss, self._stop = [], [], threading.Event()

    def _loop(self) -> None:
        while not self._stop.is_set():
            v = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
            if v.isdigit():
                self.vram.append(int(v))
            t = run(["tasklist", "/FO", "CSV", "/NH"])
            kb = 0
            for row in csv.reader(io.StringIO(t)):
                if len(row) >= 5 and row[0].lower() in RUNNER_IMAGES:
                    kb += int("".join(ch for ch in row[4] if ch.isdigit()) or 0)
            if kb:
                self.rss.append(kb)
            self._stop.wait(0.5)

    def __enter__(self) -> "Sampler":
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._t.join(timeout=30)

    def peaks(self) -> dict:
        return {"vram_tepe_mib": max(self.vram) if self.vram else None, "ollama_ram_tepe_mib": round(max(self.rss) / 1024) if self.rss else None,
                "ornek": len(self.vram)}  # fmt: skip


# ---- Onaylı belgeler -------------------------------------------------------------------------------------------


def verify_approvals(store: Path) -> dict:
    meta = FileMetadataStore(CORPUS, store)
    corpus = meta.corpus()
    events = meta.ledger.events()  # zincir bozuksa LedgerError
    states, problems = effective_states([d.record for d in corpus.documents], events)
    approved = sorted(u for u, s in states.items() if s.status.value == "approved")
    elig = eligible_documents(states, parse_utc("2026-11-23T12:00:00Z", "as_of"), problems)
    return {"olay": len(events), "zincir": "gecerli", "onayli": approved, "onayli_sayi": len(approved),
            "icerik_butun": all(states[u].content_intact for u in approved), "tutarsizlik": problems,
            "sorguya_girebilen": len(elig.allowed), "cakisma": list(elig.conflicts), "corpus_sorunlari": list(corpus.problems)}  # fmt: skip


# ---- Aday değerlendirmesi -----------------------------------------------------------------------------------------


def unload_wait(emb: OllamaEmbedder, model: str, timeout_s: float = 60) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if not any(m.get("name", "").startswith(model.split(":")[0]) for m in emb.loaded_models()):
            return True
        time.sleep(1)
    return False


def bootstrap(cases: list, tau: float | None, n: int = BOOT_N, seed: int = 7) -> dict:
    """Vaka düzeyinde yeniden örnekleme ile %95 güven aralıkları (dev içi; iyimser)."""
    import random

    rng = random.Random(seed)
    stats: dict[str, list[float]] = {"recall_at_5": [], "mrr_at_10": [], "kacinma_recall": [], "yanlis_kacinma": []}
    for _ in range(n):
        sample = [cases[rng.randrange(len(cases))] for _ in cases]
        s = summarize([CaseOutcome(c.case_key, c.answerable, r, g, b is None or tau is None or b > tau) for c, r, g, b in sample])
        for k, v in (("recall_at_5", s.recall_at_5), ("mrr_at_10", s.mrr_at_10), ("kacinma_recall", s.abstention_recall),
                     ("yanlis_kacinma", s.false_abstention)):  # fmt: skip
            if v is not None:
                stats[k].append(v)
    return {k: [round(pct(v, 0.025), 3), round(pct(v, 0.975), 3)] for k, v in stats.items() if v}


def group_cv(cases: list, folds: int = CV_FOLDS) -> dict:
    """Paraphrase grubu bazlı k-katlı çapraz doğrulama: eşik k-1 katta seçilir, dışarıdaki katta uygulanır."""
    fold_of = lambda c: int(hashlib.sha256(c.paraphrase_group.encode()).hexdigest()[:8], 16) % folds  # noqa: E731
    flags, taus = [], []
    for f in range(folds):
        train = [DevObservation(c.case_key, "dev", c.answerable, b) for c, _, _, b in cases if fold_of(c) != f]
        held = [(c, b) for c, _, _, b in cases if fold_of(c) == f]
        tau = calibrate(train).max_distance
        taus.append(None if tau is None else round(tau, 4))
        flags += [(c.answerable, b is None or tau is None or b > tau) for c, b in held]
    unans = [a for ans, a in flags if not ans]
    ans = [a for ans, a in flags if ans]
    return {"katlar": folds, "kat_esikleri": taus, "kacinma_recall": round(sum(unans) / len(unans), 3) if unans else None,
            "yanlis_kacinma": round(sum(ans) / len(ans), 3) if ans else None}  # fmt: skip


def verify_model_blobs(model: str) -> dict:
    import urllib.request

    name, tag = (model.split(":") + ["latest"])[:2]
    path = OLLAMA_MODELS / "manifests" / "registry.ollama.ai" / "library" / name / tag
    manifest = json.loads(path.read_bytes())
    ok, size = True, 0
    for layer in [manifest["config"], *manifest["layers"]]:
        digest = layer["digest"].split(":")[1]
        blob = OLLAMA_MODELS / "blobs" / f"sha256-{digest}"
        h = hashlib.sha256()
        with blob.open("rb") as fh:
            for part in iter(lambda: fh.read(1 << 20), b""):
                h.update(part)
        ok &= h.hexdigest() == digest and blob.stat().st_size == layer["size"]
        size += blob.stat().st_size
    tags = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/tags"))
    digest = next((m["digest"] for m in tags["models"] if m["name"] in (model, f"{model}:latest")), None)
    return {"manifest": str(path), "ollama_digest": digest, "boyut": size, "butunluk": "gecti" if ok else "BASARISIZ"}


def evaluate_candidate(model: str, store: Path, profiles: dict, dev) -> dict:
    common = profiles["common"]
    url, timeout = common["ollama_base_url"], float(common["timeout_seconds"])
    warm = OllamaEmbedder(model, url, 2048, timeout, keep_alive=WARM_KEEP)
    info = warm.describe()
    vectors = ChromaVectorStore(store)
    meta = FileMetadataStore(CORPUS, store)
    chunks = chunk_corpus(meta.corpus().documents)
    t0 = time.perf_counter()
    with Sampler() as s_index:
        built = build_index(chunks, ChunkConfig(), warm, vectors, meta.registry())
    index_s = round(time.perf_counter() - t0, 1)
    ready = meta.registry().last(built.index_key, "ready")
    manifest = meta.registry().manifest(built.index_key)
    ret = Retriever(meta, vectors, warm, model)
    cases, obs, per_case = [], [], []
    for c in dev.cases:
        res = ret.retrieve(c.question, as_of=c.as_of, evaluation=True)
        ranked = tuple(ch.chunk_id for ch in res.chunks)
        gold = gold_chunk_ids(c.gold, manifest["parcalar"])
        best = res.chunks[0].distance if res.chunks else None
        obs.append(DevObservation(c.case_key, "dev", c.answerable, best))
        cases.append((c, ranked, gold, best))
        per_case.append({"vaka": c.case_key, "cevaplanabilir": c.answerable, "sonuc": res.outcome.value,
                         "en_iyi_uzaklik": None if best is None else round(best, 4), "gold_parca": len(gold),
                         "ilk_gold_sirasi": next((i + 1 for i, x in enumerate(ranked) if x in set(gold)), None),
                         "ilk_kaynak": f"{res.chunks[0].source_key}@{res.chunks[0].version}" if res.chunks else None})  # fmt: skip
    cal = calibrate(obs)
    tau = cal.max_distance
    uncertainty = {"bootstrap_95": bootstrap(cases, tau), "grup_cv": group_cv(cases),
                   "esik_payi": {"en_buyuk_cevaplanabilir": max((b for c, _, _, b in cases if c.answerable and b is not None), default=None),
                                 "en_kucuk_kaynak_disi": min((b for c, _, _, b in cases if not c.answerable and b is not None), default=None)}}  # fmt: skip
    outcomes = [CaseOutcome(c.case_key, c.answerable, ranked, gold, best is None or tau is None or best > tau) for c, ranked, gold, best in cases]
    summ = summarize(outcomes)
    # Gecikme: sıcak (model yüklü tutulur) ve soğuk (önce boşaltma doğrulanır; keep_alive=0)
    questions = [c.question for c in dev.cases]
    warm_ms = []
    with Sampler() as s_warm:
        ret.retrieve(questions[0], evaluation=True)  # ısınma (ölçüme girmez)
        for i in range(WARM_N):
            warm_ms.append(ret.retrieve(questions[i % len(questions)], evaluation=True).timings.total_ms)
    loaded = [{"ad": m.get("name"), "boyut_mib": round(m.get("size", 0) / 1048576), "vram_mib": round(m.get("size_vram", 0) / 1048576)}
              for m in warm.loaded_models()]  # fmt: skip
    cold = OllamaEmbedder(model, url, 2048, timeout, keep_alive=0)
    cold_ret = Retriever(meta, vectors, cold, model)
    cold_ms, cold_unloaded = [], []
    cold.embed_one("boşaltma")  # keep_alive=0 ile çağrı: model iş bitince bellekten çıkar
    for i in range(COLD_N):
        cold_unloaded.append(unload_wait(cold, model))
        cold_ms.append(cold_ret.retrieve(questions[(i * 7) % len(questions)], evaluation=True).timings.total_ms)
    unload_wait(cold, model)
    vectors.close()
    return {
        "model": model, "digest": info.digest, "boyut": info.dimensions, "baglam": info.context_length, "num_ctx": info.num_ctx,
        "onek_kaynagi": info.prefix_source, "indeks": built.index_key, "koleksiyon": built.collection, "vektor": built.vector_count,
        "indeksleme_sn": index_s, "yeni_gomme": built.embedded_now, "token_en_fazla": ready.get("token_en_fazla"),
        "token_toplam": ready.get("token_toplam"), "indeks_kaynak": s_index.peaks(),
        "dev": {"recall_at_5": summ.recall_at_5, "mrr_at_10": summ.mrr_at_10, "kacinma_recall": summ.abstention_recall,
                "yanlis_kacinma": summ.false_abstention, "cevaplanabilir": summ.n_answerable, "kaynak_disi": summ.n_unanswerable},
        "esik": {"onerilen_en_fazla_uzaklik": tau, "neden": cal.reason, "kalibrasyon_kacinma_recall": cal.abstention_recall,
                 "kalibrasyon_yanlis_kacinma": cal.false_abstention, "not": "dev içi (örneklem içi) değer; iyimserdir"},
        "gecikme_ms": {"sicak_p50": pct(warm_ms, 0.5), "sicak_p95": pct(warm_ms, 0.95), "sicak_n": len(warm_ms),
                       "soguk": [round(x, 1) for x in cold_ms], "soguk_bosaltma_dogrulandi": cold_unloaded},
        "sicak_kaynak": {**s_warm.peaks(), "yuklu_model": loaded},
        "belirsizlik": uncertainty, "model_dosyasi": verify_model_blobs(model),
        "vakalar": per_case,
    }  # fmt: skip


# ---- Güvenlik vakaları -------------------------------------------------------------------------------------------


class Counting:
    def __init__(self, inner) -> None:
        self.inner, self.calls = inner, 0

    def describe(self):
        return self.inner.describe()

    def embed_one(self, text):
        self.calls += 1
        return self.inner.embed_one(text)


def digest_files(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        for f in sorted(p.rglob("*")) if p.is_dir() else [p]:
            if f.is_file():
                h.update(f.read_bytes())
    return h.hexdigest()


def security_cases(model: str, tau: float | None, profiles: dict) -> list[dict]:
    common = profiles["common"]
    tmp = check_store_dir(ROOT / "data" / f"guvenlik_{uuid.uuid4().hex[:8]}", min_free_gb=30)
    corpus = tmp / "corpus"
    shutil.copytree(CORPUS, corpus)
    emb = OllamaEmbedder(model, common["ollama_base_url"], 2048, float(common["timeout_seconds"]), keep_alive=WARM_KEEP)
    vectors = ChromaVectorStore(tmp)
    base = FileMetadataStore(corpus, tmp)
    key = build_index(chunk_corpus(base.corpus().documents), ChunkConfig(), emb, vectors, base.registry()).index_key
    if tau is not None:
        base.registry().set_threshold(key, tau, now(), {"kume": "dev", "not": "güvenlik vakaları için dev eşiği (geçici depo)"})
    normal13 = verify_approvals(load_settings(env={}).rag_store_dir)["onayli"]
    results = []
    for case in (json.loads(x) for x in (EVAL / "guvenlik.jsonl").read_text(encoding="utf-8").splitlines()):
        meta = FileMetadataStore(corpus, tmp, ledger_name=f"izler/{case['case_key']}.jsonl")
        recs = {d.record.uid: d.record for d in meta.corpus().documents}
        refused = []
        for uid in (normal13 if case["onay"] == "normal13" else case["onay"]):
            try:
                meta.ledger.append(APPROVE, recs[uid], "guvenlik-vakasi", "gecici", now(), list(recs.values()))
            except LedgerError as exc:
                refused.append(f"{uid}:{exc.code}")
        for uid in case.get("emekli", []):
            meta.ledger.append(RETIRE, recs[uid], "guvenlik-vakasi", "gecici", now(), list(recs.values()))
        modified = None
        if case.get("degistir"):
            path = corpus / recs[case["degistir"]].path
            modified = path.read_bytes()
            path.write_bytes(modified + "Sonradan eklenmiş ifade.\n".encode("utf-8"))
            mf = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
            for e in mf:
                if e["path"] == path.name:
                    e["content_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            (corpus / "manifest.json").write_text(json.dumps(mf, ensure_ascii=False), encoding="utf-8")
        watched = [meta.ledger.path, base.registry().events_path, ROOT / "data" / "onay_izi", load_settings(env={}).rag_store_dir / "belge_onaylari.jsonl"]
        before = digest_files([p for p in watched if p.exists()])
        counting = Counting(emb)
        ret = Retriever(meta, vectors, counting, model, clock=now)
        res = ret.retrieve(case["question"], as_of=parse_utc(case["as_of"], "as_of"))
        after = digest_files([p for p in watched if p.exists()])
        uids = {f"{c.source_key}@{c.version}" for c in res.chunks}
        exp, fails = case["beklenen"], []
        if "sonuc" in exp and res.outcome.value != exp["sonuc"]:
            fails.append(f"sonuc={res.outcome.value}")
        if "sonuc_in" in exp and res.outcome.value not in exp["sonuc_in"]:
            fails.append(f"sonuc={res.outcome.value}")
        if "sonuc_degil" in exp and res.outcome.value == exp["sonuc_degil"]:
            fails.append(f"sonuc={res.outcome.value}")
        if any(u in uids for u in exp.get("yasak_belge", [])):
            fails.append(f"yasak_belge_dondu:{sorted(uids)}")
        if exp.get("durum_degismez") and before != after:
            fails.append("durum_degisti")
        if exp.get("parca_yok") and res.chunks:
            fails.append("parca_dondu")
        if exp.get("gomme_yok") and counting.calls:
            fails.append(f"gomme_cagrildi:{counting.calls}")
        if "onay_reddi" in exp and not any(r.startswith(exp["onay_reddi"]) for r in refused):
            fails.append("ortusen_onay_reddedilmedi")
        if modified is not None:
            (corpus / recs[case["degistir"]].path).write_bytes(modified)
            shutil.copy2(CORPUS / "manifest.json", corpus / "manifest.json")
        results.append({"vaka": case["case_key"], "tur": case["tur"], "sonuc": res.outcome.value, "donen_belgeler": sorted(uids),
                        "uyarilar": list(res.warnings)[:4], "onay_reddi": refused, "gecti": not fails, "sorun": fails})  # fmt: skip
    vectors.close()
    unloader = OllamaEmbedder(model, common["ollama_base_url"], 2048, float(common["timeout_seconds"]), keep_alive=0)
    unloader.embed_one("boşaltma")  # keep_alive=0: model bellekten çıkar; sonraki aday tek başına yüklenir
    unload_wait(unloader, model)
    shutil.rmtree(tmp, ignore_errors=True)
    return results


def main() -> int:
    started = now()
    settings = load_settings(env={})
    store = check_store_dir(settings.rag_store_dir, min_free_gb=settings.rag_min_free_gb)
    profiles = load_rag_profiles(settings)
    seal = json.loads((EVAL / "muhur.json").read_text(encoding="utf-8"))
    seal_ok = sha_file(EVAL / "muhurlu_test.jsonl") == seal["sha256"]  # yalnız bayt özeti; içerik okunmaz
    parts = [load_dev_set(EVAL / name) for name in DEV_FILES]
    dev = replace(parts[0], cases=tuple(c for p in parts for c in p.cases), sha256="+".join(p.sha256 for p in parts))
    out = {"baslangic": started.isoformat(), "donanim_once": hardware(), "ollama_surum": OllamaEmbedder(CANDIDATES[0]).version(),
           "onaylar": verify_approvals(store), "dev_sha256": dev.sha256, "dev_vaka": len(dev.cases),
           "test_muhru": {"sha256": seal["sha256"], "dogrulandi": seal_ok, "okundu": False}, "adaylar": {}, "guvenlik": {}}  # fmt: skip
    if not seal_ok:
        print("Mühür doğrulanamadı; durduruldu.")
        return 2
    for model in CANDIDATES:  # sırayla; aynı anda yüklenmez
        print("Aday:", model, flush=True)
        out["adaylar"][model] = evaluate_candidate(model, store, profiles, dev)
    for model in reversed(CANDIDATES):  # dönüşümlü sıra: güvenlik vakaları ters sırada
        tau = out["adaylar"][model]["esik"]["onerilen_en_fazla_uzaklik"]
        print("Güvenlik vakaları:", model, flush=True)
        out["guvenlik"][model] = security_cases(model, tau, profiles)
    out["donanim_sonra"] = hardware()
    out["bitis"] = now().isoformat()
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for model, r in out["adaylar"].items():
        print(model, json.dumps({k: r[k] for k in ("dev", "esik", "gecikme_ms")}, ensure_ascii=False))
    for model, rs in out["guvenlik"].items():
        print(model, "güvenlik:", sum(x["gecti"] for x in rs), "/", len(rs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
