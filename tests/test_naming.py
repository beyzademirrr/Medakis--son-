"""Ad tutarlılığı: eski FLOWCARE adı kodda, testlerde ve yapılandırmada geçmemeli.

Eski ad yalnız tarihsel kayıtlarda (docs/paket_v2, iki tarihsel rapor, bütünlük aracı
eşlemeleri) bilerek korunur.
"""

from pathlib import Path

OLD = "flowcare"


def _files(root: Path):
    for rel in ("src", "tests"):
        yield from (root / rel).rglob("*.py")
    for rel in ("app.py", "pyproject.toml", ".vscode/launch.json", ".vscode/settings.json",
                ".streamlit/config.toml", "config/rag_profiles.json"):
        yield root / rel


def test_old_name_absent_from_code_and_config(project_root):
    this = Path(__file__).resolve()
    hits = [
        str(p.relative_to(project_root))
        for p in _files(project_root)
        if p.resolve() not in (this, this.with_name("test_presentation.py")) and OLD in p.read_text(encoding="utf-8").lower()
    ]
    assert not hits, hits


def test_package_and_files_use_new_name(project_root):
    assert (project_root / "src" / "medakis" / "__init__.py").is_file()
    assert not (project_root / "src" / "flowcare").exists()
    for name in ("medakis_schema.sql", "MedAkis_Proje_Sartnamesi.md", "MedAkis_Proje_Sartnamesi.docx"):
        assert (project_root / name).is_file(), name
