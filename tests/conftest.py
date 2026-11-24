import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_MARKERS = ("postgres", "postgres_lifecycle", "prova_rol")
# Varsayılan: demo da parmak izine girer. "1" verilirse medakis_demo'ya hiç bağlanılmaz (G4 kararı).
SKIP_DEMO_ENV = "MEDAKIS_CANLI_DEMO_ATLA"


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT


def dev_fingerprint() -> dict:
    """medakis_dev ve medakis_demo'nun salt okunur parmak izi (owner rolüyle; hiçbir şey yazılmaz).

    İçerik özeti, tarihsel sayımlar, denetim kayıtları, kimlik sayaçları, Alembic sürümü, üç rolün
    tablo yetkileri, seed sayaç yetkileri, medakis rol üyelikleri ve demo tablo sayısı.
    """
    from sqlalchemy import text

    from medakis.repositories import seed_access, seed_repo
    from medakis.repositories.database import migration_engine
    from medakis.synthetic.canonical import content_sha256

    privs = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"]
    result: dict = {}
    dev = migration_engine("medakis_dev", allow_prompt=False)
    try:
        with dev.connect() as conn:
            q = lambda sql, **p: conn.execute(text(sql), p)  # noqa: E731
            result["alembic"] = q("SELECT version_num FROM alembic_version").scalar_one()
            result["content_sha256"] = content_sha256(seed_repo.read_tables(conn))
            result["operational_counts"] = seed_repo.operational_counts(conn)
            result["protected_counts"] = seed_repo.protected_counts(conn)
            result["audit"] = [tuple(r) for r in q("SELECT id, occurred_at, actor, action, entity_id, detail_json::text FROM audit_events ORDER BY id")]
            result["sequences"] = {
                s: q("SELECT pg_sequence_last_value(CAST(:s AS regclass))", s=s).scalar_one() for s in seed_access.identity_sequences(conn)
            }
            result["table_privileges"] = sorted(
                tuple(r)
                for r in q(
                    "SELECT r, t.tablename, p FROM unnest(CAST(:roles AS text[])) r, pg_tables t, unnest(CAST(:privs AS text[])) p "
                    "WHERE t.schemaname = 'public' AND has_table_privilege(r, format('public.%I', t.tablename), p)",
                    roles=["medakis_app", "medakis_rag", "medakis_seed"], privs=privs,
                )
            )
            result["seed_write_open"] = seed_access.write_open(conn)
            result["role_memberships"] = sorted(
                tuple(r)
                for r in q(
                    "SELECT r.rolname, u.rolname, m.admin_option, m.inherit_option, m.set_option FROM pg_auth_members m "
                    "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
                    "WHERE r.rolname LIKE 'medakis%' OR u.rolname LIKE 'medakis%'"
                )
            )
    finally:
        dev.dispose()
    if os.environ.get(SKIP_DEMO_ENV) == "1":
        # G4: proje sahibi kararıyla gerçek medakis_demo'ya bağlanılmaz. Demo'nun dokunulmadığı,
        # bağlantı kurmadan pg_stat_database oturum sayacıyla ayrıca gösterilir.
        result["demo_tables"] = "atlandı"
        return result
    demo = migration_engine("medakis_demo", allow_prompt=False)
    try:
        with demo.connect() as conn:
            result["demo_tables"] = conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")).scalar_one()
    finally:
        demo.dispose()
    return result


@pytest.fixture(scope="session", autouse=True)
def dev_untouched_by_tests(request):
    """T4: canlı testler medakis_dev'e ve medakis_demo'ya yazmamalı. Oturum başı ve sonu karşılaştırılır.

    Testler çalışırken aynı anda dev'e uygulama yoluyla yazılırsa bu kontrol de (doğru olarak) uyarır.
    """
    live = any(item.get_closest_marker(m) for item in request.session.items for m in LIVE_MARKERS)
    if not live:
        yield
        return
    before = dev_fingerprint()
    yield
    after = dev_fingerprint()
    changed = sorted(k for k in before if before[k] != after.get(k))
    if changed:
        pytest.fail(f"Test oturumu medakis_dev/medakis_demo durumunu değiştirdi: {changed}")
