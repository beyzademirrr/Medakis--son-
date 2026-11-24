"""Yönetici onaylı öneriler için Telegram bildirim servisi.

Akış: öneri → yönetici açıkça onaylar → onay kaydı doğrulanır → bildirim hazırlanır → gönderilir (ya da dry-run) →
sonuç ayrı, özet zincirli bildirim izine yazılır.

Güvenlik kuralları:
- Varsayılan kip DRY-RUN: ağ isteği yapılmaz, yalnız hazırlanan mesaj ve sonuç denetim izine yazılır.
- Gerçek gönderim yalnız `MEDAKIS_TELEGRAM_MODE=live` VE `TELEGRAM_BOT_TOKEN` ile `TELEGRAM_CHAT_ID` ortam
  değişkenleri birlikte verildiğinde yapılır. Token hiçbir kayda, mesaja ya da hata metnine yazılmaz.
- Yalnız durumu "onaylandi" olan ve sunulan plan özeti onay kaydıyla aynı olan öneri için bildirim hazırlanır.
- Aynı öneri ve plan özeti için ikinci başarılı gönderim engellenir. Başarısız gönderim başarısız olarak kaydedilir.
- Tek bir demo sohbetine gönderim yapılır; bu, personelin kişisel hesaplarına bildirim gönderildiği anlamına gelmez.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from medakis.approvals.audit_log import Journal
from medakis.approvals.workflow import APPROVED

DRY_RUN, LIVE = "dry_run", "live"
SENT, DRY, FAILED, REFUSED = "gonderildi", "dry_run", "basarisiz", "reddedildi"
API = "https://api.telegram.org"
_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")


@dataclass(frozen=True)
class TelegramConfig:
    mode: str
    token: str | None
    chat_id: str | None

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> TelegramConfig:
        e = os.environ if env is None else env
        mode = (e.get("MEDAKIS_TELEGRAM_MODE") or DRY_RUN).strip().lower()
        return TelegramConfig(LIVE if mode == LIVE else DRY_RUN, e.get("TELEGRAM_BOT_TOKEN") or None, e.get("TELEGRAM_CHAT_ID") or None)

    @property
    def live_ready(self) -> bool:
        return self.mode == LIVE and bool(self.token) and bool(self.chat_id)

    def __repr__(self) -> str:  # token asla görünmez
        return f"TelegramConfig(mode={self.mode!r}, token={'***' if self.token else None}, chat_id={'***' if self.chat_id else None})"


@dataclass(frozen=True)
class NotificationResult:
    status: str
    proposal_id: str
    message: str
    detail: str = ""


def redact(text: str) -> str:
    return _TOKEN.sub("***", text)


def build_message(proposal: Mapping[str, Any]) -> str:
    """Yalnız onay kaydındaki idari özet; serbest metin ya da kişisel sağlık verisi içermez."""
    return ("MedAkis — onaylanan öneri (sentetik prototip)\n"
            f"Öneri: {proposal['id']}\n"
            f"Plan özeti: {str(proposal['plan_sha256'])[:12]}…\n"
            f"Onay zamanı: {(proposal.get('kararlar') or [{}])[-1].get('zaman', '-')}\n"
            "Bu bildirim yönetici onayından sonra gönderildi; otomatik personel emri değildir.")  # fmt: skip


class TelegramNotifier:
    def __init__(self, journal: Journal, config: TelegramConfig | None = None, post: Callable[..., Any] | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:  # fmt: skip
        self.journal, self.config, self.clock = journal, config or TelegramConfig.from_env(), clock
        self._post = post

    def _already_sent(self, proposal_id: str, sha: str) -> bool:
        return any(e.get("oneri_id") == proposal_id and e.get("plan_sha256") == sha and e.get("durum") == SENT for e in self.journal.events())

    def _record(self, status: str, proposal_id: str, sha: str, detail: str) -> None:
        events = self.journal.events()
        self.journal.append({"olay": "bildirim", "kanal": "telegram", "kip": self.config.mode, "durum": status, "oneri_id": proposal_id,
                             "plan_sha256": sha, "ayrinti": redact(detail)[:300], "zaman": self.clock().isoformat()}, len(events))  # fmt: skip

    def notify_approved(self, proposal: Mapping[str, Any] | None, presented_sha256: str) -> NotificationResult:
        pid = str((proposal or {}).get("id", ""))
        sha = str((proposal or {}).get("plan_sha256", ""))
        if not proposal or proposal.get("durum") != APPROVED:
            self._record(REFUSED, pid, sha, "oneri_onayli_degil")
            return NotificationResult(REFUSED, pid, "", "Öneri onaylı değil; bildirim hazırlanmadı.")
        if presented_sha256 != sha:
            self._record(REFUSED, pid, sha, "plan_ozeti_uyusmuyor")
            return NotificationResult(REFUSED, pid, "", "Sunulan plan özeti onay kaydıyla uyuşmuyor.")
        if self._already_sent(pid, sha):
            return NotificationResult(REFUSED, pid, "", "Bu öneri için bildirim daha önce gönderildi; tekrar gönderilmez.")
        text = build_message(proposal)
        if not self.config.live_ready:
            reason = "dry_run" if self.config.mode == DRY_RUN else "canli_kip_icin_token_veya_chat_id_eksik"
            self._record(DRY, pid, sha, reason)
            return NotificationResult(DRY, pid, text, "Dry-run: ağ isteği yapılmadı.")
        try:
            post = self._post or _httpx_post
            resp = post(f"{API}/bot{self.config.token}/sendMessage", json={"chat_id": self.config.chat_id, "text": text}, timeout=10.0)
            ok = getattr(resp, "status_code", 0) == 200 and bool((resp.json() or {}).get("ok"))
        except Exception as exc:  # noqa: BLE001 — ağ hatası başarısız olarak kaydedilir
            self._record(FAILED, pid, sha, f"{type(exc).__name__}")
            return NotificationResult(FAILED, pid, text, "Gönderim başarısız (ağ hatası).")
        if not ok:
            self._record(FAILED, pid, sha, f"http_{getattr(resp, 'status_code', '?')}")
            return NotificationResult(FAILED, pid, text, "Gönderim başarısız (Telegram yanıtı olumsuz).")
        self._record(SENT, pid, sha, "ok")
        return NotificationResult(SENT, pid, text, "Gönderildi (tek demo sohbetine).")


def _httpx_post(url: str, **kwargs: Any) -> Any:
    import httpx

    return httpx.post(url, **kwargs)
