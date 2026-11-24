"""Aşama 8.6 ölçümü: keep_alive politikalarına göre soğuk/sıcak süreler, RAM ve VRAM (gpu_standart profili; TEST kipi).

    .\\.venv\\Scripts\\python.exe scripts\\rag_donanim_olcumu.py

- Her politika TAM BOŞALTILMIŞ durumdan başlar; aynı 6 dev sorusu hat üzerinden (aday_deneyi; yalnız süre ölçümü için,
  A6 kararı ölçülmez ve hiçbir parametre değişmez) sırayla çalıştırılır. İlk sorgu soğuk, sonrakiler politikaya göre.
- Ölçülen: retrieval ms, kanıt denetimi (üretici) ms, toplam ms; VRAM (nvidia-smi) ve ollama.exe + llama-server.exe RAM
  tepe değerleri (0,5 sn örnekleme); her sorgudan sonra yüklü modeller (/api/ps).
- Model indirilmez; eşik yazılmaz; normal sorgu kapalı kalır. Çıktı: docs/veri/asama8_donanim_olcumu.json
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_settings  # noqa: E402
from medakis.rag.embeddings.ollama_client import OllamaEmbedder  # noqa: E402
from medakis.rag.evaluation.eval_set import load_dev_set  # noqa: E402
from medakis.rag.generation.lifecycle import ModelLifecycle  # noqa: E402
from medakis.rag.generation.mode import GenerationMode  # noqa: E402
from medakis.rag.generation.ollama_generator import OllamaGenerator  # noqa: E402
from medakis.rag.generation.pipeline import POLICY_CANDIDATES, EvidencePipeline  # noqa: E402
from medakis.rag.generation.profiles import PROFILES, KeepAlivePolicy  # noqa: E402
from medakis.rag.retrieval.factory import open_retriever  # noqa: E402

OUT = ROOT / "docs" / "veri" / "asama8_donanim_olcumu.json"
IMAGES = {"ollama.exe", "llama-server.exe"}
N = 6


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace").stdout.strip()


class Sampler:
    def __init__(self):
        self.vram, self.rss, self._stop = [], [], threading.Event()

    def _loop(self):
        while not self._stop.is_set():
            v = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
            if v.isdigit():
                self.vram.append(int(v))
            kb = sum(int("".join(ch for ch in r[4] if ch.isdigit()) or 0)
                     for r in csv.reader(io.StringIO(run(["tasklist", "/FO", "CSV", "/NH"]))) if len(r) >= 5 and r[0].lower() in IMAGES)  # fmt: skip
            if kb:
                self.rss.append(kb)
            self._stop.wait(0.5)

    def __enter__(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=30)

    def peaks(self):
        return {"vram_tepe_mib": max(self.vram) if self.vram else None, "ollama_ram_tepe_mib": round(max(self.rss) / 1024) if self.rss else None}


def probe() -> tuple[float | None, int | None]:
    """Anlık boş RAM (GB) ve boş VRAM (MiB); ikisi_sicak politikasının yeterlilik denetimi için."""
    mem = run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"])
    gpu = run(["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"])
    ram = round(int(mem) / 1048576, 1) if mem.isdigit() else None
    vram = None
    if "," in gpu:
        total, used = (int(x.strip()) for x in gpu.splitlines()[0].split(","))
        vram = total - used
    return ram, vram


def main() -> int:
    settings = load_settings(env={})
    profile = PROFILES["gpu_standart"]
    cases = [c for c in load_dev_set(ROOT / "evaluation" / "asama7" / "dev.jsonl").cases if c.answerable][:N]
    out = {"profil": profile.name, "embedding": profile.embed_model, "uretici": profile.generator_model, "sorgu": len(cases), "politikalar": {}}
    for policy in KeepAlivePolicy:
        embed_ka, gen_ka = policy.values()
        embedder = OllamaEmbedder(profile.embed_model, keep_alive=embed_ka)
        generator = OllamaGenerator(GenerationMode.TEST, keep_alive=gen_ka, num_ctx=profile.num_ctx, num_predict=profile.num_predict)
        life = ModelLifecycle(embedder, generator, policy, mode=GenerationMode.TEST, resources=probe)  # KM31: kip ve kaynak denetimi
        life.unload_all()  # tam boşaltılmış başlangıç
        retriever = open_retriever(settings, embedder=embedder, model=profile.embed_model)
        pipe = EvidencePipeline(retriever, generator, GenerationMode.TEST, lifecycle=life)
        rows = []
        with Sampler() as sampler:
            for c in cases:
                t0 = time.perf_counter()
                retrieval = pipe.retrieve(c.question, c.as_of)
                t1 = time.perf_counter()
                result = pipe.decide(c.question, retrieval, POLICY_CANDIDATES)
                t2 = time.perf_counter()
                rows.append({"vaka": c.case_key, "getirme_ms": round((t1 - t0) * 1000), "uretici_ms": round((t2 - t1) * 1000),
                             "toplam_ms": round((t2 - t0) * 1000), "sonuc": result.outcome.value,
                             "yuklu": sorted(m.get("name") for m in generator.loaded_models())})  # fmt: skip
        retriever.vectors.close()
        life.unload_all()
        warm = rows[1:]
        out["politikalar"][policy.value] = {
            "keep_alive": {"embedding": embed_ka, "uretici": gen_ka}, "ilk_sorgu_soguk_ms": rows[0]["toplam_ms"],
            "sonraki_toplam_ms": [r["toplam_ms"] for r in warm], "sonraki_toplam_ort_ms": round(sum(r["toplam_ms"] for r in warm) / len(warm)),
            "sonraki_en_fazla_ms": max(r["toplam_ms"] for r in warm), **sampler.peaks(), "sorgular": rows, "yasam_dongusu": life.events[-8:],
        }  # fmt: skip
        print(policy.value, {k: out["politikalar"][policy.value][k] for k in ("ilk_sorgu_soguk_ms", "sonraki_toplam_ort_ms", "sonraki_en_fazla_ms",
                                                                              "vram_tepe_mib", "ollama_ram_tepe_mib")}, flush=True)  # fmt: skip
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
