"""Aşama 8.6 başlangıç denetimi: RAM, VRAM, GPU, sürücü, disk, Ollama sürümü, kurulu/yüklü modeller ve profil seçimi.

    .\\.venv\\Scripts\\python.exe scripts\\rag_baslangic_denetimi.py [--profil gpu_standart|cpu_yedek|retrieval_only]

Salt okunur: model indirmez, yüklemez, boşaltmaz; Ollama'ya yalnız /api/version, /api/tags, /api/ps çağrısı yapar.
D: politikası denetlenir (OLLAMA_MODELS ve RAG deposu sistem sürücüsünde olmamalı). Çıktı: docs/veri/asama8_baslangic_denetimi.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medakis.config import load_settings  # noqa: E402
from medakis.rag.generation.profiles import HardwareSnapshot, select_profile  # noqa: E402

OUT = ROOT / "docs" / "veri" / "asama8_baslangic_denetimi.json"
OLLAMA = "http://127.0.0.1:11434"


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace").stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _get(path: str) -> dict | None:
    try:
        with urllib.request.urlopen(OLLAMA + path, timeout=5) as resp:  # yalnız loopback
            return json.load(resp)
    except Exception:  # noqa: BLE001
        return None


def collect() -> tuple[HardwareSnapshot, dict]:
    settings = load_settings(env={})
    mem = _run(["powershell", "-NoProfile", "-Command",
                "$o=Get-CimInstance Win32_OperatingSystem; '{0};{1}' -f $o.TotalVisibleMemorySize,$o.FreePhysicalMemory"])  # fmt: skip
    total_kb, free_kb = (int(x) for x in mem.split(";")) if ";" in mem else (0, 0)
    gpu = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"])
    name = total = used = driver = None
    if gpu.count(",") >= 3:
        name, total, used, driver = (x.strip() for x in gpu.splitlines()[0].split(","))
    version = (_get("/api/version") or {}).get("version")
    tags = (_get("/api/tags") or {}).get("models", [])
    ps = (_get("/api/ps") or {}).get("models", [])
    models_dir = os.environ.get("OLLAMA_MODELS", "")
    snap = HardwareSnapshot(
        ram_total_gb=round(total_kb / 1048576, 1), ram_free_gb=round(free_kb / 1048576, 1), gpu_name=name,
        vram_total_mib=int(total) if total else None, vram_used_mib=int(used) if used else None, driver=driver,
        disk_free_gb={d: round(shutil.disk_usage(f"{d}:\\").free / 1024**3, 1) for d in ("C", "D") if Path(f"{d}:\\").exists()},
        ollama_version=version, installed={m["name"]: m["digest"] for m in tags}, loaded=tuple(m["name"] for m in ps),
        models_drive=PureWindowsPath(models_dir).drive.upper() or None, store_drive=PureWindowsPath(str(settings.rag_store_dir)).drive.upper(),
        system_drive=(os.environ.get("SystemDrive") or "C:").upper(),
    )  # fmt: skip
    store = Path(settings.rag_store_dir)
    try:  # yerel mutlak yol kayda yazılmaz: proje köküne göreli yol ve sürücü
        store_text = f"{PureWindowsPath(str(store)).drive}<proje>/{store.relative_to(ROOT).as_posix()}"
    except ValueError:
        store_text = f"{PureWindowsPath(str(store)).drive}<proje dışı>"
    extra = {"ollama_models": models_dir or "(tanımsız: varsayılan kullanıcı klasörü)", "rag_deposu": store_text,
             "yuklu_model_bellek": [{"ad": m["name"], "boyut_mib": round(m.get("size", 0) / 1048576), "vram_mib": round(m.get("size_vram", 0) / 1048576)} for m in ps]}  # fmt: skip
    return snap, extra


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profil", default="gpu_standart")
    args = parser.parse_args(argv)
    snap, extra = collect()
    sel = select_profile(snap, args.profil)
    out = {"zaman": datetime.now(UTC).isoformat(), "anlik_goruntu": {**snap.__dict__, "vram_bos_mib": snap.vram_free_mib}, **extra,
           "istenen_profil": sel.requested, "secilen_profil": sel.profile.name, "uretici": sel.profile.generator_model,
           "elenen_profiller": list(sel.reasons), "uyarilar": list(sel.warnings)}  # fmt: skip
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
