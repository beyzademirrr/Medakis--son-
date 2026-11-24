"""Kontrollü belge sözleşmeleri (Aşama 7.1).

- Belge kimliği `source_key@version`'dır. Geçerlilik `[valid_from, valid_until)` UTC aralığıdır.
- Manifest yalnız belgeyi TANIMLAR; onay durumu manifestten değil, proje sahibinin yerel onay izinden gelir
  (`ingestion.approval_ledger`). Bu yüzden manifestte durum her zaman `draft`tır.
- Durumlar: draft → approved → retired. Retired son durumdur.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

DOC_TYPES = ("procedure", "job_description", "competency", "workflow")
PURPOSES = ("kaynak", "guvenlik_fiksturu", "surum_fiksturu")  # yalnız "kaynak" normal onaya girer


class DocStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


def require_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{name} UTC zaman damgası olmalıdır.")
    return value


@dataclass(frozen=True)
class DocumentRecord:
    source_key: str
    source_name: str
    doc_type: str
    version: str
    valid_from: datetime
    valid_until: datetime | None
    path: str
    content_sha256: str
    is_synthetic: bool
    purpose: str = "kaynak"
    rule_conflict: str | None = None  # yapılandırılmış kuralla bilinen çatışma (fikstür); sorguda conflicting_sources

    @property
    def uid(self) -> str:
        return f"{self.source_key}@{self.version}"

    def covers(self, at: datetime) -> bool:
        require_utc(at, "Sorgu anı")
        return self.valid_from <= at and (self.valid_until is None or at < self.valid_until)

    def overlaps(self, other: DocumentRecord) -> bool:
        a_end = self.valid_until or datetime.max.replace(tzinfo=UTC)
        b_end = other.valid_until or datetime.max.replace(tzinfo=UTC)
        return self.valid_from < b_end and other.valid_from < a_end


@dataclass(frozen=True)
class DocumentState:
    """Belgenin onay izine göre ETKİN durumu."""

    record: DocumentRecord
    status: DocStatus
    approved_by: str | None = None
    approved_at: datetime | None = None
    approved_sha256: str | None = None  # onaylanan içeriğin özeti; güncel içerikten farklıysa belge kullanılamaz
    retired_at: datetime | None = None
    retired_by: str | None = None

    @property
    def content_intact(self) -> bool:
        return self.status is DocStatus.DRAFT or self.approved_sha256 == self.record.content_sha256
