"""SHA-256 bütünlük manifestlerini doğrular ve günceller (yalnız standart kütüphane + git).

İki manifest vardır:

1. docs/paket_v2/SHA256SUMS_PAKET_v2.txt  (özgün paket kaydı, ASLA güncellenmez)
   Paket teslimindeki 20 dosyanın özgün hash'leri. MedAkis yeniden adlandırmasından
   sonra güncel belgelerin içeriği değiştiği için özgün baytlar çalışma klasöründe
   değil, paketin depoya ilk alındığı commit'te (ORIGINAL_IMPORT_COMMIT) doğrulanır.
   Bu commit'te özgün README docs/paket_v2/README_PAKET_v2.md konumundadır.
   Git geçmişi yoksa (ör. GitHub'dan ZIP indirme) bu kontrol "skipped" olur.

2. SHA256SUMS.txt  (aynı paket dosyalarının GÜNCEL hâli, güncel adlarıyla)
   Bir paket dosyası bilerek değiştirildiğinde `update` ile yeniden hesaplanır.

Kullanım (proje kökünde):
    .\\.venv\\Scripts\\python.exe scripts\\checksums.py check
    .\\.venv\\Scripts\\python.exe scripts\\checksums.py update
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "SHA256SUMS.txt"
ORIGINAL = ROOT / "docs" / "paket_v2" / "SHA256SUMS_PAKET_v2.txt"

# Paketin depoya ilk alındığı commit (FAZ 1.1). Özgün baytlar burada doğrulanır.
ORIGINAL_IMPORT_COMMIT = "5f89cb8a3a1045f312580bc07f35d87b5fed99cd"
# Özgün manifest yolu -> ORIGINAL_IMPORT_COMMIT içindeki konum
ORIGINAL_RELOCATED = {"README.md": "docs/paket_v2/README_PAKET_v2.md"}
# Özgün manifest yolu -> bugünkü dosya adı (MedAkis yeniden adlandırması, 5 Kasım 2026)
CURRENT_NAMES = {
    "FLOWCARE_AI_Proje_Sartnamesi.docx": "MedAkis_Proje_Sartnamesi.docx",
    "FLOWCARE_AI_Proje_Sartnamesi.md": "MedAkis_Proje_Sartnamesi.md",
    "flowcare_schema.sql": "medakis_schema.sql",
    # Paylaşılan kurallar araçtan bağımsız dosyaya taşındı; CLAUDE.md yerel ve git dışında.
    "CLAUDE.md": "docs/DEVELOPMENT_RULES.md",
    "BASLANGIC_TALIMATLARI.md": "docs/paket_v2/BASLANGIC_TALIMATLARI.md",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse(manifest: Path) -> list[tuple[str, str]]:
    entries = []
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line:
            digest, rel = line.split(maxsplit=1)
            entries.append((digest.lower(), rel.strip().lstrip("*")))
    return entries


def current_paths() -> list[str]:
    return [CURRENT_NAMES.get(rel, rel) for _, rel in parse(ORIGINAL)]


def _git_show(commit: str, rel: str) -> bytes | None:
    git = shutil.which("git")
    if not git:
        return None
    proc = subprocess.run(
        [git, "-C", str(ROOT), "show", f"{commit}:{rel}"], capture_output=True, timeout=30
    )
    return proc.stdout if proc.returncode == 0 else None


def verify_original() -> tuple[str, list[str]]:
    """Özgün manifesti ilk içe aktarma commit'indeki baytlara karşı doğrular."""
    if _git_show(ORIGINAL_IMPORT_COMMIT, "SHA256SUMS.txt") is None:
        return "skipped (git geçmişi yok)", []
    errors = []
    for digest, rel in parse(ORIGINAL):
        data = _git_show(ORIGINAL_IMPORT_COMMIT, ORIGINAL_RELOCATED.get(rel, rel))
        if data is None:
            errors.append(f"MISSING: {rel}")
        elif sha256(data) != digest:
            errors.append(f"MISMATCH: {rel}")
    return "verified", errors


def verify_current() -> list[str]:
    errors = []
    for digest, rel in parse(CURRENT):
        target = ROOT / rel
        if not target.is_file():
            errors.append(f"MISSING: {rel}")
        elif sha256(target.read_bytes()) != digest:
            errors.append(f"MISMATCH: {rel}")
    return errors


def update() -> int:
    lines = [f"{sha256((ROOT / rel).read_bytes())}  {rel}" for rel in current_paths()]
    CURRENT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(lines)


def check() -> dict:
    status, original_errors = verify_original()
    return {
        "original_manifest": "docs/paket_v2/SHA256SUMS_PAKET_v2.txt",
        "original_source": f"git {ORIGINAL_IMPORT_COMMIT[:7]}",
        "original_status": status,
        "original_files": len(parse(ORIGINAL)),
        "original_errors": original_errors,
        "current_manifest": CURRENT.name,
        "current_files": len(parse(CURRENT)),
        "current_errors": verify_current(),
        "same_file_set": [rel for _, rel in parse(CURRENT)] == current_paths(),
    }


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "update":
        print(json.dumps({"updated": CURRENT.name, "files": update()}))
        return 0
    if cmd == "check":
        result = check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        ok = not result["original_errors"] and not result["current_errors"] and result["same_file_set"]
        return 0 if ok else 1
    print("Kullanım: checksums.py [check|update]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
