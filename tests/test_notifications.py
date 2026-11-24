"""Telegram bildirim servisi: dry-run varsayılanı, onay doğrulaması, tekrar engeli, başarısızlık kaydı, token gizliliği."""

from medakis.approvals.audit_log import MemoryJournal, verify_chain
from medakis.approvals.workflow import APPROVED, PENDING
from medakis.notifications.telegram import DRY, FAILED, REFUSED, SENT, TelegramConfig, TelegramNotifier, redact

TOKEN = "1234567890:" + "A" * 35  # sahte biçim; gerçek token değildir
PROP = {"id": "p1", "plan_sha256": "a" * 64, "durum": APPROVED, "kararlar": [{"zaman": "2026-11-24T10:00:00+00:00"}]}


class Resp:
    def __init__(self, code, ok):
        self.status_code, self._ok = code, ok

    def json(self):
        return {"ok": self._ok}


def live(post):
    return TelegramNotifier(MemoryJournal(), TelegramConfig("live", TOKEN, "42"), post=post)


def test_default_mode_is_dry_run_and_makes_no_request():
    calls = []
    n = TelegramNotifier(MemoryJournal(), TelegramConfig.from_env({}), post=lambda *a, **k: calls.append(a))
    r = n.notify_approved(PROP, "a" * 64)
    assert r.status == DRY and calls == [] and "p1" in r.message
    assert n.journal.events()[-1]["durum"] == DRY and verify_chain(n.journal.events()) == []


def test_live_requires_token_and_chat_id_otherwise_dry_run():
    cfg = TelegramConfig.from_env({"MEDAKIS_TELEGRAM_MODE": "live", "TELEGRAM_BOT_TOKEN": TOKEN})
    assert not cfg.live_ready
    r = TelegramNotifier(MemoryJournal(), cfg, post=lambda *a, **k: 1 / 0).notify_approved(PROP, "a" * 64)
    assert r.status == DRY


def test_only_approved_and_matching_hash():
    n = live(lambda *a, **k: Resp(200, True))
    assert n.notify_approved({**PROP, "durum": PENDING}, "a" * 64).status == REFUSED
    assert n.notify_approved(PROP, "b" * 64).status == REFUSED
    assert n.notify_approved(None, "a" * 64).status == REFUSED


def test_success_once_then_duplicate_blocked():
    sent = []
    n = live(lambda url, **k: sent.append(k["json"]) or Resp(200, True))
    assert n.notify_approved(PROP, "a" * 64).status == SENT
    assert n.notify_approved(PROP, "a" * 64).status == REFUSED and len(sent) == 1
    assert sent[0]["chat_id"] == "42"


def test_failure_is_recorded_as_failure_and_retry_allowed():
    n = live(lambda *a, **k: Resp(500, False))
    assert n.notify_approved(PROP, "a" * 64).status == FAILED
    assert [e["durum"] for e in n.journal.events()] == [FAILED]

    def boom(*a, **k):
        raise ConnectionError(f"bağlantı yok {TOKEN}")

    n2 = live(boom)
    assert n2.notify_approved(PROP, "a" * 64).status == FAILED
    n2._post = lambda *a, **k: Resp(200, True)
    assert n2.notify_approved(PROP, "a" * 64).status == SENT


def test_token_never_in_journal_or_repr():
    n = live(lambda *a, **k: Resp(200, True))
    n.notify_approved(PROP, "a" * 64)
    blob = repr(n.journal.events()) + repr(n.config)
    assert TOKEN not in blob and "***" in repr(n.config)
    assert redact(f"hata {TOKEN} son") == "hata *** son"


def test_end_to_end_review_approval_then_dry_run_notification():
    """Aşama 6 onay akışı (sentetik fikstür) → onaylı öneri → dry-run bildirim → bildirim izi; tekrar engeli."""
    import test_review_asama6 as R6

    svc = R6.make_service()
    _, pid, sha = R6.registered(svc, actor="demo_planlayici")
    pending = svc.proposal(pid)
    n = TelegramNotifier(MemoryJournal(), TelegramConfig.from_env({}))
    assert n.notify_approved({**pending, "id": pid}, sha).status == REFUSED  # onaysız öneri: bildirim yok
    assert svc.decide("onayla", svc.open_session("demo_yonetici"), pid, sha, "uygun").ok
    r = n.notify_approved({**svc.proposal(pid), "id": pid}, sha)
    assert r.status == DRY and pid in r.message
    assert [e["durum"] for e in n.journal.events()] == [REFUSED, DRY] and verify_chain(n.journal.events()) == []
