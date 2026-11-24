"""PostgreSQL kısıt ihlallerini parametre değeri içermeyen Türkçe alan hatalarına çevirir."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from medakis.domain import errors
from medakis.domain.errors import DomainError

CONSTRAINT_MESSAGES = {
    "ex_staff_shifts_staff_overlap": (errors.SHIFT_OVERLAP, "Personelin bu aralıkta başka vardiyası var."),
    "uq_staff_shifts_staff_id_starts_at": (errors.SHIFT_OVERLAP, "Personelin aynı başlangıçlı vardiyası var."),
    "ex_encounters_bed_overlap": (errors.BED_OCCUPIED, "Yatak bu aralıkta başka bir yatışa ayrılmış."),
    "ex_assignments_staff_overlap": (errors.CONSTRAINT, "Personelin aynı çalışmada çakışan ataması var."),
    "ex_rag_documents_approved_overlap": (errors.CONSTRAINT, "Aynı kaynağın onaylı sürümleri çakışıyor."),
    "fk_care_tasks_encounter_id_unit_id": (errors.CONSTRAINT, "Görevin servisi başvurunun servisiyle aynı olmalı."),
    "ck_care_tasks_completion": (errors.TASK_TRANSITION, "Görev durumu ile tamamlanma bilgisi tutarsız."),
    "ck_care_tasks_completion_order": (errors.TASK_ORDER, "Tamamlanma zamanı oluşturulma zamanından önce olamaz."),
    "ck_patients_synthetic_code_format": (errors.INVALID_CODE, "Hasta kodu yalnız sentetik SYN biçiminde olabilir."),
    "ck_staff_synthetic_code_format": (errors.INVALID_CODE, "Personel kodu yalnız sentetik STAFF biçiminde olabilir."),
}


def translate(exc: IntegrityError) -> DomainError:
    """Bilinen kısıtlar için açıklayıcı, diğerleri için genel bir alan hatası döndürür."""
    name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or ""
    if name in CONSTRAINT_MESSAGES:
        code, message = CONSTRAINT_MESSAGES[name]
        return DomainError(code, message)
    return DomainError(errors.CONSTRAINT, f"Veritabanı kuralı ihlal edildi ({name or 'adı bilinmiyor'}).")
