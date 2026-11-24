"""Personel servisi: şablondan vardiya, uygunsuzluk ve yetkinlik kontrolleri."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import Engine

from medakis.config import load_settings
from medakis.domain import errors
from medakis.domain.errors import DomainError
from medakis.domain.rules import check_break_fits_shift, has_overlap, skill_valid_throughout
from medakis.domain.time import Interval, ensure_utc, shift_from_template
from medakis.repositories import facility_repo, staff_repo
from medakis.services._tx import TransactionFactory, factory_for, unit_of_work

BREAK_REASON = "Mola"  # somut mola aralıkları staff_unavailability'de bu nedenle tutulur


class StaffService:
    def __init__(
        self, engine: Engine | None = None, begin: TransactionFactory | None = None, timezone: str | None = None
    ) -> None:
        if begin is None:
            if engine is None:
                raise ValueError("engine veya begin verilmeli")
            begin = factory_for(engine)
        self._begin = begin
        self._timezone = timezone or load_settings().timezone

    def add_shift_from_template(
        self,
        staff_id: int,
        unit_id: int,
        template_id: int,
        local_day: date,
        breaks: Sequence[tuple[datetime, datetime]] = (),
    ) -> int:
        """Yerel şablon saatinden somut UTC vardiya ve molalarını tek işlemde oluşturur.

        Mola özeti (break_minutes) dışarıdan alınmaz; somut mola aralıklarının toplamından
        türetilir. Böylece sentetik doğrulayıcının kuralı (özet = vardiya içindeki somut molalar)
        uygulama yoluyla oluşturulan vardiyalarda da sağlanır.
        """
        intervals = [Interval(start, end) for start, end in breaks]
        if has_overlap(intervals):
            raise DomainError(errors.BREAK_OVERLAP, "Mola aralıkları birbiriyle çakışamaz.")
        for b in intervals:
            if not b.minutes().is_integer():
                raise DomainError(errors.INVALID_INTERVAL, "Mola süresi tam dakika olmalı.")
        break_minutes = int(sum(b.minutes() for b in intervals))
        with unit_of_work(self._begin) as conn:
            person = staff_repo.get_staff(conn, staff_id)
            if person is None:
                raise DomainError(errors.STAFF_NOT_FOUND, "Personel bulunamadı.")
            if not person.active:
                raise DomainError(errors.STAFF_INACTIVE, "Personel aktif değil.")
            unit = facility_repo.get_unit(conn, unit_id)
            if unit is None:
                raise DomainError(errors.UNIT_NOT_FOUND, "Servis bulunamadı.")
            if not unit.active:
                raise DomainError(errors.UNIT_INACTIVE, "Servis aktif değil.")
            template = staff_repo.get_template(conn, template_id)
            if template is None:
                raise DomainError(errors.TEMPLATE_NOT_FOUND, "Vardiya şablonu bulunamadı.")
            shift = shift_from_template(local_day, template.start_local, template.duration_minutes, self._timezone)
            check_break_fits_shift(break_minutes, shift)
            if any(not shift.covers(b) for b in intervals):
                raise DomainError(errors.BREAK_OUTSIDE_SHIFT, "Mola vardiyanın içinde olmalı.")
            if staff_repo.staff_has_overlapping_shift(conn, staff_id, shift.start, shift.end):
                raise DomainError(errors.SHIFT_OVERLAP, "Personelin bu aralıkta başka vardiyası var.")
            if staff_repo.staff_has_unavailability(conn, staff_id, shift.start, shift.end, BREAK_REASON):
                raise DomainError(errors.BREAK_OVERLAP, "Bu aralıkta vardiyaya bağlı olmayan mola kaydı var.")
            shift_id = staff_repo.insert_shift(conn, staff_id, unit_id, template_id, shift.start, shift.end, break_minutes)
            for b in intervals:
                staff_repo.insert_unavailability(conn, staff_id, b.start, b.end, BREAK_REASON)
            return shift_id

    def add_unavailability(self, staff_id: int, starts_at: datetime, ends_at: datetime, reason: str) -> int:
        interval = Interval(starts_at, ends_at)
        if not reason.strip():
            raise DomainError(errors.INVALID_INTERVAL, "Uygunsuzluk nedeni boş olamaz.")
        if reason.strip().casefold() == BREAK_REASON.casefold():
            raise DomainError(errors.BREAK_RESERVED, "Mola yalnız vardiyayla birlikte kaydedilir.")
        with unit_of_work(self._begin) as conn:
            if staff_repo.get_staff(conn, staff_id) is None:
                raise DomainError(errors.STAFF_NOT_FOUND, "Personel bulunamadı.")
            return staff_repo.insert_unavailability(conn, staff_id, interval.start, interval.end, reason)

    def skill_valid_for(self, staff_id: int, skill_id: int, start: datetime, end: datetime) -> bool:
        """Yetkinlik [start, end) boyunca kesintisiz geçerli mi? (atama doğrulamasında kullanılacak)"""
        work = Interval(ensure_utc(start), ensure_utc(end))
        with unit_of_work(self._begin) as conn:
            validities = staff_repo.list_skill_validities(conn, staff_id, skill_id)
        return skill_valid_throughout([Interval(a, b) for a, b in validities], work)
