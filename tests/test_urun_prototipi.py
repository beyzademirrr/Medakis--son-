"""Ürün prototipi: sentetik yoğunluk simülasyonu, yoğunluğa duyarlı CP-SAT görevlendirme, onay ve asistan."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from medakis.agent import tools as T
from medakis.agent.orchestrator import answer
from medakis.approvals.audit_log import MemoryJournal, verify_chain
from medakis.density import staffing as S
from medakis.density.simulator import Simulator, count_from_frame
from medakis.notifications.telegram import DRY, REFUSED, TelegramConfig, TelegramNotifier
from medakis.retrieval_demo import search_sources

ROOT = Path(__file__).resolve().parents[1]


def surge(steps=12):
    sim = Simulator("acil_yogunluk")
    for _ in range(steps):
        sim.step()
    return sim


def service():
    return T.TransferService(MemoryJournal(), TelegramNotifier(MemoryJournal(), TelegramConfig.from_env({})))


def test_simulation_is_deterministic_and_frame_count_matches():
    a, b = surge(), surge()
    assert a.counts() == b.counts()
    assert count_from_frame(a.frame()) == a.counts()  # sentetik karede figürler üst üste binmez
    with pytest.raises(ValueError):
        Simulator("bilinmeyen")


def test_surge_makes_emergency_critical():
    rows = {r["alan"]: r for r in S.area_status(surge().counts(), S.roster())}
    assert rows["acil"]["seviye"] == "kritik" and S.needs_support(rows["acil"])


def test_density_alone_is_not_enough():
    row = {"seviye": "kritik", "personel_basina_gorev": 1.0, "gereken_personel": 9, "aktif_personel": 4}
    assert not S.needs_support(row)  # iş yükü eşiği aşılmadan destek yok


def test_optimizer_respects_constraints_and_verifier_passes():
    sim, staff = surge(), S.roster()
    plan = S.optimize(sim.counts(), staff)
    assert plan["durum"] == "oneri" and 1 <= len(plan["aktarimlar"]) <= 2
    assert S.verify(plan, sim.counts(), staff) == []
    by = {s.id: s for s in staff}
    for m in plan["aktarimlar"]:
        s = by[m["staff_id"]]
        assert s.on_shift and not s.busy and S.SKILL_FOR["acil"] in s.skills and s.area != "acil"


def test_verifier_catches_violations():
    sim, staff = surge(), S.roster()
    busy = next(s for s in staff if s.busy)
    bad = {"aktarimlar": [{"staff_id": busy.id, "source": busy.area, "target": "acil"}]}
    assert any(p.startswith("aktif_gorevde") for p in S.verify(bad, sim.counts(), staff))


def test_manual_request_for_unsuitable_staff_is_refused():
    sim = surge()
    busy = next(s for s in S.roster() if s.busy)
    plan = T.optimize_staff_allocation(sim, target="acil", pinned=(busy.id,))
    assert plan["durum"] == "uygun_degil" and not plan["aktarimlar"]


def test_proposal_approval_notification_and_audit():
    sim, svc = surge(), service()
    plan = T.optimize_staff_allocation(sim)
    prop = T.create_transfer_proposal(svc, plan, "test")["oneri"]
    assert T.prepare_approved_notification(svc, prop["id"])["durum"] == REFUSED  # onaysız: bildirim yok
    assert svc.decide(prop["id"], "f" * 64, True, "y")[0] is False  # plan özeti uyuşmazlığı
    assert svc.decide(prop["id"], prop["plan_sha256"], False, "y", " ")[0] is False  # gerekçesiz ret
    assert svc.decide(prop["id"], prop["plan_sha256"], True, "y")[0] is True
    assert svc.decide(prop["id"], prop["plan_sha256"], True, "y")[0] is False  # ikinci karar yok
    assert T.prepare_approved_notification(svc, prop["id"])["durum"] == DRY
    assert T.prepare_approved_notification(svc, prop["id"])["durum"] == REFUSED  # mükerrer bildirim yok
    events, issues = svc.audit()
    assert [e["olay"] for e in events] == ["oneri_olusturuldu", "onaylandi"] and issues == []
    assert verify_chain(svc.notifier.journal.events()) == []


def test_assistant_creates_proposal_but_cannot_approve():
    sim, svc = surge(), service()
    r = answer("Acil servise uygun iki personel öner", sim, svc)
    assert r.proposal and "create_transfer_proposal" in r.tools
    answer("Bu öneriyi onayla", sim, svc)
    assert all(p["durum"] == "beklemede" for p in svc.proposals().values())


def test_assistant_routes_intents():
    sim, svc = surge(), service()
    assert answer("Acil servis neden yoğun?", sim, svc).tools == ["get_service_density"]
    assert answer("Katman B'de rezerv nasıl hesaplanır?", sim, svc).tools == ["retrieve_policy_evidence"]
    assert answer("Onay durumu nedir?", sim, svc).tools == ["get_approval_status"]


def test_source_search_cites_or_abstains():
    hit = search_sources("Katman B'de rezerv nasıl hesaplanır?")
    assert hit["bulundu"] and hit["kaynaklar"][0]["surum"] and "rezerv" in hit["kaynaklar"][0]["alinti"].lower()
    assert not search_sources("Ay neden yuvarlak görünür?")["bulundu"]


def test_dashboard_renders_without_database_or_models():
    at = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("Kontrol Merkezi" in m.value for m in at.markdown)
