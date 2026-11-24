"""D5 model indirme ve doğrulama (yalnız izole ortamda; önce `. .\\experiments\\d5\\ortam.ps1`).

    & $PY experiments\\d5\\indir.py qwen06 modernbert

- Yalnız onaylı modeller ve sabit revision; yalnız gereken dosyalar (ONNX ve ek biçimler indirilmez).
- Token KULLANILMAZ (token=False). İndirilen her LFS dosyasının SHA-256'sı Hub'daki LFS özetiyle karşılaştırılır;
  uyuşmazlıkta durur. Sonuç `D:\\medakis-deney\\d5\\kurulum\\indirme_<model>.json`.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import sys
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

sys.path.insert(0, str(Path(__file__).parent))
from modeller import MODELS  # noqa: E402

OUT = Path(r"D:\medakis-deney\d5\kurulum")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(names: list[str]) -> int:
    if os.environ.get("HF_TOKEN") or not os.environ.get("HF_HOME", "").startswith("D:\\medakis-deney\\"):
        raise SystemExit("ortam başlatıcısı çalıştırılmadı (HF_TOKEN var ya da HF_HOME D: değil)")
    api = HfApi()
    for name in names:
        m = MODELS[name]
        info = api.model_info(m["repo"], revision=m["revision"], files_metadata=True, token=False)
        if info.sha != m["revision"]:
            raise SystemExit(f"{name}: revision uyuşmuyor {info.sha}")
        wanted = [s for s in info.siblings if any(fnmatch.fnmatch(s.rfilename, p) for p in m["dosyalar"])]
        path = Path(snapshot_download(m["repo"], revision=m["revision"], allow_patterns=m["dosyalar"], token=False))
        files = []
        for s in wanted:
            local = path / s.rfilename
            got = sha256(local)
            expected = s.lfs.sha256 if s.lfs else None
            if expected and got != expected:
                raise SystemExit(f"{name}: SHA-256 uyuşmuyor {s.rfilename}")
            files.append({"dosya": s.rfilename, "boyut": local.stat().st_size, "sha256": got, "lfs_dogrulandi": bool(expected)})
        cfg = json.loads((path / "config.json").read_text(encoding="utf-8"))
        if cfg.get("auto_map"):
            raise SystemExit(f"{name}: config.auto_map var (trust_remote_code gerekebilir) — ayrı onay gerekir")
        rec = {"model": m["repo"], "revision": info.sha, "yerel_yol": str(path), "dosyalar": files,
               "toplam_bayt": sum(f["boyut"] for f in files), "mimari": cfg.get("architectures")}  # fmt: skip
        (OUT / f"indirme_{name}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(name, rec["revision"][:12], rec["toplam_bayt"], "LFS doğrulandı:", sum(f["lfs_dogrulandi"] for f in files), "/", len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
