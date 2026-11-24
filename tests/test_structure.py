"""İskelet ve mimari sınır testleri.

Sınırlar yalnız prompt veya belgeyle değil, otomatik testle korunur.
"""

import ast
import importlib
from pathlib import Path

import pytest

from medakis.services.system_info import MODULES

SRC = Path(__file__).resolve().parents[1] / "src" / "medakis"


@pytest.mark.parametrize("name", MODULES)
def test_module_importable_and_documented(name):
    module = importlib.import_module(name)
    assert (module.__doc__ or "").strip(), f"{name} sorumluluk açıklaması içermeli"


def _imports(package_dir: Path) -> set[str]:
    found: set[str] = set()
    for py in package_dir.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
    return found


def _hits(imports: set[str], forbidden: tuple[str, ...]) -> set[str]:
    return {i for i in imports if any(i == f or i.startswith(f + ".") for f in forbidden)}


@pytest.mark.parametrize("package", ["optimization", "approvals", "repositories", "domain"])
def test_decision_modules_do_not_depend_on_rag(package):
    assert not _hits(_imports(SRC / package), ("medakis.rag",))


def test_rag_cannot_reach_decision_or_write_layers():
    forbidden = (
        "medakis.optimization",
        "medakis.approvals",
        "medakis.repositories",
        "medakis.services",
        "sqlite3",
        "sqlalchemy",
        "psycopg",
        "keyring",
        "subprocess",
    )
    assert not _hits(_imports(SRC / "rag"), forbidden)


def test_synthetic_v2_is_not_an_optimizer_and_has_no_db_access():
    # Üretici 2.0 kadro kararı vermez (Aşama 5 sınırı) ve veritabanına bağlanmaz.
    forbidden = (
        "medakis.optimization", "medakis.forecasting", "medakis.approvals", "medakis.services",
        "medakis.rag", "medakis.repositories.database", "sqlalchemy", "psycopg", "ortools", "sklearn",
    )  # fmt: skip
    assert not _hits(_imports(SRC / "synthetic" / "v2"), forbidden)


def test_optimization_does_not_reach_synthetic_generator():
    assert not _hits(_imports(SRC / "optimization"), ("medakis.synthetic",))


def test_ui_has_no_sql_training_or_solver():
    forbidden = ("sqlite3", "sqlalchemy", "psycopg", "keyring", "sklearn", "ortools", "chromadb", "medakis.repositories")
    assert not _hits(_imports(SRC / "ui"), forbidden)


def test_runtime_folders_exist(project_root):
    for rel in ("data", "artifacts", "tests", "config", "corpus", "migrations"):
        assert (project_root / rel).is_dir(), rel


def test_data_and_artifacts_are_gitignored(project_root):
    text = (project_root / ".gitignore").read_text(encoding="utf-8")
    for line in (".venv/", "data/*", "artifacts/*", "*.db", "*.sqlite3",
                 "*.pkl", "*.joblib", "*.gguf", "chroma/", ".env", "*.pem"):
        assert line in text.splitlines()


def test_requirements_fully_pinned(project_root):
    lines = [
        ln.strip()
        for ln in (project_root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    assert lines, "requirements.txt boş olmamalı"
    unpinned = [ln for ln in lines if "==" not in ln]
    assert not unpinned, unpinned
    names = {ln.split("==")[0].lower() for ln in lines}
    for required in ("streamlit", "pandas", "numpy", "scikit-learn", "ortools",
                     "chromadb", "httpx", "tzdata", "pytest"):
        assert required in names, required


def test_owner_role_used_only_by_migration_path():
    # Uygulama kodu owner motorunu çağıramaz; migration_engine yalnız Alembic içindir.
    root = SRC.parents[1]
    users = [
        p.relative_to(root).as_posix()
        for p in [*SRC.rglob("*.py"), *(root / "migrations_pg").glob("*.py")]
        if "migration_engine(" in p.read_text(encoding="utf-8")
    ]
    assert sorted(users) == ["migrations_pg/env.py", "src/medakis/repositories/database.py"]


def test_spec_docx_and_markdown_have_same_text(project_root):
    import html
    import re
    import zipfile

    with zipfile.ZipFile(project_root / "MedAkis_Proje_Sartnamesi.docx") as docx:
        assert docx.testzip() is None
        xml = docx.read("word/document.xml").decode("utf-8")
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
    docx_text = html.unescape("".join(re.sub(r"<[^>]+>", "", p) for p in paragraphs))
    md_text = (project_root / "MedAkis_Proje_Sartnamesi.md").read_text(encoding="utf-8")

    def norm(value):
        return re.sub(r"\s+", "", re.sub(r"[#\-*]", "", value))

    assert norm(docx_text) == norm(md_text)


def test_current_documents_name_postgresql_as_database(project_root):
    rules = (project_root / "docs" / "DEVELOPMENT_RULES.md").read_text(encoding="utf-8")
    assert "## PostgreSQL sözleşmesi" in rules and "## SQLite sözleşmesi" not in rules
    assert "Streamlit + SQLite" not in rules
    spec = (project_root / "MedAkis_Proje_Sartnamesi.md").read_text(encoding="utf-8")
    assert "PostgreSQL 18" in spec and "Streamlit, SQLite" not in spec
    assert (project_root / "medakis_schema.sql").is_file(), "SQLite referansı silinmemeli"
    assert (project_root / "migrations" / "001_to_002.sql").is_file(), "Geçmiş migration silinmemeli"
