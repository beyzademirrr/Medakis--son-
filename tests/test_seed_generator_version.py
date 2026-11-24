"""Seed servisinde üretici sürümü seçimi ve 2.0.0 hedef korumaları (G2, veritabanına bağlanmadan).

Bağlantı gerektiren adımlar sahte motor/bağlantı ve sahte repository fonksiyonlarıyla sınanır; hiçbir
PostgreSQL sunucusuna bağlanılmaz.
"""

import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.repositories import audit_repo, seed_access, seed_repo
from medakis.repositories.schema import CONSTRAINTS_0004, REVISION_0004, SCHEMA_0004_COLUMNS, SEED_RESET_TABLES
from medakis.repositories.seed_repo import SchemaState
from medakis.services import seed_service
from medakis.services.seed_service import (
    DEFAULT_GENERATOR,
    EXPECTED_0004_TYPES,
    NEVER_V2,
    V2_DATABASES,
    SeedService,
    require_revision_0004,
    require_schema_0004,
    require_v2_target,
)
from medakis.synthetic.params import SyntheticParams
from medakis.synthetic.v2 import ParamsV2

ROOT = Path(__file__).resolve().parents[1]
SYSID = "7400000000000000001"
V1_GOLDEN = "83a619cb04371b892ed32803be9d0d16690bd72cecc6b8f560e9d59897191f42"
FULL_0004 = SchemaState(
    columns={key: (dtype, True) for key, dtype in EXPECTED_0004_TYPES.items()},
    constraints={name: (table, True) for name, (table, _) in CONSTRAINTS_0004.items()},
)
SCHEMA_0003 = SchemaState(columns={}, constraints={})


def changed(**kwargs):
    return SchemaState(**{**FULL_0004.__dict__, **kwargs})


# ---- Varsayılan ve yapıcı korumaları (bağlantı kurulmaz) ----------------------------------


def test_default_generator_is_1_0_0():
    assert DEFAULT_GENERATOR == "1.0.0"
    assert SeedService("medakis_test", engine=object()).spec.version == "1.0.0"
    assert SeedService("medakis_dev", engine=object()).spec.extended is False


def test_v2_target_lists_exclude_dev_and_demo():
    assert V2_DATABASES == {"medakis_test"} and NEVER_V2 == {"medakis_dev", "medakis_demo"}
    assert not V2_DATABASES & NEVER_V2


def test_v2_refused_for_dev_before_any_connection():
    with pytest.raises(DomainError) as info:
        SeedService("medakis_dev", generator_version="2.0.0", expected_system_identifier=SYSID)
    assert info.value.code == errors.SEED_GENERATOR_NOT_ALLOWED


def test_demo_refused_for_every_generator():
    for version in ("1.0.0", "2.0.0"):
        with pytest.raises(DomainError) as info:
            SeedService("medakis_demo", generator_version=version, expected_system_identifier=SYSID)
        assert info.value.code == errors.SEED_DATABASE_NOT_ALLOWED


@pytest.mark.parametrize("identifier", [None, "", "abc", "12 3", "1" * 21])
def test_v2_requires_explicit_system_identifier(identifier):
    with pytest.raises(DomainError) as info:
        SeedService("medakis_test", engine=object(), generator_version="2.0.0", expected_system_identifier=identifier)
    assert info.value.code == errors.SEED_TARGET_UNVERIFIED


def test_unknown_generator_version_rejected():
    with pytest.raises(DomainError) as info:
        SeedService("medakis_test", engine=object(), generator_version="3.0.0")
    assert info.value.code == errors.SEED_GENERATOR_NOT_ALLOWED


def test_params_must_match_generator():
    service = SeedService("medakis_test", engine=object(), generator_version="2.0.0", expected_system_identifier=SYSID)
    with pytest.raises(DomainError) as info:
        service.apply(SyntheticParams())
    assert info.value.code == errors.SEED_GENERATOR_NOT_ALLOWED


# ---- Saf denetimler ----------------------------------------------------------------------


def test_target_check_uses_connection_and_server_identity():
    require_v2_target("medakis_test", "medakis_test", f"{SYSID}:16384", SYSID)
    with pytest.raises(DomainError) as info:
        require_v2_target("medakis_test", "medakis_dev", f"{SYSID}:16384", SYSID)
    assert info.value.code == errors.SEED_GENERATOR_NOT_ALLOWED
    with pytest.raises(DomainError) as info:
        require_v2_target("medakis_test", "medakis_test", "999:16384", SYSID)
    assert info.value.code == errors.SEED_TARGET_UNVERIFIED


@pytest.mark.parametrize(
    ("state", "fragment"),
    [
        (SCHEMA_0003, "eksik kolon"),
        (changed(constraints={}), "eksik kısıt"),
        (changed(constraints={n: (t, False) for n, (t, _) in CONSTRAINTS_0004.items()}), "NOT VALID"),
        (changed(constraints={n: ("staff", True) for n in CONSTRAINTS_0004}), "kısıt başka tabloda"),
        (changed(columns={k: (d, False) for k, d in EXPECTED_0004_TYPES.items()}), "NULL olamayan"),
        (changed(columns={**FULL_0004.columns, ("care_tasks", "cancelled_at"): ("text", True)}), "yanlış tür"),
        (changed(columns={**FULL_0004.columns, ("encounters", "triage_category"): ("character varying", True)}), "yanlış tür"),
    ],
)  # fmt: skip
def test_catalog_check_refuses_incomplete_or_different_schema(state, fragment):
    with pytest.raises(DomainError) as info:
        require_schema_0004(state)
    assert info.value.code == errors.SEED_SCHEMA_REVISION and fragment in info.value.message


def test_catalog_check_accepts_exact_0004_traces():
    require_schema_0004(FULL_0004)
    assert set(EXPECTED_0004_TYPES) == {(t, c) for t, cols in SCHEMA_0004_COLUMNS.items() for c in cols}


@pytest.mark.parametrize("revision", [None, "", "0003_seed_write_window", "0005_future", "0004_triage_cancel_columns "])
def test_exact_revision_is_mandatory_unknown_means_refuse(revision):
    with pytest.raises(DomainError) as info:
        require_revision_0004(revision)
    assert info.value.code == errors.SEED_SCHEMA_REVISION


def test_exact_revision_accepted():
    require_revision_0004(REVISION_0004)


# ---- Sahte bağlantıyla uçtan uca (yazma yolu dahil) --------------------------------------


class FakeDB:
    def __init__(self, database="medakis_test", identity=f"{SYSID}:16384", schema=FULL_0004, revision=REVISION_0004):
        self.database, self.identity, self.schema, self.revision = database, identity, schema, revision
        self.read_only = False
        self.tables = {t: [] for t in SEED_RESET_TABLES}
        self.inserted: dict[str, list[dict]] = {}
        self.audit: list[dict] = []

    @contextmanager
    def connect(self):
        yield self

    begin = connect


@pytest.fixture()
def fake(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(seed_repo, "current_database", lambda conn: conn.database)
    monkeypatch.setattr(seed_repo, "server_identity", lambda conn: conn.identity)
    monkeypatch.setattr(seed_repo, "schema_state", lambda conn: conn.schema)
    monkeypatch.setattr(seed_repo, "alembic_revision", lambda conn: conn.revision)
    monkeypatch.setattr(seed_repo, "set_read_only", lambda conn: setattr(conn, "read_only", True))
    monkeypatch.setattr(seed_repo, "protected_counts", lambda conn: {})
    monkeypatch.setattr(seed_repo, "operational_counts", lambda conn: {t: len(r) for t, r in conn.tables.items()})

    def read_tables(conn, extended=False):
        hidden = {} if extended else SCHEMA_0004_COLUMNS
        return {t: [{k: v for k, v in r.items() if k not in hidden.get(t, ())} for r in rows] for t, rows in conn.tables.items()}

    def insert_rows(conn, name, rows):
        conn.inserted[name] = [dict(r) for r in rows]
        # Veritabanı gibi: gönderilmeyen 0004 kolonları NULL olur.
        conn.tables[name] = [{**{c: None for c in SCHEMA_0004_COLUMNS.get(name, ())}, **r} for r in rows]

    monkeypatch.setattr(seed_repo, "read_tables", read_tables)
    monkeypatch.setattr(seed_repo, "insert_rows", insert_rows)
    monkeypatch.setattr(seed_repo, "lock", lambda conn, timeout: None)
    monkeypatch.setattr(seed_repo, "sync_sequences", lambda conn: None)
    monkeypatch.setattr(seed_repo, "delete_operational", lambda conn: conn.tables.update({t: [] for t in conn.tables}))
    monkeypatch.setattr(seed_access, "write_open", lambda conn: False)
    monkeypatch.setattr(audit_repo, "insert_event", lambda conn, *args: conn.audit.append(args[-1]))
    return db


def _v2(db, revision_db=None):
    return SeedService("medakis_test", engine=db, generator_version="2.0.0", expected_system_identifier=SYSID,
                       revision_engine=revision_db or FakeDB())  # fmt: skip


def test_v2_load_maps_triage_and_cancellation_columns(fake):
    result = _v2(fake).apply(ParamsV2(days=5))
    assert result.action == "loaded" and result.generator_version == "2.0.0"
    encounters = fake.inserted["encounters"]
    tasks = fake.inserted["care_tasks"]
    assert any(e["triage_category"] for e in encounters)
    assert {e["triage_category"] for e in encounters} <= {None, "red", "yellow", "green"}
    cancelled = [r for r in tasks if r["status"] == "cancelled"]
    assert cancelled and all(r["cancelled_at"] is not None and r["cancel_reason"] for r in cancelled)
    assert all(r["cancelled_at"] is None and r["cancel_reason"] is None for r in tasks if r["status"] != "cancelled")
    # Yalnız bellekte kalması kararlaştırılan bilgiler satırlara sızmaz.
    assert not {"started_at", "staff_id", "outcome", "appointment_id"} & set(tasks[0])
    assert set(fake.inserted) == set(SEED_RESET_TABLES)
    assert fake.audit[-1]["generator_version"] == "2.0.0"
    assert _v2(fake).apply(ParamsV2(days=5)).action == "unchanged"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"identity": "999:16384"}, errors.SEED_TARGET_UNVERIFIED),
        ({"schema": SCHEMA_0003}, errors.SEED_SCHEMA_REVISION),
        ({"database": "medakis_dev"}, errors.SEED_WRONG_DATABASE),
    ],
)
def test_v2_wrong_target_or_schema_writes_nothing(fake, change, code):
    for key, value in change.items():
        setattr(fake, key, value)
    with pytest.raises(DomainError) as info:
        _v2(fake).apply(ParamsV2(days=3))
    assert info.value.code == code
    assert fake.inserted == {} and fake.audit == []


@pytest.mark.parametrize(
    ("revision_db", "code"),
    [
        (FakeDB(revision=None), errors.SEED_SCHEMA_REVISION),  # okunamadı / tablo yok / çok satır
        (FakeDB(revision="0003_seed_write_window"), errors.SEED_SCHEMA_REVISION),
        (FakeDB(identity="999:16384"), errors.SEED_TARGET_UNVERIFIED),  # başka sunucu
        (FakeDB(identity=f"{SYSID}:99999"), errors.SEED_TARGET_UNVERIFIED),  # aynı sunucu, başka veritabanı
        (FakeDB(database="medakis_dev"), errors.SEED_TARGET_UNVERIFIED),
    ],
)  # fmt: skip
def test_v2_refuses_without_exact_revision_even_if_catalog_looks_complete(fake, revision_db, code):
    assert fake.schema == FULL_0004  # katalog izleri tam; yine de kesin revizyon olmadan yazılmaz
    with pytest.raises(DomainError) as info:
        _v2(fake, revision_db).apply(ParamsV2(days=3))
    assert info.value.code == code
    assert fake.inserted == {} and fake.audit == []


def test_revision_is_read_in_a_read_only_transaction(fake):
    revision_db = FakeDB()
    _v2(fake, revision_db).apply(ParamsV2(days=3))
    assert revision_db.read_only is True


def test_v1_default_load_unchanged_and_ignores_0004_columns(fake):
    result = SeedService("medakis_test", engine=fake).apply(SyntheticParams())
    assert result.content_sha256 == V1_GOLDEN and result.generator_version == "1.0.0"
    assert "triage_category" not in fake.inserted["encounters"][0]
    assert "cancelled_at" not in fake.inserted["care_tasks"][0]
    assert fake.audit[-1]["generator_version"] == "1.0.0"
    # 1.0.0 yolu hedef/şema denetimi gerektirmez (0003'teki veritabanında da çalışır).
    fake.schema = SCHEMA_0003
    assert SeedService("medakis_test", engine=fake).apply(SyntheticParams()).action == "unchanged"


def test_scoped_grant_path_never_runs_v2():
    service = SeedService("medakis_test", engine=object(), generator_version="2.0.0", expected_system_identifier=SYSID)
    with pytest.raises(DomainError) as info:
        service._write_with_scoped_grant(None, None, "", None, "")
    assert info.value.code == errors.SEED_GENERATOR_NOT_ALLOWED


# ---- Komut satırı --------------------------------------------------------------------------


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "db" / "seed.py"), *args],
        cwd=ROOT, input="", capture_output=True, text=True, encoding="utf-8", timeout=120,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("--database", "medakis_dev", "--uretici", "2.0.0", "--hedef-sistem-kimligi", SYSID), "SEED_GENERATOR_NOT_ALLOWED"),
        (("--database", "medakis_dev", "--uretici", "2.0.0", "--sifirla", "--onay", "medakis_dev",
          "--hedef-sistem-kimligi", SYSID), "SEED_GENERATOR_NOT_ALLOWED"),
        (("--database", "medakis_test", "--uretici", "2.0.0"), "SEED_TARGET_UNVERIFIED"),
        (("--database", "medakis_demo", "--uretici", "2.0.0", "--hedef-sistem-kimligi", SYSID), "SEED_DATABASE_NOT_ALLOWED"),
    ],
)  # fmt: skip
def test_cli_refuses_v2_for_wrong_targets_without_connecting(args, code):
    proc = run_cli("apply", *args)
    assert proc.returncode == 1 and code in proc.stdout, proc.stdout + proc.stderr


def test_cli_rejects_unknown_generator():
    proc = run_cli("apply", "--database", "medakis_test", "--uretici", "9.9.9")
    assert proc.returncode == 2 and "invalid choice" in proc.stderr


def test_seed_service_module_keeps_v1_as_default_constant():
    assert seed_service.GENERATORS[DEFAULT_GENERATOR].extended is False


@pytest.mark.parametrize("database", ["medakis_demo", "postgres", ""])
def test_revision_read_engine_limited_to_seedable_databases(database):
    from medakis.config import ConfigError
    from medakis.repositories.database import revision_read_engine

    with pytest.raises(ConfigError):
        revision_read_engine(database)
