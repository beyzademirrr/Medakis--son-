"""K3: bekleyen iş hesabında iptal zamanı (revizyon 0004 / üretici 2.0.0) ve 0003 geriye dönük uyumu.

Veritabanına bağlanılmaz. Repository sorgusu SQL'e derlenerek, domain zaman semantiği doğrudan sınanır.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from medakis.domain.models import CareTask
from medakis.domain.workload import pending_at
from medakis.forecasting.dataset import care_task, known_view
from medakis.repositories import workload_repo
from medakis.synthetic.generator import generate as generate_v1
from medakis.synthetic.v2 import ParamsV2, generate_v2

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def task(i, status, created=-60, due=30, done=None, cancelled=None, est=10.0):
    completed = at(done) if done is not None else None
    actual = est if status == "completed" else None
    return CareTask(i, 1, 1, 1, at(created), at(due), est, actual, status, completed,
                    at(cancelled) if cancelled is not None else None)  # fmt: skip


def legacy_pending(tasks, moment):
    """G4'e kadar kullanılan davranış (iptal zamanı yokken): karşılaştırma referansı."""
    pending, uncertain = [], []
    for t in tasks:
        if t.created_at > moment:
            continue
        if t.status == "cancelled":
            if t.due_at >= moment:
                uncertain.append(t)
            continue
        if t.status == "pending" or (t.completed_at is not None and t.completed_at > moment):
            pending.append(t)
    return sorted(x.id for x in pending), sorted(x.id for x in uncertain)


# ---- Domain zaman semantiği ----------------------------------------------------------------


def test_cancellation_after_moment_keeps_task_pending():
    result = pending_at([task(1, "cancelled", cancelled=20)], at(0))
    assert [t.id for t in result.tasks] == [1] and result.minutes == 10 and result.uncertain_cancelled == ()


@pytest.mark.parametrize("cancelled", [0, -5])
def test_cancellation_at_or_before_moment_closes_task(cancelled):
    result = pending_at([task(1, "cancelled", cancelled=cancelled)], at(0))
    assert result.tasks == () and result.uncertain_cancelled == ()


def test_known_cancellation_counts_even_after_due_until_it_happens():
    # Vadesi geçmiş ama henüz iptal edilmemiş görev o anda açıktı (kapasite nedeniyle bekliyordu).
    result = pending_at([task(1, "cancelled", due=-30, cancelled=60)], at(0))
    assert [t.id for t in result.tasks] == [1]


def test_unknown_cancellation_time_keeps_k3_uncertainty():
    tasks = [task(1, "cancelled", due=30), task(2, "cancelled", due=-30)]
    result = pending_at(tasks, at(0))
    assert result.tasks == () and [t.id for t in result.uncertain_cancelled] == [1]


def test_future_knowledge_is_not_used():
    # Oluşturulmamış görev, iptal zamanı ne olursa olsun yoktur.
    assert pending_at([task(1, "cancelled", created=10, cancelled=20)], at(0)).tasks == ()
    # Aynı görevin t anındaki görünümü (henüz iptal edilmemiş, durum 'pending') aynı sonucu vermeli.
    later_cancel = task(2, "cancelled", cancelled=45)
    as_known = task(2, "pending")
    assert [t.id for t in pending_at([later_cancel], at(0)).tasks] == [t.id for t in pending_at([as_known], at(0)).tasks]


def test_completed_and_pending_semantics_unchanged():
    tasks = [task(1, "pending"), task(2, "completed", done=5), task(3, "completed", done=0), task(4, "completed", done=-1)]
    assert [t.id for t in pending_at(tasks, at(0)).tasks] == [1, 2]


def test_mixed_moments_follow_close_times():
    tasks = [task(1, "cancelled", cancelled=15), task(2, "completed", done=30), task(3, "pending")]
    assert [sorted(t.id for t in pending_at(tasks, at(m)).tasks) for m in (0, 15, 30, 45)] == [[1, 2, 3], [2, 3], [3], [3]]


# ---- Gerçek veri kümeleri --------------------------------------------------------------------


@pytest.fixture(scope="module")
def v1_tasks():
    return [care_task({**r, "cancelled_at": None}) for r in generate_v1().tables["care_tasks"]]


@pytest.fixture(scope="module")
def v2():
    return generate_v2(ParamsV2(days=7))


def test_v1_behaviour_is_identical_to_legacy(v1_tasks):
    start = min(t.created_at for t in v1_tasks)
    for hours in range(24, 28 * 24, 37):
        moment = start + timedelta(hours=hours)
        result = pending_at(v1_tasks, moment)
        assert (sorted(t.id for t in result.tasks), sorted(t.id for t in result.uncertain_cancelled)) == legacy_pending(v1_tasks, moment)


def test_v2_has_no_uncertain_cancellations_and_matches_as_of_view(v2):
    tasks = [care_task(r) for r in v2.tables["care_tasks"]]
    for hours in range(6, 7 * 24, 11):
        moment = v2.scenario_start + timedelta(hours=hours)
        full = pending_at(tasks, moment)
        view = pending_at([care_task(r) for r in known_view(v2.tables, moment)["care_tasks"]], moment)
        assert full.uncertain_cancelled == ()
        assert sorted(t.id for t in full.tasks) == sorted(t.id for t in view.tasks)
        assert full.minutes == pytest.approx(view.minutes)


@pytest.mark.parametrize("reason", ["clinical", "discharged", "capacity"])
def test_each_cancel_reason_is_pending_before_and_closed_after(v2, reason):
    rows = [r for r in v2.tables["care_tasks"] if r["cancel_reason"] == reason and r["cancelled_at"] > r["created_at"]]
    assert rows, reason
    for r in rows[:25]:
        t = care_task(r)
        before = r["created_at"] + (r["cancelled_at"] - r["created_at"]) / 2
        assert t.id in {x.id for x in pending_at([t], before).tasks}
        assert pending_at([t], r["cancelled_at"]).tasks == ()


def test_legacy_behaviour_would_have_flagged_v2_cancellations_as_uncertain(v2):
    tasks = [care_task(r) for r in v2.tables["care_tasks"]]
    moment = v2.scenario_start + timedelta(days=3)
    _, old_uncertain = legacy_pending(tasks, moment)
    assert old_uncertain and pending_at(tasks, moment).uncertain_cancelled == ()


# ---- Repository: 0003 ve 0004 ------------------------------------------------------------------


def _sql(with_cancel_time):
    stmt = workload_repo.unfinished_statement(1, T0, with_cancel_time)
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_revision_0003_query_never_references_new_column():
    sql = _sql(False)
    assert "cancelled_at" not in sql and "cancel_reason" not in sql and "triage" not in sql


def test_revision_0004_query_reads_cancel_time_and_widens_candidates():
    sql = _sql(True)
    assert "care_tasks.cancelled_at" in sql.split("FROM")[0]  # seçilen kolon
    assert "care_tasks.cancelled_at >" in sql  # iptalden önce açık olan görevler aday


@pytest.mark.parametrize("with_cancel_time", [False, True])
def test_rows_from_either_schema_build_domain_tasks(with_cancel_time):
    names = [c.name for c in workload_repo.unfinished_statement(1, T0, with_cancel_time).selected_columns]
    row = dict.fromkeys(names)
    row.update(id=1, encounter_id=1, unit_id=1, task_type_id=1, created_at=at(-60), due_at=at(30), estimated_minutes=5.0,
               status="cancelled")  # fmt: skip
    if with_cancel_time:
        row["cancelled_at"] = at(20)
    t = CareTask(**row)
    assert (t.cancelled_at is None) is (not with_cancel_time)


def test_catalog_probe_is_read_only_sql():
    import inspect

    source = inspect.getsource(workload_repo.has_cancel_time_column)
    assert "information_schema.columns" in source and "INSERT" not in source and "UPDATE" not in source
