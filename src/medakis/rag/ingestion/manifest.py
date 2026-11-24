"""Corpus manifestinin tek kayıt doğrulaması. Eksik ya da hatalı metadata açık hata koduyla reddedilir."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from medakis.rag.contracts.documents import DOC_TYPES, PURPOSES, DocumentRecord

REQUIRED = ("source_key", "source_name", "doc_type", "version", "valid_from", "valid_until", "status", "approved_by",
            "approved_at", "is_synthetic", "path", "content_sha256", "purpose")  # fmt: skip
SOURCE_KEY = re.compile(r"^SYN-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
VERSION = re.compile(r"^\d+\.\d+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.md$")


def parse_utc(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{name}_utc_degil")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ValueError(f"{name}_gecersiz") from None
    return parsed.astimezone(UTC)


def parse_entry(entry: Any) -> tuple[DocumentRecord | None, list[str]]:
    """Manifest kaydı → (kayıt, sorunlar). Sorun varsa kayıt None'dır (belge yüklenmez)."""
    if not isinstance(entry, dict):
        return None, ["kayit_nesne_degil"]
    missing = [k for k in REQUIRED if k not in entry]
    if missing:
        return None, [f"eksik_alan:{k}" for k in missing]
    unknown = sorted(set(entry) - set(REQUIRED) - {"rule_conflict", "note"})
    problems = [f"bilinmeyen_alan:{k}" for k in unknown]
    key = entry["source_key"]
    if not isinstance(key, str) or not SOURCE_KEY.match(key):
        problems.append("source_key_gecersiz")
    for name in ("source_name",):
        if not isinstance(entry[name], str) or not entry[name].strip() or len(entry[name]) > 200:
            problems.append(f"{name}_gecersiz")
    if entry["doc_type"] not in DOC_TYPES:
        problems.append("doc_type_gecersiz")
    if not isinstance(entry["version"], str) or not VERSION.match(entry["version"]):
        problems.append("version_gecersiz")
    if entry["status"] != "draft" or entry["approved_by"] is not None or entry["approved_at"] is not None:
        problems.append("manifest_onay_tasiyamaz")  # onay yalnız proje sahibinin yerel onay izinden gelir
    if entry["is_synthetic"] is not True:
        problems.append("sentetik_degil")
    if not isinstance(entry["path"], str) or not FILE_NAME.match(entry["path"]) or ".." in entry["path"]:
        problems.append("yol_gecersiz")
    if not isinstance(entry["content_sha256"], str) or not SHA256.match(entry["content_sha256"]):
        problems.append("content_sha256_gecersiz")
    if entry["purpose"] not in PURPOSES:
        problems.append("purpose_gecersiz")
    conflict = entry.get("rule_conflict")
    if conflict is not None and (not isinstance(conflict, str) or not conflict.strip()):
        problems.append("rule_conflict_gecersiz")
    valid_from = valid_until = None
    try:
        valid_from = parse_utc(entry["valid_from"], "valid_from")
    except ValueError as exc:
        problems.append(str(exc))
    if entry["valid_until"] is not None:
        try:
            valid_until = parse_utc(entry["valid_until"], "valid_until")
        except ValueError as exc:
            problems.append(str(exc))
    if valid_from and valid_until and valid_until <= valid_from:
        problems.append("gecerlilik_araligi_bos")
    if problems:
        return None, problems
    return DocumentRecord(key, entry["source_name"].strip(), entry["doc_type"], entry["version"], valid_from, valid_until,  # type: ignore[arg-type]
                          entry["path"], entry["content_sha256"], True, entry["purpose"], conflict), []  # fmt: skip
