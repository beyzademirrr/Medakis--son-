"""D5 reranker skorlaması (YALNIZ izole ortamda; önce `. .\\experiments\\d5\\ortam.ps1`; Ollama modelleri boşaltılmış).

    & $PY experiments\\d5\\skorla.py smoke qwen06
    & $PY experiments\\d5\\skorla.py havuz qwen06 --tekrar 1 --talimat cevap

- fp32 ZORUNLU (GTX 16xx fp16 NaN riski; bf16 donanımda yok). Yerel dosyalardan, revision sabit, ağsız yükleme;
  `trust_remote_code` KULLANILMAZ (gerekirse yükleme hata verir ve durulur).
- Her çağrı ayrı süreçtir: model yüklenir, skorlanır, boşaltılır ve boşaltma doğrulanır.
- Qwen3-Reranker skoru model kartındaki biçimle: sistem öneki + `<Instruct>/<Query>/<Document>` + sonek; son konumda
  log_softmax([no, yes]) → P(yes). Sol dolgu. Kodlayıcılar: tek çıkışlı sınıflandırma logit'i (ham).
- Skorlar olasılık ya da anlamsal doğruluk garantisi DEĞİLDİR.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import torch
from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, AutoTokenizer

sys.path.insert(0, str(Path(__file__).parent))
from modeller import INSTRUCT_ANSWERABLE, INSTRUCT_DEFAULT, MAX_LENGTH, MODELS  # noqa: E402

D5 = Path(r"D:\medakis-deney\d5")
FIXTURE = Path(__file__).parent / "smoke_fikstur.json"
POOL = D5 / "sonuclar" / "aday_havuzu.json"
QWEN_PREFIX = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. "
               "Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n")  # fmt: skip
QWEN_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
# Tolerans gerekçesi: fp32 makine epsilonu ≈1,2e-7; 24–28 katman × ~1024 boyutlu toplamlarda yuvarlama birikimi göreli
# ~1e-6–1e-5 düzeyindedir; toplu/tekli ve dolgu farkı GPU çekirdek döşemesini değiştirir. Eşik: |Δ| ≤ 1e-4·max(1,|s|).
TOL_REL = 1e-4


def guard() -> None:
    if os.environ.get("HF_TOKEN") or not os.environ.get("HF_HOME", "").startswith("D:\\medakis-deney\\"):
        raise SystemExit("ortam başlatıcısı çalıştırılmadı")
    os.environ["HF_HUB_OFFLINE"] = "1"  # yalnız yerel dosyalar


class Sampler:
    def __init__(self):
        self.vram, self.rss, self._stop, self.proc = [], [], threading.Event(), psutil.Process()

    def _loop(self):
        while not self._stop.is_set():
            v = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
            if v.isdigit():
                self.vram.append(int(v))
            self.rss.append(self.proc.memory_info().rss)
            self._stop.wait(0.5)

    def __enter__(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=30)


class Scorer:
    def __init__(self, name: str, device: str, batch: int):
        info = json.loads((D5 / "kurulum" / f"indirme_{name}.json").read_text(encoding="utf-8"))
        self.name, self.kind, self.device, self.batch = name, MODELS[name]["tur"], device, batch
        path = info["yerel_yol"]
        t0 = time.perf_counter()
        if self.kind == "qwen":
            self.tok = AutoTokenizer.from_pretrained(path, padding_side="left", local_files_only=True, trust_remote_code=False)
            self.model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32, local_files_only=True, trust_remote_code=False)
            self.yes = self.tok.convert_tokens_to_ids("yes")
            self.no = self.tok.convert_tokens_to_ids("no")
            self.pre = self.tok.encode(QWEN_PREFIX, add_special_tokens=False)
            self.suf = self.tok.encode(QWEN_SUFFIX, add_special_tokens=False)
        else:
            self.tok = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
            self.model = AutoModelForSequenceClassification.from_pretrained(path, dtype=torch.float32, local_files_only=True, trust_remote_code=False)
            if self.model.config.num_labels != 1:
                raise SystemExit(f"{name}: beklenmeyen num_labels={self.model.config.num_labels}")
        self.model.to(device).eval()
        if device == "cuda":
            torch.cuda.synchronize()
        self.load_s = time.perf_counter() - t0
        self.path = path

    def checks(self) -> dict:
        out = {"dtype": str(next(self.model.parameters()).dtype), "cihaz": self.device}
        if self.kind == "qwen":
            out |= {"yes_id": self.yes, "no_id": self.no, "yes_tek_token": len(self.tok.encode("yes", add_special_tokens=False)) == 1,
                    "no_tek_token": len(self.tok.encode("no", add_special_tokens=False)) == 1,
                    "yes_no_farkli": self.yes != self.no and self.yes != self.tok.unk_token_id, "dolgu": self.tok.padding_side}  # fmt: skip
        return out

    def _encode_qwen(self, query, docs, instruct):
        pairs = [f"<Instruct>: {instruct}\n<Query>: {query}\n<Document>: {d}" for d in docs]
        room = MAX_LENGTH - len(self.pre) - len(self.suf)
        full = self.tok(pairs, add_special_tokens=False)["input_ids"]
        trunc = sum(len(x) > room for x in full)
        ids = [self.pre + x[:room] + self.suf for x in full]
        enc = self.tok.pad({"input_ids": ids}, padding=True, return_tensors="pt")
        return enc, trunc

    @torch.inference_mode()
    def score(self, query: str, docs: list[str], instruct: str | None = None) -> tuple[list[float], int]:
        scores, trunc = [], 0
        self.logodds = []
        for i in range(0, len(docs), self.batch):
            chunk = docs[i:i + self.batch]
            if self.kind == "qwen":
                enc, t = self._encode_qwen(query, chunk, instruct or INSTRUCT_DEFAULT)
                enc = {k: v.to(self.device) for k, v in enc.items()}
                logits = self.model(**enc).logits[:, -1, :]
                pair = torch.stack([logits[:, self.no], logits[:, self.yes]], dim=1).float()
                s = torch.nn.functional.log_softmax(pair, dim=1)[:, 1].exp()
                # P(yes)'in tekdüze karşılığı (log-oran): 1,0'daki fp32 doymasında eşitlikleri çözmek için ayrıca kaydedilir
                self.logodds += (pair[:, 1] - pair[:, 0]).cpu().tolist()
            else:
                full = self.tok([query] * len(chunk), chunk)["input_ids"]
                t = sum(len(x) > MAX_LENGTH for x in full)
                enc = self.tok([query] * len(chunk), chunk, padding=True, truncation=True, max_length=MAX_LENGTH, return_tensors="pt")
                enc = {k: v.to(self.device) for k, v in enc.items()}
                s = self.model(**enc).logits.squeeze(-1).float()
            scores += s.cpu().tolist()
            trunc += t
        return scores, trunc

    def unload(self) -> dict:
        del self.model
        gc.collect()
        if self.device == "cuda":
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            return {"bellekte_kalan_bayt": torch.cuda.memory_allocated(), "tepe_ayrilan_bayt": torch.cuda.max_memory_allocated()}
        return {"bellekte_kalan_bayt": 0}


def close(a: float, b: float) -> bool:
    return abs(a - b) <= TOL_REL * max(1.0, abs(a), abs(b))


def smoke(sc: Scorer, instruct: str | None) -> dict:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    res = {"gruplar": [], "kontroller": {}}
    all_ok = {"sonlu": True, "sabit_degil": True, "D_gt_U": True, "determinizm": True, "toplu_tekli": True, "sira_degismezligi": True}
    max_det = max_bt = max_perm = 0.0
    trunc = 0
    for g in fx["gruplar"]:
        keys = list(g["belgeler"])
        docs = [g["belgeler"][k] for k in keys]
        base, t = sc.score(g["soru"], docs, instruct)
        trunc += t
        reps = [sc.score(g["soru"], docs, instruct)[0] for _ in range(2)]
        singles = [sc.score(g["soru"], [d], instruct)[0][0] for d in docs]
        perms = {}
        for pname, order in {"ters": keys[::-1], "D_sonda": [k for k in keys if k != "D"] + ["D"], "kaydirma3": keys[3:] + keys[:3]}.items():
            sc_p, _ = sc.score(g["soru"], [g["belgeler"][k] for k in order], instruct)
            perms[pname] = dict(zip(order, sc_p))
        s = dict(zip(keys, base))
        finite = all(math.isfinite(x) for x in base)
        spread = max(base) - min(base)
        det = max(abs(a - b) for r in reps for a, b in zip(base, r))
        bt = max(abs(a - b) for a, b in zip(base, singles))
        perm = max(abs(s[k] - p[k]) for p in perms.values() for k in keys)
        rank_D = sorted(keys, key=lambda k: -s[k]).index("D") + 1
        perm_rank_same = all(sorted(keys, key=lambda k: -p[k]).index("D") + 1 == rank_D for p in perms.values())
        max_det, max_bt, max_perm = max(max_det, det), max(max_bt, bt), max(max_perm, perm)
        all_ok["sonlu"] &= finite
        all_ok["sabit_degil"] &= spread > 1e-3
        all_ok["D_gt_U"] &= s["D"] > s["U"]
        all_ok["determinizm"] &= all(close(a, b) for r in reps for a, b in zip(base, r))
        all_ok["toplu_tekli"] &= all(close(a, b) for a, b in zip(base, singles))
        all_ok["sira_degismezligi"] &= all(close(s[k], p[k]) for p in perms.values() for k in keys) and perm_rank_same
        res["gruplar"].append({"id": g["id"], "skor": {k: round(v, 6) for k, v in s.items()}, "D_sirasi": rank_D, "yayilim": round(spread, 6),
                               "bilgi": {"D_gt_I": s["D"] > s["I"], "D_gt_C": s["D"] > s["C"], "D_gt_O": s["D"] > s["O"],
                                         "D_gt_S": s["D"] > s["S"], "N_en_ust": max(s, key=s.get) == "N"}})  # fmt: skip
    res["kontroller"] = {**all_ok, "kesilen_cift": trunc}
    res["en_buyuk_fark"] = {"determinizm": max_det, "toplu_tekli": max_bt, "sira": max_perm, "tolerans_goreli": TOL_REL}
    res["bilgi_ozet"] = {k: sum(gr["bilgi"][k] for gr in res["gruplar"]) for k in res["gruplar"][0]["bilgi"]}
    res["gecti"] = all(all_ok.values())
    return res


def st_crosscheck(sc: Scorer) -> dict:
    """Model kartının sentence-transformers yolu (depodaki varsayılan istem) ile elle yazılan P(yes) hesabının
    karşılaştırması. Yalnız Qwen ve yalnız VARSAYILAN talimatla (ST'nin depo istemiyle aynı olduğu için)."""
    try:
        from sentence_transformers import CrossEncoder

        ce = CrossEncoder(sc.path, device=sc.device, local_files_only=True, trust_remote_code=False,
                          model_kwargs={"dtype": torch.float32}, activation_fn=torch.nn.Sigmoid())  # fmt: skip
        prompts = dict(getattr(ce, "prompts", None) or {})
        fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
        diffs, same = [], True
        for g in fx["gruplar"]:
            docs = list(g["belgeler"].values())
            mine, _ = sc.score(g["soru"], docs, INSTRUCT_DEFAULT)
            theirs = [float(x) for x in ce.predict([(g["soru"], d) for d in docs])]
            diffs.append(max(abs(a - b) for a, b in zip(mine, theirs)))
            same &= sorted(range(len(docs)), key=lambda i: -mine[i]) == sorted(range(len(docs)), key=lambda i: -theirs[i])
        del ce
        gc.collect()
        return {"yapildi": True, "en_buyuk_fark": max(diffs), "ayni_siralama": same, "st_istemleri": prompts}
    except Exception as exc:  # noqa: BLE001 — yalnız kayıt; skorlamayı etkilemez
        return {"yapildi": False, "hata": f"{type(exc).__name__}: {str(exc)[:300]}"}


def pool(sc: Scorer, instruct: str | None) -> dict:
    data = json.loads(POOL.read_text(encoding="utf-8"))
    text = {p["parca"]: p["metin"] for p in data["parcalar"]}
    out, lo, times, trunc, nonfinite = {}, {}, [], 0, 0
    for c in data["vakalar"]:
        ids = sorted({a["parca"] for h in c["havuzlar"].values() for a in h["adaylar"]})
        t0 = time.perf_counter()
        s, t = sc.score(c["soru"], [text[i] for i in ids], instruct)
        if sc.device == "cuda":
            torch.cuda.synchronize()
        times.append((time.perf_counter() - t0) * 1000)
        trunc += t
        nonfinite += sum(not math.isfinite(x) for x in s)
        out[c["vaka"]] = dict(zip(ids, s))
        if sc.kind == "qwen":
            lo[c["vaka"]] = dict(zip(ids, sc.logodds))
    return {"skorlar": out, "skorlar_logoran": lo, "vaka_ms": times, "kesilen_cift": trunc, "sonlu_olmayan": nonfinite, "cift": sum(len(v) for v in out.values())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kip", choices=["smoke", "havuz"])
    ap.add_argument("model", choices=list(MODELS))
    ap.add_argument("--talimat", choices=["varsayilan", "cevap"], default="varsayilan")
    ap.add_argument("--tekrar", type=int, default=1)
    ap.add_argument("--cihaz", choices=["cuda", "cpu"], default="cuda")
    ap.add_argument("--batch", type=int, default=4)
    a = ap.parse_args()
    guard()
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if a.cihaz == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA kullanılamıyor")
    instruct = (INSTRUCT_ANSWERABLE if a.talimat == "cevap" else INSTRUCT_DEFAULT) if MODELS[a.model]["tur"] == "qwen" else None
    t_start = time.perf_counter()
    with Sampler() as smp:
        sc = Scorer(a.model, a.cihaz, a.batch)
        meta = {"model": MODELS[a.model]["repo"], "revision": MODELS[a.model]["revision"], "kip": a.kip, "talimat": a.talimat if instruct else None,
                "tekrar": a.tekrar, "batch": a.batch, "yukleme_s": round(sc.load_s, 2), "torch": torch.__version__,
                "transformers": __import__("transformers").__version__, "gpu": torch.cuda.get_device_name(0) if a.cihaz == "cuda" else None,
                **sc.checks()}  # fmt: skip
        if a.kip == "smoke":
            body = smoke(sc, instruct)
            if sc.kind == "qwen":
                body["st_karsilastirma"] = st_crosscheck(sc) if a.talimat == "varsayilan" else {"yapildi": False, "neden": "yalnız varsayılan talimatla"}
        else:
            t0 = time.perf_counter()
            body = pool(sc, instruct)
            body["ilk_vaka_ms"] = body["vaka_ms"][0]
            body["soguk_ms"] = round((sc.load_s * 1000) + body["vaka_ms"][0], 1)
            body["sicak_ms_medyan"] = sorted(body["vaka_ms"][1:])[len(body["vaka_ms"][1:]) // 2]
            body["sicak_ms_p95"] = sorted(body["vaka_ms"][1:])[int(0.95 * (len(body["vaka_ms"]) - 2))]
            body["toplam_s"] = round(time.perf_counter() - t0, 2)
        meta["bosaltma"] = sc.unload()
    meta["vram_tepe_mib"] = max(smp.vram, default=None)
    v = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
    meta["vram_kullanilan_mib_bosaltma_sonrasi_surec_ici"] = int(v) if v.isdigit() else None
    meta["ram_tepe_mib"] = round(max(smp.rss, default=0) / 2**20)
    meta["sure_s"] = round(time.perf_counter() - t_start, 2)
    tag = f"{a.kip}_{a.model}_{a.talimat if instruct else 'yok'}_{a.cihaz}_t{a.tekrar}"
    out = D5 / "sonuclar" / f"{tag}.json"
    out.write_text(json.dumps({"meta": meta, **body}, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = body["kontroller"] if a.kip == "smoke" else {k: body[k] for k in ("cift", "kesilen_cift", "sonlu_olmayan", "soguk_ms", "sicak_ms_medyan")}
    print(tag, json.dumps({"gecti": body.get("gecti"), **summary, "vram_tepe": meta["vram_tepe_mib"], "ram_tepe": meta["ram_tepe_mib"],
                           "bosaltma": meta["bosaltma"]}, ensure_ascii=False))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
