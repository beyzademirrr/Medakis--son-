"""Geri yükleme öncesi yedek denetimi (scripts/db/check_dump.py): veritabanısız kurallar."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_dump", ROOT / "scripts" / "db" / "check_dump.py")
check_dump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_dump)

HEAD = "0003_seed_write_window"  # medakis_dev/medakis_demo için beklenen (sabitlenmiş) sürüm
REPO_HEAD = "0004_triage_cancel_columns"


def archive(dbname="medakis_dev", revision=HEAD, grants=("GRANT SELECT ON TABLE public.facilities TO medakis_seed;",)):
    lines = [";", f";     dbname: {dbname}", ";"]
    if revision is not None:
        lines += ["COPY public.alembic_version (version_num) FROM stdin;", revision, "\\.", ""]
    return "\n".join(lines + list(grants)) + "\n"


def test_head_revision_matches_migrations():
    assert check_dump.head_revision() == REPO_HEAD
    # Tasarım D11: dev ve demo 0003'te kalır; test head'i izler.
    assert check_dump.expected_revision("medakis_dev") == check_dump.expected_revision("medakis_demo") == HEAD
    assert check_dump.expected_revision("medakis_test") == REPO_HEAD


def test_clean_current_dev_archive_passes():
    assert check_dump.archive_problems(archive(), "medakis_dev", HEAD) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (archive(revision="0002_seed_role_grants"), "şema sürümü 0002_seed_role_grants"),
        (archive(revision=None), "şema sürümü yok"),
        (archive(grants=("GRANT SELECT,INSERT,DELETE ON TABLE public.facilities TO medakis_seed;",)), "yazma/kilit/sayaç yetkisi"),
        (archive(grants=("GRANT SELECT,MAINTAIN ON TABLE public.model_runs TO medakis_seed;",)), "yazma/kilit/sayaç yetkisi"),
        (archive(grants=("GRANT SELECT,USAGE ON SEQUENCE public.facilities_id_seq TO medakis_seed;",)), "yazma/kilit/sayaç yetkisi"),
        (archive(grants=("GRANT ALL ON TABLE public.facilities TO medakis_seed;",)), "yazma/kilit/sayaç yetkisi"),
        (archive(dbname="medakis_test"), "başka bir veritabanına ait (medakis_test)"),
    ],
)  # fmt: skip
def test_dev_and_demo_refuse_old_or_wide_archives(text, expected):
    for target in ("medakis_dev",):
        problems = check_dump.archive_problems(text, target, HEAD)
        assert any(expected in p for p in problems), problems
    if "dbname" not in expected:
        demo_text = text.replace("dbname: medakis_dev", "dbname: medakis_demo")
        assert check_dump.archive_problems(demo_text, "medakis_demo", HEAD)


def test_app_grants_do_not_count_as_seed_grants():
    text = archive(grants=("GRANT SELECT,INSERT,UPDATE ON TABLE public.facilities TO medakis_app;",))
    assert check_dump.archive_problems(text, "medakis_dev", HEAD) == []


def test_test_target_only_requires_matching_database():
    text = archive(dbname="medakis_test", revision="0002_seed_role_grants",
                   grants=("GRANT SELECT,INSERT,DELETE ON TABLE public.facilities TO medakis_seed;",))  # fmt: skip
    assert check_dump.archive_problems(text, "medakis_test", HEAD) == []
    assert check_dump.archive_problems(archive(dbname="medakis_dev"), "medakis_test", HEAD)


def _write(tmp_path, name, data=b"PGDMP-test", sha_name=None, sha=None):
    path = tmp_path / name
    path.write_bytes(data)
    digest = sha or hashlib.sha256(data).hexdigest()
    (tmp_path / f"{name}.sha256").write_text(f"{digest}  {sha_name or name}\n", encoding="utf-8")
    return path


def test_file_rules(tmp_path):
    assert check_dump.file_problems(_write(tmp_path, "a.dump")) == []
    assert "yarım kalmış" in check_dump.file_problems(tmp_path / "b.dump.partial")[0]
    assert "bulunamadı" in check_dump.file_problems(tmp_path / "yok.dump")[0]
    no_sha = tmp_path / "c.dump"
    no_sha.write_bytes(b"x")
    assert "SHA-256 dosyası yok" in check_dump.file_problems(no_sha)[0]
    assert "eşleşmedi" in check_dump.file_problems(_write(tmp_path, "d.dump", sha="0" * 64))[0]
    assert "bu yedeğe ait değil" in check_dump.file_problems(_write(tmp_path, "e.dump", sha_name="baska.dump"))[0]
    assert ".dump uzantılı" in check_dump.file_problems(_write(tmp_path, "f.sql"))[0]
