"""Servisin kapasite kartı kuralları (veritabanı yok): sıfır yalnız tam kapsanan vardiyada kesindir."""

import pytest

from medakis.domain.capacity import CapacitySummary
from medakis.services.workload_service import WorkloadService

metrics = WorkloadService._capacity_metrics
EMPTY = CapacitySummary(0.0, {}, ())
SUPPORT_ONLY = CapacitySummary(120.0, {"support": 120.0}, ())


def test_missing_scope_is_missing():
    total, nurse = metrics(None, False, "nurse", 0, "0/48 pencere")
    assert total.status == nurse.status == "missing" and total.value is None


def test_full_scope_without_shifts_is_a_real_zero():
    total, nurse = metrics(EMPTY, False, "nurse", 0, "48/48 pencere")
    assert (total.status, total.value) == ("zero", 0.0) and nurse.status == "zero"
    assert "kayıtlı personel vardiyası yok" in total.note


@pytest.mark.parametrize("primary_role", ["nurse", None])
def test_partial_scope_without_shifts_stays_partial(primary_role):
    total, nurse = metrics(EMPTY, True, primary_role, 0, "16/48 pencere")
    assert (total.status, total.value) == ("partial", 0.0)
    assert "kalan saatler ölçülmedi" in total.note and "16/48 pencere" in total.note
    assert nurse.status == ("partial" if primary_role else "missing")


def test_partial_scope_without_primary_role_staff_stays_partial():
    total, nurse = metrics(SUPPORT_ONLY, True, "nurse", 2, "16/48 pencere")
    assert total.status == "partial" and total.value == 120.0
    assert (nurse.status, nurse.value) == ("partial", 0.0) and "rolünde vardiyada personel yok" in nurse.note


def test_full_scope_without_primary_role_staff_is_zero():
    total, nurse = metrics(SUPPORT_ONLY, False, "nurse", 2, "48/48 pencere")
    assert total.status == "ok" and (nurse.status, nurse.value) == ("zero", 0.0)
