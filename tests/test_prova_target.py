"""A1: rol üyeliği testlerinin ve yönetici adımının hedef bağlaması için olumsuz regresyon testleri.

Hiçbir test rol üyeliğini değiştirmez. Gerçek sunucuya yalnız salt okunur docker ps/inspect ve
pg_auth_members okuması yapılır. Yönetici betiği testleri sahte bir docker komutuyla çalışır.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from prova_target import (
    ENV_ID,
    ENV_SYSID,
    PROVA_CONTAINER,
    TargetMismatch,
    admin_command,
    explicit_target,
    guard_sql,
    psql_command,
    verify_prova_target,
)

ROOT = Path(__file__).resolve().parents[1]
PROVA_ID = "a" * 64
REAL_ID = "b" * 64


def inspect_json(full_id, name, project, volumes, running=True):
    return json.dumps([{
        "Id": full_id, "Name": f"/{name}", "State": {"Running": running},
        "Config": {"Labels": {"com.docker.compose.project": project}},
        "Mounts": [{"Type": "volume", "Name": v} for v in volumes],
    }])  # fmt: skip


class FakeRun:
    """compose ps / inspect / sysid okuması yanıtlar; başka her komutu reddeder ve kaydeder."""

    def __init__(self, ids, inspect, sysid="111"):
        self.ids, self.inspect, self.sysid, self.calls = ids, inspect, sysid, []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if args[:4] == ["docker", "compose", "ps", "-q"]:
            return SimpleNamespace(returncode=0, stdout="\n".join(self.ids), stderr="")
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=self.inspect, stderr="")
        if args[:2] == ["docker", "exec"] and "SELECT system_identifier FROM pg_control_system()" in args:
            return SimpleNamespace(returncode=0, stdout=self.sysid + "\n", stderr="")
        raise AssertionError(f"beklenmeyen komut: {args}")


PROVA_INSPECT = inspect_json(PROVA_ID, PROVA_CONTAINER, "medakis-prova", ["medakis_prova_pgdata"])


# ---- Doğrulayıcı -----------------------------------------------------------------------------


def test_compose_selecting_real_cluster_is_refused_before_any_sql():
    fake = FakeRun([REAL_ID[:12]], inspect_json(REAL_ID, "medakis-postgres", "medakis", ["medakis_pgdata"]))
    with pytest.raises(TargetMismatch) as info:
        verify_prova_target(PROVA_ID, "111", run=fake)
    assert "açıkça verilen prova kimliği değil" in str(info.value) and "gerçek medakis_pgdata bağlı" in str(info.value)
    assert [c[:2] for c in fake.calls] == [["docker", "compose"], ["docker", "inspect"]]  # psql hiç çağrılmadı


@pytest.mark.parametrize(
    ("ids", "inspect", "sysid", "expected"),
    [
        ([], "[]", "111", "bulunan: 0"),
        (["x", "y"], "[]", "111", "bulunan: 2"),
        ([REAL_ID[:12]], inspect_json(REAL_ID, PROVA_CONTAINER, "medakis-prova", ["medakis_prova_pgdata"]), "111", "açıkça verilen"),
        ([PROVA_ID[:12]], inspect_json(PROVA_ID, PROVA_CONTAINER, "medakis", ["medakis_prova_pgdata"]), "111", "Compose projesi"),
        ([PROVA_ID[:12]], inspect_json(PROVA_ID, PROVA_CONTAINER, "medakis-prova", ["medakis_pgdata"]), "111", "gerçek medakis_pgdata"),
        ([PROVA_ID[:12]], inspect_json(PROVA_ID, PROVA_CONTAINER, "medakis-prova", ["medakis_prova_pgdata", "x"]), "111", "volume"),
        ([PROVA_ID[:12]], inspect_json(PROVA_ID, "baska", "medakis-prova", ["medakis_prova_pgdata"]), "111", "konteyner"),
        ([PROVA_ID[:12]], inspect_json(PROVA_ID, PROVA_CONTAINER, "medakis-prova", ["medakis_prova_pgdata"], False), "111", "çalışmıyor"),
        ([PROVA_ID[:12]], PROVA_INSPECT, "999", "system_identifier uyuşmuyor"),
    ],
)  # fmt: skip
def test_any_target_mismatch_is_refused(ids, inspect, sysid, expected):
    with pytest.raises(TargetMismatch) as info:
        verify_prova_target(PROVA_ID, "111", run=FakeRun(ids, inspect, sysid))
    assert expected in str(info.value)


def test_exact_prova_target_returns_immutable_full_id():
    assert verify_prova_target(PROVA_ID, "111", run=FakeRun([PROVA_ID[:12]], PROVA_INSPECT)) == PROVA_ID


def test_explicit_target_absent_means_disabled_and_malformed_is_refused(monkeypatch):
    monkeypatch.delenv(ENV_ID, raising=False)
    monkeypatch.delenv(ENV_SYSID, raising=False)
    assert explicit_target() is None
    monkeypatch.setenv(ENV_ID, "medakis-postgres")
    monkeypatch.setenv(ENV_SYSID, "1")
    with pytest.raises(TargetMismatch):
        explicit_target()


def test_every_test_sql_is_bound_to_container_id_and_instance_in_one_transaction():
    cmd = psql_command(PROVA_ID, "111", "REVOKE medakis_seed FROM medakis_owner")
    assert cmd[:4] == ["docker", "exec", "-i", PROVA_ID] and "compose" not in cmd
    assert "--single-transaction" in cmd
    assert cmd.index(guard_sql("111")) < cmd.index("REVOKE medakis_seed FROM medakis_owner")
    with pytest.raises(TargetMismatch):
        psql_command("medakis-postgres", "111", "SELECT 1")


def test_admin_command_is_bound_to_container_id_and_instance():
    cmd = admin_command(PROVA_ID, "111", "-Onayla")
    assert cmd[cmd.index("-KonteynerKimligi") + 1] == PROVA_ID and cmd[cmd.index("-BeklenenSistemKimligi") + 1] == "111"


# ---- Yönetici betiği, sahte docker ile: Compose'u yeniden seçemez ----------------------------------


def run_admin_with_fake_docker(tmp_path, *args, running=True):
    # Batch dosyası ASCII olmalı: proje yolu ASCII dışı karakter içerdiğinden betik ve yorumlayıcı
    # ASCII yollardan çağrılır (geçici klasöre kopya, temel Python; sahte betik yalnız stdlib kullanır).
    fake = tmp_path / "fake_docker.py"
    fake.write_bytes((ROOT / "tests" / "fake_docker.py").read_bytes())
    python = getattr(sys, "_base_executable", sys.executable)
    if not (str(python).isascii() and str(fake).isascii()):
        pytest.skip("ASCII yol bulunamadı; sahte docker kurulamadı")
    shim = tmp_path / "docker.cmd"
    shim.write_text(f'@"{python}" "{fake}" %*\r\n', encoding="ascii")
    log = tmp_path / "docker.log"
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}", FAKE_DOCKER_LOG=str(log),
               FAKE_DOCKER_RUNNING="true" if running else "false")  # fmt: skip
    proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "scripts" / "db" / "seed_membership.ps1"), *args],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)  # fmt: skip
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return proc, calls


def test_bound_admin_step_never_consults_compose_and_targets_only_the_given_container(tmp_path):
    # Bağımsız inceleme senaryosu: doğrulamadan sonra Compose gerçek kümeyi seçiyor (sahte docker öyle yanıtlıyor).
    proc, calls = run_admin_with_fake_docker(tmp_path, "-KonteynerKimligi", PROVA_ID, "-BeklenenSistemKimligi", "111", "-Onayla")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [c for c in calls if c["args"][:1] == ["compose"]], calls
    execs = [c for c in calls if c["args"][:2] == ["exec", "-i"]]
    assert len(execs) == 1 and execs[0]["args"][2] == PROVA_ID
    assert "--single-transaction" in execs[0]["args"]
    assert execs[0]["stdin"].lstrip("﻿").startswith("SET LOCAL medakis.beklenen_sistem = '111';")  # psql BOM'u yok sayar
    assert "target PostgreSQL instance mismatch" in execs[0]["stdin"]  # örnek denetimi aynı işlemde


@pytest.mark.parametrize(
    ("args", "running", "message"),
    [
        (["-KonteynerKimligi", PROVA_ID, "-BeklenenSistemKimligi", "111", "-Onayla"], False, "calismiyor veya yok"),
        (["-KonteynerKimligi", PROVA_ID, "-Onayla"], True, "beklenen sistem kimligi zorunlu"),
        (["-KonteynerKimligi", "medakis-postgres", "-BeklenenSistemKimligi", "111", "-Onayla"], True, "Gecersiz konteyner"),
        (["-KonteynerKimligi", PROVA_ID, "-BeklenenSistemKimligi", "111"], True, "-Onayla"),
    ],
)  # fmt: skip
def test_bound_admin_step_refuses_without_sending_sql(tmp_path, args, running, message):
    proc, calls = run_admin_with_fake_docker(tmp_path, *args, running=running)
    assert proc.returncode == 1 and message in proc.stdout, proc.stdout + proc.stderr
    assert not [c for c in calls if c["args"][:1] == ["compose"] or c["args"][:2] == ["exec", "-i"]], calls


# ---- Normal çalıştırma: rol üyeliği testleri seçilmez, gerçek üyelik değişmez -----------------------


def real_memberships():
    proc = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "postgres", "-Atc",
                           "SELECT string_agg(r.rolname || '>' || u.rolname, ',' ORDER BY 1) FROM pg_auth_members m "
                           "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
                           "WHERE r.rolname LIKE 'medakis%' OR u.rolname LIKE 'medakis%'"],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def pytest_subprocess(*args, env=None):
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)  # fmt: skip


@pytest.mark.postgres
def test_normal_runs_never_select_or_activate_membership_tests():
    if os.environ.get(ENV_ID):
        pytest.skip("Açık prova çalıştırmasında anlamsız")
    target = "tests/test_seed_membership_admin_live.py"
    before = real_memberships()
    for selection in ([], ["-m", "postgres"], ["-m", "postgres_lifecycle"]):
        proc = pytest_subprocess("--collect-only", "-q", *selection, target)
        assert "test_seed_membership_admin_live.py::" not in proc.stdout, (selection, proc.stdout)
    env = {k: v for k, v in os.environ.items() if k not in (ENV_ID, ENV_SYSID)}
    proc = pytest_subprocess("-q", "-rs", "-m", "prova_rol", target, env=env)  # elle seçilse bile
    assert proc.returncode == 0 and " skipped" in proc.stdout.splitlines()[-1] and "passed" not in proc.stdout.splitlines()[-1], proc.stdout
    assert real_memberships() == before


@pytest.mark.postgres
def test_explicit_ids_that_do_not_match_compose_target_stop_before_any_command():
    # Uçtan uca: açık kimlikler var ama Compose gerçek medakis projesini seçiyor (bağımsız incelemenin ilk senaryosu).
    # Kimlik mevcut olmayan bir konteynere ait; koruma bozuk olsa bile komutlar oraya bağlı olurdu.
    if os.environ.get("COMPOSE_PROJECT_NAME") == "medakis-prova":
        pytest.skip("Gerçek Compose hedefiyle sınanır")
    before = real_memberships()
    env = dict(os.environ, **{ENV_ID: "0123456789ab" * 4, ENV_SYSID: "1"})
    proc = pytest_subprocess("-q", "-m", "prova_rol", "tests/test_seed_membership_admin_live.py", env=env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert out.count("Prova hedefi doğrulanamadı; hiçbir yetki komutu çalıştırılmadı") >= 7, out
    assert "passed" not in out.splitlines()[-1], out
    assert real_memberships() == before
