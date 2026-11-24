"""D5 mimari karşılaştırması ortak yardımcıları (ana MedAkis ortamı; model çalıştırmaz, işçi sürecini yönetir).

- Aday havuzu: D5'te bir kez üretilen `aday_havuzu.json` (aynı havuzlar). Parçalar manifest ve corpus'tan yeniden kurulur;
  hash uyuşmazlığında durulur. Retrieval sonucu `found` değilse aynen geçer.
- Kapı eşikleri: her reranker için AYRI; D5 skorlarından (fp32; üç tekrar birebir aynı) grup bazlı 5 katlı CV.
  Vakanın eşiği yalnız o vakanın DIŞINDAKİ katlardan seçilir. Hiçbir eşik kayda yazılmaz.
- İşçi: izole ortam Python'u (`D:\\medakis-deney\\d5\\.venv`), ortam değişkenleri D: yollarına çekilmiş ve HF_TOKEN
  kaldırılmış olarak başlatılır.
"""

from __future__ import annotations

import ctypes
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.rag.contracts.retrieval import RETRIEVAL_ONLY_LABEL, RetrievalOutcome, RetrievalResult, RetrievedChunk  # noqa: E402
from medakis.rag.evaluation.answerability import CvCase, fold_of, group_cv, to_distance  # noqa: E402
from medakis.rag.ingestion.corpus_loader import load_corpus  # noqa: E402
from medakis.rag.retrieval.thresholds import DevObservation, calibrate  # noqa: E402

D5 = Path(r"D:\medakis-deney\d5")
POOL = ROOT / "docs" / "veri" / "asama8_d5_aday_havuzu.json"
HAM = ROOT / "docs" / "veri" / "asama8_d5_ham"
CORPUS = ROOT / "corpus" / "kontrollu"
GATE_MODELS = {"lamar": "havuz_lamar_yok_cuda_t1.json", "modernbert": "havuz_modernbert_yok_cuda_t1.json"}
POOL_MAIN = "H10+"  # D5'te H20+ ile birebir aynı sonuç: deneysel öncelikli havuz


def split_of(group: str) -> str:
    return "teyit" if int(hashlib.sha256(("secim_teyit_v1:" + group).encode()).hexdigest()[:8], 16) % 3 == 0 else "secim"


def iso(dt):
    return None if dt is None else dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def load_pool() -> dict:
    return json.loads(POOL.read_text(encoding="utf-8"))


def chunk_factory(data: dict):
    docs = load_corpus(CORPUS).by_uid()
    parts = {p["parca"]: p for p in data["parcalar"]}

    def make(cid: str, rank: int, distance: float) -> RetrievedChunk:
        p = parts[cid]
        doc = docs[p["belge"]]
        s, e = p["aralik"]
        text = doc.text[s:e]
        if text != p["metin"]:
            raise SystemExit(f"parça metni corpus ile uyuşmuyor: {cid}")
        r = doc.record
        return RetrievedChunk(rank, cid, distance, r.source_key, r.source_name, r.doc_type, r.version, p["bolum"], iso(r.valid_from),
                              iso(r.valid_until), s, e, text)  # fmt: skip

    return make


def retrieval_for(case: dict, pool: str, make) -> RetrievalResult:
    h = case["havuzlar"][pool]
    as_of = datetime.fromisoformat(case["as_of"])
    if h["sonuc"] != "found":
        return RetrievalResult(RetrievalOutcome(h["sonuc"]), "x", (), as_of, None, None, warnings=tuple(h["uyarilar"]), evaluation=True)
    chunks = tuple(make(a["parca"], i, a["uzaklik"]) for i, a in enumerate(sorted(h["adaylar"], key=lambda a: a["sira"]), 1))
    return RetrievalResult(RetrievalOutcome.FOUND, RETRIEVAL_ONLY_LABEL, chunks, as_of, None, None, warnings=tuple(h["uyarilar"]), evaluation=True)


def d5_scores(model: str) -> dict:
    return json.loads((HAM / GATE_MODELS[model]).read_text(encoding="utf-8"))["skorlar"]


def gate_thresholds(data: dict, model: str, pool: str = POOL_MAIN) -> tuple[dict, dict]:
    """{vaka: eşik skoru (yüksek-iyi) ya da None} — CV, eğitim katları; ve bilgi kaydı."""
    sc = d5_scores(model)
    cases = []
    for c in data["vakalar"]:
        ids = [a["parca"] for a in c["havuzlar"][pool]["adaylar"]]
        cases.append(CvCase(c["vaka"], c["grup"], c["ans"], max(sc[c["vaka"]][i] for i in ids) if ids else None))
    _, taus = group_cv(cases, higher_is_better=True)
    per = {c.case_key: (None if taus[fold_of(c.group)] is None else -taus[fold_of(c.group)]) for c in cases}
    full = calibrate([DevObservation(c.case_key, "dev", c.answerable, to_distance(c.score, True)) for c in cases]).max_distance
    return per, {"kat_esikleri_skor": [None if t is None else -t for t in taus], "tum_dev_esigi_skor": None if full is None else -full}


def d5_env() -> dict:
    env = dict(os.environ)
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        env.pop(k, None)
    base = str(D5)
    env.update({"TMP": f"{base}\\tmp", "TEMP": f"{base}\\tmp", "TMPDIR": f"{base}\\tmp", "PIP_CACHE_DIR": f"{base}\\pip-cache",
                "HF_HOME": f"{base}\\hf", "HF_HUB_CACHE": f"{base}\\hf\\hub", "TORCH_HOME": f"{base}\\torch",
                "CUDA_CACHE_PATH": f"{base}\\cuda-cache", "XDG_CACHE_HOME": f"{base}\\tmp", "PYTHONNOUSERSITE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "HF_HUB_OFFLINE": "1",
                "TOKENIZERS_PARALLELISM": "false", "PYTHONIOENCODING": "utf-8"})  # fmt: skip
    for k in ("TMP", "TEMP", "HF_HOME", "TORCH_HOME", "CUDA_CACHE_PATH"):
        if not env[k].startswith("D:\\medakis-deney\\"):
            raise SystemExit(f"C: geri dönüşü: {k}")
    return env


class Worker:
    """İzole ortamdaki reranker işçisi (stdin/stdout JSON satırları)."""

    def __init__(self) -> None:
        t0 = time.perf_counter()
        self.p = subprocess.Popen([str(D5 / ".venv" / "Scripts" / "python.exe"), str(ROOT / "experiments" / "d5" / "isci.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=d5_env(),
                                  text=True, encoding="utf-8", bufsize=1)  # fmt: skip
        hello = self._read()
        if not hello.get("hazir") or not hello.get("cuda"):
            raise SystemExit(f"işçi hazır değil: {hello}")
        self.start_ms = round((time.perf_counter() - t0) * 1000, 1)

    def _read(self) -> dict:
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise SystemExit("işçi kapandı")
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)

    def call(self, **msg) -> dict:
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return self._read()

    def close(self) -> None:
        try:
            self.call(komut="cik")
        finally:
            self.p.wait(timeout=60)


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]  # fmt: skip


def system_ram_used_mib() -> int:
    m = _MemStatus()
    m.dwLength = ctypes.sizeof(_MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return round((m.ullTotalPhys - m.ullAvailPhys) / 2**20)


def gpu_used_mib() -> int | None:
    v = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
    return int(v) if v.isdigit() else None


class SystemSampler:
    """Sistem düzeyinde VRAM (bütün süreçler) ve kullanılan fiziksel RAM; örnek o anki etikete yazılır."""

    def __init__(self) -> None:
        self.label, self.samples, self._stop = None, [], threading.Event()
        self.baseline = {"vram_mib": gpu_used_mib(), "ram_mib": system_ram_used_mib()}

    def _loop(self):
        while not self._stop.is_set():
            lab = self.label
            if lab:
                self.samples.append((lab, gpu_used_mib(), system_ram_used_mib()))
            self._stop.wait(0.5)

    def __enter__(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=30)

    def peaks(self, label: str) -> dict:
        v = [s[1] for s in self.samples if s[0] == label and s[1] is not None]
        r = [s[2] for s in self.samples if s[0] == label]
        return {"vram_tepe_mib": max(v, default=None), "sistem_ram_tepe_mib": max(r, default=None), "ornek": len(r)}


def gold_overlap(chunks_or_quotes, gold_ranges) -> bool:
    for q in chunks_or_quotes:
        uid = f"{q.source_key}@{q.version}"
        s = getattr(q, "doc_start", getattr(q, "quote_start", None))
        e = getattr(q, "doc_end", getattr(q, "quote_end", None))
        if any(uid == g[0] and s < g[2] and g[1] < e for g in gold_ranges):
            return True
    return False


def replace_rank(chunks, order):
    return tuple(dataclasses.replace(c, rank=i) for i, c in enumerate(order, 1))
