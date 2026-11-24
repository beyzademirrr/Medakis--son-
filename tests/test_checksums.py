"""Paket bütünlüğü: özgün manifest korunur, güncel manifest dosyalarla eşleşir."""

import json
import subprocess
import sys


def test_checksum_manifests(project_root):
    proc = subprocess.run(
        [sys.executable, "-I", str(project_root / "scripts" / "checksums.py"), "check"],
        cwd=project_root, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    result = json.loads(proc.stdout)
    assert result["original_files"] == 20
    assert result["original_status"] == "verified", "Git geçmişi bu depoda bulunmalı"
    assert result["original_errors"] == [], "Özgün paket dosyaları değişmiş olmamalı"
    assert result["same_file_set"]
    assert result["current_errors"] == [], (
        "SHA256SUMS.txt güncel değil. Paket dosyası bilerek değiştirildiyse: "
        "python scripts/checksums.py update"
    )
    assert proc.returncode == 0
