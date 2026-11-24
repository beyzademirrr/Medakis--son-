"""Kanonik içerik özeti: aynı satırlar her zaman aynı SHA-256'yı verir.

Üretilen veri kümesi ile veritabanından geri okunan satırlar aynı fonksiyonla özetlenir; böylece
yüklemenin birebir doğru olduğu kanıtlanır.

Kolon kümesi sürüme bağlıdır: varsayılan (`extended=False`) revizyon 0003 kolonlarıdır ve 1.0.0
altın özetini korur; `extended=True` revizyon 0004 kolonlarını da katar (üretici 2.0.0).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, time
from typing import Any

from medakis.repositories.schema import SEED_RESET_TABLES, legacy_columns, metadata


def _value(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return format(round(value, 6), ".6f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, time):
        return value.isoformat()
    raise TypeError(f"Kanonik biçimi tanımsız tür: {type(value).__name__}")


def primary_key(table: str) -> list[str]:
    return [c.name for c in metadata.tables[table].primary_key.columns]


def columns_for(table: str, extended: bool = False) -> list[str]:
    t = metadata.tables[table]
    return [c.name for c in (t.columns if extended else legacy_columns(t))]


def table_lines(table: str, rows: Iterable[Mapping[str, Any]], extended: bool = False) -> list[str]:
    columns = columns_for(table, extended)
    key = primary_key(table)
    ordered = sorted(rows, key=lambda r: tuple(r[k] for k in key))
    return [json.dumps([table, [_value(r[c]) for c in columns]], ensure_ascii=True, separators=(",", ":")) for r in ordered]


def content_sha256(tables: Mapping[str, Iterable[Mapping[str, Any]]], extended: bool = False) -> str:
    digest = hashlib.sha256()
    for table in SEED_RESET_TABLES:
        for line in table_lines(table, tables.get(table, ()), extended):
            digest.update(line.encode("ascii"))
            digest.update(b"\n")
    return digest.hexdigest()
