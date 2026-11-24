"""Sorguya girebilecek belgeler (Aşama 7.1). Her sorguda yeniden hesaplanır; önbellek yoktur.

- Yalnız `approved`, içeriği onaylanan özetle aynı ve `as_of` anında `[valid_from, valid_until)` içinde olan belgeler.
- `draft` hiçbir sorguya girmez. `retired` hiçbir sorguya, tarihsel sorguya da girmez.
- Aynı source_key için onaylı ve geçerlilikleri örtüşen sürümler (ya da onay izinde tutarsızlık) → sorgu
  fail-closed: `conflicts` doludur ve çağıran `conflicting_sources` döndürür.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from medakis.rag.contracts.documents import DocStatus, DocumentState, require_utc


@dataclass(frozen=True)
class Eligibility:
    as_of: datetime
    allowed: tuple[DocumentState, ...]
    conflicts: tuple[str, ...] = ()
    excluded: dict[str, str] = field(default_factory=dict)  # uid → neden

    @property
    def allowed_uids(self) -> tuple[str, ...]:
        return tuple(s.record.uid for s in self.allowed)


def eligible_documents(states: dict[str, DocumentState], as_of: datetime, ledger_problems: list[str] | tuple[str, ...] = ()) -> Eligibility:
    require_utc(as_of, "Sorgu anı")
    conflicts = [f"onay_izi:{p}" for p in ledger_problems]
    approved = [s for s in states.values() if s.status is DocStatus.APPROVED]
    for i, a in enumerate(approved):
        for b in approved[i + 1:]:
            if a.record.source_key == b.record.source_key and a.record.overlaps(b.record):
                conflicts.append(f"surum_cakismasi:{a.record.uid}|{b.record.uid}")
    allowed: list[DocumentState] = []
    excluded: dict[str, str] = {}
    for uid, s in sorted(states.items()):
        if s.status is DocStatus.DRAFT:
            excluded[uid] = "taslak"
        elif s.status is DocStatus.RETIRED:
            excluded[uid] = "emekli"
        elif not s.content_intact:
            excluded[uid] = "onayli_icerik_degisti"
            conflicts.append(f"onayli_icerik_degisti:{uid}")
        elif not s.record.covers(as_of):
            excluded[uid] = "gecerlilik_disi"
        else:
            allowed.append(s)
    return Eligibility(as_of, tuple(allowed), tuple(sorted(set(conflicts))), excluded)
