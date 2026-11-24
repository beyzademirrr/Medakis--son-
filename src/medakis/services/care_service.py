"""Bakım akışı servisi: yatış, bakım değerlendirmesi ve görev yaşam döngüsü.

Her işlem tek transaction içinde okur, kuralı uygular ve yazar. Veritabanı kısıtları (EXCLUDE,
bileşik FK) eşzamanlı yazmalarda son savunmadır; ihlal açık bir alan hatasına çevrilir.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine

from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.domain.models import CareTask
from medakis.domain.rules import (
    check_task_transition,
    estimated_minutes,
    latest_multiplier_at,
    require_patient_code,
    require_positive,
)
from medakis.domain.time import ensure_utc
from medakis.repositories import care_repo, facility_repo
from medakis.services._tx import TransactionFactory, factory_for, unit_of_work


class CareService:
    def __init__(self, engine: Engine | None = None, begin: TransactionFactory | None = None) -> None:
        if begin is None:
            if engine is None:
                raise ValueError("engine veya begin verilmeli")
            begin = factory_for(engine)
        self._begin = begin

    def admit_patient(self, unit_id: int, patient_code: str, admitted_at: datetime, bed_id: int | None = None) -> int:
        """Sentetik hastayı servise yatırır; yatak verilmişse aynı serviste ve boş olmalıdır."""
        require_patient_code(patient_code)
        admitted_at = ensure_utc(admitted_at, "Yatış zamanı")
        with unit_of_work(self._begin) as conn:
            unit = facility_repo.get_unit(conn, unit_id)
            if unit is None:
                raise DomainError(errors.UNIT_NOT_FOUND, "Servis bulunamadı.")
            if not unit.active:
                raise DomainError(errors.UNIT_INACTIVE, "Servis aktif değil.")
            if bed_id is not None:
                placement = facility_repo.get_bed_placement(conn, bed_id)
                if placement is None:
                    raise DomainError(errors.BED_NOT_FOUND, "Yatak bulunamadı.")
                if placement.unit_id != unit_id:
                    raise DomainError(errors.BED_UNIT_MISMATCH, "Yatak başvurunun servisine ait değil.")
                if not placement.bed_active:
                    raise DomainError(errors.BED_INACTIVE, "Yatak kullanım dışı.")
                if care_repo.bed_has_overlapping_stay(conn, bed_id, admitted_at, None):
                    raise DomainError(errors.BED_OCCUPIED, "Yatak bu aralıkta başka bir yatışa ayrılmış.")
            patient_id = care_repo.insert_patient(conn, patient_code)
            return care_repo.insert_encounter(conn, patient_id, unit_id, bed_id, admitted_at)

    def record_acuity(self, encounter_id: int, assessed_at: datetime, level: int, multiplier: float) -> int:
        assessed_at = ensure_utc(assessed_at, "Değerlendirme zamanı")
        require_positive(multiplier, "Bakım katsayısı")
        with unit_of_work(self._begin) as conn:
            encounter = care_repo.get_encounter(conn, encounter_id)
            if encounter is None:
                raise DomainError(errors.ENCOUNTER_NOT_FOUND, "Başvuru bulunamadı.")
            if assessed_at < encounter.admitted_at or (
                encounter.discharged_at is not None and assessed_at >= encounter.discharged_at
            ):
                raise DomainError(errors.ENCOUNTER_CLOSED, "Değerlendirme yatış süresi içinde olmalı.")
            return care_repo.insert_acuity(conn, encounter_id, assessed_at, level, multiplier)

    def create_task(self, encounter_id: int, task_type_id: int, created_at: datetime, due_at: datetime) -> CareTask:
        """Tahmini süre, görev oluşturulduğu anda bilinen en son katsayıyla bir kez hesaplanır."""
        created_at = ensure_utc(created_at, "Oluşturulma zamanı")
        due_at = ensure_utc(due_at, "Son tarih")
        if due_at < created_at:
            raise DomainError(errors.TASK_ORDER, "Son tarih oluşturulma zamanından önce olamaz.")
        with unit_of_work(self._begin) as conn:
            encounter = care_repo.get_encounter(conn, encounter_id)
            if encounter is None:
                raise DomainError(errors.ENCOUNTER_NOT_FOUND, "Başvuru bulunamadı.")
            if created_at < encounter.admitted_at or (
                encounter.discharged_at is not None and created_at >= encounter.discharged_at
            ):
                raise DomainError(errors.ENCOUNTER_CLOSED, "Görev yatış süresi içinde oluşturulmalı.")
            task_type = care_repo.get_task_type(conn, task_type_id)
            if task_type is None:
                raise DomainError(errors.TASK_TYPE_NOT_FOUND, "Görev türü bulunamadı.")
            multiplier = latest_multiplier_at(care_repo.list_acuity_until(conn, encounter_id, created_at), created_at)
            minutes = estimated_minutes(task_type.base_minutes, multiplier)
            task_id = care_repo.insert_task(
                conn, encounter_id, encounter.unit_id, task_type_id, created_at, due_at, minutes
            )
            return care_repo.get_task(conn, encounter.unit_id, task_id)

    def complete_task(self, unit_id: int, task_id: int, completed_at: datetime, actual_minutes: float) -> None:
        completed_at = ensure_utc(completed_at, "Tamamlanma zamanı")
        require_positive(actual_minutes, "Gerçekleşen süre")
        with unit_of_work(self._begin) as conn:
            task = care_repo.get_task(conn, unit_id, task_id, for_update=True)
            if task is None:
                raise DomainError(errors.TASK_NOT_FOUND, "Görev bu serviste bulunamadı.")
            check_task_transition(task.status, "completed")
            if completed_at < task.created_at:
                raise DomainError(errors.TASK_ORDER, "Tamamlanma zamanı oluşturulma zamanından önce olamaz.")
            care_repo.mark_completed(conn, unit_id, task_id, completed_at, actual_minutes)

    def cancel_task(self, unit_id: int, task_id: int) -> None:
        """Yalnız görevi iptal eder. Mevcut optimizasyon çalışmaları ve atamaları değiştirilmez;
        güncel duruma göre yeni çalışma oluşturulur (tarihsel anlık görüntü korunur)."""
        with unit_of_work(self._begin) as conn:
            task = care_repo.get_task(conn, unit_id, task_id, for_update=True)
            if task is None:
                raise DomainError(errors.TASK_NOT_FOUND, "Görev bu serviste bulunamadı.")
            check_task_transition(task.status, "cancelled")
            care_repo.mark_cancelled(conn, unit_id, task_id)
