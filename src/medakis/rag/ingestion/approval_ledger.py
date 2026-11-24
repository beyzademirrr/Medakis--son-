"""Belge onay izi: ekleme-yalnız, SHA-256 zincirli JSONL (Aşama 7.1).

- Onay ve emekliye ayırma yalnız proje sahibinin yerel onay komutuyla (`scripts/rag_belge_onay.py`) eklenir.
  RAG çıktısı, belge metni veya manifest onay veremez.
- Onay, belgenin o anki içerik özetine bağlanır. İçerik sonradan değişirse belge kullanılamaz (yeni sürüm gerekir).
- Aynı source_key için geçerlilik aralığı örtüşen ikinci bir onay reddedilir.
- Geçişler: draft → approved (onayla), approved → retired (emekliye_ayir). Retired son durumdur.
- Zincir bozuksa (değiştirilmiş/silinmiş satır) hiçbir belge onaylı sayılmaz (fail-closed).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from medakis.rag.contracts.documents import DocStatus, DocumentRecord, DocumentState, require_utc
from medakis.rag.errors import LedgerError

APPROVE = "onayla"
RETIRE = "emekliye_ayir"
SESSION_KIND = "yerel_onay_komutu"
GENESIS = "0" * 64
_ACTOR = re.compile(r"^[^\x00-\x1f\x7f]{1,64}$")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(event: dict[str, Any]) -> str:
    body = {k: v for k, v in event.items() if k != "ozet"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _iso(at: datetime) -> str:
    return require_utc(at, "Karar anı").astimezone(UTC).isoformat().replace("+00:00", "Z")


class ApprovalLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # ---- Okuma -------------------------------------------------------------------------------------------

    def events(self) -> list[dict[str, Any]]:
        """Zinciri doğrulayarak olayları döndürür; bozuk zincirde LedgerError."""
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        previous = GENESIS
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                raise LedgerError("bos_satir", f"satir {n}")
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                raise LedgerError("satir_okunamadi", f"satir {n}") from None
            if event.get("sira") != n or event.get("onceki_ozet") != previous or event.get("ozet") != _digest(event):
                raise LedgerError("zincir_bozuk", f"satir {n}")
            if event.get("oturum") != SESSION_KIND or event.get("karar") not in (APPROVE, RETIRE):
                raise LedgerError("olay_gecersiz", f"satir {n}")
            previous = event["ozet"]
            out.append(event)
        return out

    # ---- Yazma (yalnız yerel onay komutu çağırır) ------------------------------------------------------

    def append(self, decision: str, record: DocumentRecord, actor: str, reason: str, at: datetime,
               known: Iterable[DocumentRecord]) -> dict[str, Any]:  # fmt: skip
        """Kurala uygun geçişi ekler. `known`: corpus'ta yüklenebilen bütün kayıtlar (çakışma denetimi için)."""
        if decision not in (APPROVE, RETIRE):
            raise LedgerError("karar_gecersiz")
        if not isinstance(actor, str) or not _ACTOR.match(actor.strip() or "\x00"):
            raise LedgerError("aktor_gecersiz")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise LedgerError("gerekce_gerekli")
        events = self.events()
        states, problems = effective_states(list(known), events)
        if problems:
            raise LedgerError("iz_tutarsiz", ";".join(problems))
        state = states.get(record.uid)
        if state is None:
            raise LedgerError("belge_corpus_ta_yok")
        if decision == APPROVE:
            if state.status is not DocStatus.DRAFT:
                raise LedgerError("yalniz_taslak_onaylanir")
            clash = [s.record.uid for s in states.values() if s.status is DocStatus.APPROVED
                     and s.record.source_key == record.source_key and s.record.overlaps(record)]  # fmt: skip
            if clash:
                raise LedgerError("onayli_surum_ortusuyor", ",".join(sorted(clash)))
        elif state.status is not DocStatus.APPROVED:
            raise LedgerError("yalniz_onayli_belge_emekliye_ayrilir")
        event = {
            "sira": len(events), "zaman": _iso(at), "karar": decision, "source_key": record.source_key,
            "version": record.version, "content_sha256": record.content_sha256, "aktor": actor.strip(),
            "oturum": SESSION_KIND, "gerekce": reason.strip(), "onceki_ozet": events[-1]["ozet"] if events else GENESIS,
        }  # fmt: skip
        event["ozet"] = _digest(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(_canonical(event) + "\n")
        return event


def effective_states(records: list[DocumentRecord], events: list[dict[str, Any]]) -> tuple[dict[str, DocumentState], list[str]]:
    """Manifest kayıtları + onay olayları → etkin durumlar ve tutarsızlıklar.

    İçerik özeti onaylanan özetten farklı olan belge `approved` görünür ama `content_intact=False` olur ve
    sorguya giremez (politika katmanı dışarıda bırakır).
    """
    states = {r.uid: DocumentState(r, DocStatus.DRAFT) for r in records}
    problems: list[str] = []
    for ev in events:
        uid = f"{ev['source_key']}@{ev['version']}"
        state = states.get(uid)
        at = datetime.fromisoformat(ev["zaman"].replace("Z", "+00:00"))
        if state is None:
            problems.append(f"{uid}:izde_var_corpus_ta_yok")
            continue
        if ev["karar"] == APPROVE:
            if state.status is not DocStatus.DRAFT:
                problems.append(f"{uid}:gecersiz_gecis")
                continue
            states[uid] = DocumentState(state.record, DocStatus.APPROVED, ev["aktor"], at, ev["content_sha256"])
        else:
            if state.status is not DocStatus.APPROVED:
                problems.append(f"{uid}:gecersiz_gecis")
                continue
            states[uid] = DocumentState(state.record, DocStatus.RETIRED, state.approved_by, state.approved_at,
                                        state.approved_sha256, at, ev["aktor"])  # fmt: skip
    return states, problems
