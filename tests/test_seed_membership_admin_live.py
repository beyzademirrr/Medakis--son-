"""B5: rol üyeliği yönetici adımı — YALNIZ açıkça başlatılmış izole prova çalıştırmasında (A1).

Marker: prova_rol. Normal pytest ve `pytest -m postgres` bu testleri hiç seçmez. `-m prova_rol` elle
verilse bile prova.ps1'in açık hedef kimlikleri (tests/prova_target.py) yoksa atlanır; varsa ilk
komuttan ÖNCE Compose seçimi, konteyner, volume ve PostgreSQL örneği doğrulanır. Uyuşmazlıkta test
hiçbir GRANT, REVOKE veya yönetici komutu çalıştırmadan başarısız olur.

Doğrulamadan sonra bütün SQL ve yönetici adımı Compose'a değil, sabit Docker konteyner kimliğine gider
ve her işlem aynı transaction içinde PostgreSQL system_identifier'ını yeniden denetler.
"""

import subprocess
from pathlib import Path

import pytest
from prova_target import TargetMismatch, admin_command, explicit_target, psql_command, verify_prova_target

pytestmark = pytest.mark.prova_rol
ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [("medakis_seed", "medakis_owner", False, False, True)]  # (rol, üye, admin, inherit, set)
VERIFIED: dict[str, str] = {}


@pytest.fixture(autouse=True)
def verified_prova():
    try:
        target = explicit_target()
    except TargetMismatch as exc:
        pytest.fail(f"Prova hedefi doğrulanamadı; hiçbir yetki komutu çalıştırılmadı: {exc}", pytrace=False)
    if target is None:
        pytest.skip("Rol üyeliği testleri yalnız prova.ps1 ile açıkça başlatılan izole çalıştırmada etkinleşir")
    container_id, sysid = target
    try:
        VERIFIED.update(id=verify_prova_target(container_id, sysid), sysid=sysid)
    except TargetMismatch as exc:
        VERIFIED.clear()
        pytest.fail(f"Prova hedefi doğrulanamadı; hiçbir yetki komutu çalıştırılmadı: {exc}", pytrace=False)


def psql(sql, sysid=None):
    proc = subprocess.run(psql_command(VERIFIED["id"], sysid or VERIFIED["sysid"], sql), cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)  # fmt: skip
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout.strip()


def memberships():
    out = psql(
        "SELECT r.rolname, u.rolname, m.admin_option, m.inherit_option, m.set_option FROM pg_auth_members m "
        "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
        "WHERE r.rolname LIKE 'medakis\\_%' OR u.rolname LIKE 'medakis\\_%' ORDER BY 1, 2"
    )
    rows = []
    for line in filter(None, out.splitlines()):
        if "|" not in line:
            continue  # koruma ifadesinin "DO" çıktısı
        role, member, admin, inherit, set_ = line.split("|")
        rows.append((role, member, admin == "t", inherit == "t", set_ == "t"))
    return rows


def revoke_all():
    for role, member, *_ in memberships():
        psql(f"REVOKE {role} FROM {member}")


def admin_step(*args, sysid=None):
    return subprocess.run(admin_command(VERIFIED["id"], sysid or VERIFIED["sysid"], *args), cwd=ROOT,
                          capture_output=True, text=True, timeout=120)  # fmt: skip


@pytest.fixture(autouse=True)
def restore_expected_membership(verified_prova):
    yield
    revoke_all()
    proc = admin_step("-Onayla")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert memberships() == EXPECTED


def test_requires_explicit_confirmation():
    revoke_all()
    proc = admin_step()
    assert proc.returncode == 1 and "-Onayla" in proc.stdout
    assert memberships() == []


def test_clean_cluster_gets_exactly_the_set_only_membership():
    revoke_all()
    proc = admin_step("-Onayla")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert memberships() == EXPECTED


def test_second_run_changes_nothing():
    revoke_all()
    assert admin_step("-Onayla").returncode == 0
    before = memberships()
    proc = admin_step("-Onayla")
    assert proc.returncode == 0 and "already present" in (proc.stdout + proc.stderr)
    assert memberships() == before == EXPECTED


def test_wrong_existing_membership_is_refused_and_left_unchanged():
    revoke_all()
    psql("GRANT medakis_seed TO medakis_owner WITH INHERIT TRUE, SET TRUE, ADMIN FALSE")  # beklenmeyen seçenek
    before = memberships()
    proc = admin_step("-Onayla")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "unexpected medakis role membership" in (proc.stdout + proc.stderr)
    assert memberships() == before


def test_unrelated_unexpected_membership_blocks_grant_entirely():
    revoke_all()
    psql("GRANT medakis_app TO medakis_owner WITH INHERIT FALSE, SET FALSE, ADMIN FALSE")
    before = memberships()
    proc = admin_step("-Onayla")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert memberships() == before
    assert not [r for r in memberships() if r[0] == "medakis_seed"]


# ---- A1: doğrulamadan sonra hedef PostgreSQL örneği farklıysa hiçbir yetki komutu uygulanmaz -------


def test_admin_step_with_other_instance_identity_changes_nothing():
    revoke_all()
    wrong = str(int(VERIFIED["sysid"]) + 1)  # sabit konteyner, ama beklenen örnek kimliği farklı
    proc = admin_step("-Onayla", sysid=wrong)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "target PostgreSQL instance mismatch" in (proc.stdout + proc.stderr)
    assert memberships() == []  # GRANT işlemle birlikte geri alındı


def test_test_sql_with_other_instance_identity_changes_nothing():
    before = memberships()
    wrong = str(int(VERIFIED["sysid"]) + 1)
    with pytest.raises(RuntimeError, match="hedef PostgreSQL ornegi degisti"):
        psql("REVOKE medakis_seed FROM medakis_owner", sysid=wrong)
    assert memberships() == before
