"""Testlerin oluşturduğu yedek dosyalarını kayda alır ve yalnız onları siler (B3; pytest bunu toplamaz).

Klasör farkına göre silme yapılmaz: test sürerken başka bir süreçin aldığı yedek (ör. bir dev güvenlik
yedeği) korunur. Bir dosya yalnız şu yollarla kayda girer:
- backup.ps1 / restore.ps1 çıktısındaki "Yedek: backups\\<ad>" satırları (iç içe güvenlik yedekleri dahil),
- testin kendisinin oluşturduğu dosyalar (add / add_dump).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
YEDEK_RE = re.compile(r"Yedek: backups\\(\S+)")


class OwnedBackups:
    def __init__(self) -> None:
        self.paths: set[Path] = set()

    def add(self, path: Path) -> Path:
        path = Path(path)
        if path.resolve().parent != BACKUPS.resolve():
            raise ValueError(f"backups/ dışında dosya kayda alınmaz: {path}")
        self.paths.add(path)
        return path

    def add_dump(self, path: Path) -> Path:
        """Yedek ve SHA-256 dosyası birlikte."""
        self.add(path)
        self.add(Path(path).with_name(Path(path).name + ".sha256"))
        return Path(path)

    def from_output(self, text: str) -> list[Path]:
        return [self.add_dump(BACKUPS / name) for name in YEDEK_RE.findall(text or "")]

    def cleanup(self) -> None:
        for path in self.paths:
            path.unlink(missing_ok=True)
        self.paths.clear()
