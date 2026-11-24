"""Geri yükleme öncesi yedek denetimi (restore.ps1 her geri yüklemeden önce çağırır).

    .\\.venv\\Scripts\\python.exe scripts\\db\\check_dump.py --database medakis_dev --file backups\\x.dump

Hiçbir veritabanına yazmaz. Reddedilen yedek için bütün nedenleri yazar ve 1 ile çıkar.

Her hedef için:
- dosya var, adı .dump ile biter, yarım kalmış (.partial) değil, SHA-256 dosyası var ve eşleşiyor;
- arşiv pg_restore ile baştan sona okunabiliyor;
- arşivdeki veritabanı adı hedefle aynı (test yedeği dev'e yüklenemez).
medakis_test dışındaki hedefler için ayrıca (R1):
- arşivin Alembic sürümü hedefin beklenen sürümüyle aynı (eski şema/yetki durumu geri gelmez);
  beklenen sürüm: medakis_dev/medakis_demo için 0003 (tasarım kararı D11), diğerleri için head;
- arşiv medakis_seed'e hiçbir yazma, kilit veya sayaç yetkisi vermiyor;
- hedef veritabanı da beklenen sürümde (yalnız okunur).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

PERMISSIVE_TARGETS = frozenset({"medakis_test"})
# Revizyon 0004 (üretici 2.0.0 kolonları) bu veritabanlarına uygulanmaz; şemaları 0003'te sabittir.
PINNED_REVISIONS = {"medakis_dev": "0003_seed_write_window", "medakis_demo": "0003_seed_write_window"}
SEED_FORBIDDEN = {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "MAINTAIN", "TRIGGER", "REFERENCES", "USAGE", "ALL"}
GRANT_RE = re.compile(r"^GRANT (?P<privs>[A-Z ,]+) ON (?:TABLE|SEQUENCE) \S+ TO (?P<to>[a-z_]+);", re.M)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


def head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()


def expected_revision(database: str) -> str:
    """Hedefin olması gereken şema sürümü: sabitlenmiş (dev/demo) ya da depodaki head."""
    return PINNED_REVISIONS.get(database) or head_revision()


def file_problems(path: Path) -> list[str]:
    if path.name.endswith(".partial"):
        return ["yarım kalmış yedek (.partial) geri yüklenemez"]
    if not path.is_file():
        return [f"yedek dosyası bulunamadı: {path.name}"]
    if path.suffix != ".dump":
        return ["yedek dosyası .dump uzantılı olmalı"]
    sha_file = path.with_name(path.name + ".sha256")
    if not sha_file.is_file():
        return ["SHA-256 dosyası yok (yedek yarım kalmış veya eksik olabilir)"]
    expected = sha_file.read_text(encoding="utf-8").split()
    if len(expected) != 2 or expected[1] != path.name:
        return ["SHA-256 dosyası bu yedeğe ait değil"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected[0]:
        return ["SHA-256 eşleşmedi: yedek bozuk veya değiştirilmiş"]
    return []


def read_archive(path: Path) -> tuple[str | None, str | None]:
    """(arşiv metni, hata). Arşiv konteynerde okunur ve hemen silinir."""
    remote = f"/tmp/check-{uuid.uuid4().hex}.dump"
    if run("docker", "compose", "cp", str(path), f"postgres:{remote}").returncode != 0:
        return None, "yedek konteynere kopyalanamadı"
    try:
        listing = run("docker", "compose", "exec", "-T", "postgres", "pg_restore", "--list", remote)
        if listing.returncode != 0:
            return None, "arşiv okunamadı (pg_restore --list)"
        full = run("docker", "compose", "exec", "-T", "postgres", "pg_restore", "-f", "-", remote)
        if full.returncode != 0:
            return None, "arşiv baştan sona okunamadı (pg_restore)"
        return listing.stdout + "\n" + full.stdout, None
    finally:
        run("docker", "compose", "exec", "-T", "postgres", "rm", "-f", remote)


def archive_database(text: str) -> str | None:
    match = re.search(r"^;\s+dbname: (\S+)", text, re.M)
    return match.group(1) if match else None


def archive_revision(text: str) -> str | None:
    match = re.search(r"^COPY public\.alembic_version \(version_num\) FROM stdin;\n(.*?)^\\\.", text, re.M | re.S)
    if not match:
        return None
    rows = [r for r in match.group(1).splitlines() if r]
    return rows[0] if len(rows) == 1 else "|".join(rows)


def seed_write_grants(text: str) -> list[str]:
    found = []
    for m in GRANT_RE.finditer(text):
        if m.group("to") != "medakis_seed":
            continue
        privs = {p.strip() for p in m.group("privs").split(",")}
        if privs & SEED_FORBIDDEN:
            found.append(m.group(0))
    return found


def archive_problems(text: str, database: str, head: str) -> list[str]:
    problems = []
    source = archive_database(text)
    if source != database:
        problems.append(f"yedek başka bir veritabanına ait ({source or 'bilinmiyor'}); hedef {database}")
    if database not in PERMISSIVE_TARGETS:
        revision = archive_revision(text)
        if revision != head:
            problems.append(f"yedeğin şema sürümü {revision or 'yok'}, beklenen {head}: eski şema ve yetkiler geri gelir")
        grants = seed_write_grants(text)
        if grants:
            problems.append(f"yedek medakis_seed'e {len(grants)} yazma/kilit/sayaç yetkisi veriyor (geniş yetki)")
    return problems


def target_problems(database: str, head: str) -> list[str]:
    if database in PERMISSIVE_TARGETS:
        return []
    from sqlalchemy import text

    from medakis.repositories.database import migration_engine

    engine = migration_engine(database, allow_prompt=False)
    try:
        with engine.connect() as conn:
            exists = conn.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar_one()
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() if exists else None
    finally:
        engine.dispose()
    return [] if current == head else [f"hedef veritabanı {current or 'boş'} sürümünde; önce {head} olmalı"]


def check(database: str, path: Path) -> list[str]:
    problems = file_problems(path)
    if problems:
        return problems
    text, error = read_archive(path)
    if error:
        return [error]
    expected = expected_revision(database)
    problems = archive_problems(text, database, expected)
    return problems or target_problems(database, expected)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Geri yükleme öncesi yedek denetimi")
    parser.add_argument("--database", required=True, choices=["medakis_dev", "medakis_test", "medakis_demo"])
    parser.add_argument("--file", required=True)
    args = parser.parse_args(argv)
    path = Path(args.file)
    if not path.is_absolute():
        path = ROOT / path
    problems = check(args.database, path)
    for problem in problems:
        print(f"RED: {problem}")
    if problems:
        return 1
    print(f"Yedek denetimi geçti: {path.name} -> {args.database}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
