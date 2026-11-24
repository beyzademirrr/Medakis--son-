"""Aşama 3: iş yükü ve kapasite servisi gerçek PostgreSQL'de (yalnız medakis_test): pytest -m postgres

Servis sonuçları, aynı kuralları yazılı haliyle izleyen BAĞIMSIZ SQL hesaplarıyla karşılaştırılır
(pencere dağıtımı generate_series ile, kapasite tstzmultirange ile). Servis medakis_app rolüyle ve
READ ONLY işlemle okur.
"""

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text

from medakis.repositories import seed_repo
from medakis.repositories.database import migration_engine, runtime_engine, seed_engine
from medakis.services.seed_service import SeedService
from medakis.services.workload_service import WorkloadService, read_only_factory
from medakis.synthetic.params import SyntheticParams

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
PARAMS = SyntheticParams(days=3)  # yerel 1–3 Ekim 2026
INPATIENT, OUTPATIENT = 1, 3
DAY, NIGHT, CLINIC = 1, 2, 3


def alembic(*args):
    proc = subprocess.run([sys.executable, "-m", "alembic", "-x", "db=medakis_test", *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.fixture(scope="module")
def engines():
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    engines = {"seed": seed_engine("medakis_test", allow_prompt=False), "owner": migration_engine("medakis_test", allow_prompt=False),
               "app": runtime_engine("medakis_app", "medakis_test")}  # fmt: skip
    SeedService("medakis_test", engine=engines["seed"], backup=lambda *a: None).apply(PARAMS)
    yield engines
    for engine in engines.values():
        engine.dispose()
    alembic("downgrade", "base")


@pytest.fixture(scope="module")
def service(engines):
    return WorkloadService(engine=engines["app"], timezone="Europe/Istanbul")


def oracle_windows(owner, unit_id, start, end):
    sql = text("""
        WITH w AS (
            SELECT gs AS ws, gs + interval '15 minutes' AS we
            FROM generate_series(CAST(:s AS timestamptz), CAST(:e AS timestamptz) - interval '15 minutes', interval '15 minutes') gs
        ), t AS (
            SELECT completed_at - actual_minutes * interval '1 minute' AS ts, completed_at AS te, actual_minutes AS m
            FROM care_tasks WHERE unit_id = :u AND status = 'completed'
        )
        SELECT w.ws,
               coalesce(sum(t.m * extract(epoch FROM least(t.te, w.we) - greatest(t.ts, w.ws)) / extract(epoch FROM t.te - t.ts)), 0)
        FROM w LEFT JOIN t ON t.te > w.ws AND t.ts < w.we
        GROUP BY w.ws ORDER BY w.ws
    """)  # fmt: skip
    with owner.connect() as conn:
        return [float(v) for _, v in conn.execute(sql, {"s": start, "e": end, "u": unit_id})]


def oracle_capacity(owner, unit_id, start, end):
    sql = text("""
        WITH plan AS (SELECT tstzrange(CAST(:s AS timestamptz), CAST(:e AS timestamptz), '[)') AS p),
        work AS (
            SELECT s.staff_id, range_agg(tstzrange(s.starts_at, s.ends_at, '[)') * plan.p) AS wm
            FROM staff_shifts s, plan WHERE s.unit_id = :u AND tstzrange(s.starts_at, s.ends_at, '[)') && plan.p
            GROUP BY s.staff_id
        ), un AS (
            SELECT staff_id, range_agg(tstzrange(starts_at, ends_at, '[)')) AS um FROM staff_unavailability GROUP BY staff_id
        )
        SELECT w.staff_id, st.role,
               (SELECT coalesce(sum(extract(epoch FROM upper(r) - lower(r))), 0) / 60
                FROM unnest(w.wm - coalesce(un.um, '{}'::tstzmultirange)) r)
        FROM work w JOIN staff st ON st.id = w.staff_id LEFT JOIN un USING (staff_id)
        ORDER BY w.staff_id
    """)  # fmt: skip
    with owner.connect() as conn:
        return {sid: (role, float(m)) for sid, role, m in conn.execute(sql, {"s": start, "e": end, "u": unit_id})}


@pytest.mark.parametrize(("local_day", "template"), [(date(2026, 10, 2), NIGHT), (date(2026, 10, 2), DAY), (date(2026, 10, 1), DAY)])
def test_observed_windows_match_independent_sql(service, engines, local_day, template):
    snap = service.unit_snapshot(INPATIENT, local_day, template)
    expected = oracle_windows(engines["owner"], INPATIENT, snap.shift.start, snap.shift.end)
    assert len(snap.windows) == len(expected) == 48
    for row, value in zip(snap.windows, expected):
        assert row.observed_minutes == pytest.approx(value, abs=1e-6), row.window
    assert snap.observed_total.value == pytest.approx(sum(expected), abs=1e-6)
    assert snap.observed_total.status == "ok"


@pytest.mark.parametrize(("local_day", "template"), [(date(2026, 10, 2), NIGHT), (date(2026, 10, 2), DAY)])
def test_capacity_matches_independent_multirange_sql(service, engines, local_day, template):
    snap = service.unit_snapshot(INPATIENT, local_day, template)
    expected = oracle_capacity(engines["owner"], INPATIENT, snap.shift.start, snap.shift.end)
    got = {row.staff_id: (row.role, row.net_minutes) for row in snap.capacity.staff}
    assert got.keys() == expected.keys() and got
    for sid, (role, minutes) in expected.items():
        assert got[sid][0] == role and got[sid][1] == pytest.approx(minutes, abs=1e-6), sid
    assert snap.capacity_total.value == pytest.approx(sum(m for _, m in expected.values()), abs=1e-6)
    nurse = sum(m for r, m in expected.values() if r == "nurse")
    assert snap.primary_role == "nurse" and snap.primary_role_capacity.value == pytest.approx(nurse, abs=1e-6)
    assert sum(snap.capacity_by_role.values()) == pytest.approx(snap.capacity_total.value)
    assert sum(w.capacity_minutes for w in snap.windows) == pytest.approx(snap.capacity_total.value, abs=1e-6)


def test_pending_and_patient_count_match_sql(service, engines):
    snap = service.unit_snapshot(INPATIENT, date(2026, 10, 2), NIGHT)
    at = snap.at
    with engines["owner"].connect() as conn:
        pending = conn.execute(text(
            "SELECT coalesce(sum(estimated_minutes), 0), count(*) FROM care_tasks WHERE unit_id = :u AND created_at <= :t "
            "AND (status = 'pending' OR (status = 'completed' AND completed_at > :t))"), {"u": INPATIENT, "t": at}).one()  # fmt: skip
        uncertain = conn.execute(text(
            "SELECT count(*) FROM care_tasks WHERE unit_id = :u AND created_at <= :t AND status = 'cancelled' AND due_at >= :t"),
            {"u": INPATIENT, "t": at}).scalar_one()  # fmt: skip
        patients = conn.execute(text(
            "SELECT count(*) FROM encounters WHERE unit_id = :u AND admitted_at <= :t AND (discharged_at IS NULL OR discharged_at > :t)"),
            {"u": INPATIENT, "t": at}).scalar_one()  # fmt: skip
    assert snap.pending.value == pytest.approx(float(pending[0])) and len(snap.pending_tasks) == pending[1]
    assert snap.quality.uncertain_cancelled == uncertain
    assert snap.patient_count.value == patients and snap.patient_count.status in ("ok", "zero")
    assert sum(snap.pending_by_role.values()) == pytest.approx(snap.pending.value)


def test_later_moment_in_shift_changes_pending_view(service):
    first = service.unit_snapshot(INPATIENT, date(2026, 10, 2), NIGHT)
    later = service.unit_snapshot(INPATIENT, date(2026, 10, 2), NIGHT, at=first.shift.start + timedelta(hours=4))
    assert later.at == first.shift.start + timedelta(hours=4)
    assert later.observed_total == first.observed_total  # vardiya toplamı değerlendirme anından bağımsız


# ---- Eksik veri ile gerçek sıfır --------------------------------------------------------------


def test_shift_outside_coverage_is_missing_not_zero(service):
    snap = service.unit_snapshot(INPATIENT, date(2026, 10, 10), NIGHT)
    assert snap.observed_total.status == "missing" and snap.observed_total.value is None
    assert snap.capacity_total.status == "missing" and snap.pending.status == "missing"
    assert snap.patient_count.value is None and all(w.observed_minutes is None for w in snap.windows)


def test_last_night_partly_outside_coverage_is_partial(service):
    snap = service.unit_snapshot(INPATIENT, date(2026, 10, 3), NIGHT)  # 3 Ekim 20:00 – 4 Ekim 08:00; kapsam 4 Ekim 00:00'da biter
    assert snap.observed_total.status == "partial"
    assert sum(1 for w in snap.windows if w.observed_minutes is None) == 32  # 00:00–08:00
    assert snap.capacity_total.status == "partial" and snap.notes


def test_no_scheduled_staff_is_a_real_zero_with_reason(service):
    snap = service.unit_snapshot(OUTPATIENT, date(2026, 10, 2), NIGHT)  # ayaktan klinikte gece vardiyası yok
    assert snap.capacity_total.status == "zero" and snap.capacity_total.value == 0.0
    assert "kayıtlı personel vardiyası yok" in snap.capacity_total.note
    assert snap.capacity is None


# ---- Yalnız okuma -----------------------------------------------------------------------------


def test_service_connection_is_read_only_at_database_level(engines):
    connect = read_only_factory(engines["app"])
    with connect() as conn:
        with pytest.raises(Exception) as info:
            conn.execute(text("INSERT INTO facilities (name) VALUES ('yazma denemesi')"))
        assert isinstance(info.value.orig, psycopg.errors.ReadOnlySqlTransaction)


def test_snapshot_changes_nothing(service, engines):
    with engines["owner"].connect() as conn:
        before = seed_repo.operational_counts(conn), conn.execute(text("SELECT count(*) FROM audit_events")).scalar_one()
    service.unit_snapshot(INPATIENT, date(2026, 10, 2), NIGHT)
    service.options()
    with engines["owner"].connect() as conn:
        after = seed_repo.operational_counts(conn), conn.execute(text("SELECT count(*) FROM audit_events")).scalar_one()
    assert before == after


def test_options_and_empty_database(service, engines):
    options = service.options()
    assert options.has_data and [u.id for u in options.units] == [1, 2, 3]
    assert options.coverage.source == "son seed kaydı" and options.coverage.seed["seed"] == 42
    with engines["seed"].begin() as conn:
        seed_repo.delete_operational(conn)  # medakis_test: seed kaydı kalır, operasyonel veri yok
    try:
        empty = service.options()
        assert not empty.has_data and empty.coverage.source == "veri yok" and empty.units == ()
    finally:
        SeedService("medakis_test", engine=engines["seed"], backup=lambda *a: None).apply(PARAMS)


# ---- Kısmi kapsam: kart, grafik ve tablolar tek kapsamı kullanır (bağımsız inceleme bulguları) ----


def test_last_night_cards_chart_and_tables_use_the_same_measured_scope(service, engines):
    snap = service.unit_snapshot(INPATIENT, date(2026, 10, 3), NIGHT)  # 20:00–08:00, kapsam 00:00'da biter
    assert snap.measured is not None and snap.measured.start == snap.shift.start
    assert snap.measured.end == snap.coverage.end and snap.measured.minutes() == 240
    visible = [w for w in snap.windows if w.capacity_minutes is not None]
    assert len(visible) == 16 and all(w.observed_minutes is not None for w in visible)
    assert all(w.capacity_minutes is None and w.primary_role_capacity is None for w in snap.windows[16:])

    card = snap.capacity_total.value
    assert snap.capacity_total.status == "partial" and "kalan saatler ölçülmedi" in snap.capacity_total.note
    assert card == pytest.approx(sum(w.capacity_minutes for w in visible), abs=1e-6)  # kart = grafik
    assert card == pytest.approx(sum(snap.capacity_by_role.values()), abs=1e-6)  # kart = rol tablosu
    assert card == pytest.approx(sum(r.net_minutes for r in snap.capacity.staff), abs=1e-6)  # kart = personel tablosu
    assert all(r.work_minutes <= 240 for r in snap.capacity.staff)  # kapsam dışı dakika yok
    expected = oracle_capacity(engines["owner"], INPATIENT, snap.measured.start, snap.measured.end)
    assert card == pytest.approx(sum(m for _, m in expected.values()), abs=1e-6)  # bağımsız SQL

    nurse = snap.primary_role_capacity
    assert nurse.status == "partial"
    assert nurse.value == pytest.approx(sum(w.primary_role_capacity for w in visible), abs=1e-6)
    assert nurse.value == pytest.approx(sum(m for r, m in expected.values() if r == "nurse"), abs=1e-6)

    assert snap.observed_total.value == pytest.approx(sum(w.observed_minutes for w in visible), abs=1e-6)

    # Tam vardiya planlı kapasitesi ayrı ve açık etiketli; ölçülen kapasiteye karışmaz.
    full = oracle_capacity(engines["owner"], INPATIENT, snap.shift.start, snap.shift.end)
    planned = snap.planned_full_shift_capacity
    assert planned is not None and "Ölçülmüş kapasite değildir" in planned.note
    assert planned.value == pytest.approx(sum(m for _, m in full.values()), abs=1e-6)
    assert planned.value > card


def test_fully_covered_shift_has_no_separate_planned_capacity(service):
    snap = service.unit_snapshot(INPATIENT, date(2026, 10, 2), NIGHT)
    assert snap.measured == snap.shift and snap.planned_full_shift_capacity is None
    assert snap.capacity_total.status == "ok"


def test_partial_shift_without_staff_is_partial_not_real_zero(service):
    snap = service.unit_snapshot(OUTPATIENT, date(2026, 10, 3), NIGHT)  # ayaktan klinikte gece vardiyası yok
    assert snap.capacity_total.status == "partial" and snap.capacity_total.value == 0.0
    assert "kalan saatler ölçülmedi" in snap.capacity_total.note
    assert "kayıtlı personel vardiyası yok" in snap.capacity_total.note
    assert snap.capacity is None


def test_shift_outside_coverage_with_shift_record_shows_no_capacity_tables(service, engines):
    shift = service.shift_interval(next(t for t in service.options().templates if t.id == NIGHT), date(2026, 10, 10))
    with engines["owner"].begin() as conn:  # yalnız medakis_test: kapsam dışında planlı vardiya kaydı
        staff_id = conn.execute(text("SELECT id FROM staff WHERE role = 'nurse' ORDER BY id LIMIT 1")).scalar_one()
        shift_id = conn.execute(text(
            "INSERT INTO staff_shifts (staff_id, unit_id, template_id, starts_at, ends_at, break_minutes) "
            "VALUES (:s, :u, :t, :a, :b, 0) RETURNING id"),
            {"s": staff_id, "u": INPATIENT, "t": NIGHT, "a": shift.start, "b": shift.end}).scalar_one()  # fmt: skip
    try:
        snap = service.unit_snapshot(INPATIENT, date(2026, 10, 10), NIGHT)
        assert snap.measured is None and snap.capacity is None and snap.capacity_by_role == {}
        assert snap.capacity_total.status == "missing" and snap.primary_role_capacity.status == "missing"
        assert snap.planned_full_shift_capacity is None
        assert all(w.capacity_minutes is None and w.observed_minutes is None for w in snap.windows)
    finally:
        with engines["owner"].begin() as conn:
            conn.execute(text("DELETE FROM staff_shifts WHERE id = :i"), {"i": shift_id})


def test_panel_with_real_service_shows_partial_scope_consistently(engines, monkeypatch):
    # Panel, gerçek servisten (medakis_test) üretilen kısmi son gece görünümüyle (varsayılan seçim) sınanır.
    from streamlit.testing.v1 import AppTest

    from medakis.ui import workload_panel

    service = WorkloadService(engine=engines["app"], timezone="Europe/Istanbul")
    monkeypatch.setattr(workload_panel, "service_provider", lambda: service)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    snap = service.unit_snapshot(INPATIENT, date(2026, 10, 3), NIGHT)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Kullanılabilir personel (vardiya)"] == workload_panel.metric_value(snap.capacity_total)
    assert metrics["Kullanılabilir personel (vardiya)"].endswith("(kısmi)")
    assert metrics["Hemşire kapasitesi (vardiya)"].endswith("(kısmi)")
    captions = " ".join(c.value for c in at.caption)
    assert "Kısmi kapsam" in captions and "16/48 pencere" in captions
    assert "Tam vardiya planlı kapasitesi" in captions and "ölçülmüş kapasite değildir" in captions

    outpatient = next(u for u in service.options().units if u.id == OUTPATIENT)
    at.selectbox(key="panel_unit").set_value(outpatient).run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Kullanılabilir personel (vardiya)"] == "0 dk (kısmi)"  # kesin "0 dk" değil
