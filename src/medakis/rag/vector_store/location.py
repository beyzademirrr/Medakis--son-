"""RAG yerel deposunun konum denetimi (KM29).

Depo (Chroma, belge onay izi, indeks kaydı, sorgu kaydı) merkezi yapılandırmadaki MUTLAK yoldadır
(`config.Settings.rag_store_dir`). Denetim:
- yol mutlak ve sürücü harfi taşıyor (UNC/göreli yol reddedilir);
- sistem sürücüsünde DEĞİL (Windows: %SystemDrive%, varsayılan C:);
- klasör yoksa yalnız bu güvenli sürücüde oluşturulur; yazılabilir olmalı;
- sürücüde en az `min_free_gb` boş alan kalmalı.
Denetim geçmezse StoreUnavailable; başka bir konuma sessiz geçiş YAPILMAZ.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

from medakis.rag.errors import StoreUnavailable

GB = 1024**3


def system_drive() -> str:
    return (os.environ.get("SystemDrive") or "C:").upper()


def check_store_dir(path: Path | str | None, *, min_free_gb: float, sys_drive: str | None = None,
                    disk_usage: Callable[[str], object] = shutil.disk_usage, create: bool = True) -> Path:  # fmt: skip
    if path is None:
        raise StoreUnavailable("depo_yolu_tanimsiz")
    raw = str(path)
    drive = PureWindowsPath(raw).drive.upper()
    if not PureWindowsPath(raw).is_absolute() or len(drive) != 2 or drive[1] != ":":
        raise StoreUnavailable("depo_yolu_mutlak_degil", raw)
    if drive == (sys_drive or system_drive()):
        raise StoreUnavailable("sistem_surucusu_yasak", drive)
    target = Path(raw)
    if target.exists() and (target.is_symlink() or not target.is_dir()):
        raise StoreUnavailable("depo_klasor_degil")
    if not target.exists():
        if not create:
            raise StoreUnavailable("depo_yok")
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StoreUnavailable("depo_olusturulamadi", type(exc).__name__) from None
    free = getattr(disk_usage(str(target)), "free", 0)
    if free < min_free_gb * GB:
        raise StoreUnavailable("bos_alan_yetersiz", f"{free / GB:.1f} GB < {min_free_gb:g} GB")
    probe = target / f".yazma_denetimi_{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        raise StoreUnavailable("depo_yazilamiyor", type(exc).__name__) from None
    return target.resolve()
