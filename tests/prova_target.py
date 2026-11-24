"""A1: rol üyeliği değiştiren testlerin hedefi (pytest bunu toplamaz).

Bu testler yalnız prova.ps1'in AÇIKÇA başlattığı çalıştırmada etkinleşir: prova.ps1, kendi doğruladığı
prova konteynerinin değişmez Docker kimliğini (MEDAKIS_PROVA_KONTEYNER_ID) ve o kümenin PostgreSQL
sistem kimliğini (MEDAKIS_PROVA_SISTEM_KIMLIGI) verir. Test ayrıca bağımsız olarak doğrular:

- docker compose'un seçtiği tek postgres konteyneri bu kimlikle aynı,
- konteyner adı, Compose proje etiketi ve bağlı tek volume prova kümesininki; gerçek volume bağlı değil,
- o konteynerdeki PostgreSQL'in system_identifier değeri beklenenle aynı.

Doğrulamadan sonra her komut Compose'a değil bu sabit konteyner kimliğine gider ve her SQL işlemi
(yönetici adımı dahil) aynı transaction içinde system_identifier'ı yeniden denetler; hedef değişirse
GRANT/REVOKE çalışmadan işlem geri alınır.
"""

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVA_PROJECT = "medakis-prova"
PROVA_CONTAINER = "medakis-prova-postgres"
PROVA_VOLUME = "medakis_prova_pgdata"
REAL_VOLUME = "medakis_pgdata"
ENV_ID = "MEDAKIS_PROVA_KONTEYNER_ID"
ENV_SYSID = "MEDAKIS_PROVA_SISTEM_KIMLIGI"
ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
SYSID_RE = re.compile(r"^[0-9]+$")


class TargetMismatch(RuntimeError):
    pass


def explicit_target() -> tuple[str, str] | None:
    """prova.ps1'in açıkça verdiği (konteyner kimliği, sistem kimliği); yoksa None."""
    container_id, sysid = os.environ.get(ENV_ID, ""), os.environ.get(ENV_SYSID, "")
    if not container_id and not sysid:
        return None
    if not ID_RE.match(container_id) or not SYSID_RE.match(sysid):
        raise TargetMismatch("Prova hedef kimlikleri geçersiz biçimde")
    return container_id, sysid


def _run(args, run, stdin=None):
    return run(args, cwd=ROOT, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=60)


def guard_sql(sysid: str) -> str:
    if not SYSID_RE.match(sysid):
        raise TargetMismatch("Geçersiz sistem kimliği")
    return (
        "DO $$ BEGIN IF (SELECT system_identifier::text FROM pg_control_system()) <> '" + sysid + "' THEN "
        "RAISE EXCEPTION 'hedef PostgreSQL ornegi degisti; islem yapilmadi'; END IF; END $$;"
    )


def verify_prova_target(expected_id: str, expected_sysid: str, run=subprocess.run) -> str:
    """Compose seçimi, konteyner, volume ve PostgreSQL örneği beklenen prova hedefiyse tam kimliği döndürür."""
    proc = _run(["docker", "compose", "ps", "-q", "postgres"], run)
    ids = proc.stdout.split() if proc.returncode == 0 else []
    if len(ids) != 1:
        raise TargetMismatch(f"Compose hedefinde tek bir çalışan postgres konteyneri yok (bulunan: {len(ids)})")
    inspect = _run(["docker", "inspect", ids[0]], run)
    if inspect.returncode != 0:
        raise TargetMismatch("Compose hedefi incelenemedi")
    info = json.loads(inspect.stdout)[0]
    full_id = info.get("Id", "")
    name = info.get("Name", "").lstrip("/")
    project = (info.get("Config", {}).get("Labels") or {}).get("com.docker.compose.project")
    volumes = [m.get("Name") for m in info.get("Mounts", []) if m.get("Type") == "volume"]
    problems = []
    if not full_id.startswith(expected_id) and not expected_id.startswith(full_id or "-"):
        problems.append("Compose'un seçtiği konteyner açıkça verilen prova kimliği değil")
    if not (info.get("State") or {}).get("Running"):
        problems.append("konteyner çalışmıyor")
    if name != PROVA_CONTAINER:
        problems.append(f"konteyner {name!r}")
    if project != PROVA_PROJECT:
        problems.append(f"Compose projesi {project!r}")
    if volumes != [PROVA_VOLUME]:
        problems.append(f"volume {volumes!r}")
    if REAL_VOLUME in volumes:
        problems.append("gerçek medakis_pgdata bağlı")
    if problems:
        raise TargetMismatch("Hedef prova kümesi değil: " + ", ".join(problems))
    sysid = _run(["docker", "exec", full_id, "psql", "-U", "postgres", "-d", "postgres", "-Atc",
                  "SELECT system_identifier FROM pg_control_system()"], run)  # fmt: skip
    if sysid.returncode != 0 or sysid.stdout.strip() != expected_sysid:
        raise TargetMismatch("PostgreSQL örneği beklenen prova kümesi değil (system_identifier uyuşmuyor)")
    return full_id


def psql_command(container_id: str, sysid: str, sql: str) -> list[str]:
    """Sabit konteyner kimliğine giden, aynı işlemde örnek kimliğini denetleyen psql komutu."""
    if not ID_RE.match(container_id):
        raise TargetMismatch("Geçersiz konteyner kimliği")
    return ["docker", "exec", "-i", container_id, "psql", "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1",
            "--single-transaction", "-At", "-c", guard_sql(sysid), "-c", sql]  # fmt: skip


def admin_command(container_id: str, sysid: str, *args: str) -> list[str]:
    """Yönetici adımı, Compose'u yeniden seçmeden yalnız bu konteyner kimliğine ve örneğe bağlı çalışır."""
    if not ID_RE.match(container_id) or not SYSID_RE.match(sysid):
        raise TargetMismatch("Geçersiz hedef kimliği")
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "db" / "seed_membership.ps1"),
            "-KonteynerKimligi", container_id, "-BeklenenSistemKimligi", sysid, *args]  # fmt: skip
