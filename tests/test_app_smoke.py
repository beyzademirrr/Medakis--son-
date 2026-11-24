"""Streamlit smoke testleri: uygulama hatasız açılır, prototip uyarısı görünür, servis paneli
veritabanı olmadan da güvenli davranır. Panel servisi testte sahte bir servisle değiştirilir;
varsayılan pytest gerçek veritabanına bağlanmaz.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from streamlit.testing.v1 import AppTest

from medakis.domain.capacity import unit_capacity
from medakis.domain.models import ShiftTemplate, Staff, StaffShift, Unit
from medakis.domain.time import Interval, shift_from_template
from medakis.domain.workload import window_grid
from medakis.services.workload_service import (
    DataCoverage,
    DataQuality,
    Metric,
    PanelOptions,
    UnitSnapshot,
    WindowRow,
)
from medakis.ui import workload_panel

UNIT = Unit(1, 1, "Yatakli Servis", "inpatient", True)
NIGHT = ShiftTemplate(2, "GECE", time(20, 0), 720)
COVERAGE = DataCoverage(datetime(2026, 9, 30, 21, tzinfo=UTC), datetime(2026, 10, 28, 21, tzinfo=UTC), "son seed kaydı",
                        {"database": "medakis_dev", "seed": 42, "days": 28, "content_sha256": "83a619cb0437", "generator_version": "1.0.0"})  # fmt: skip


def snapshot(observed=Metric(120.0, "ok"), capacity=Metric(1380.0, "ok"), pending=Metric(45.0, "ok"),
             patients=Metric(12.0, "ok"), nurse=Metric(1380.0, "ok"), missing_windows=0, uncertain=0):  # fmt: skip
    shift = shift_from_template(date(2026, 10, 4), time(20, 0), 720, "Europe/Istanbul")
    grid = window_grid(shift)
    windows = tuple(
        WindowRow(w, None, None, None, None) if i >= len(grid) - missing_windows else WindowRow(w, 2.5, 30.0, 30.0, w.end)
        for i, w in enumerate(grid)
    )
    staff = [Staff(7, "STAFF007", "nurse", True), Staff(8, "STAFF008", "nurse", True)]
    shifts = [StaffShift(1, 7, 1, shift.start, shift.end, 30), StaffShift(2, 8, 1, shift.start, shift.end, 0)]
    return UnitSnapshot(
        unit=UNIT, template=NIGHT, local_day=date(2026, 10, 4), timezone="Europe/Istanbul", shift=shift, at=shift.start,
        coverage=COVERAGE, windows=windows, observed_total=observed, capacity_total=capacity, capacity_by_role={"nurse": 1380.0},
        primary_role="nurse", primary_role_capacity=nurse, pending=pending, pending_by_role={"nurse": 45.0},
        pending_tasks=({"id": 1, "code": "VITAL", "name": "Vital bulgu", "role": "nurse", "due_at": shift.start + timedelta(minutes=10),
                        "estimated_minutes": 12.0, "status_at": "bekliyor"},),
        patient_count=patients, capacity=unit_capacity(staff, shifts, [], shift),
        quality=DataQuality(40, 2, uncertain, 6.0 * uncertain, ()), computed_at=datetime(2026, 11, 13, 9, tzinfo=UTC),
    )  # fmt: skip


class FakeService:
    def __init__(self, snap=None, has_data=True):
        self.snap = snap or snapshot()
        self.coverage = COVERAGE if has_data else DataCoverage(None, None, "veri yok")

    def options(self):
        return PanelOptions((UNIT,), (NIGHT,), self.coverage, "Europe/Istanbul")

    def shift_interval(self, template, local_day):
        return shift_from_template(local_day, template.start_local, template.duration_minutes, "Europe/Istanbul")

    def unit_snapshot(self, unit_id, local_day, template_id, at=None):
        return self.snap


def unavailable():
    raise ConnectionError("veritabanı kapalı")


def _run(project_root, monkeypatch, provider=unavailable):
    monkeypatch.setattr(workload_panel, "service_provider", provider)
    at = AppTest.from_file(str(project_root / "app.py"), default_timeout=60)
    at.run()
    return at


def panel_metrics(at):
    return {m.label: m.value for m in at.metric}


def test_app_opens_without_exception(project_root, monkeypatch):
    at = _run(project_root, monkeypatch)
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == "MedAkis"


def test_disclaimer_visible(project_root, monkeypatch):
    at = _run(project_root, monkeypatch)
    text = " ".join(w.value for w in at.warning)
    assert "sentetik veri" in text
    assert "klinik ürün değildir" in text


def test_tabs_present(project_root, monkeypatch):
    at = _run(project_root, monkeypatch)
    assert [t.label for t in at.tabs] == ["Genel bakış", "Servis paneli", "Öneri inceleme ve onay", "Mimari ve aşamalar", "Yapılandırma"]


def test_database_unavailable_shows_reason_and_no_numbers(project_root, monkeypatch):
    at = _run(project_root, monkeypatch)
    assert any("Veritabanına bağlanılamadı" in e.value for e in at.error)
    assert not at.metric
    assert any("Aşama 4" in i.value for i in at.info)  # genel bakış: tahmin yok


def test_empty_database_shows_guidance_not_zeros(project_root, monkeypatch):
    at = _run(project_root, monkeypatch, lambda: FakeService(has_data=False))
    assert any("sentetik operasyonel veri yok" in i.value for i in at.info)
    assert not at.metric and not at.exception


def test_panel_cards_show_values_and_no_forecast_number(project_root, monkeypatch):
    at = _run(project_root, monkeypatch, FakeService)
    assert not at.exception, [e.value for e in at.exception]
    m = panel_metrics(at)
    assert m["Hasta sayısı (seçilen an)"] == "12"
    assert m["Bekleyen iş (seçilen an)"] == "45 dk"
    assert m["Kullanılabilir personel (vardiya)"] == "1.380 dk"
    assert m["Hemşire kapasitesi (vardiya)"] == "1.380 dk"
    assert m["Gerçekleşen iş (vardiya)"] == "120 dk"
    assert not [label for label in m if "tahmin" in label.lower()]
    assert any("30/60 dakikalık tahmin modelleri Aşama 4" in i.value and "gösterilmez" in i.value for i in at.info)
    assert any("medakis_dev" in c.value and "Model: bu panelde kullanılmıyor" in c.value for c in at.caption)


def test_missing_zero_and_partial_are_displayed_differently(project_root, monkeypatch):
    snap = snapshot(
        observed=Metric(60.0, "partial", "32 pencere veri kapsamı dışında"),
        capacity=Metric(0.0, "zero", "Bu servis ve vardiyada kayıtlı personel vardiyası yok."),
        pending=Metric(None, "missing", "Seçilen an veri kapsamı dışında."),
        patients=Metric(None, "missing", "Seçilen an veri kapsamı dışında."),
        nurse=Metric(0.0, "zero", "yok"),
        missing_windows=32,
    )
    at = _run(project_root, monkeypatch, lambda: FakeService(snap))
    m = panel_metrics(at)
    assert m["Bekleyen iş (seçilen an)"] == "—" and m["Hasta sayısı (seçilen an)"] == "—"  # eksik: sıfır değil
    assert m["Kullanılabilir personel (vardiya)"] == "0 dk"  # gerçek sıfır
    assert m["Gerçekleşen iş (vardiya)"] == "60 dk (kısmi)"
    captions = " ".join(c.value for c in at.caption)
    assert "kayıtlı personel vardiyası yok" in captions and "sıfır değildir" in captions


def test_uncertain_cancellations_are_explained(project_root, monkeypatch):
    at = _run(project_root, monkeypatch, lambda: FakeService(snapshot(uncertain=3)))
    captions = " ".join(c.value for c in at.caption)
    assert "İptal zamanı kaydedilmediği için 3 iptal görevin" in captions and "kesin değildir" in captions


@pytest.mark.parametrize(
    ("metric", "expected"),
    [(Metric(None, "missing"), "—"), (Metric(0.0, "zero"), "0 dk"), (Metric(1234.4, "ok"), "1.234 dk"),
     (Metric(10.0, "partial"), "10 dk (kısmi)")],
)  # fmt: skip
def test_metric_formatting(metric, expected):
    assert workload_panel.metric_value(metric) == expected
