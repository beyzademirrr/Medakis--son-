"""Depo sunumu: güncel dosyalarda araç markalaması, eski ad veya yerel yol bulunmaz.

Tarihsel kayıtlar bilerek hariç tutulur ve içerikleri korunur: paket teslim arşivi
(docs/paket_v2), FAZ 1.1 raporu, yeniden adlandırma raporu ve bütünlük aracının eski dosya
adı eşlemesi. Git geçmişi bu testin kapsamında değildir.
"""

import re
import subprocess

PATTERN = re.compile(r"chatgpt|claude|anthropic|flowcare|referenced-chatgpt|co-authored-by", re.I)
HISTORICAL = (
    "docs/paket_v2/",
    "docs/raporlar/FAZ_1_1_RAPORU.md",
    "docs/raporlar/MEDAKIS_YENIDEN_ADLANDIRMA_RAPORU.md",
    "scripts/checksums.py",
    "tests/test_naming.py",
    "tests/test_presentation.py",
)
TEXT_SUFFIXES = (".md", ".py", ".toml", ".ini", ".json", ".yaml", ".yml", ".sql", ".ps1", ".txt", ".mako", ".example")


def tracked_files(root):
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True)
    return [line for line in out.stdout.splitlines() if line]


def test_no_tool_branding_in_current_files(project_root):
    hits = []
    for rel in tracked_files(project_root):
        if rel.startswith(HISTORICAL) or not rel.endswith(TEXT_SUFFIXES + (".gitignore",)):
            continue
        text = (project_root / rel).read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            # .gitignore yerel araç dosyalarını dışarıda tutmak için adlarını içermek zorundadır.
            if rel == ".gitignore" and line.strip() in {"CLAUDE.md", ".claude/"}:
                continue
            if PATTERN.search(line):
                hits.append(f"{rel}:{number}")
    assert not hits, hits


def test_local_assistant_files_are_not_tracked(project_root):
    tracked = set(tracked_files(project_root))
    assert "CLAUDE.md" not in tracked
    assert not [p for p in tracked if p.startswith(".claude/")]
    assert "docs/DEVELOPMENT_RULES.md" in tracked
