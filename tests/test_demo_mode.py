"""Aşama 6 demo modu: ana uygulama veritabanısız Sentetik Demo olarak açılır; Normal mod önceki davranışı korur.

Kanıt yöntemi: veritabanı zincirinin her halkası kayıt tutan casusla değiştirilir (Servis panelinin varsayılan servis
sağlayıcısı, `default_service`, `runtime_engine`, `create_engine`). Normal modda casusların tetiklendiği görülür
(casuslar etkin), demo modunda tam onay akışı boyunca hiçbiri çağrılmaz. Gerçek veritabanına hiçbir testte bağlanılmaz.
"""

import sys
from pathlib import Path

import pytest
import test_review_asama6 as R6
from streamlit.testing.v1 import AppTest

from medakis.repositories import database
from medakis.services import workload_service
from medakis.ui import home, review_panel, workload_panel
from medakis.ui import mode as M

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def db_calls(monkeypatch):
    calls = []

    def spy(name):
        def fn(*args, **kwargs):
            calls.append(name)
            raise ConnectionError(f"test: veritabanı yok ({name})")

        return fn

    for target, attr in ((workload_panel, "default_service"), (workload_service, "default_service"), (database, "runtime_engine"),
                         (database, "create_engine"), (database, "_make_engine")):  # fmt: skip
        monkeypatch.setattr(target, attr, spy(f"{target.__name__}.{attr}"))
    workload_panel._default_service.clear()  # önbellekte servis kalmasın
    monkeypatch.setattr(workload_panel, "service_provider", workload_panel._default_service)  # GERÇEK varsayılan sağlayıcı
    monkeypatch.delenv("MEDAKIS_MODE", raising=False)
    monkeypatch.setattr(sys, "argv", ["streamlit"])
    return calls


def _app(monkeypatch, provider=None, query=None):
    monkeypatch.setattr(review_panel, "service_provider", provider or (lambda: R6.make_service()))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    for k, v in (query or {}).items():
        at.query_params[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def _infos(at):
    return " ".join(i.value for i in at.info)


# ---- Mod çözümleme -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("selected,query,env,argv,expected", [
    (None, {}, {}, (), (M.NORMAL, "varsayilan")),
    (None, {}, {}, ("app.py", "--demo"), (M.DEMO, "baslatma_bayragi")),
    (None, {}, {"MEDAKIS_MODE": "demo"}, (), (M.DEMO, "ortam")),
    (None, {"mod": "demo"}, {"MEDAKIS_MODE": "normal"}, (), (M.DEMO, "url")),
    (None, {"mod": ["normal", "demo"]}, {}, (), (M.DEMO, "url")),
    (M.NORMAL, {"mod": "demo"}, {}, ("--demo",), (M.NORMAL, "kenar_cubugu")),  # açık seçim kazanır
    (None, {"mod": "bilinmeyen"}, {"MEDAKIS_MODE": "xyz"}, (), (M.NORMAL, "varsayilan")),
    (None, {"mod": " Sentetik_Demo "}, {}, (), (M.DEMO, "url")),
])  # fmt: skip
def test_resolve_mode(selected, query, env, argv, expected):
    assert M.resolve_mode(selected, query, env, argv) == expected


# ---- Demo modu: veritabanı zinciri hiç çağrılmaz ---------------------------------------------------------------


def test_demo_mode_via_env_never_touches_database_through_full_review_flow(db_calls, monkeypatch):
    monkeypatch.setenv("MEDAKIS_MODE", "demo")
    service = R6.make_service()
    at = _app(monkeypatch, lambda: service)
    assert at.sidebar.radio(key="calisma_modu").value == M.DEMO
    assert "Sentetik Demo Modu" in _infos(at) and "test_sentetik" in _infos(at) and "otomatik uygulama yapılmaz" in _infos(at)
    assert "Servis paneli veritabanlı (Aşama 3) bir ekrandır" in _infos(at) and not at.metric  # panel çizilmedi
    at.button(key="senaryo_yukle").click().run()
    at.selectbox(key="aktor_secimi").select("demo_yonetici").run()
    at.button(key="oturum_ac").click().run()
    at.button(key="degerlendir").click().run()
    at.button(key="kaydet_a").click().run()
    pid = service.proposals()[0]["kimlik"]
    at.radio(key=f"eylem_{pid}").set_value("onayla").run()
    at.button(key=f"karar_gonder_{pid}").click().run()
    assert not at.exception and service.proposals()[0]["durum"] == "onaylandi"
    assert [r["eylem"] for r in service.history()[0]] == ["oneri_kaydedildi", "onaylandi"]
    assert db_calls == []  # bütün akış boyunca veritabanı servisi, repository ya da bağlantı fabrikası çağrılmadı


def test_demo_mode_via_url_and_launch_flag(db_calls, monkeypatch):
    at = _app(monkeypatch, query={"mod": "demo"})
    assert at.sidebar.radio(key="calisma_modu").value == M.DEMO and db_calls == []
    monkeypatch.setattr(sys, "argv", ["app.py", "--demo"])
    at2 = _app(monkeypatch)
    assert at2.sidebar.radio(key="calisma_modu").value == M.DEMO and db_calls == []
    assert "Mod kaynağı: baslatma_bayragi" in " ".join(c.value for c in at2.sidebar.caption)


def test_switching_from_normal_to_demo_stops_database_calls(db_calls, monkeypatch):
    at = _app(monkeypatch)  # varsayılan: Normal
    assert db_calls  # Normal mod veritabanı servisini dener (önceki davranış) — casuslar etkin
    before = len(db_calls)
    at.sidebar.radio(key="calisma_modu").set_value(M.DEMO).run()
    at.run()
    assert not at.exception and len(db_calls) == before and "Sentetik Demo Modu" in _infos(at)


def test_demo_review_works_without_scenario_files_safely(db_calls, monkeypatch):
    monkeypatch.setenv("MEDAKIS_MODE", "demo")

    def broken():
        raise R6.RS.ScenarioUnavailable("dosya_yok:artifacts/forecasting/asama4_seed42/random_forest")

    at = _app(monkeypatch, broken)
    at.button(key="senaryo_yukle").click().run()
    assert any("İnceleme senaryosu açılamadı" in e.value for e in at.error) and db_calls == []


# ---- Normal mod: önceki davranış korunur -------------------------------------------------------------------


def test_normal_mode_default_keeps_database_panel_behaviour(db_calls, monkeypatch):
    at = _app(monkeypatch)
    assert at.sidebar.radio(key="calisma_modu").value == M.NORMAL
    assert db_calls == ["medakis.ui.workload_panel.default_service"]  # panel önceki gibi veritabanı servisini çağırdı
    assert "veritabanına bağlanılmaz; Servis paneli bu modda kapalıdır" not in _infos(at)  # demo uyarısı yok
    assert [t.label for t in at.tabs] == ["Genel bakış", "Servis paneli", "Öneri inceleme ve onay", "Mimari ve aşamalar", "Yapılandırma"]
    assert any("Veritabanına bağlanılamadı" in e.value for e in at.error)  # önceki hata mesajı aynen


def test_normal_mode_with_fake_service_still_renders_panel(monkeypatch):
    import test_app_smoke as SM

    monkeypatch.delenv("MEDAKIS_MODE", raising=False)
    monkeypatch.setattr(sys, "argv", ["streamlit"])
    monkeypatch.setattr(workload_panel, "service_provider", lambda: SM.FakeService())
    at = _app(monkeypatch)
    assert at.metric  # veritabanlı panel (sahte servisle) önceki gibi çizildi
    at.button(key="senaryo_yukle").click().run()  # inceleme ekranı normal modda da çalışır
    assert not at.exception and at.button(key="degerlendir")


def test_demo_mode_module_has_no_database_imports():
    import ast

    for name in ("mode.py", "review_panel.py"):
        src = (ROOT / "src" / "medakis" / "ui" / name).read_text(encoding="utf-8")
        mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
        assert not [m for m in mods if m.startswith(("medakis.repositories", "sqlalchemy", "psycopg", "medakis.services.workload"))], name


def test_home_mentions_demo_launch_command():
    assert "--demo" in Path(home.__file__).read_text(encoding="utf-8")


# ---- Teslim: uçtan uca demo akışı, eksik model dosyaları, güncel aşama metinleri -------------------------------


def _history_events(at):
    frames = [d.value for d in at.dataframe if "eylem" in getattr(d.value, "columns", [])]
    return list(frames[-1]["eylem"]) if frames else []


def _submit(at, pid, action, reason=""):
    """Form alanları gerçek kullanıcı gibi doldurulur ve TEK gönderimle iletilir (form içi değerler ara çalıştırmada gönderilmez)."""
    at.radio(key=f"eylem_{pid}").set_value(action)
    at.text_input(key=f"gerekce_{pid}").input(reason)
    at.button(key=f"karar_gonder_{pid}").click().run()


def _full_demo_flow(at, service, moment_label=None):
    at.button(key="senaryo_yukle").click().run()
    assert not at.exception, at.exception
    at.selectbox(key="aktor_secimi").select("demo_yonetici").run()
    at.button(key="oturum_ac").click().run()
    if moment_label is not None:
        at.selectbox(key="an_secimi").select(moment_label).run()
    at.button(key="degerlendir").click().run()
    at.button(key="kaydet_a").click().run()
    pid = service.proposals()[0]["kimlik"]
    _submit(at, pid, "onayla")
    assert "Onay şu an geçerli." in " ".join(s.value for s in at.success)
    _submit(at, pid, "geri_al", "sunum: yeniden inceleme")
    assert not at.exception, at.exception
    return pid


def test_demo_end_to_end_open_evaluate_register_approve_revoke_history(db_calls, monkeypatch):
    monkeypatch.setenv("MEDAKIS_MODE", "demo")
    service = R6.make_service()
    at = _app(monkeypatch, lambda: service)
    pid = _full_demo_flow(at, service)
    assert service.proposals()[0]["durum"] == "geri_alindi"
    assert _history_events(at) == ["oneri_kaydedildi", "onaylandi", "geri_alindi"]
    assert "Özet zinciri doğrulandı (3 olay)" in " ".join(c.value for c in at.caption)
    assert not service.applicability(pid)[0] and service.approved_locks()[0].revoked_at is not None
    assert db_calls == []


def _real_service(root):
    from medakis.approvals.audit_log import MemoryJournal
    from medakis.services.review_scenario import SyntheticReviewScenario
    from medakis.services.review_service import ReviewService, SimulationClock, demo_policies

    scenario = SyntheticReviewScenario(root)
    authz, policy = demo_policies(R6.UA.KNOWN)
    start = min(m["an"] for m in scenario.moments()) if not scenario.problems() else R6.T
    return ReviewService(scenario, MemoryJournal(), authz, policy, SimulationClock(start))


def test_missing_model_files_show_error_and_guidance_without_fake_output(db_calls, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDAKIS_MODE", "demo")
    service = _real_service(tmp_path)  # boş kök: model dosyası ve karar anı listesi yok
    at = _app(monkeypatch, lambda: service)
    at.button(key="senaryo_yukle").click().run()
    errors = " ".join(e.value for e in at.error)
    assert "İnceleme senaryosu açılamadı" in errors and "dosya_yok:artifacts/forecasting/asama4_seed42/random_forest" in errors
    assert any("asama4_egit.py" in c.value and "asama5_u1_kalibrasyon.py" in c.value for c in at.code)
    assert not [b for b in at.button if b.key == "degerlendir"] and db_calls == []  # değerlendirme yok, sahte çıktı yok


def test_corrupt_model_files_reported_explicitly(tmp_path):
    from medakis.services import review_scenario as RS

    for rel in RS.ARTIFACTS.values():
        target = tmp_path / rel
        if target.suffix:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}", encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)  # boş model klasörü
    (tmp_path / RS.MOMENTS_FILE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / RS.MOMENTS_FILE).write_text('{"calistirmalar": []}', encoding="utf-8")
    scenario = RS.SyntheticReviewScenario(tmp_path)
    assert scenario.problems() == []
    with pytest.raises(RS.ScenarioUnavailable) as err:
        scenario.preflight()
    assert err.value.codes[0].startswith("model_yuklenemedi:")


ARTIFACTS_PRESENT = all((ROOT / p).exists() for p in ("artifacts/forecasting/asama4_seed42/random_forest",
                                                      "artifacts/forecasting/asama5_u1/baseline_bilgi",
                                                      "artifacts/forecasting/asama5_u1/kalibrasyon.json"))  # fmt: skip


@pytest.mark.skipif(not ARTIFACTS_PRESENT, reason="model dosyaları (artifacts/, Git dışı) bu ortamda yok")
def test_real_scenario_end_to_end_in_demo_mode(db_calls, monkeypatch):
    """Gerçek sentetik senaryo (üretici 2.1 + RF + U1): açılış → değerlendirme → kayıt → onay → geri alma → geçmiş."""
    monkeypatch.setenv("MEDAKIS_MODE", "demo")
    service = _real_service(ROOT)
    monkeypatch.setattr(review_panel, "service_provider", lambda: service)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=600)
    at.run()
    assert not at.exception, at.exception
    pid = _full_demo_flow(at, service, "2027-01-19 09:30 · Katman A (varsayılan): OPTIMAL")
    snap = service.proposal(pid)["anlik_goruntu"]
    assert snap["karar_ani"].startswith("2027-01-19T06:30") and snap["model_surumleri"]
    assert {m["model"] for m in snap["model_surumleri"]} == {"random_forest", "baseline_bilgi"}
    assert _history_events(at) == ["oneri_kaydedildi", "onaylandi", "geri_alindi"] and db_calls == []


def test_stage_table_and_overview_are_current(monkeypatch, db_calls):
    from medakis.services import system_info

    status = {row["Aşama"]: row["Durum"] for row in system_info.stage_rows()}
    assert all(status[str(n)] == "Tamamlandı (prototip)" for n in range(1, 7)) and status["7"] == "Planlandı"
    monkeypatch.setenv("MEDAKIS_MODE", "demo")
    text = _infos(_app(monkeypatch))
    for phrase in ("Aşama 4 — Tahmin", "Aşama 5 — Optimizasyon", "Aşama 6 — İnsan onayı", "Sistem hiçbir planı uygulamaz"):
        assert phrase in text
    assert "Aşama 4'te eklenecek" not in text
