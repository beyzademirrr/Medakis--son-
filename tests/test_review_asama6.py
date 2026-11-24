"""Aşama 6: öneri inceleme servisi ve Streamlit onay ekranı (veritabanı yok; test_sentetik fikstürler).

Ağır senaryo (üretici 2.1 + modeller) yerine U4 ve U6 test fikstürleriyle kurulan sahte senaryo kullanılır. Bütün
kararlar U7 kontrollerinden geçer; deneysel K1 hiçbir zaman çağrılmaz ve gösterilmez; sistem hiçbir planı uygulamaz.
"""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
import test_layer_a_u4 as UA
import test_layer_b_u6 as UB
from streamlit.testing.v1 import AppTest

from medakis.approvals import audit_log as AL
from medakis.approvals.plan_hash import KIND_A, KIND_B, SafetyInputs
from medakis.domain.planning import PlannedUnavailability
from medakis.optimization import layer_a as L
from medakis.services import review_scenario as RS
from medakis.services import review_service as RV
from medakis.ui import review_panel, workload_panel

T, M, H = UA.T, UA.M, UA.H
ROOT = Path(__file__).resolve().parents[1]


class FakeScenario:
    """İki karar anı: T ve T+15 dk. Katman A: U4 fikstürü; Katman B: U6 fikstürü (karar anına göre kurulur)."""

    timezone = UA.TZ

    def __init__(self):
        self.later_absence = None  # karar anından sonra bildirilen devamsızlık (güncel kaynakta görünür)
        self.later_task = False  # karar anından sonra gelen görev (Katman B güncel kaynağında görünür)
        self.b_inputs = {}

    def problems(self):
        return []

    def moments(self):
        return [{"an": T, "yerel": "27.10.2026 10:00", "katman_a_varsayilan": "OPTIMAL", "katman_a_yayin": True},
                {"an": T + 15 * M, "yerel": "27.10.2026 10:15", "katman_a_varsayilan": "OPTIMAL", "katman_a_yayin": True}]  # fmt: skip

    def _a(self, at, locks=()):
        return UA.build({UA.A: 30, UA.B: 120}, at=at, locks=list(locks))

    def u3_rows(self, at):
        return self._a(at).u3_rows

    def layer_a_inputs(self, at, rows, locks=()):
        return replace(self._a(at, locks), u3_rows=rows)

    def layer_b_inputs(self, at, rows):
        tasks = [UB.task(1, due=at + 50 * M, created=at - 30 * M), UB.task(2, 20.0, due=at + 55 * M, created=at - 30 * M)]
        inp = UB.build(tasks, at=at, start=at - 2 * H)
        self.b_inputs[at] = inp
        return inp

    def current_sources(self, kind, now, locks=()):
        if kind == KIND_A:
            base = self._a(T)
            unav = base.plan.unavailability + ((self.later_absence,) if self.later_absence else ())
            return SafetyInputs(replace(base.plan, unavailability=unav), tuple(base.skills), tuple(base.calendars), tuple(locks))
        inp = self.b_inputs[max(a for a in self.b_inputs if a <= now)]
        tasks = tuple(inp.tasks) + ((UB.task(9, due=inp.at + 40 * M, created=inp.at + M),) if self.later_task else ())
        return replace(SafetyInputs.from_layer_b(inp), tasks=tasks)


def make_service(journal=None, start=T):
    authz, policy = RV.demo_policies(UA.KNOWN)
    return RV.ReviewService(FakeScenario(), journal or AL.MemoryJournal(), authz, policy, RV.SimulationClock(start))


@pytest.fixture()
def svc():
    return make_service()


def registered(svc, kind=KIND_A, actor="demo_yonetici"):
    run = svc.evaluate(T)
    d = svc.register(svc.open_session(actor), run, kind)
    assert d.ok, d.codes
    return run, d.proposal_id, d.event["plan_sha256"]


# ---- Servis akışları ------------------------------------------------------------------------------------------


def test_evaluate_uses_default_policy_only(svc, monkeypatch):
    calls = []
    real = L.optimize

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(L, "optimize", spy)
    run = svc.evaluate(T)
    assert calls == [{}]  # politika ve deneysel mod verilmez: varsayılan küresel ret
    assert run.layer_a["dondurma_politikasi"] == L.FREEZE_OFF and run.layer_a["deneysel_mod"] is None
    assert run.layer_a["deneysel_sonuc"] is None and run.layer_a["oneri_yayinlanabilir"] and run.plan_sha_a
    assert run.layer_b["denetim_gosterilebilir"] and run.plan_sha_b


def test_experimental_result_can_never_reach_review(svc, monkeypatch):
    real = L.optimize
    monkeypatch.setattr(L, "optimize", lambda inp, **kw: real(inp, freeze_policy=L.FREEZE_TWO_WAY, experimental_mode="test"))
    with pytest.raises(AssertionError, match="deneysel_sonuc_inceleme_ekranina_ulasamaz"):
        svc.evaluate(T)


def test_full_flow_register_approve_lock_and_revoke(svc):
    run, pid, sha = registered(svc, actor="demo_planlayici")  # planlayıcı kaydeder
    planner = svc.open_session("demo_planlayici")
    refused = svc.decide("onayla", planner, pid, sha)
    assert not refused.ok and "yetkisiz:onayla:1" in refused.codes  # kaydeden onaylayamaz (yetkisi yok)
    manager = svc.open_session("demo_yonetici")
    d = svc.decide("onayla", manager, pid, sha, "uygun")
    assert d.ok and d.state == "onaylandi"
    locks = svc.approved_locks()
    assert [(k.staff_id, k.target_unit_id, k.approval_id) for k in locks] == [(m["personel"], m["hedef_servis"], pid) for m in run.layer_a["tasimalar"]]
    assert svc.applicability(pid) == (True, [])  # kendi kilidi kendi güncellik denetimine girmez
    svc.clock.at_least(T + 15 * M)
    nxt = svc.evaluate(T + 15 * M)
    assert nxt.layer_a_inputs.locks == locks and nxt.layer_a["kilitli_yerlesimler"]  # onaylı taşıma kilit olarak girer
    assert svc.decide("geri_al", manager, pid, sha, "yeniden inceleme").ok
    assert all(k.revoked_at is not None for k in svc.approved_locks())
    assert not svc.applicability(pid)[0]


def test_area_scoped_actor_cannot_approve_multi_unit_plan(svc):
    _, pid, sha = registered(svc)
    d = svc.decide("onayla", svc.open_session("demo_servis_A_sorumlusu"), pid, sha)
    assert not d.ok and any(c.startswith("yetkisiz:onayla:") for c in d.codes)


def test_stale_input_and_hash_and_reason_checks_preserved(svc):
    _, pid, sha = registered(svc)
    manager = svc.open_session("demo_yonetici")
    assert "plan_ozeti_uyusmuyor" in svc.decide("onayla", manager, pid, "f" * 64).codes
    assert "gerekce_zorunlu" in svc.decide("reddet", manager, pid, sha, " ").codes
    svc.scenario.later_absence = PlannedUnavailability(990, 11, T + 10 * M, T + 40 * M, "devamsizlik", T, "ani")
    d = svc.decide("onayla", manager, pid, sha)
    assert not d.ok and "oneri_eskimis:girdi_degisti:plan" in d.codes
    svc.scenario.later_absence = None
    svc.clock.advance(30)  # onay süresi (prototip: 30 dk) doldu
    assert "oneri_eskimis:sure_doldu" in svc.decide("onayla", manager, pid, sha).codes


def test_layer_b_check_flow_and_new_task_staleness(svc):
    _, pid, sha = registered(svc, kind=KIND_B)
    manager = svc.open_session("demo_yonetici")
    svc.scenario.later_task = True
    d = svc.decide("onayla", manager, pid, sha)
    assert not d.ok and "oneri_eskimis:girdi_degisti:gorev" in d.codes
    svc.scenario.later_task = False
    assert svc.decide("onayla", manager, pid, sha).ok
    assert svc.approved_locks() == ()  # Katman B onayı kilit üretmez (gerçek atama yok)


def test_cancel_and_double_approval(svc):
    _, pid, sha = registered(svc)
    manager = svc.open_session("demo_yonetici")
    assert svc.decide("onayla", manager, pid, sha).ok
    assert "zaten_onaylandi" in svc.decide("onayla", manager, pid, sha).codes
    _, pid_b, sha_b = registered(svc, kind=KIND_B)
    assert svc.decide("iptal_et", manager, pid_b, sha_b, "girdi değişti").ok
    assert "gecersiz_durum_gecisi:iptal_edildi->onaylandi" in svc.decide("onayla", manager, pid_b, sha_b).codes


def test_simulation_clock_only_moves_forward():
    clock = RV.SimulationClock(T)
    with pytest.raises(ValueError):
        clock.advance(0)
    assert clock.advance(5) == T + 5 * M and clock.at_least(T) == T + 5 * M and clock.at_least(T + H) == T + H


def test_history_rows_and_chain(svc):
    _, pid, sha = registered(svc)
    svc.decide("onayla", svc.open_session("demo_gozlemci"), pid, sha)
    rows, issues = svc.history()
    assert issues == [] and [r["eylem"] for r in rows] == ["oneri_kaydedildi", "islem_reddedildi"]
    assert rows[1]["denenen"] == "onayla" and "yetkisiz" in rows[1]["neden"]


def test_real_scenario_reports_missing_artifacts(tmp_path):
    scenario = RS.SyntheticReviewScenario(tmp_path)
    assert any(p.startswith("dosya_yok:artifacts/") for p in scenario.problems())
    with pytest.raises(RS.ScenarioUnavailable):
        scenario.moments()


def test_demo_policy_is_labelled_synthetic():
    authz, policy = RV.demo_policies(UA.KNOWN)
    assert authz.source.startswith("test_sentetik") and policy.source.startswith("test_sentetik")
    assert authz.grants["demo_gozlemci"] == frozenset() and all(a == "kaydet" for a, _ in authz.grants["demo_planlayici"])


# ---- Streamlit arayüz akışları ------------------------------------------------------------------------------


def _unavailable():
    raise ConnectionError("veritabanı kapalı")


def _app(monkeypatch, provider):
    monkeypatch.setattr(workload_panel, "service_provider", _unavailable)
    monkeypatch.setattr(review_panel, "service_provider", provider)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    at.button(key="senaryo_yukle").click().run()  # tembel yükleme
    assert not at.exception, at.exception
    return at


def _texts(at):
    return " ".join([m.value for m in at.markdown] + [m.value for m in at.info] + [m.value for m in at.caption] +
                    [e.value for e in at.error] + [s.value for s in at.success] + [w.value for w in at.warning])  # fmt: skip


def test_ui_tab_shows_notice_and_requires_session(monkeypatch):
    service = make_service()
    at = _app(monkeypatch, lambda: service)
    text = _texts(at)
    assert "Öneri inceleme ve onay" in [t.label for t in at.tabs]
    assert "Sistem hiçbir planı uygulamaz" in text and "Deneysel servis-rol dondurma (K1) sonuçları burada gösterilmez" in text
    at.button(key="degerlendir").click().run()
    assert not at.exception
    assert at.button(key="kaydet_a").disabled and at.button(key="kaydet_b").disabled  # oturum yok
    assert "Kayıt için oturum açın." in _texts(at)


def test_ui_full_approval_flow(monkeypatch):
    service = make_service()
    at = _app(monkeypatch, lambda: service)
    at.selectbox(key="aktor_secimi").select("demo_yonetici").run()
    at.button(key="oturum_ac").click().run()
    at.button(key="degerlendir").click().run()
    at.button(key="kaydet_a").click().run()
    assert any("İşlem kaydedildi" in s.value for s in at.success), _texts(at)
    pid = service.proposals()[0]["kimlik"]
    at.radio(key=f"eylem_{pid}").set_value("onayla").run()
    at.button(key=f"karar_gonder_{pid}").click().run()
    assert not at.exception and service.proposals()[0]["durum"] == "onaylandi", _texts(at)
    assert any("İşlem kaydedildi" in m.value for m in at.success)  # işlem sonrası yeniden çizim: güncel durum
    text = _texts(at)
    assert "Onay şu an geçerli." in text and "Özet zinciri doğrulandı (2 olay)" in text, text
    rows, issues = service.history()
    assert issues == [] and [r["eylem"] for r in rows] == ["oneri_kaydedildi", "onaylandi"]


def test_ui_refusal_is_shown_and_logged(monkeypatch):
    service = make_service()
    _, pid, _ = registered(service)
    at = _app(monkeypatch, lambda: service)
    at.selectbox(key="aktor_secimi").select("demo_gozlemci").run()
    at.button(key="oturum_ac").click().run()
    at.radio(key=f"eylem_{pid}").set_value("onayla").run()
    at.button(key=f"karar_gonder_{pid}").click().run()
    assert any("İşlem reddedildi" in e.value and "yetkisiz:onayla" in e.value for e in at.error), _texts(at)
    assert service.proposals()[0]["durum"] == "beklemede" and service.history()[0][-1]["eylem"] == "islem_reddedildi"


def test_ui_scenario_unavailable_is_safe(monkeypatch):
    def broken():
        raise RS.ScenarioUnavailable("dosya_yok:artifacts/forecasting/asama4_seed42/random_forest")

    at = _app(monkeypatch, broken)
    assert any("İnceleme senaryosu açılamadı" in e.value for e in at.error)


def test_ui_clock_buttons_move_forward(monkeypatch):
    service = make_service()
    at = _app(monkeypatch, lambda: service)
    at.button(key="saat_15").click().run()
    assert service.clock() == T + 15 * M


def test_ui_scenario_not_loaded_until_requested(monkeypatch):
    calls = []
    monkeypatch.setattr(workload_panel, "service_provider", _unavailable)
    monkeypatch.setattr(review_panel, "service_provider", lambda: calls.append(1) or make_service())
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception and calls == [] and not at.button(key="senaryo_yukle").disabled


def test_ui_package_does_not_import_solver_or_db():
    import ast

    src = (ROOT / "src" / "medakis" / "ui" / "review_panel.py").read_text(encoding="utf-8")
    mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
    assert not [m for m in mods if m.startswith(("medakis.optimization", "medakis.repositories", "ortools", "sqlalchemy", "medakis.rag"))]


def test_file_journal_default_location_is_gitignored():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/*" in text  # varsayılan iz data/onay_izi/ altında; sürüm kontrolüne girmez
