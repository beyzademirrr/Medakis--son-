"""Paketle gelen ağsız doğrulayıcıları test paketine bağlar.

verify_schema.py bellek içi SQLite kullanır; dosyalara dokunmaz.
Bu testler uygulama katmanı kurallarını test etmiş sayılmaz (Aşama 2+).
"""

import json
import subprocess
import sys


def _run(project_root, script):
    proc = subprocess.run(
        [sys.executable, "-I", str(project_root / script)],
        cwd=project_root, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_schema_verifier(project_root):
    out = _run(project_root, "verify_schema.py")
    assert out["table_count"] == 34
    assert out["schema_version"] == 2
    assert out["clean_install"] == "passed" and out["migration"] == "passed"
    assert out["integrity_check"] == "ok" and out["foreign_key_check"] == []


def test_metric_verifier(project_root):
    out = _run(project_root, "evaluation/verify_metrics.py")
    assert out["status"] == "passed"
    assert out["model_quality_measured"] is False
