"""Bütünleşik ürün: sentetik kamera, dijital ikiz, yoğunluğa duyarlı CP-SAT, what-if, agent, onay, Telegram ve denetim.

Ağır testler (tahmin modeli ve CP-SAT) yerel model dosyaları yoksa atlanır; sahte sonuçla geçmez.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from medakis.agent import llm
from medakis.agent import tools as T
from medakis.agent.orchestrator import answer
from medakis.approvals.audit_log import verify_chain
from medakis.density import camera as CAM
from medakis.notifications.telegram import DRY, REFUSED
from medakis.retrieval_demo import search_sources
from medakis.twin import whatif as W
from medakis.twin.engine import level_of
from medakis.twin.hub import Engine, Session

ROOT = Path(__file__).resolve().parents[1]
AT = datetime.fromisoformat("2027-01-19T06:30:00+00:00")  # doğrulanmış (OPTIMAL) karar anı
HAS_MODELS = (ROOT / "artifacts" / "forecasting" / "asama4_seed42" / "random_forest").exists()
needs_models = pytest.mark.skipif(not HAS_MODELS, reason="yerel model dosyaları yok (./baslat.ps1 -ModelHazirla)")


@pytest.fixture(scope="module")
def engine():
    return Engine.build(load_models=HAS_MODELS)


@pytest.fixture
def session(engine):
    s = Session.open(engine, persist=False)
    s.sync_clock(AT, 0)
    return s


def ctx(engine, session, surges=None, minute=20):
    return T.ToolContext(engine.twin, AT, minute, surges or {}, create_proposal=lambda o: session.create_proposal(o, "agent"),
                         create_request=session.create_request, approval_status=session.proposals)  # fmt: skip


# ---- Sentetik kamera ve dedektör --------------------------------------------------------------------------------


def test_camera_is_deterministic_and_counts_figures():
    a = CAM.render("KAM", "3", "an", 10, 12, "09:40")
    b = CAM.render("KAM", "3", "an", 10, 12, "09:40")
    assert a.tobytes() == b.tobytes()
    for n in (0, 1, 5, 12, 15):
        assert CAM.detect(CAM.render("KAM", "3", "an", 5, n, "09:35")).count == n


def test_figures_move_between_minutes_and_views_keep_size():
    p0, p1 = CAM.positions("3", "an", 0, 10), CAM.positions("3", "an", 5, 10)
    assert p0 != p1  # hareket
    img = CAM.render("KAM", "3", "an", 5, 10, "09:35")
    det = CAM.detect(img)
    for view in ("video", "kutular", "isi", "hepsi"):
        assert CAM.overlay(img, det, view).size == img.size


def test_density_levels_follow_thresholds():
    th = {"sari": 0.6, "turuncu": 0.85, "kirmizi": 1.0}
    assert [level_of(x, th) for x in (0.2, 0.6, 0.9, 1.0)] == ["normal", "sari", "turuncu", "kirmizi"]


# ---- Dijital ikiz: veri, saat, yoğunluk ---------------------------------------------------------------------------


def test_twin_data_staff_and_hourly_matrix(engine):
    duty = engine.data.staff_on_duty(AT)
    assert duty and all(engine.data.staff[s["id"]]["kod"] == s["kod"] for s in duty)
    assert all(0 <= s["kullanilabilir_dk"] <= 60 for s in duty)
    rows = engine.data.hourly_matrix(engine.data.local(AT).date(), "nurse")
    assert len(rows) == 24 * len(engine.data.units)
    for r in rows:
        if r["etkin_kapasite_dk"]:
            assert r["oran"] == pytest.approx(r["talep_dk"] / r["etkin_kapasite_dk"], abs=1e-3)


def test_surge_raises_density_and_changes_map(engine):
    base = {d["birim"]: d for d in engine.twin.density(AT, 20, {})}
    surge = {d["birim"]: d for d in engine.twin.density(AT, 20, {3: 1.5})}
    assert surge[3]["gercek"] > base[3]["gercek"] and surge[3]["doluluk"] > base[3]["doluluk"]
    assert surge[1]["gercek"] == base[1]["gercek"]  # yalnız seçilen birim değişir


def test_clock_moves_window_and_staffing_with_moves(engine):
    a = engine.twin.people_target(3, AT, 0)
    b = engine.twin.people_target(3, AT + timedelta(minutes=15), 0)
    assert isinstance(a, int) and isinstance(b, int)
    st0 = engine.twin.staffing(AT)
    nurse = next(s for s in engine.data.staff_on_duty(AT) if s["birim"] == 1 and s["rol"] == "nurse" and not s["izinli"])
    st1 = engine.twin.staffing(AT, {nurse["id"]: 3})
    assert st1[3]["nurse"] == st0[3]["nurse"] + 1 and st1[1]["nurse"] == st0[1]["nurse"] - 1


def test_density_signal_only_above_capacity(engine):
    dens = [{"birim": 3, "tespit": 30, "kapasite": 22}, {"birim": 1, "tespit": 10, "kapasite": 14}]
    sig = engine.twin.density_signal(dens)
    assert sig == {3: 8 * engine.data.config["ek_talep_sinyali"]["kisi_basi_dk"]}


# ---- Tahmin + CP-SAT + doğrulayıcı -------------------------------------------------------------------------------


@needs_models
def test_forecast_and_density_aware_optimization_keep_verifier(engine):
    rows = engine.twin.forecast(AT)
    assert rows and {r["rol"] for r in rows} >= {"nurse"}
    base, dens = engine.twin.optimize(AT, {}), engine.twin.optimize(AT, {3: 60.0})
    for res in (base, dens):
        assert res["sonuc"]["dogrulama"]["gecti"] is True  # kısıtlar gevşetilmez; doğrulayıcı geçer
    assert dens["sinyal"] == {3: 60.0} and base["sinyal"] == {}
    rows30 = engine.twin.forecast(AT, 30)
    assert rows30 and rows30[0]["ufuk_dk"] == 30


@needs_models
def test_candidates_explain_elimination(engine):
    cands = engine.twin.candidates(AT, 3)
    assert any("rol uyumsuz" in c["gerekce"] for c in cands)
    assert any("geçerli yetkinlik yok: TRIAGE" in c["gerekce"] for c in cands)
    assert all(c["birim"] != "Acil Servis" for c in cands)


@needs_models
def test_whatif_checks_eligibility_and_source_safety(engine):
    bad = W.evaluate(engine.twin, AT, 20, {}, W.Scenario(move=("STAFF0020", 3)))
    assert bad["gercek_atama"] is False
    assert any(name.startswith("Rol, yetkinlik") and not ok for name, ok, _ in bad["kontroller"])
    plus = W.evaluate(engine.twin, AT, 20, {}, W.Scenario(extra={3: 1}))
    acil = next(r for r in plus["satirlar"] if r["birim"] == 3)
    assert acil["acik_sonra"] < acil["acik_once"]


# ---- Agent, onay, bildirim, denetim -------------------------------------------------------------------------------


def test_agent_has_no_approval_assignment_or_send_tool():
    banned = ("approve", "onay", "assign", "notify", "send", "telegram")
    assert not [n for n in T.TOOLS if any(b in n for b in banned) and n != "get_approval_status"]
    assert set(T.WRITE_TOOLS) == {"create_transfer_proposal", "create_staff_request"}


def test_agent_density_answer_uses_tools(engine, session):
    c = ctx(engine, session, {3: 1.5})
    r = answer("Acil serviste durum ne?", c)
    assert [x["arac"] for x in r.calls][:1] == ["get_density"] and all(x["sure_ms"] >= 0 for x in r.calls)
    d = next(x for x in T.get_density(c) if x["birim"] == 3)
    assert f"{d['tespit']}/{d['kapasite']}" in r.text


def test_agent_request_and_source_abstain(engine, session):
    r = answer("Acil için ek personel talebi oluştur", ctx(engine, session))
    assert session.request_list()[0]["durum"] == "beklemede" and "onaylayamaz" in r.text
    r2 = answer("Hastaya hangi ilaç verilmeli?", ctx(engine, session))
    assert "dayanak bulunamadı" in r2.text


@needs_models
def test_proposal_approval_notification_duplicate_and_audit(engine, session):
    r = answer("Acil servise personel öner", ctx(engine, session, {3: 1.5}))
    assert r.proposal and r.proposal["olusturuldu"], r.text
    pid = r.proposal["oneri"]
    sha = next(p["plan_sha256"] for p in session.proposals() if p["kimlik"] == pid)
    assert session.notify(pid, sha).status == REFUSED  # onaysız bildirim yok
    agent = session.review.open_session("demo_planlayici")
    assert not session.review.decide("onayla", agent, pid, sha).ok  # planlayıcı/agent onaylayamaz
    assert not session.decide("onayla", pid, "0" * 64).ok  # yanlış plan özeti
    assert session.decide("onayla", pid, sha).ok
    first = session.notify(pid, sha)
    assert first.status == DRY and first.message.startswith("MEDAKİS | PERSONEL YÖNLENDİRME") and "Personel: STAFF" in first.message
    assert session.notify(pid, sha).status == REFUSED  # mükerrer engeli
    assert session.approved_moves(AT)  # onaylı etki yalnız ikizde simüle edilir
    rows, issues = session.audit()
    assert not issues and {"oneri_olusturma", "onay_karari", "bildirim_denemesi"} <= {r.get("olay") for r in rows}
    assert verify_chain(session.ops.events()) == []


def test_source_search_cites_or_abstains():
    hit = search_sources("acil serviste triaj yetkinliği")
    assert hit["bulundu"] and hit["kaynaklar"][0]["alinti"] and hit["kaynaklar"][0]["surum"]
    assert not search_sources("kuantum fiziği roket yakıtı")["bulundu"]


def test_llm_output_must_be_grounded_in_tool_numbers():
    src = "Acil Servis: 18/22 kişi, açık 235 personel-dk"
    assert llm.grounded("Acil'de 18 kişi var, açık 235 dk.", src)
    assert not llm.grounded("Acil'de 40 kişi var.", src)


@needs_models
def test_dashboard_renders_default_page():
    at = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=240)
    at.run()
    assert not at.exception, at.exception
    assert any("Kontrol Merkezi" in m.value for m in at.markdown)
