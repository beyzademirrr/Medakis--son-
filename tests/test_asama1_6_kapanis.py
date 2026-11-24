"""Aşama 1–6 kapanış: görev/personel ekranlarında salt okunur yetkinlik, bakım gereksinimi ve uygunsuzluk aralıkları;
çözücü süre sınırının merkezi yapılandırmaya bağlanması.

Veritabanı yok: Normal mod sorguları sahte bağlantıyla (yalnız SELECT olduğu, filtreleri) ve panel sahte anlık
görüntüyle sınanır. Demo modu U4/U6 fikstürlü sahte senaryo ile; gerçek senaryo yalnız model dosyaları varsa
(altın plan SHA-256 özetleri değişmemeli).
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import test_app_smoke as S
import test_review_asama6 as R6
from sqlalchemy.dialects import postgresql
from streamlit.testing.v1 import AppTest

from medakis.config import load_settings
from medakis.domain.planning import PlannedUnavailability
from medakis.optimization.layer_a_contracts import StaffSkill
from medakis.repositories import workload_repo
from medakis.services import review_scenario as RS
from medakis.ui import review_panel, workload_panel

ROOT = Path(__file__).resolve().parents[1]
T, M = R6.T, R6.M


# ---- Normal mod: yalnız SELECT sorguları ----------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _Conn:
    """Sorguyu kaydeden sahte bağlantı; yazma komutu gelirse test başarısız olur."""

    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, stmt):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        self.sql.append(sql)
        return _Result(self.rows)


def _read_only(sql: str) -> bool:
    upper = sql.upper()
    return upper.lstrip().startswith("SELECT") and not any(w in upper for w in ("INSERT", "UPDATE ", "DELETE", "TRUNCATE"))


def test_repo_skill_query_is_select_and_filters_validity_at_moment():
    at = datetime(2026, 10, 4, 18, tzinfo=UTC)
    conn = _Conn([{"staff_id": 7, "name": "Monitörizasyon"}, {"staff_id": 7, "name": "Monitörizasyon"}, {"staff_id": 8, "name": "Triyaj"}])
    out = workload_repo.skills_valid_at(conn, [7, 8], at)
    assert out == {7: ["Monitörizasyon"], 8: ["Triyaj"]}  # aynı yetkinliğin ardışık geçerlilik kayıtları tek gösterilir
    sql = conn.sql[0]
    assert _read_only(sql) and "staff_skills.valid_from <=" in sql and "staff_skills.valid_until IS NULL" in sql
    assert "staff_skills.valid_until >" in sql
    assert workload_repo.skills_valid_at(_Conn([]), [], at) == {}  # personel yoksa sorgu yok


def test_repo_acuity_uses_only_assessments_known_at_moment_and_keeps_latest():
    at = datetime(2026, 10, 4, 18, tzinfo=UTC)
    rows = [{"encounter_id": 5, "assessed_at": at - timedelta(hours=5), "level": 2, "multiplier": 1.1},
            {"encounter_id": 5, "assessed_at": at - timedelta(hours=1), "level": 4, "multiplier": 1.6}]  # fmt: skip
    conn = _Conn(rows)
    out = workload_repo.latest_acuity_at(conn, [5], at)
    assert out[5]["level"] == 4 and out[5]["multiplier"] == 1.6  # sıralı sonuç: en son değerlendirme
    sql = conn.sql[0]
    assert _read_only(sql) and "acuity_assessments.assessed_at <=" in sql and "ORDER BY" in sql
    assert workload_repo.latest_acuity_at(_Conn([]), [], at) == {}


def test_repo_task_skill_requirements_query_is_select():
    conn = _Conn([{"task_type_id": 3, "name": "Monitörizasyon"}, {"task_type_id": 3, "name": "İlaç"}])
    assert workload_repo.task_type_skill_names(conn) == {3: ["Monitörizasyon", "İlaç"]}
    assert _read_only(conn.sql[0]) and "task_skill_requirements" in conn.sql[0]


# ---- Normal mod: panel ------------------------------------------------------------------------------------------


def _frames(at):
    return [d.value for d in at.dataframe]


def _frame_with(at, column):
    return next(f for f in _frames(at) if column in f.columns)


def test_normal_panel_shows_skills_acuity_and_unavailability(project_root, monkeypatch):
    base = S.snapshot()
    task = {**base.pending_tasks[0], "required_skills": ("Monitörizasyon",), "acuity_level": 4, "acuity_multiplier": 1.6,
            "acuity_assessed_at": base.shift.start - timedelta(hours=1)}  # fmt: skip
    unav = {"staff_id": 7, "reason": "egitim", "starts_at": base.shift.start + timedelta(hours=2), "ends_at": base.shift.start + timedelta(hours=3)}
    snap = replace(base, pending_tasks=(task,), staff_skills={7: ("Monitörizasyon", "İlaç")}, unavailability_rows=(unav,))
    at = S._run(project_root, monkeypatch, lambda: S.FakeService(snap))
    assert not at.exception, at.exception
    staff = _frame_with(at, "Yetkinlik (seçilen an)").set_index("Personel")
    assert staff.loc["STAFF007", "Yetkinlik (seçilen an)"] == "Monitörizasyon, İlaç"
    assert staff.loc["STAFF008", "Yetkinlik (seçilen an)"] == "—"  # kayıt yok: uydurulmaz
    tasks = _frame_with(at, "Bakım düzeyi")
    assert tasks.loc[0, "Gerekli yetkinlik"] == "Monitörizasyon" and tasks.loc[0, "Bakım düzeyi"] == "4"
    assert tasks.loc[0, "Bakım katsayısı"] == "1.60"
    windows = _frame_with(at, "Neden")
    assert windows.loc[0, "Personel"] == "STAFF007" and windows.loc[0, "Neden"] == "egitim"
    assert "Uygunsuzluk aralıkları (vardiyayla kesişen kayıtlar, salt okunur)" in [e.label for e in at.expander]


def test_normal_panel_marks_missing_fields_without_inventing(project_root, monkeypatch):
    at = S._run(project_root, monkeypatch, lambda: S.FakeService())  # eski biçimli satır: yeni alanlar yok
    assert not at.exception, at.exception
    tasks = _frame_with(at, "Bakım düzeyi")
    assert (tasks.loc[0, "Gerekli yetkinlik"], tasks.loc[0, "Bakım düzeyi"], tasks.loc[0, "Bakım katsayısı"]) == ("—", "—", "—")
    assert "Bu vardiyada kayıtlı uygunsuzluk yok." in " ".join(c.value for c in at.caption)


# ---- Sentetik Demo: senaryo verisi --------------------------------------------------------------------------------


@pytest.fixture()
def svc():
    return R6.make_service()


def test_demo_view_mirrors_run_inputs_and_window(svc):
    run = svc.evaluate(T)
    view = svc.people_and_tasks(run)
    inp = run.layer_b_inputs
    assert view["pencere"].start == T and view["pencere"].end == T + 60 * M  # çözücülerle aynı pencere
    assert [r["gorev"] for r in view["gorevler"]] == [t.task_id for t in inp.tasks]
    assert [r["gerekli_yetkinlik"] for r in view["gorevler"]] == [list(t.required_skills) for t in inp.tasks]
    valid = {}
    for sk in inp.skills:
        if sk.valid_from <= T and (sk.valid_until is None or sk.valid_until > T):
            valid.setdefault(inp.staff[sk.staff_id].synthetic_code, set()).add(sk.skill)
    assert view["personel"] and all(set(r["yetkinlikler"]) == valid.get(r["personel"], set()) for r in view["personel"])


def test_demo_view_does_not_invent_acuity_when_scenario_lacks_it(svc):
    view = svc.people_and_tasks(svc.evaluate(T))
    assert all(r["bakim_duzeyi"] is None and r["bakim_katsayisi"] is None for r in view["gorevler"])
    assert any("bakım değerlendirmesi sağlamıyor" in n for n in view["notlar"])


def test_demo_view_uses_scenario_acuity_and_reports_gaps(svc):
    run = svc.evaluate(T)
    first = run.layer_b_inputs.tasks[0].task_id
    svc.scenario.acuity_known_at = lambda ids, at: {first: {"duzey": 3, "katsayi": 1.3, "degerlendirme_ani": at - M}}
    view = svc.people_and_tasks(run)
    rows = {r["gorev"]: r for r in view["gorevler"]}
    assert rows[first]["bakim_duzeyi"] == 3 and rows[first]["bakim_katsayisi"] == 1.3
    assert all(r["bakim_duzeyi"] is None for tid, r in rows.items() if tid != first)
    assert any("bilinen bakım değerlendirmesi yok" in n for n in view["notlar"])


def test_demo_view_hides_records_not_yet_known_and_outside_window(svc):
    run = svc.evaluate(T)
    inp = run.layer_b_inputs
    sid = next(iter(sorted(inp.staff)))
    later = PlannedUnavailability(901, sid, T + 10 * M, T + 40 * M, "devamsizlik", T + 5 * M, "ani")  # sonradan bildirildi
    known = PlannedUnavailability(902, sid, T + 20 * M, T + 30 * M, "egitim", T - 60 * M, "planli")
    outside = PlannedUnavailability(903, sid, T + 120 * M, T + 150 * M, "izin", T - 60 * M, "planli")
    plan = replace(inp.plan, unavailability=tuple(inp.plan.unavailability) + (later, known, outside))
    view = svc.people_and_tasks(replace(run, layer_b_inputs=replace(inp, plan=plan)))
    reasons = [(u["neden"], u["baslangic"]) for u in view["uygunsuzluk"]]
    assert ("egitim", T + 20 * M) in reasons
    assert ("devamsizlik", T + 10 * M) not in reasons and ("izin", T + 120 * M) not in reasons


def test_real_scenario_acuity_lookup_uses_only_known_assessments(monkeypatch):
    scenario = RS.SyntheticReviewScenario(ROOT)
    at = datetime(2027, 1, 19, 6, 30, tzinfo=UTC)
    tables = {"care_tasks": [{"id": 1, "encounter_id": 10}, {"id": 2, "encounter_id": 11}],
              "acuity_assessments": [{"encounter_id": 10, "assessed_at": at - 60 * M, "level": 2, "multiplier": 1.1},
                                     {"encounter_id": 10, "assessed_at": at - 5 * M, "level": 4, "multiplier": 1.6},
                                     {"encounter_id": 10, "assessed_at": at + 5 * M, "level": 5, "multiplier": 2.0},
                                     {"encounter_id": 11, "assessed_at": at + 5 * M, "level": 1, "multiplier": 0.9}]}  # fmt: skip
    scenario.__dict__["_bundle"] = {"base": type("B", (), {"tables": tables})()}
    out = scenario.acuity_known_at([1, 2], at)
    assert out == {1: {"duzey": 4, "katsayi": 1.6, "degerlendirme_ani": at - 5 * M}}  # gelecek kayıt yok; 2 boş kalır


def test_demo_ui_shows_people_and_tasks_expander(monkeypatch):
    service = R6.make_service()
    monkeypatch.setenv("MEDAKIS_MODE", "demo")
    monkeypatch.setattr(workload_panel, "service_provider", R6._unavailable)
    monkeypatch.setattr(review_panel, "service_provider", lambda: service)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.button(key="senaryo_yukle").click().run()
    at.selectbox(key="aktor_secimi").select("demo_yonetici").run()
    at.button(key="oturum_ac").click().run()
    at.button(key="degerlendir").click().run()
    assert not at.exception, at.exception
    assert "Personel ve görev ayrıntıları (karar anında bilinen, salt okunur)" in [e.label for e in at.expander]
    assert {"Personel yetkinlikleri", "Uygunsuzluk aralıkları", "Bekleyen görevler"} <= {t.label for t in at.tabs}
    tasks = _frame_with(at, "Bakım katsayısı")
    assert set(tasks["Bakım düzeyi"]) == {"—"}  # sahte senaryoda değerlendirme yok: uydurulmaz


# ---- Çözücü süre sınırı --------------------------------------------------------------------------------------------


def test_scenario_time_limit_comes_from_central_config(tmp_path, monkeypatch):
    for name in ("PGHOST", "PGPORT", "PGDATABASE", "MEDAKIS_RAG_PROFILE"):
        monkeypatch.setenv(name, "gecersiz")  # ortam değişkenleri süre sınırını etkilemez, senaryoyu bozmaz
    scenario = RS.SyntheticReviewScenario(tmp_path)
    assert scenario.solver_time_limit_s == load_settings(env={}).solver_time_limit_seconds == 10.0
    assert RS.SyntheticReviewScenario(tmp_path, 4.0).solver_time_limit_s == 4.0  # açık değer korunur


def test_no_hard_coded_limit_left_in_scenario():
    text = (ROOT / "src/medakis/services/review_scenario.py").read_text(encoding="utf-8")
    assert "10.0" not in text.replace("değer 10.0 kaldıkça", "")


@pytest.mark.parametrize("script", ["asama5_guvenli_degerlendirme.py", "asama5_u4_ornek.py", "asama5_u5_degerlendirme.py",
                                    "asama5_u6_degerlendirme.py"])  # fmt: skip
def test_experiment_scripts_keep_their_explicit_limit(script):
    text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert "10.0, src" in text or "10.0, SRC" in text or "LayerBConfig(10.0" in text  # deneyler kendi açık sınırını korur


ARTIFACTS_PRESENT = all((ROOT / p).exists() for p in RS.ARTIFACTS.values()) and (ROOT / RS.MOMENTS_FILE).exists()


@pytest.mark.skipif(not ARTIFACTS_PRESENT, reason="model dosyaları (artifacts/, Git dışı) bu ortamda yok")
def test_real_scenario_plan_hashes_unchanged_with_config_limit():
    """Altın plan özetleri: A 51e1e11b60f6bbd3…, B 31f0d653a70ad9df… (Aşama 6 ve son uçtan uca doğrulama)."""
    from medakis.approvals.audit_log import MemoryJournal
    from medakis.services.review_service import ReviewService, SimulationClock, demo_policies

    scenario = RS.SyntheticReviewScenario(ROOT)
    assert scenario.solver_time_limit_s == 10.0
    authz, policy = demo_policies(R6.UA.KNOWN)
    at = next(m["an"] for m in scenario.moments() if m["an"] == datetime(2027, 1, 19, 6, 30, tzinfo=UTC))
    service = ReviewService(scenario, MemoryJournal(), authz, policy, SimulationClock(at))
    run = service.evaluate(at)
    assert run.layer_a_inputs.config.time_limit_s == run.layer_b_inputs.config.time_limit_s == 10.0
    assert run.plan_sha_a.startswith("51e1e11b60f6bbd3") and run.plan_sha_b.startswith("31f0d653a70ad9df")
    view = service.people_and_tasks(run)
    assert len(view["gorevler"]) == len(run.layer_b_inputs.tasks) and view["personel"]
    known = [r for r in view["gorevler"] if r["bakim_duzeyi"] is not None]
    assert known and all(r["bakim_degerlendirme_ani"] <= at for r in known)
    assert all(1 <= r["bakim_duzeyi"] <= 5 for r in known)
