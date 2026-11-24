"""D5 reranker işçi süreci (YALNIZ izole ortam). Ana MedAkis ortamındaki orkestratör stdin/stdout JSON satırlarıyla
yönetir; ağ ya da port kullanılmaz. Komutlar:
  {"komut": "yukle", "model": "lamar", "cihaz": "cuda"}   → model yüklenir (fp32; yerel dosya; trust_remote_code yok)
  {"komut": "skorla", "soru": "...", "belgeler": [...]}    → {"skorlar": [...], "sure_ms": ...}
  {"komut": "bosalt"}                                      → model bellekten çıkarılır ve doğrulanır
  {"komut": "cik"}
Ortam denetimi `skorla.guard()` ile aynıdır (HF_TOKEN yok, yollar D:, HF_HUB_OFFLINE=1).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).parent))
from skorla import Scorer, guard  # noqa: E402


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    guard()
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True, warn_only=True)
    sc = None
    send({"hazir": True, "torch": torch.__version__, "cuda": torch.cuda.is_available()})
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            cmd = msg.get("komut")
            if cmd == "yukle":
                if sc is not None:
                    raise RuntimeError("model zaten yüklü")
                t0 = time.perf_counter()
                sc = Scorer(msg["model"], msg.get("cihaz", "cuda"), int(msg.get("batch", 4)))
                send({"tamam": True, "yukleme_ms": round((time.perf_counter() - t0) * 1000, 1)})
            elif cmd == "skorla":
                t0 = time.perf_counter()
                s, trunc = sc.score(msg["soru"], msg["belgeler"], msg.get("talimat"))
                if sc.device == "cuda":
                    torch.cuda.synchronize()
                send({"tamam": True, "skorlar": s, "kesilen": trunc, "sure_ms": round((time.perf_counter() - t0) * 1000, 1)})
            elif cmd == "bosalt":
                t0 = time.perf_counter()
                info = sc.unload() if sc is not None else {"bellekte_kalan_bayt": 0}
                sc = None
                send({"tamam": True, "bosaltma_ms": round((time.perf_counter() - t0) * 1000, 1), **info})
            elif cmd == "cik":
                send({"tamam": True})
                return 0
            else:
                send({"tamam": False, "hata": f"bilinmeyen komut {cmd}"})
        except Exception as exc:  # noqa: BLE001 — orkestratör teknik hata olarak kaydeder
            send({"tamam": False, "hata": f"{type(exc).__name__}: {str(exc)[:300]}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
