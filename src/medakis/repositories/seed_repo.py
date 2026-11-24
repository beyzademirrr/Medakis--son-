"""Seed veritabanı işlemleri. Yalnız medakis_seed bağlantısıyla kullanılır (seed_service).

Silme yalnız SEED_RESET_TABLES üzerinde ve FK sırasına göre yapılır; TRUNCATE ve CASCADE
kullanılmaz. Korunan tarihsel tablolarda kayıt varsa FK silmeyi durdurur.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, delete, func, insert, select, text

from medakis.repositories.schema import (
    CONSTRAINTS_0004,
    SCHEMA_0004_COLUMNS,
    SEED_PROTECTED_HISTORY,
    SEED_RESET_TABLES,
    legacy_columns,
    metadata,
)

ADVISORY_LOCK_KEY = 4242001  # aynı anda iki seed işlemini sıraya koyar


def current_database(conn: Connection) -> str:
    return conn.execute(text("SELECT current_database()")).scalar_one()


def server_identity(conn: Connection) -> str:
    """Sunucu örneği + veritabanı kimliği: '<system_identifier>:<veritabanı oid>' (yedek eşleşmesi, F4)."""
    return conn.execute(
        text(
            "SELECT (SELECT system_identifier FROM pg_control_system())::text || ':' || "
            "(SELECT oid FROM pg_database WHERE datname = current_database())::text"
        )
    ).scalar_one()


def lock(conn: Connection, lock_timeout_ms: int = 10_000) -> None:
    """İşlem sonuna kadar tutulan kilitler (F2).

    1. Danışma kilidi: iki seed işlemi sıraya girer (bekleme süresi sınırsız).
    2. Operasyonel tablolar EXCLUSIVE: başka oturumlar yalnız okuyabilir; ekleme, güncelleme,
       silme ve bu satırlara yabancı anahtarla bağlanan eklemeler commit'e kadar bekler.
    3. Tarihsel tablolar SHARE: commit'e kadar yeni tarihsel kayıt eklenemez.
    audit_events kilitlenmez: yalnız eklenen bir tablodur ve seed içeriğini etkilemez.
    Uygulama işlemi tabloları tutuyorsa lock_timeout sonunda hata verilir; hiçbir şey değişmez.
    """
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY})
    conn.execute(text("SELECT set_config('lock_timeout', :v, true)"), {"v": f"{int(lock_timeout_ms)}ms"})
    conn.execute(text(f"LOCK TABLE {', '.join(SEED_RESET_TABLES)} IN EXCLUSIVE MODE"))
    conn.execute(text(f"LOCK TABLE {', '.join(SEED_PROTECTED_HISTORY)} IN SHARE MODE"))


def counts(conn: Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {name: conn.execute(select(func.count()).select_from(metadata.tables[name])).scalar_one() for name in tables}


def operational_counts(conn: Connection) -> dict[str, int]:
    return counts(conn, SEED_RESET_TABLES)


def protected_counts(conn: Connection) -> dict[str, int]:
    return counts(conn, SEED_PROTECTED_HISTORY)


def read_tables(conn: Connection, extended: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Operasyonel tablolar. Varsayılan: revizyon 0003 kolonları (0003'teki veritabanında da çalışır).

    `extended=True` revizyon 0004 kolonlarını da okur; yalnız şeması doğrulanmış hedefte kullanılır.
    """
    out = {}
    for name in SEED_RESET_TABLES:
        table = metadata.tables[name]
        columns = list(table.columns) if extended else legacy_columns(table)
        out[name] = [dict(r) for r in conn.execute(select(*columns)).mappings()]
    return out


@dataclass(frozen=True)
class SchemaState:
    """Revizyon 0004 katalog izleri (ikincil denetim; kesin kanıt Alembic revizyonudur).

    columns: (tablo, kolon) -> (information_schema data_type, NULL olabilir mi)
    constraints: kısıt adı -> (bağlı olduğu tablo, doğrulanmış mı)
    """

    columns: dict[tuple[str, str], tuple[str, bool]]
    constraints: dict[str, tuple[str, bool]]


def schema_state(conn: Connection) -> SchemaState:
    """Yalnız katalog okur; seed rolünün yeni yetkiye ihtiyacı yoktur."""
    wanted = [c for cols in SCHEMA_0004_COLUMNS.values() for c in cols]
    columns = conn.execute(
        text(
            "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ANY(CAST(:t AS text[])) "
            "AND column_name = ANY(CAST(:c AS text[]))"
        ),
        {"t": list(SCHEMA_0004_COLUMNS), "c": wanted},
    ).all()
    constraints = conn.execute(
        text(
            "SELECT c.conname, cl.relname, c.convalidated FROM pg_constraint c "
            "JOIN pg_namespace n ON n.oid = c.connamespace JOIN pg_class cl ON cl.oid = c.conrelid "
            "WHERE n.nspname = 'public' AND c.contype = 'c' AND c.conname = ANY(CAST(:n AS text[]))"
        ),
        {"n": list(CONSTRAINTS_0004)},
    ).all()
    return SchemaState(
        columns={(t, c): (dtype, nullable == "YES") for t, c, dtype, nullable in columns},
        constraints={name: (table, bool(ok)) for name, table, ok in constraints},
    )


def set_read_only(conn: Connection) -> None:
    conn.execute(text("SET TRANSACTION READ ONLY"))


def alembic_revision(conn: Connection) -> str | None:
    """alembic_version içindeki tek revizyon; tablo yoksa veya satır sayısı 1 değilse None.

    Bu tabloyu yalnız medakis_owner okuyabilir (seed rolüne yetki verilmez); bkz. revision_read_engine.
    """
    if not conn.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar_one():
        return None
    rows = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    return rows[0] if len(rows) == 1 else None


def delete_operational(conn: Connection) -> None:
    """Çocuktan ebeveyne siler. Tarihsel bir kayıt bu satırlara bağlıysa FK hatasıyla durur."""
    for name in reversed(SEED_RESET_TABLES):
        conn.execute(delete(metadata.tables[name]))


def insert_rows(conn: Connection, name: str, rows: list[dict[str, Any]]) -> None:
    if rows:
        conn.execute(insert(metadata.tables[name]), rows)


def sync_sequences(conn: Connection) -> None:
    """Kimlik sayaçlarını yalnız ileri taşır: yeni değer = max(tablodaki en büyük id, sayacın değeri).

    setval PostgreSQL'de işlemle geri alınmaz (F1). Sayaç hiçbir zaman geri çekilmediği için
    işlem geri alındığında sayaç, tabloda kalan her id'nin üstünde kalır; sonraki eklemeler
    çakışmaz. Geri alma sonrasında kalan tek etki kullanılmamış id aralığıdır (boşluk).
    Boş tablonun sayacına dokunulmaz.
    """
    for name in SEED_RESET_TABLES:
        table = metadata.tables[name]
        if "id" not in table.c or table.c.id.identity is None:
            continue
        conn.execute(
            text(
                "SELECT setval(s.seq, GREATEST(m.max_id, COALESCE(pg_sequence_last_value(s.seq), 0)), true) "
                f"FROM (SELECT max(id) AS max_id FROM {name}) m, "
                "(SELECT CAST(pg_get_serial_sequence(:t, 'id') AS regclass) AS seq) s "
                "WHERE m.max_id IS NOT NULL"
            ),
            {"t": f"public.{name}"},
        )
