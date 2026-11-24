"""MedAkis PostgreSQL şeması (SQLAlchemy Core).

medakis_schema.sql (SQLite v2 referansı) ile aynı 34 tablo, aynı kolon adları, anahtarlar ve
indeksler. Tip eşlemesi: zaman -> TIMESTAMPTZ, bayrak -> BOOLEAN, *_json -> JSONB, REAL -> DOUBLE
PRECISION, yerel saat -> TIME.

Referansa göre eklenen güvenceler:
- PostgreSQL'de NaN her sayıdan büyük sayılır, yani 'NaN' > 0 doğrudur. Her ondalık kolon bu
  nedenle ayrıca sonlu olmaya zorlanır.
- SHA-256 kolonları 64 uzunluk yerine 64 küçük harf onaltılık karakter olmak zorundadır.
- Sentetik kodlar SYN / STAFF biçimindedir (gerçek ad veya kimlik numarası yazılamaz).
- Dört EXCLUDE kısıtı; istisnaları docs/raporlar/FAZ_1_2B_RAPORU.md içinde tanımlıdır.

Kolon yazma yetkileri PRIVILEGES tablosundadır ve Alembic başlangıç revizyonunda uygulanır.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

ROLE_VALUES = "'nurse','supervisor','support'"

# ---- Revizyon 0004 (üretici 2.0.0) ------------------------------------------------------------
# Yalnız NULL olabilen üç kolon. Kısıtların hepsi NULL değerle sağlanır; bu yüzden 1.0.0 kayıtları
# (yeni kolonları NULL) ek dönüşüm olmadan geçerlidir ve kısıtlar NOT VALID gerektirmez.
REVISION_0004 = "0004_triage_cancel_columns"
SCHEMA_0004_COLUMNS: dict[str, tuple[str, ...]] = {
    "encounters": ("triage_category",),
    "care_tasks": ("cancelled_at", "cancel_reason"),
}
TRIAGE_VALUES = ("red", "yellow", "green")
CANCEL_REASON_VALUES = ("clinical", "discharged", "capacity")
TRIAGE_CHECK = "triage_category IS NULL OR triage_category IN ('red','yellow','green')"
CANCEL_CHECKS: dict[str, str] = {
    "cancel_reason": "cancel_reason IS NULL OR cancel_reason IN ('clinical','discharged','capacity')",
    # Zaman ve neden birlikte vardır ya da birlikte yoktur.
    "cancel_pair": "(cancelled_at IS NULL) = (cancel_reason IS NULL)",
    # İptal bilgisi yalnız iptal edilmiş görevde bulunur.
    "cancel_status": "cancelled_at IS NULL OR status = 'cancelled'",
    "cancel_order": "cancelled_at IS NULL OR cancelled_at >= created_at",
}
CONSTRAINTS_0004: dict[str, tuple[str, str]] = {
    "ck_encounters_triage_category": ("encounters", TRIAGE_CHECK),
    **{f"ck_care_tasks_{name}": ("care_tasks", sql) for name, sql in CANCEL_CHECKS.items()},
}


def _pk() -> Column:
    return Column("id", BigInteger, Identity(always=False), primary_key=True)


def _ts(name: str, nullable: bool = False) -> Column:
    return Column(name, DateTime(timezone=True), nullable=nullable)


def _finite(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} > '-Infinity'::float8 AND {column} < 'Infinity'::float8",
        name=f"{column}_finite",
    )


def _sha256(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '^[0-9a-f]{{64}}$'", name=f"{column}_format")


def _range(start: str, end: str):
    """[başlangıç, bitiş) aralığı; bitiş NULL ise sınırsız."""
    return text(f"tstzrange({start}, {end}, '[)')")


# ---- Fiziksel yapı -------------------------------------------------------------------------

facilities = Table(
    "facilities",
    metadata,
    _pk(),
    Column("name", Text, nullable=False, unique=True),
    Column("timezone", Text, nullable=False, server_default="Europe/Istanbul"),
)

units = Table(
    "units",
    metadata,
    _pk(),
    Column("facility_id", BigInteger, ForeignKey("facilities.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    CheckConstraint("kind IN ('inpatient','emergency','outpatient')", name="kind"),
    UniqueConstraint("facility_id", "name"),
)

rooms = Table(
    "rooms",
    metadata,
    _pk(),
    Column("unit_id", BigInteger, ForeignKey("units.id"), nullable=False),
    Column("code", Text, nullable=False),
    UniqueConstraint("unit_id", "code"),
    UniqueConstraint("id", "unit_id"),
)

beds = Table(
    "beds",
    metadata,
    _pk(),
    Column("room_id", BigInteger, ForeignKey("rooms.id"), nullable=False),
    Column("code", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    UniqueConstraint("room_id", "code"),
)

# ---- Bakım akışı ---------------------------------------------------------------------------

patients = Table(
    "patients",
    metadata,
    _pk(),
    Column("synthetic_code", Text, nullable=False, unique=True),
    Column("is_synthetic", Boolean, nullable=False, server_default=text("true")),
    CheckConstraint("is_synthetic", name="is_synthetic"),
    CheckConstraint("synthetic_code ~ '^SYN-?[0-9A-Z]+$'", name="synthetic_code_format"),
)

encounters = Table(
    "encounters",
    metadata,
    _pk(),
    Column("patient_id", BigInteger, ForeignKey("patients.id"), nullable=False),
    Column("unit_id", BigInteger, ForeignKey("units.id"), nullable=False),
    Column("bed_id", BigInteger, ForeignKey("beds.id")),
    _ts("admitted_at"),
    _ts("discharged_at", nullable=True),
    # Revizyon 0004: yalnız üretici 2.0.0 doldurur; 1.0.0 kayıtlarında NULL kalır.
    Column("triage_category", Text),
    CheckConstraint("discharged_at IS NULL OR discharged_at > admitted_at", name="discharge_order"),
    CheckConstraint(TRIAGE_CHECK, name="triage_category"),
    UniqueConstraint("id", "unit_id"),
    ExcludeConstraint(
        ("bed_id", "="),
        (_range("admitted_at", "discharged_at"), "&&"),
        using="gist",
        where=text("bed_id IS NOT NULL"),
        name="ex_encounters_bed_overlap",
    ),
)

acuity_assessments = Table(
    "acuity_assessments",
    metadata,
    _pk(),
    Column("encounter_id", BigInteger, ForeignKey("encounters.id"), nullable=False),
    _ts("assessed_at"),
    Column("level", Integer, nullable=False),
    Column("multiplier", Double, nullable=False),
    CheckConstraint("level BETWEEN 1 AND 5", name="level"),
    CheckConstraint("multiplier > 0", name="multiplier_positive"),
    _finite("multiplier"),
    UniqueConstraint("encounter_id", "assessed_at"),
)

# ---- Personel ------------------------------------------------------------------------------

staff = Table(
    "staff",
    metadata,
    _pk(),
    Column("synthetic_code", Text, nullable=False, unique=True),
    Column("role", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    CheckConstraint(f"role IN ({ROLE_VALUES})", name="role"),
    CheckConstraint("synthetic_code ~ '^STAFF-?[0-9A-Z]+$'", name="synthetic_code_format"),
)

skills = Table(
    "skills",
    metadata,
    _pk(),
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
)

staff_skills = Table(
    "staff_skills",
    metadata,
    Column("staff_id", BigInteger, ForeignKey("staff.id"), nullable=False),
    Column("skill_id", BigInteger, ForeignKey("skills.id"), nullable=False),
    _ts("valid_from"),
    _ts("valid_until", nullable=True),
    PrimaryKeyConstraint("staff_id", "skill_id", "valid_from"),
    CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="validity"),
)

shift_templates = Table(
    "shift_templates",
    metadata,
    _pk(),
    Column("name", Text, nullable=False, unique=True),
    Column("start_local", Time(timezone=False), nullable=False),
    Column("duration_minutes", Integer, nullable=False),
    CheckConstraint("duration_minutes BETWEEN 1 AND 1440", name="duration"),
)

staff_shifts = Table(
    "staff_shifts",
    metadata,
    _pk(),
    Column("staff_id", BigInteger, ForeignKey("staff.id"), nullable=False),
    Column("unit_id", BigInteger, ForeignKey("units.id"), nullable=False),
    Column("template_id", BigInteger, ForeignKey("shift_templates.id")),
    _ts("starts_at"),
    _ts("ends_at"),
    Column("break_minutes", Integer, nullable=False, server_default=text("0")),
    CheckConstraint("break_minutes >= 0", name="break_minutes"),
    CheckConstraint("ends_at > starts_at", name="order"),
    UniqueConstraint("staff_id", "starts_at"),
    ExcludeConstraint(
        ("staff_id", "="),
        (_range("starts_at", "ends_at"), "&&"),
        using="gist",
        name="ex_staff_shifts_staff_overlap",
    ),
)

staff_unavailability = Table(
    "staff_unavailability",
    metadata,
    _pk(),
    Column("staff_id", BigInteger, ForeignKey("staff.id"), nullable=False),
    _ts("starts_at"),
    _ts("ends_at"),
    Column("reason", Text, nullable=False),
    CheckConstraint("ends_at > starts_at", name="order"),
)

# ---- Görevler ------------------------------------------------------------------------------

task_types = Table(
    "task_types",
    metadata,
    _pk(),
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("base_minutes", Double, nullable=False),
    Column("required_role", Text, nullable=False),
    Column("duration_source", Text, nullable=False, server_default="synthetic_assumption"),
    CheckConstraint("base_minutes > 0", name="base_minutes_positive"),
    _finite("base_minutes"),
    CheckConstraint(f"required_role IN ({ROLE_VALUES})", name="required_role"),
    CheckConstraint("duration_source = 'synthetic_assumption'", name="duration_source"),
)

task_skill_requirements = Table(
    "task_skill_requirements",
    metadata,
    Column("task_type_id", BigInteger, ForeignKey("task_types.id"), nullable=False),
    Column("skill_id", BigInteger, ForeignKey("skills.id"), nullable=False),
    PrimaryKeyConstraint("task_type_id", "skill_id"),
)

care_tasks = Table(
    "care_tasks",
    metadata,
    _pk(),
    Column("encounter_id", BigInteger, nullable=False),
    Column("unit_id", BigInteger, nullable=False),
    Column("task_type_id", BigInteger, ForeignKey("task_types.id"), nullable=False),
    _ts("created_at"),
    _ts("due_at"),
    Column("estimated_minutes", Double, nullable=False),
    Column("actual_minutes", Double),
    Column("status", Text, nullable=False),
    _ts("completed_at", nullable=True),
    # Revizyon 0004: iptal zamanı ve nedeni (üretici 2.0.0). 1.0.0 iptallerinde ikisi de NULL kalır.
    _ts("cancelled_at", nullable=True),
    Column("cancel_reason", Text),
    # Servis izolasyonu: görevin servisi başvurunun servisiyle aynı olmak zorundadır.
    ForeignKeyConstraint(["encounter_id", "unit_id"], ["encounters.id", "encounters.unit_id"]),
    CheckConstraint("estimated_minutes > 0", name="estimated_minutes_positive"),
    _finite("estimated_minutes"),
    CheckConstraint("actual_minutes > 0", name="actual_minutes_positive"),
    _finite("actual_minutes"),
    CheckConstraint("status IN ('pending','completed','cancelled')", name="status"),
    CheckConstraint(
        "(status = 'completed' AND completed_at IS NOT NULL AND actual_minutes IS NOT NULL) "
        "OR (status IN ('pending','cancelled') AND completed_at IS NULL)",
        name="completion",
    ),
    CheckConstraint("completed_at IS NULL OR completed_at >= created_at", name="completion_order"),
    *(CheckConstraint(sql, name=name) for name, sql in CANCEL_CHECKS.items()),
)

# ---- Gözlem, model, tahmin -----------------------------------------------------------------

workload_observations = Table(
    "workload_observations",
    metadata,
    _pk(),
    Column("unit_id", BigInteger, ForeignKey("units.id"), nullable=False),
    _ts("window_start"),
    _ts("window_end"),
    Column("observed_minutes", Double, nullable=False),
    Column("patient_count", Integer, nullable=False),
    _ts("available_at"),
    CheckConstraint("observed_minutes >= 0", name="observed_minutes_nonnegative"),
    _finite("observed_minutes"),
    CheckConstraint("patient_count >= 0", name="patient_count"),
    CheckConstraint("window_end > window_start", name="window_order"),
    CheckConstraint("available_at >= window_end", name="available_after_window"),
    UniqueConstraint("unit_id", "window_start", "window_end"),
)

model_runs = Table(
    "model_runs",
    metadata,
    _pk(),
    Column("model_name", Text, nullable=False),
    _ts("created_at"),
    _ts("train_start"),
    _ts("train_end"),
    _ts("test_start"),
    _ts("test_end"),
    Column("horizon_minutes", Integer, nullable=False),
    Column("mae", Double, nullable=False),
    Column("rmse", Double, nullable=False),
    Column("artifact_path", Text, nullable=False),
    Column("feature_spec", Text, nullable=False),
    Column("seed", Integer, nullable=False),
    CheckConstraint("model_name IN ('baseline','random_forest')", name="model_name"),
    CheckConstraint("horizon_minutes IN (30,60)", name="horizon"),
    CheckConstraint("mae >= 0", name="mae_nonnegative"),
    _finite("mae"),
    CheckConstraint("rmse >= 0", name="rmse_nonnegative"),
    _finite("rmse"),
    CheckConstraint(
        "train_start < train_end AND train_end <= test_start AND test_start < test_end",
        name="chronology",
    ),
)

workload_forecasts = Table(
    "workload_forecasts",
    metadata,
    _pk(),
    Column("model_run_id", BigInteger, ForeignKey("model_runs.id"), nullable=False),
    Column("unit_id", BigInteger, ForeignKey("units.id"), nullable=False),
    _ts("issued_at"),
    Column("horizon_minutes", Integer, nullable=False),
    Column("predicted_minutes", Double, nullable=False),
    CheckConstraint("horizon_minutes IN (30,60)", name="horizon"),
    CheckConstraint("predicted_minutes >= 0", name="predicted_minutes_nonnegative"),
    _finite("predicted_minutes"),
    UniqueConstraint(
        "model_run_id", "unit_id", "issued_at", "horizon_minutes", name="uq_workload_forecasts_run_unit_issued_horizon"
    ),
)

# ---- Optimizasyon --------------------------------------------------------------------------

optimization_runs = Table(
    "optimization_runs",
    metadata,
    _pk(),
    Column("unit_id", BigInteger, ForeignKey("units.id"), nullable=False),
    Column("forecast_id", BigInteger, ForeignKey("workload_forecasts.id")),
    _ts("starts_at"),
    _ts("ends_at"),
    _ts("created_at"),
    Column("status", Text, nullable=False),
    Column("objective_value", Double),
    Column("config_json", JSONB, nullable=False),
    Column("solver_seconds", Double, nullable=False),
    CheckConstraint("status IN ('OPTIMAL','FEASIBLE','INFEASIBLE','UNKNOWN','MODEL_INVALID')", name="status"),
    _finite("objective_value"),
    CheckConstraint("solver_seconds >= 0", name="solver_seconds_nonnegative"),
    _finite("solver_seconds"),
    CheckConstraint("ends_at > starts_at", name="order"),
)

assignments = Table(
    "assignments",
    metadata,
    _pk(),
    Column("run_id", BigInteger, ForeignKey("optimization_runs.id"), nullable=False),
    Column("task_id", BigInteger, ForeignKey("care_tasks.id"), nullable=False),
    Column("staff_id", BigInteger, ForeignKey("staff.id"), nullable=False),
    _ts("starts_at"),
    _ts("ends_at"),
    CheckConstraint("ends_at > starts_at", name="order"),
    UniqueConstraint("run_id", "task_id"),
    ExcludeConstraint(
        ("run_id", "="),
        ("staff_id", "="),
        (_range("starts_at", "ends_at"), "&&"),
        using="gist",
        name="ex_assignments_staff_overlap",
    ),
)

staffing_gaps = Table(
    "staffing_gaps",
    metadata,
    _pk(),
    Column("run_id", BigInteger, ForeignKey("optimization_runs.id"), nullable=False),
    Column("task_id", BigInteger, ForeignKey("care_tasks.id")),
    Column("required_role", Text, nullable=False),
    Column("missing_minutes", Double, nullable=False),
    Column("reason", Text, nullable=False),
    CheckConstraint(f"required_role IN ({ROLE_VALUES})", name="required_role"),
    CheckConstraint("missing_minutes > 0", name="missing_minutes_positive"),
    _finite("missing_minutes"),
    UniqueConstraint("run_id", "task_id"),
)

audit_events = Table(
    "audit_events",
    metadata,
    _pk(),
    _ts("occurred_at"),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text),
    Column("detail_json", JSONB, nullable=False),
)

Index("idx_tasks_unit_due", care_tasks.c.unit_id, care_tasks.c.due_at, care_tasks.c.status)
Index("idx_encounters_unit", encounters.c.unit_id, encounters.c.admitted_at)
Index("idx_shifts_staff_time", staff_shifts.c.staff_id, staff_shifts.c.starts_at, staff_shifts.c.ends_at)
Index(
    "idx_unavailable_staff_time",
    staff_unavailability.c.staff_id,
    staff_unavailability.c.starts_at,
    staff_unavailability.c.ends_at,
)
Index("idx_observations_unit_time", workload_observations.c.unit_id, workload_observations.c.window_start)
Index("idx_forecasts_unit_time", workload_forecasts.c.unit_id, workload_forecasts.c.issued_at)
Index("idx_assignments_staff_time", assignments.c.staff_id, assignments.c.starts_at, assignments.c.ends_at)

# ---- Yönetici onayı ve RAG -----------------------------------------------------------------

plan_approvals = Table(
    "plan_approvals",
    metadata,
    _pk(),
    Column("run_id", BigInteger, ForeignKey("optimization_runs.id"), nullable=False),
    Column("decision", Text, nullable=False),
    Column("manager_actor", Text, nullable=False),
    _ts("decided_at"),
    Column("plan_sha256", Text, nullable=False),
    Column("note", Text, nullable=False, server_default=""),
    CheckConstraint("decision IN ('approved','rejected','revoked')", name="decision"),
    CheckConstraint("length(trim(manager_actor)) > 0", name="manager_actor"),
    _sha256("plan_sha256"),
)
Index("idx_approvals_run", plan_approvals.c.run_id, plan_approvals.c.decided_at, plan_approvals.c.id)

rag_documents = Table(
    "rag_documents",
    metadata,
    _pk(),
    Column("source_key", Text, nullable=False),
    Column("source_name", Text, nullable=False),
    Column("doc_type", Text, nullable=False),
    Column("version", Text, nullable=False),
    _ts("valid_from"),
    _ts("valid_until", nullable=True),
    Column("status", Text, nullable=False),
    Column("approved_by", Text),
    _ts("approved_at", nullable=True),
    Column("content_sha256", Text, nullable=False),
    Column("local_path", Text, nullable=False),
    Column("is_synthetic", Boolean, nullable=False, server_default=text("true")),
    CheckConstraint("doc_type IN ('procedure','job_description','competency','workflow')", name="doc_type"),
    CheckConstraint("status IN ('draft','approved','retired')", name="status"),
    _sha256("content_sha256"),
    CheckConstraint("is_synthetic", name="is_synthetic"),
    CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="validity"),
    CheckConstraint(
        "status != 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
        name="approval_required",
    ),
    UniqueConstraint("source_key", "version"),
    ExcludeConstraint(
        ("source_key", "="),
        (_range("valid_from", "valid_until"), "&&"),
        using="gist",
        where=text("status = 'approved'"),
        name="ex_rag_documents_approved_overlap",
    ),
)

rag_chunks = Table(
    "rag_chunks",
    metadata,
    _pk(),
    Column("document_id", BigInteger, ForeignKey("rag_documents.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("section", Text, nullable=False),
    Column("text_content", Text, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("token_count", Integer, nullable=False),
    CheckConstraint("ordinal >= 0", name="ordinal"),
    CheckConstraint("length(trim(text_content)) > 0", name="text_content"),
    _sha256("content_sha256"),
    CheckConstraint("token_count > 0", name="token_count"),
    UniqueConstraint("document_id", "ordinal"),
)

rag_indexes = Table(
    "rag_indexes",
    metadata,
    _pk(),
    Column("collection_name", Text, nullable=False, unique=True),
    Column("embedding_model", Text, nullable=False),
    Column("model_digest", Text, nullable=False),
    Column("dimensions", Integer, nullable=False),
    Column("distance_metric", Text, nullable=False),
    Column("chunk_config_json", JSONB, nullable=False),
    Column("corpus_sha256", Text, nullable=False),
    Column("state", Text, nullable=False),
    _ts("created_at"),
    CheckConstraint("dimensions > 0", name="dimensions"),
    CheckConstraint("distance_metric = 'cosine'", name="distance_metric"),
    _sha256("corpus_sha256"),
    CheckConstraint("state IN ('building','ready','failed','retired')", name="state"),
)

rag_index_chunks = Table(
    "rag_index_chunks",
    metadata,
    Column("index_id", BigInteger, ForeignKey("rag_indexes.id"), nullable=False),
    Column("chunk_id", BigInteger, ForeignKey("rag_chunks.id"), nullable=False),
    Column("vector_id", Text, nullable=False),
    PrimaryKeyConstraint("index_id", "chunk_id"),
    UniqueConstraint("index_id", "vector_id"),
)

rag_queries = Table(
    "rag_queries",
    metadata,
    _pk(),
    Column("index_id", BigInteger, ForeignKey("rag_indexes.id"), nullable=False),
    _ts("asked_at"),
    _ts("as_of"),
    Column("question", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("answer", Text),
    Column("generator_model", Text),
    Column("generator_digest", Text),
    Column("prompt_version", Text, nullable=False),
    Column("retrieval_ms", Double, nullable=False),
    Column("generation_ms", Double, nullable=False),
    Column("total_ms", Double, nullable=False),
    Column("config_json", JSONB, nullable=False),
    CheckConstraint(
        "outcome IN ('answered','no_source','conflicting_sources','unavailable','rejected')",
        name="outcome",
    ),
    CheckConstraint("retrieval_ms >= 0", name="retrieval_ms_nonnegative"),
    _finite("retrieval_ms"),
    CheckConstraint("generation_ms >= 0", name="generation_ms_nonnegative"),
    _finite("generation_ms"),
    CheckConstraint("total_ms >= 0", name="total_ms_nonnegative"),
    _finite("total_ms"),
    CheckConstraint(
        "outcome != 'answered' OR (answer IS NOT NULL AND length(trim(answer)) > 0)",
        name="answered_requires_text",
    ),
)
Index("idx_rag_queries_time", rag_queries.c.asked_at)

rag_retrieval_hits = Table(
    "rag_retrieval_hits",
    metadata,
    Column("query_id", BigInteger, ForeignKey("rag_queries.id"), nullable=False),
    Column("chunk_id", BigInteger, ForeignKey("rag_chunks.id"), nullable=False),
    Column("rank", Integer, nullable=False),
    Column("cosine_distance", Double, nullable=False),
    Column("cited", Boolean, nullable=False, server_default=text("false")),
    PrimaryKeyConstraint("query_id", "rank"),
    CheckConstraint("rank > 0", name="rank"),
    CheckConstraint("cosine_distance >= 0 AND cosine_distance <= 2.00001", name="cosine_distance"),
    _finite("cosine_distance"),
    UniqueConstraint("query_id", "chunk_id"),
)

rag_eval_sets = Table(
    "rag_eval_sets",
    metadata,
    _pk(),
    Column("name", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("content_sha256", Text, nullable=False),
    _ts("created_at"),
    _sha256("content_sha256"),
    UniqueConstraint("name", "version"),
)

rag_eval_cases = Table(
    "rag_eval_cases",
    metadata,
    _pk(),
    Column("set_id", BigInteger, ForeignKey("rag_eval_sets.id"), nullable=False),
    Column("case_key", Text, nullable=False),
    Column("split", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("question", Text, nullable=False),
    _ts("as_of"),
    Column("answerable", Boolean, nullable=False),
    Column("relevant_chunk_ids_json", JSONB, nullable=False),
    Column("expected_claims_json", JSONB, nullable=False),
    CheckConstraint("split IN ('dev','test')", name="split"),
    UniqueConstraint("set_id", "case_key"),
)

rag_eval_runs = Table(
    "rag_eval_runs",
    metadata,
    _pk(),
    Column("set_id", BigInteger, ForeignKey("rag_eval_sets.id"), nullable=False),
    Column("index_id", BigInteger, ForeignKey("rag_indexes.id"), nullable=False),
    _ts("created_at"),
    Column("generator_model", Text),
    Column("config_json", JSONB, nullable=False),
    Column("metrics_json", JSONB, nullable=False),
)

rag_eval_results = Table(
    "rag_eval_results",
    metadata,
    Column("run_id", BigInteger, ForeignKey("rag_eval_runs.id"), nullable=False),
    Column("case_id", BigInteger, ForeignKey("rag_eval_cases.id"), nullable=False),
    Column("query_id", BigInteger, ForeignKey("rag_queries.id")),
    Column("recall_at_5", Double),
    Column("reciprocal_rank_at_10", Double),
    Column("citation_accuracy", Double),
    Column("faithfulness", Double),
    Column("latency_ms", Double, nullable=False),
    Column("abstention_correct", Boolean),
    Column("reviewer", Text),
    Column("review_note", Text, nullable=False, server_default=""),
    PrimaryKeyConstraint("run_id", "case_id"),
    CheckConstraint("recall_at_5 BETWEEN 0 AND 1", name="recall_at_5"),
    CheckConstraint("reciprocal_rank_at_10 BETWEEN 0 AND 1", name="reciprocal_rank_at_10"),
    CheckConstraint("citation_accuracy BETWEEN 0 AND 1", name="citation_accuracy"),
    CheckConstraint("faithfulness BETWEEN 0 AND 1", name="faithfulness"),
    CheckConstraint("latency_ms >= 0", name="latency_ms_nonnegative"),
    _finite("latency_ms"),
)

Index("idx_rag_documents_validity", rag_documents.c.status, rag_documents.c.valid_from, rag_documents.c.valid_until)
Index("idx_rag_chunks_document", rag_chunks.c.document_id, rag_chunks.c.ordinal)

# ---- Yetkiler ------------------------------------------------------------------------------
# Alembic revizyonları ve yetki testleri bu listeyi kullanır (revizyonlar kendi kopyasını taşır).
# DELETE ve TRUNCATE hiçbir uygulama rolüne verilmez: kayıtlar silinmez, geçersiz kılınır.
# Tek istisna: medakis_seed, yalnız SEED_RESET_TABLES üzerinde DELETE yapar. medakis_test'te bu
# yetki kalıcıdır; medakis_dev'de yalnız seed işlemi süresince owner tarafından açılır (seed_access).

_APP_READ_WRITE = (
    "facilities", "units", "rooms", "beds", "patients", "encounters", "acuity_assessments",
    "staff", "skills", "staff_skills", "shift_templates", "staff_shifts", "staff_unavailability",
    "task_types", "task_skill_requirements", "care_tasks", "workload_observations",
    "rag_documents", "rag_chunks", "rag_indexes", "rag_index_chunks",
)  # fmt: skip
_APP_APPEND_ONLY = (
    "model_runs", "workload_forecasts", "optimization_runs", "assignments", "staffing_gaps",
    "audit_events", "plan_approvals", "rag_eval_sets", "rag_eval_cases",
)  # fmt: skip
_APP_READ_ONLY = ("rag_queries", "rag_retrieval_hits", "rag_eval_runs", "rag_eval_results")
_RAG_READ_ONLY = ("rag_documents", "rag_chunks", "rag_indexes", "rag_index_chunks", "rag_eval_sets", "rag_eval_cases")
_RAG_APPEND_ONLY = ("rag_queries", "rag_retrieval_hits", "rag_eval_runs", "rag_eval_results")

# ---- Sentetik veri sıfırlama kapsamı (FAZ 1.2C) --------------------------------------------
# Yükleme sırası (ebeveynden çocuğa). Silme bunun tersidir. TRUNCATE kullanılmaz: PostgreSQL,
# bağlı tablolar boş olsa bile onları aynı komuta katmayı ister; bu da tarihsel tabloları silerdi.
SEED_RESET_TABLES = (
    "facilities", "units", "rooms", "beds", "patients", "encounters", "acuity_assessments",
    "staff", "skills", "staff_skills", "shift_templates", "staff_shifts", "staff_unavailability",
    "task_types", "task_skill_requirements", "care_tasks", "workload_observations",
)  # fmt: skip
# Hiçbir zaman silinmez. Bu tablolarda kayıt varsa sıfırlama durur (audit_events hariç: ona yalnız eklenir).
SEED_PROTECTED_HISTORY = (
    "model_runs", "workload_forecasts", "optimization_runs", "assignments", "staffing_gaps", "plan_approvals",
)  # fmt: skip
SEED_AUDIT_TABLE = "audit_events"

PRIVILEGES: dict[str, dict[str, frozenset[str]]] = {
    "medakis_app": {
        **{t: frozenset({"SELECT", "INSERT", "UPDATE"}) for t in _APP_READ_WRITE},
        **{t: frozenset({"SELECT", "INSERT"}) for t in _APP_APPEND_ONLY},
        **{t: frozenset({"SELECT"}) for t in _APP_READ_ONLY},
    },
    # medakis_rag iş tablolarını okuyamaz da: operasyonel özet RAG'e servis üzerinden DTO olarak gelir.
    "medakis_rag": {
        **{t: frozenset({"SELECT"}) for t in _RAG_READ_ONLY},
        **{t: frozenset({"SELECT", "INSERT"}) for t in _RAG_APPEND_ONLY},
    },
    # Yalnız medakis_dev/medakis_test'e bağlanabilir (provision.sql). RAG tablolarına erişimi yoktur.
    # Bu küme medakis_test'teki kalıcı yetkidir ve medakis_dev'de yazma penceresi açıkken geçerlidir.
    # MAINTAIN yalnız LOCK TABLE içindir: seed, tarihsel tablolara yazılmasını işlem boyunca engeller.
    "medakis_seed": {
        **{t: frozenset({"SELECT", "INSERT", "DELETE"}) for t in SEED_RESET_TABLES},
        **{t: frozenset({"SELECT", "MAINTAIN"}) for t in SEED_PROTECTED_HISTORY},
        SEED_AUDIT_TABLE: frozenset({"SELECT", "INSERT"}),
    },
}

# medakis_dev'de yazma penceresi kapalıyken medakis_seed yalnız okur (FAZ 1.2C düzeltmesi, F3).
SEED_IDLE_PRIVILEGES: dict[str, frozenset[str]] = {
    t: frozenset({"SELECT"}) for t in (*SEED_RESET_TABLES, *SEED_PROTECTED_HISTORY, SEED_AUDIT_TABLE)
}
# Yetkisi kalıcı olan (pencere gerektirmeyen) tek seed hedefi.
SEED_PERMANENT_WRITE_DATABASES = frozenset({"medakis_test"})


def legacy_columns(table: Table) -> list[Column]:
    """Revizyon 0003 kolonları (0004 kolonları hariç), tablo sırasıyla.

    0003'te kalan veritabanları (medakis_dev) bu listeyle okunur; tam tablo seçimi orada hata verir.
    """
    extra = SCHEMA_0004_COLUMNS.get(table.name, ())
    return [c for c in table.columns if c.name not in extra]
