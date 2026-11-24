"""Aşama 3 servis paneli verisi: seçilen servis, yerel tarih ve vardiya için iş yükü ve kapasite.

Yalnız okur: bağlantı `medakis_app` rolüyle açılır ve işlem PostgreSQL'de READ ONLY başlatılır.

Eksik veri ile gerçek sıfır ayrımı (proje sahibi koşulu):
- Verinin kapsadığı dönem (son seed kaydının parametrelerinden, yoksa görev zamanlarından) dışındaki
  pencere ve anlar EKSİK sayılır: değer None, nedeni yazılır.
- Kapsam içindeki 0 gerçektir; nedeni (ör. kayıtlı vardiya yok, bütün personel izinli) ayrıca yazılır.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, text

from medakis.config import load_settings
from medakis.domain.capacity import CapacitySummary, unit_capacity, window_capacity
from medakis.domain.models import ShiftTemplate, Unit
from medakis.domain.time import Interval, ensure_utc, shift_from_template
from medakis.domain.workload import (
    PendingWork,
    WindowObservation,
    check_grid_timezone,
    distribute,
    patient_count_at,
    pending_at,
    realized_before_created,
    window_grid,
)
from medakis.repositories import staff_repo, workload_repo

Status = Literal["ok", "zero", "missing", "partial"]
PRIMARY_ROLE_BY_KIND = {"inpatient": "nurse"}  # K4: yataklı serviste hemşire kapasitesi vurgulanır


@dataclass(frozen=True)
class Metric:
    value: float | None
    status: Status
    note: str = ""


@dataclass(frozen=True)
class DataCoverage:
    start: datetime | None
    end: datetime | None
    source: str
    seed: dict[str, Any] | None = None

    def contains(self, moment: datetime) -> bool:
        return self.start is not None and self.end is not None and self.start <= moment < self.end

    def covers(self, interval: Interval) -> bool:
        return self.start is not None and self.end is not None and self.start <= interval.start and interval.end <= self.end  # type: ignore[operator]


@dataclass(frozen=True)
class WindowRow:
    window: Interval
    observed_minutes: float | None  # None: kapsam dışı (eksik)
    capacity_minutes: float | None
    primary_role_capacity: float | None
    available_at: datetime | None


@dataclass(frozen=True)
class DataQuality:
    completed_in_shift: int
    realized_before_created: int  # K2
    uncertain_cancelled: int  # K3
    uncertain_cancelled_minutes: float
    break_summary_warnings: tuple[str, ...]


@dataclass(frozen=True)
class UnitSnapshot:
    unit: Unit
    template: ShiftTemplate
    local_day: date
    timezone: str
    shift: Interval
    at: datetime
    coverage: DataCoverage
    windows: tuple[WindowRow, ...]
    observed_total: Metric
    capacity_total: Metric
    capacity_by_role: dict[str, float]
    primary_role: str | None
    primary_role_capacity: Metric
    pending: Metric
    pending_by_role: dict[str, float]
    pending_tasks: tuple[dict[str, Any], ...]
    patient_count: Metric
    capacity: CapacitySummary | None
    quality: DataQuality
    computed_at: datetime
    notes: tuple[str, ...] = field(default=())
    # Kapasite, gerçekleşen iş, grafik ve tablolar bu aralıktan hesaplanır: vardiya ∩ veri kapsamı.
    measured: Interval | None = None
    # Yalnız kısmi kapsamda: tam vardiyanın planlı kapasitesi (ölçülmemiş saatler dahil, ayrı etiketli).
    planned_full_shift_capacity: Metric | None = None
    # Salt okunur ayrıntılar (yalnız SELECT): seçilen anda geçerli yetkinlik adları ve vardiyayla kesişen uygunsuzluk
    # aralıkları. Kayıt yoksa boş kalır; panel bunu "—" ve nedeniyle gösterir, değer uydurulmaz.
    staff_skills: dict[int, tuple[str, ...]] = field(default_factory=dict)
    unavailability_rows: tuple[dict[str, Any], ...] = field(default=())


@dataclass(frozen=True)
class PanelOptions:
    units: tuple[Unit, ...]
    templates: tuple[ShiftTemplate, ...]
    coverage: DataCoverage
    timezone: str

    @property
    def has_data(self) -> bool:
        return self.coverage.start is not None


ConnectionFactory = Callable[[], AbstractContextManager[Connection]]


def read_only_factory(engine: Engine) -> ConnectionFactory:
    @contextmanager
    def connect():
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            try:
                yield conn
            finally:
                conn.rollback()

    return connect


def default_service() -> WorkloadService:
    """Panel için: yapılandırılmış veritabanına yalnız medakis_app rolüyle, READ ONLY okuyan servis."""
    from medakis.repositories.database import runtime_engine

    settings = load_settings()
    return WorkloadService(engine=runtime_engine("medakis_app", settings.pg_database), timezone=settings.timezone)


class WorkloadService:
    def __init__(self, engine: Engine | None = None, connect: ConnectionFactory | None = None, timezone: str | None = None):
        if connect is None:
            if engine is None:
                raise ValueError("engine veya connect verilmeli")
            connect = read_only_factory(engine)
        self._connect = connect
        self._timezone = timezone or load_settings().timezone
        self._tz = ZoneInfo(self._timezone)

    # ---- Seçenekler ve kapsam ---------------------------------------------------------------

    def _coverage(self, conn: Connection) -> DataCoverage:
        low, high = workload_repo.task_time_bounds(conn)
        if low is None:
            return DataCoverage(None, None, "veri yok")  # seed kaydı olsa bile operasyonel veri yok
        record = workload_repo.last_seed_record(conn)
        if record is not None:
            detail = record["detail_json"] or {}
            params = detail.get("params") or {}
            try:
                tz = ZoneInfo(params.get("timezone", self._timezone))
                first = date.fromisoformat(params["start_day"])
                start = datetime.combine(first, time(0), tzinfo=tz).astimezone(UTC)
                end = datetime.combine(first + timedelta(days=int(params["days"])), time(0), tzinfo=tz).astimezone(UTC)
                seed = {
                    "database": record["entity_id"],
                    "content_sha256": detail.get("content_sha256"),
                    "generator_version": detail.get("generator_version"),
                    "seed": params.get("seed"),
                    "days": params.get("days"),
                    "applied_at": record["occurred_at"],
                }
                return DataCoverage(start, end, "son seed kaydı", seed)
            except (KeyError, ValueError, TypeError):
                pass
        return DataCoverage(ensure_utc(low), ensure_utc(high) + timedelta(microseconds=1), "görev zamanları (seed kaydı yok)")

    def options(self) -> PanelOptions:
        with self._connect() as conn:
            return PanelOptions(
                tuple(workload_repo.list_units(conn)),
                tuple(workload_repo.list_templates(conn)),
                self._coverage(conn),
                self._timezone,
            )

    def shift_interval(self, template: ShiftTemplate, local_day: date) -> Interval:
        return shift_from_template(local_day, template.start_local, template.duration_minutes, self._timezone)

    # ---- Anlık görünüm ----------------------------------------------------------------------

    def unit_snapshot(self, unit_id: int, local_day: date, template_id: int, at: datetime | None = None) -> UnitSnapshot:
        with self._connect() as conn:
            unit = next((u for u in workload_repo.list_units(conn) if u.id == unit_id), None)
            template = next((t for t in workload_repo.list_templates(conn) if t.id == template_id), None)
            if unit is None or template is None:
                raise LookupError("Servis veya vardiya şablonu bulunamadı.")
            shift = self.shift_interval(template, local_day)
            check_grid_timezone(self._tz, shift.start)
            moment = ensure_utc(at) if at is not None else shift.start
            if not shift.start <= moment < shift.end:  # type: ignore[operator]
                raise ValueError("Değerlendirme anı vardiya içinde olmalı.")
            coverage = self._coverage(conn)
            grid = window_grid(shift)

            completed = workload_repo.completed_tasks_overlapping(conn, unit_id, shift.start, shift.end)  # type: ignore[arg-type]
            known = workload_repo.tasks_known_unfinished_at(conn, unit_id, moment)
            open_encounters = workload_repo.encounters_open_at(conn, unit_id, moment)
            shifts = staff_repo.list_shifts(conn, unit_id, shift.start, shift.end)  # type: ignore[arg-type]
            staff_ids = sorted({s.staff_id for s in shifts})
            people = workload_repo.staff_by_ids(conn, staff_ids)
            unavailable = workload_repo.unavailability_overlapping(conn, staff_ids, shift.start, shift.end)  # type: ignore[arg-type]
            types = workload_repo.task_type_info(conn)
            staff_skill_names = workload_repo.skills_valid_at(conn, staff_ids, moment)
            type_skills = workload_repo.task_type_skill_names(conn)
            acuity = workload_repo.latest_acuity_at(conn, sorted({t.encounter_id for t in known}), moment)

        primary_role = PRIMARY_ROLE_BY_KIND.get(unit.kind)
        observations = distribute(completed, grid)
        # Ölçülen aralık: vardiyanın veri kapsamındaki pencereleri (kapsam tek aralık olduğundan bitişiktir).
        covered = [w for w in grid if coverage.covers(w)]
        measured = Interval(covered[0].start, covered[-1].end) if covered else None
        partial = measured is not None and len(covered) < len(grid)
        summary = unit_capacity(people, shifts, unavailable, measured) if measured else None
        n = len(grid)
        caps = window_capacity(summary, grid) if summary else [None] * n
        primary_caps = window_capacity(summary, grid, role=primary_role) if summary and primary_role else [None] * n
        windows = tuple(self._window_row(o, c, p, coverage) for o, c, p in zip(observations, caps, primary_caps))

        notes: list[str] = []
        observed_total = self._observed_total(windows)
        shifts_in_measured = sum(1 for s in shifts if measured and s.starts_at < measured.end and s.ends_at > measured.start)  # type: ignore[operator]
        scope = f"{len(covered)}/{n} pencere"
        capacity_total, primary_capacity = self._capacity_metrics(summary, partial, primary_role, shifts_in_measured, scope)
        planned_full = None
        if partial:
            planned = unit_capacity(people, shifts, unavailable, shift)
            planned_full = Metric(
                planned.total_minutes,
                "ok" if planned.total_minutes else "zero",
                "Tam vardiya planlı kapasitesi: kayıtlı vardiya ve uygunsuzluklardan, veri kapsamı dışındaki saatler "
                "dahil hesaplanır. Ölçülmüş kapasite değildir; gerçekleşen işle karşılaştırılmaz.",
            )
        pending_metric, pending = self._pending(known, moment, coverage)
        by_role: dict[str, float] = {}
        pending_rows: list[dict[str, Any]] = []
        if pending is not None:
            for task in pending.tasks:
                info = types.get(task.task_type_id, {})
                role = info.get("required_role", "?")
                by_role[role] = by_role.get(role, 0.0) + task.estimated_minutes
                assessed = acuity.get(task.encounter_id)
                pending_rows.append({
                    "id": task.id, "code": info.get("code", "?"), "name": info.get("name", "?"), "role": role,
                    "due_at": task.due_at, "estimated_minutes": task.estimated_minutes,
                    "status_at": "bekliyor" if task.status == "pending" else "o anda bitmemişti",
                    "required_skills": tuple(type_skills.get(task.task_type_id, ())),
                    "acuity_level": assessed["level"] if assessed else None,
                    "acuity_multiplier": assessed["multiplier"] if assessed else None,
                    "acuity_assessed_at": assessed["assessed_at"] if assessed else None,
                })  # fmt: skip
        patients = (
            Metric(float(patient_count_at(open_encounters, moment)), "ok")
            if coverage.contains(moment)
            else Metric(None, "missing", "Seçilen an veri kapsamı dışında.")
        )
        if patients.status == "ok" and patients.value == 0:
            patients = Metric(0.0, "zero", "Bu anda serviste açık başvuru yok.")

        quality = DataQuality(
            completed_in_shift=len(completed),
            realized_before_created=sum(1 for t in completed if realized_before_created(t)),
            uncertain_cancelled=len(pending.uncertain_cancelled) if pending else 0,
            uncertain_cancelled_minutes=pending.uncertain_cancelled_minutes if pending else 0.0,
            break_summary_warnings=tuple(w for row in summary.staff for w in row.warnings) if summary else (),
        )
        if not coverage.covers(shift):
            notes.append("Vardiyanın bir kısmı veya tamamı veri kapsamı dışında; bu pencereler eksik gösterilir.")
        return UnitSnapshot(
            unit=unit, template=template, local_day=local_day, timezone=self._timezone, shift=shift, at=moment,
            coverage=coverage, windows=windows, observed_total=observed_total, capacity_total=capacity_total,
            capacity_by_role=summary.by_role if summary else {}, primary_role=primary_role, primary_role_capacity=primary_capacity,
            pending=pending_metric, pending_by_role=dict(sorted(by_role.items())), pending_tasks=tuple(pending_rows),
            patient_count=patients, capacity=summary if summary is not None and shifts_in_measured else None,
            quality=quality, computed_at=datetime.now(UTC), notes=tuple(notes), measured=measured,
            planned_full_shift_capacity=planned_full,
            staff_skills={sid: tuple(names) for sid, names in staff_skill_names.items()},
            unavailability_rows=tuple(
                {"staff_id": u.staff_id, "reason": u.reason, "starts_at": u.interval.start, "ends_at": u.interval.end}
                for u in unavailable
            ),
        )  # fmt: skip

    # ---- Yardımcılar ------------------------------------------------------------------------

    @staticmethod
    def _window_row(obs: WindowObservation, cap: float, primary: float | None, coverage: DataCoverage) -> WindowRow:
        if not coverage.covers(obs.window):
            return WindowRow(obs.window, None, None, None, None)
        return WindowRow(obs.window, obs.observed_minutes, cap, primary, obs.available_at)

    @staticmethod
    def _observed_total(windows: tuple[WindowRow, ...]) -> Metric:
        known = [w.observed_minutes for w in windows if w.observed_minutes is not None]
        if not known:
            return Metric(None, "missing", "Vardiya veri kapsamı dışında.")
        total = sum(known)
        if len(known) < len(windows):
            return Metric(total, "partial", f"{len(windows) - len(known)} pencere veri kapsamı dışında; toplam yalnız kapsanan pencereleri içerir.")
        if total == 0:
            return Metric(0.0, "zero", "Bu vardiyada tamamlanan görev yok.")
        return Metric(total, "ok")

    @staticmethod
    def _capacity_metrics(
        summary: CapacitySummary | None, partial: bool, primary_role: str | None, shift_count: int, scope: str
    ) -> tuple[Metric, Metric]:
        """Kapasite kartları; `summary` ölçülen aralıktan (vardiya ∩ veri kapsamı) hesaplanmıştır.

        Sıfır yalnız tam kapsanan vardiyada kesindir. Kısmi kapsamda değer yalnız kapsanan süreyi
        anlatır ve durum "partial" kalır (kalan saatler ölçülmemiştir).
        """
        if summary is None:
            missing = Metric(None, "missing", "Vardiya veri kapsamı dışında; kapasite ölçülmedi.")
            return missing, missing

        def metric(value: float, zero_reason: str) -> Metric:
            if partial:
                note = f"Yalnız veri kapsamındaki süre ({scope}) için hesaplandı; kalan saatler ölçülmedi."
                return Metric(value, "partial", note + (f" Kapsanan sürede {zero_reason}" if value == 0 else ""))
            if value == 0:
                return Metric(0.0, "zero", zero_reason[0].upper() + zero_reason[1:])
            return Metric(value, "ok")

        if shift_count == 0:
            reason = "bu servis ve vardiyada kayıtlı personel vardiyası yok."
            return metric(0.0, reason), (metric(0.0, reason) if primary_role else Metric(None, "missing"))
        total = metric(summary.total_minutes, "personel vardiyası var ama tamamı izin/mola ile kapalı.")
        if primary_role is None:
            return total, Metric(None, "missing")
        if primary_role not in summary.by_role:
            return total, metric(0.0, f"'{primary_role}' rolünde vardiyada personel yok.")
        return total, metric(summary.by_role[primary_role], f"'{primary_role}' vardiyası var ama tamamı izin/mola ile kapalı.")

    @staticmethod
    def _pending(known, moment: datetime, coverage: DataCoverage) -> tuple[Metric, PendingWork | None]:  # noqa: ANN001
        if not coverage.contains(moment):
            return Metric(None, "missing", "Seçilen an veri kapsamı dışında."), None
        pending = pending_at(known, moment)
        note = ""
        if pending.uncertain_cancelled:
            note = (
                f"İptal zamanı kaydedilmediği için {len(pending.uncertain_cancelled)} iptal görevin "
                f"({pending.uncertain_cancelled_minutes:.0f} dk) bu anda açık olup olmadığı bilinmiyor; sayıya katılmadı."
            )
        if pending.minutes == 0:
            return Metric(0.0, "zero", ("Bu anda bekleyen görev yok. " + note).strip()), pending
        return Metric(pending.minutes, "ok", note), pending
