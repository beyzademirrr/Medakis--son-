"""v2 baseline

Revision ID: 0001_v2_baseline
Revises: 
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_v2_baseline'
down_revision = None
branch_labels = None
depends_on = None

# Table privileges at this revision. Later changes belong in new revisions.
# DELETE and TRUNCATE are granted to no application role.
APP_READ_WRITE = (
    "facilities", "units", "rooms", "beds", "patients", "encounters", "acuity_assessments",
    "staff", "skills", "staff_skills", "shift_templates", "staff_shifts", "staff_unavailability",
    "task_types", "task_skill_requirements", "care_tasks", "workload_observations",
    "rag_documents", "rag_chunks", "rag_indexes", "rag_index_chunks",
)
APP_APPEND_ONLY = (
    "model_runs", "workload_forecasts", "optimization_runs", "assignments", "staffing_gaps",
    "audit_events", "plan_approvals", "rag_eval_sets", "rag_eval_cases",
)
APP_READ_ONLY = ("rag_queries", "rag_retrieval_hits", "rag_eval_runs", "rag_eval_results")
RAG_READ_ONLY = ("rag_documents", "rag_chunks", "rag_indexes", "rag_index_chunks", "rag_eval_sets", "rag_eval_cases")
RAG_APPEND_ONLY = ("rag_queries", "rag_retrieval_hits", "rag_eval_runs", "rag_eval_results")


def _require_prerequisites() -> None:
    connection = op.get_bind()
    has_extension = connection.execute(
        sa.text("SELECT count(*) FROM pg_extension WHERE extname = 'btree_gist'")
    ).scalar_one()
    if not has_extension:
        raise RuntimeError("btree_gist is missing: run scripts/db/setup.ps1 first.")
    roles = connection.execute(
        sa.text("SELECT count(*) FROM pg_roles WHERE rolname IN ('medakis_app', 'medakis_rag')")
    ).scalar_one()
    if roles != 2:
        raise RuntimeError("Application roles are missing: run scripts/db/setup.ps1 first.")


def _grant(privileges: str, tables: tuple[str, ...], role: str) -> None:
    op.execute(f"GRANT {privileges} ON {', '.join(tables)} TO {role}")


def upgrade() -> None:
    _require_prerequisites()
    op.create_table('audit_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('entity_type', sa.Text(), nullable=False),
    sa.Column('entity_id', sa.Text(), nullable=True),
    sa.Column('detail_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events'))
    )
    op.create_table('facilities',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('timezone', sa.Text(), server_default='Europe/Istanbul', nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_facilities')),
    sa.UniqueConstraint('name', name=op.f('uq_facilities_name'))
    )
    op.create_table('model_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('model_name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('train_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('train_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('test_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('test_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('horizon_minutes', sa.Integer(), nullable=False),
    sa.Column('mae', sa.Double(), nullable=False),
    sa.Column('rmse', sa.Double(), nullable=False),
    sa.Column('artifact_path', sa.Text(), nullable=False),
    sa.Column('feature_spec', sa.Text(), nullable=False),
    sa.Column('seed', sa.Integer(), nullable=False),
    sa.CheckConstraint("mae > '-Infinity'::float8 AND mae < 'Infinity'::float8", name=op.f('ck_model_runs_mae_finite')),
    sa.CheckConstraint("model_name IN ('baseline','random_forest')", name=op.f('ck_model_runs_model_name')),
    sa.CheckConstraint("rmse > '-Infinity'::float8 AND rmse < 'Infinity'::float8", name=op.f('ck_model_runs_rmse_finite')),
    sa.CheckConstraint('horizon_minutes IN (30,60)', name=op.f('ck_model_runs_horizon')),
    sa.CheckConstraint('mae >= 0', name=op.f('ck_model_runs_mae_nonnegative')),
    sa.CheckConstraint('rmse >= 0', name=op.f('ck_model_runs_rmse_nonnegative')),
    sa.CheckConstraint('train_start < train_end AND train_end <= test_start AND test_start < test_end', name=op.f('ck_model_runs_chronology')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_model_runs'))
    )
    op.create_table('patients',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('synthetic_code', sa.Text(), nullable=False),
    sa.Column('is_synthetic', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("synthetic_code ~ '^SYN-?[0-9A-Z]+$'", name=op.f('ck_patients_synthetic_code_format')),
    sa.CheckConstraint('is_synthetic', name=op.f('ck_patients_is_synthetic')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_patients')),
    sa.UniqueConstraint('synthetic_code', name=op.f('uq_patients_synthetic_code'))
    )
    op.create_table('rag_documents',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('source_key', sa.Text(), nullable=False),
    sa.Column('source_name', sa.Text(), nullable=False),
    sa.Column('doc_type', sa.Text(), nullable=False),
    sa.Column('version', sa.Text(), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('approved_by', sa.Text(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('local_path', sa.Text(), nullable=False),
    sa.Column('is_synthetic', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    postgresql.ExcludeConstraint((sa.column('source_key'), '='), (sa.text("tstzrange(valid_from, valid_until, '[)')"), '&&'), where=sa.text("status = 'approved'"), using='gist', name='ex_rag_documents_approved_overlap'),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_rag_documents_content_sha256_format')),
    sa.CheckConstraint("doc_type IN ('procedure','job_description','competency','workflow')", name=op.f('ck_rag_documents_doc_type')),
    sa.CheckConstraint("status != 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)", name=op.f('ck_rag_documents_approval_required')),
    sa.CheckConstraint("status IN ('draft','approved','retired')", name=op.f('ck_rag_documents_status')),
    sa.CheckConstraint('is_synthetic', name=op.f('ck_rag_documents_is_synthetic')),
    sa.CheckConstraint('valid_until IS NULL OR valid_until > valid_from', name=op.f('ck_rag_documents_validity')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rag_documents')),
    sa.UniqueConstraint('source_key', 'version', name=op.f('uq_rag_documents_source_key_version'))
    )
    op.create_index('idx_rag_documents_validity', 'rag_documents', ['status', 'valid_from', 'valid_until'], unique=False)
    op.create_table('rag_eval_sets',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('version', sa.Text(), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_rag_eval_sets_content_sha256_format')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rag_eval_sets')),
    sa.UniqueConstraint('name', 'version', name=op.f('uq_rag_eval_sets_name_version'))
    )
    op.create_table('rag_indexes',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('collection_name', sa.Text(), nullable=False),
    sa.Column('embedding_model', sa.Text(), nullable=False),
    sa.Column('model_digest', sa.Text(), nullable=False),
    sa.Column('dimensions', sa.Integer(), nullable=False),
    sa.Column('distance_metric', sa.Text(), nullable=False),
    sa.Column('chunk_config_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('corpus_sha256', sa.Text(), nullable=False),
    sa.Column('state', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("corpus_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_rag_indexes_corpus_sha256_format')),
    sa.CheckConstraint("distance_metric = 'cosine'", name=op.f('ck_rag_indexes_distance_metric')),
    sa.CheckConstraint("state IN ('building','ready','failed','retired')", name=op.f('ck_rag_indexes_state')),
    sa.CheckConstraint('dimensions > 0', name=op.f('ck_rag_indexes_dimensions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rag_indexes')),
    sa.UniqueConstraint('collection_name', name=op.f('uq_rag_indexes_collection_name'))
    )
    op.create_table('shift_templates',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('start_local', sa.Time(), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), nullable=False),
    sa.CheckConstraint('duration_minutes BETWEEN 1 AND 1440', name=op.f('ck_shift_templates_duration')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shift_templates')),
    sa.UniqueConstraint('name', name=op.f('uq_shift_templates_name'))
    )
    op.create_table('skills',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_skills')),
    sa.UniqueConstraint('code', name=op.f('uq_skills_code'))
    )
    op.create_table('staff',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('synthetic_code', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("role IN ('nurse','supervisor','support')", name=op.f('ck_staff_role')),
    sa.CheckConstraint("synthetic_code ~ '^STAFF-?[0-9A-Z]+$'", name=op.f('ck_staff_synthetic_code_format')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff')),
    sa.UniqueConstraint('synthetic_code', name=op.f('uq_staff_synthetic_code'))
    )
    op.create_table('task_types',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('base_minutes', sa.Double(), nullable=False),
    sa.Column('required_role', sa.Text(), nullable=False),
    sa.Column('duration_source', sa.Text(), server_default='synthetic_assumption', nullable=False),
    sa.CheckConstraint("base_minutes > '-Infinity'::float8 AND base_minutes < 'Infinity'::float8", name=op.f('ck_task_types_base_minutes_finite')),
    sa.CheckConstraint("duration_source = 'synthetic_assumption'", name=op.f('ck_task_types_duration_source')),
    sa.CheckConstraint("required_role IN ('nurse','supervisor','support')", name=op.f('ck_task_types_required_role')),
    sa.CheckConstraint('base_minutes > 0', name=op.f('ck_task_types_base_minutes_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_task_types')),
    sa.UniqueConstraint('code', name=op.f('uq_task_types_code'))
    )
    op.create_table('rag_chunks',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('document_id', sa.BigInteger(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('section', sa.Text(), nullable=False),
    sa.Column('text_content', sa.Text(), nullable=False),
    sa.Column('content_sha256', sa.Text(), nullable=False),
    sa.Column('token_count', sa.Integer(), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_rag_chunks_content_sha256_format')),
    sa.CheckConstraint('length(trim(text_content)) > 0', name=op.f('ck_rag_chunks_text_content')),
    sa.CheckConstraint('ordinal >= 0', name=op.f('ck_rag_chunks_ordinal')),
    sa.CheckConstraint('token_count > 0', name=op.f('ck_rag_chunks_token_count')),
    sa.ForeignKeyConstraint(['document_id'], ['rag_documents.id'], name=op.f('fk_rag_chunks_document_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rag_chunks')),
    sa.UniqueConstraint('document_id', 'ordinal', name=op.f('uq_rag_chunks_document_id_ordinal'))
    )
    op.create_index('idx_rag_chunks_document', 'rag_chunks', ['document_id', 'ordinal'], unique=False)
    op.create_table('rag_eval_cases',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('set_id', sa.BigInteger(), nullable=False),
    sa.Column('case_key', sa.Text(), nullable=False),
    sa.Column('split', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('answerable', sa.Boolean(), nullable=False),
    sa.Column('relevant_chunk_ids_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('expected_claims_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint("split IN ('dev','test')", name=op.f('ck_rag_eval_cases_split')),
    sa.ForeignKeyConstraint(['set_id'], ['rag_eval_sets.id'], name=op.f('fk_rag_eval_cases_set_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rag_eval_cases')),
    sa.UniqueConstraint('set_id', 'case_key', name=op.f('uq_rag_eval_cases_set_id_case_key'))
    )
    op.create_table('rag_eval_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('set_id', sa.BigInteger(), nullable=False),
    sa.Column('index_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('generator_model', sa.Text(), nullable=True),
    sa.Column('config_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('metrics_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.ForeignKeyConstraint(['index_id'], ['rag_indexes.id'], name=op.f('fk_rag_eval_runs_index_id')),
    sa.ForeignKeyConstraint(['set_id'], ['rag_eval_sets.id'], name=op.f('fk_rag_eval_runs_set_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rag_eval_runs'))
    )
    op.create_table('rag_queries',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('index_id', sa.BigInteger(), nullable=False),
    sa.Column('asked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('outcome', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('generator_model', sa.Text(), nullable=True),
    sa.Column('generator_digest', sa.Text(), nullable=True),
    sa.Column('prompt_version', sa.Text(), nullable=False),
    sa.Column('retrieval_ms', sa.Double(), nullable=False),
    sa.Column('generation_ms', sa.Double(), nullable=False),
    sa.Column('total_ms', sa.Double(), nullable=False),
    sa.Column('config_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint("generation_ms > '-Infinity'::float8 AND generation_ms < 'Infinity'::float8", name=op.f('ck_rag_queries_generation_ms_finite')),
    sa.CheckConstraint("outcome != 'answered' OR (answer IS NOT NULL AND length(trim(answer)) > 0)", name=op.f('ck_rag_queries_answered_requires_text')),
    sa.CheckConstraint("outcome IN ('answered','no_source','conflicting_sources','unavailable','rejected')", name=op.f('ck_rag_queries_outcome')),
    sa.CheckConstraint("retrieval_ms > '-Infinity'::float8 AND retrieval_ms < 'Infinity'::float8", name=op.f('ck_rag_queries_retrieval_ms_finite')),
    sa.CheckConstraint("total_ms > '-Infinity'::float8 AND total_ms < 'Infinity'::float8", name=op.f('ck_rag_queries_total_ms_finite')),
    sa.CheckConstraint('generation_ms >= 0', name=op.f('ck_rag_queries_generation_ms_nonnegative')),
    sa.CheckConstraint('retrieval_ms >= 0', name=op.f('ck_rag_queries_retrieval_ms_nonnegative')),
    sa.CheckConstraint('total_ms >= 0', name=op.f('ck_rag_queries_total_ms_nonnegative')),
    sa.ForeignKeyConstraint(['index_id'], ['rag_indexes.id'], name=op.f('fk_rag_queries_index_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rag_queries'))
    )
    op.create_index('idx_rag_queries_time', 'rag_queries', ['asked_at'], unique=False)
    op.create_table('staff_skills',
    sa.Column('staff_id', sa.BigInteger(), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('valid_until IS NULL OR valid_until > valid_from', name=op.f('ck_staff_skills_validity')),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_staff_skills_skill_id')),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_staff_skills_staff_id')),
    sa.PrimaryKeyConstraint('staff_id', 'skill_id', 'valid_from', name=op.f('pk_staff_skills'))
    )
    op.create_table('staff_unavailability',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('staff_id', sa.BigInteger(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_staff_unavailability_order')),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_staff_unavailability_staff_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff_unavailability'))
    )
    op.create_index('idx_unavailable_staff_time', 'staff_unavailability', ['staff_id', 'starts_at', 'ends_at'], unique=False)
    op.create_table('task_skill_requirements',
    sa.Column('task_type_id', sa.BigInteger(), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_task_skill_requirements_skill_id')),
    sa.ForeignKeyConstraint(['task_type_id'], ['task_types.id'], name=op.f('fk_task_skill_requirements_task_type_id')),
    sa.PrimaryKeyConstraint('task_type_id', 'skill_id', name=op.f('pk_task_skill_requirements'))
    )
    op.create_table('units',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('facility_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("kind IN ('inpatient','emergency','outpatient')", name=op.f('ck_units_kind')),
    sa.ForeignKeyConstraint(['facility_id'], ['facilities.id'], name=op.f('fk_units_facility_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_units')),
    sa.UniqueConstraint('facility_id', 'name', name=op.f('uq_units_facility_id_name'))
    )
    op.create_table('rag_eval_results',
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('case_id', sa.BigInteger(), nullable=False),
    sa.Column('query_id', sa.BigInteger(), nullable=True),
    sa.Column('recall_at_5', sa.Double(), nullable=True),
    sa.Column('reciprocal_rank_at_10', sa.Double(), nullable=True),
    sa.Column('citation_accuracy', sa.Double(), nullable=True),
    sa.Column('faithfulness', sa.Double(), nullable=True),
    sa.Column('latency_ms', sa.Double(), nullable=False),
    sa.Column('abstention_correct', sa.Boolean(), nullable=True),
    sa.Column('reviewer', sa.Text(), nullable=True),
    sa.Column('review_note', sa.Text(), server_default='', nullable=False),
    sa.CheckConstraint("latency_ms > '-Infinity'::float8 AND latency_ms < 'Infinity'::float8", name=op.f('ck_rag_eval_results_latency_ms_finite')),
    sa.CheckConstraint('citation_accuracy BETWEEN 0 AND 1', name=op.f('ck_rag_eval_results_citation_accuracy')),
    sa.CheckConstraint('faithfulness BETWEEN 0 AND 1', name=op.f('ck_rag_eval_results_faithfulness')),
    sa.CheckConstraint('latency_ms >= 0', name=op.f('ck_rag_eval_results_latency_ms_nonnegative')),
    sa.CheckConstraint('recall_at_5 BETWEEN 0 AND 1', name=op.f('ck_rag_eval_results_recall_at_5')),
    sa.CheckConstraint('reciprocal_rank_at_10 BETWEEN 0 AND 1', name=op.f('ck_rag_eval_results_reciprocal_rank_at_10')),
    sa.ForeignKeyConstraint(['case_id'], ['rag_eval_cases.id'], name=op.f('fk_rag_eval_results_case_id')),
    sa.ForeignKeyConstraint(['query_id'], ['rag_queries.id'], name=op.f('fk_rag_eval_results_query_id')),
    sa.ForeignKeyConstraint(['run_id'], ['rag_eval_runs.id'], name=op.f('fk_rag_eval_results_run_id')),
    sa.PrimaryKeyConstraint('run_id', 'case_id', name=op.f('pk_rag_eval_results'))
    )
    op.create_table('rag_index_chunks',
    sa.Column('index_id', sa.BigInteger(), nullable=False),
    sa.Column('chunk_id', sa.BigInteger(), nullable=False),
    sa.Column('vector_id', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['rag_chunks.id'], name=op.f('fk_rag_index_chunks_chunk_id')),
    sa.ForeignKeyConstraint(['index_id'], ['rag_indexes.id'], name=op.f('fk_rag_index_chunks_index_id')),
    sa.PrimaryKeyConstraint('index_id', 'chunk_id', name=op.f('pk_rag_index_chunks')),
    sa.UniqueConstraint('index_id', 'vector_id', name=op.f('uq_rag_index_chunks_index_id_vector_id'))
    )
    op.create_table('rag_retrieval_hits',
    sa.Column('query_id', sa.BigInteger(), nullable=False),
    sa.Column('chunk_id', sa.BigInteger(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('cosine_distance', sa.Double(), nullable=False),
    sa.Column('cited', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint("cosine_distance > '-Infinity'::float8 AND cosine_distance < 'Infinity'::float8", name=op.f('ck_rag_retrieval_hits_cosine_distance_finite')),
    sa.CheckConstraint('cosine_distance >= 0 AND cosine_distance <= 2.00001', name=op.f('ck_rag_retrieval_hits_cosine_distance')),
    sa.CheckConstraint('rank > 0', name=op.f('ck_rag_retrieval_hits_rank')),
    sa.ForeignKeyConstraint(['chunk_id'], ['rag_chunks.id'], name=op.f('fk_rag_retrieval_hits_chunk_id')),
    sa.ForeignKeyConstraint(['query_id'], ['rag_queries.id'], name=op.f('fk_rag_retrieval_hits_query_id')),
    sa.PrimaryKeyConstraint('query_id', 'rank', name=op.f('pk_rag_retrieval_hits')),
    sa.UniqueConstraint('query_id', 'chunk_id', name=op.f('uq_rag_retrieval_hits_query_id_chunk_id'))
    )
    op.create_table('rooms',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('unit_id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], name=op.f('fk_rooms_unit_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rooms')),
    sa.UniqueConstraint('id', 'unit_id', name=op.f('uq_rooms_id_unit_id')),
    sa.UniqueConstraint('unit_id', 'code', name=op.f('uq_rooms_unit_id_code'))
    )
    op.create_table('staff_shifts',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('staff_id', sa.BigInteger(), nullable=False),
    sa.Column('unit_id', sa.BigInteger(), nullable=False),
    sa.Column('template_id', sa.BigInteger(), nullable=True),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('break_minutes', sa.Integer(), server_default=sa.text('0'), nullable=False),
    postgresql.ExcludeConstraint((sa.column('staff_id'), '='), (sa.text("tstzrange(starts_at, ends_at, '[)')"), '&&'), using='gist', name='ex_staff_shifts_staff_overlap'),
    sa.CheckConstraint('break_minutes >= 0', name=op.f('ck_staff_shifts_break_minutes')),
    sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_staff_shifts_order')),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_staff_shifts_staff_id')),
    sa.ForeignKeyConstraint(['template_id'], ['shift_templates.id'], name=op.f('fk_staff_shifts_template_id')),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], name=op.f('fk_staff_shifts_unit_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff_shifts')),
    sa.UniqueConstraint('staff_id', 'starts_at', name=op.f('uq_staff_shifts_staff_id_starts_at'))
    )
    op.create_index('idx_shifts_staff_time', 'staff_shifts', ['staff_id', 'starts_at', 'ends_at'], unique=False)
    op.create_table('workload_forecasts',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('model_run_id', sa.BigInteger(), nullable=False),
    sa.Column('unit_id', sa.BigInteger(), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('horizon_minutes', sa.Integer(), nullable=False),
    sa.Column('predicted_minutes', sa.Double(), nullable=False),
    sa.CheckConstraint("predicted_minutes > '-Infinity'::float8 AND predicted_minutes < 'Infinity'::float8", name=op.f('ck_workload_forecasts_predicted_minutes_finite')),
    sa.CheckConstraint('horizon_minutes IN (30,60)', name=op.f('ck_workload_forecasts_horizon')),
    sa.CheckConstraint('predicted_minutes >= 0', name=op.f('ck_workload_forecasts_predicted_minutes_nonnegative')),
    sa.ForeignKeyConstraint(['model_run_id'], ['model_runs.id'], name=op.f('fk_workload_forecasts_model_run_id')),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], name=op.f('fk_workload_forecasts_unit_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workload_forecasts')),
    sa.UniqueConstraint('model_run_id', 'unit_id', 'issued_at', 'horizon_minutes', name='uq_workload_forecasts_run_unit_issued_horizon')
    )
    op.create_index('idx_forecasts_unit_time', 'workload_forecasts', ['unit_id', 'issued_at'], unique=False)
    op.create_table('workload_observations',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('unit_id', sa.BigInteger(), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('window_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('observed_minutes', sa.Double(), nullable=False),
    sa.Column('patient_count', sa.Integer(), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("observed_minutes > '-Infinity'::float8 AND observed_minutes < 'Infinity'::float8", name=op.f('ck_workload_observations_observed_minutes_finite')),
    sa.CheckConstraint('available_at >= window_end', name=op.f('ck_workload_observations_available_after_window')),
    sa.CheckConstraint('observed_minutes >= 0', name=op.f('ck_workload_observations_observed_minutes_nonnegative')),
    sa.CheckConstraint('patient_count >= 0', name=op.f('ck_workload_observations_patient_count')),
    sa.CheckConstraint('window_end > window_start', name=op.f('ck_workload_observations_window_order')),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], name=op.f('fk_workload_observations_unit_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workload_observations')),
    sa.UniqueConstraint('unit_id', 'window_start', 'window_end', name=op.f('uq_workload_observations_unit_id_window_start_window_end'))
    )
    op.create_index('idx_observations_unit_time', 'workload_observations', ['unit_id', 'window_start'], unique=False)
    op.create_table('beds',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('room_id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], name=op.f('fk_beds_room_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_beds')),
    sa.UniqueConstraint('room_id', 'code', name=op.f('uq_beds_room_id_code'))
    )
    op.create_table('optimization_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('unit_id', sa.BigInteger(), nullable=False),
    sa.Column('forecast_id', sa.BigInteger(), nullable=True),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('objective_value', sa.Double(), nullable=True),
    sa.Column('config_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('solver_seconds', sa.Double(), nullable=False),
    sa.CheckConstraint("objective_value > '-Infinity'::float8 AND objective_value < 'Infinity'::float8", name=op.f('ck_optimization_runs_objective_value_finite')),
    sa.CheckConstraint("solver_seconds > '-Infinity'::float8 AND solver_seconds < 'Infinity'::float8", name=op.f('ck_optimization_runs_solver_seconds_finite')),
    sa.CheckConstraint("status IN ('OPTIMAL','FEASIBLE','INFEASIBLE','UNKNOWN','MODEL_INVALID')", name=op.f('ck_optimization_runs_status')),
    sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_optimization_runs_order')),
    sa.CheckConstraint('solver_seconds >= 0', name=op.f('ck_optimization_runs_solver_seconds_nonnegative')),
    sa.ForeignKeyConstraint(['forecast_id'], ['workload_forecasts.id'], name=op.f('fk_optimization_runs_forecast_id')),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], name=op.f('fk_optimization_runs_unit_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_optimization_runs'))
    )
    op.create_table('encounters',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('patient_id', sa.BigInteger(), nullable=False),
    sa.Column('unit_id', sa.BigInteger(), nullable=False),
    sa.Column('bed_id', sa.BigInteger(), nullable=True),
    sa.Column('admitted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('discharged_at', sa.DateTime(timezone=True), nullable=True),
    postgresql.ExcludeConstraint((sa.column('bed_id'), '='), (sa.text("tstzrange(admitted_at, discharged_at, '[)')"), '&&'), where=sa.text('bed_id IS NOT NULL'), using='gist', name='ex_encounters_bed_overlap'),
    sa.CheckConstraint('discharged_at IS NULL OR discharged_at > admitted_at', name=op.f('ck_encounters_discharge_order')),
    sa.ForeignKeyConstraint(['bed_id'], ['beds.id'], name=op.f('fk_encounters_bed_id')),
    sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], name=op.f('fk_encounters_patient_id')),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], name=op.f('fk_encounters_unit_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_encounters')),
    sa.UniqueConstraint('id', 'unit_id', name=op.f('uq_encounters_id_unit_id'))
    )
    op.create_index('idx_encounters_unit', 'encounters', ['unit_id', 'admitted_at'], unique=False)
    op.create_table('plan_approvals',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('decision', sa.Text(), nullable=False),
    sa.Column('manager_actor', sa.Text(), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('plan_sha256', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.CheckConstraint("decision IN ('approved','rejected','revoked')", name=op.f('ck_plan_approvals_decision')),
    sa.CheckConstraint("plan_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_plan_approvals_plan_sha256_format')),
    sa.CheckConstraint('length(trim(manager_actor)) > 0', name=op.f('ck_plan_approvals_manager_actor')),
    sa.ForeignKeyConstraint(['run_id'], ['optimization_runs.id'], name=op.f('fk_plan_approvals_run_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_approvals'))
    )
    op.create_index('idx_approvals_run', 'plan_approvals', ['run_id', 'decided_at', 'id'], unique=False)
    op.create_table('acuity_assessments',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('encounter_id', sa.BigInteger(), nullable=False),
    sa.Column('assessed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('multiplier', sa.Double(), nullable=False),
    sa.CheckConstraint("multiplier > '-Infinity'::float8 AND multiplier < 'Infinity'::float8", name=op.f('ck_acuity_assessments_multiplier_finite')),
    sa.CheckConstraint('level BETWEEN 1 AND 5', name=op.f('ck_acuity_assessments_level')),
    sa.CheckConstraint('multiplier > 0', name=op.f('ck_acuity_assessments_multiplier_positive')),
    sa.ForeignKeyConstraint(['encounter_id'], ['encounters.id'], name=op.f('fk_acuity_assessments_encounter_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_acuity_assessments')),
    sa.UniqueConstraint('encounter_id', 'assessed_at', name=op.f('uq_acuity_assessments_encounter_id_assessed_at'))
    )
    op.create_table('care_tasks',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('encounter_id', sa.BigInteger(), nullable=False),
    sa.Column('unit_id', sa.BigInteger(), nullable=False),
    sa.Column('task_type_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('estimated_minutes', sa.Double(), nullable=False),
    sa.Column('actual_minutes', sa.Double(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'completed' AND completed_at IS NOT NULL AND actual_minutes IS NOT NULL) OR (status IN ('pending','cancelled') AND completed_at IS NULL)", name=op.f('ck_care_tasks_completion')),
    sa.CheckConstraint("actual_minutes > '-Infinity'::float8 AND actual_minutes < 'Infinity'::float8", name=op.f('ck_care_tasks_actual_minutes_finite')),
    sa.CheckConstraint("estimated_minutes > '-Infinity'::float8 AND estimated_minutes < 'Infinity'::float8", name=op.f('ck_care_tasks_estimated_minutes_finite')),
    sa.CheckConstraint("status IN ('pending','completed','cancelled')", name=op.f('ck_care_tasks_status')),
    sa.CheckConstraint('actual_minutes > 0', name=op.f('ck_care_tasks_actual_minutes_positive')),
    sa.CheckConstraint('completed_at IS NULL OR completed_at >= created_at', name=op.f('ck_care_tasks_completion_order')),
    sa.CheckConstraint('estimated_minutes > 0', name=op.f('ck_care_tasks_estimated_minutes_positive')),
    sa.ForeignKeyConstraint(['encounter_id', 'unit_id'], ['encounters.id', 'encounters.unit_id'], name=op.f('fk_care_tasks_encounter_id_unit_id')),
    sa.ForeignKeyConstraint(['task_type_id'], ['task_types.id'], name=op.f('fk_care_tasks_task_type_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_tasks'))
    )
    op.create_index('idx_tasks_unit_due', 'care_tasks', ['unit_id', 'due_at', 'status'], unique=False)
    op.create_table('assignments',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.BigInteger(), nullable=False),
    sa.Column('staff_id', sa.BigInteger(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    postgresql.ExcludeConstraint((sa.column('run_id'), '='), (sa.column('staff_id'), '='), (sa.text("tstzrange(starts_at, ends_at, '[)')"), '&&'), using='gist', name='ex_assignments_staff_overlap'),
    sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_assignments_order')),
    sa.ForeignKeyConstraint(['run_id'], ['optimization_runs.id'], name=op.f('fk_assignments_run_id')),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_assignments_staff_id')),
    sa.ForeignKeyConstraint(['task_id'], ['care_tasks.id'], name=op.f('fk_assignments_task_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assignments')),
    sa.UniqueConstraint('run_id', 'task_id', name=op.f('uq_assignments_run_id_task_id'))
    )
    op.create_index('idx_assignments_staff_time', 'assignments', ['staff_id', 'starts_at', 'ends_at'], unique=False)
    op.create_table('staffing_gaps',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.BigInteger(), nullable=True),
    sa.Column('required_role', sa.Text(), nullable=False),
    sa.Column('missing_minutes', sa.Double(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.CheckConstraint("missing_minutes > '-Infinity'::float8 AND missing_minutes < 'Infinity'::float8", name=op.f('ck_staffing_gaps_missing_minutes_finite')),
    sa.CheckConstraint("required_role IN ('nurse','supervisor','support')", name=op.f('ck_staffing_gaps_required_role')),
    sa.CheckConstraint('missing_minutes > 0', name=op.f('ck_staffing_gaps_missing_minutes_positive')),
    sa.ForeignKeyConstraint(['run_id'], ['optimization_runs.id'], name=op.f('fk_staffing_gaps_run_id')),
    sa.ForeignKeyConstraint(['task_id'], ['care_tasks.id'], name=op.f('fk_staffing_gaps_task_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staffing_gaps')),
    sa.UniqueConstraint('run_id', 'task_id', name=op.f('uq_staffing_gaps_run_id_task_id'))
    )
    _grant("SELECT, INSERT, UPDATE", APP_READ_WRITE, "medakis_app")
    _grant("SELECT, INSERT", APP_APPEND_ONLY, "medakis_app")
    _grant("SELECT", APP_READ_ONLY, "medakis_app")
    _grant("SELECT", RAG_READ_ONLY, "medakis_rag")
    _grant("SELECT, INSERT", RAG_APPEND_ONLY, "medakis_rag")


def downgrade() -> None:
    op.drop_table('staffing_gaps')
    op.drop_index('idx_assignments_staff_time', table_name='assignments')
    op.drop_table('assignments')
    op.drop_index('idx_tasks_unit_due', table_name='care_tasks')
    op.drop_table('care_tasks')
    op.drop_table('acuity_assessments')
    op.drop_index('idx_approvals_run', table_name='plan_approvals')
    op.drop_table('plan_approvals')
    op.drop_index('idx_encounters_unit', table_name='encounters')
    op.drop_table('encounters')
    op.drop_table('optimization_runs')
    op.drop_table('beds')
    op.drop_index('idx_observations_unit_time', table_name='workload_observations')
    op.drop_table('workload_observations')
    op.drop_index('idx_forecasts_unit_time', table_name='workload_forecasts')
    op.drop_table('workload_forecasts')
    op.drop_index('idx_shifts_staff_time', table_name='staff_shifts')
    op.drop_table('staff_shifts')
    op.drop_table('rooms')
    op.drop_table('rag_retrieval_hits')
    op.drop_table('rag_index_chunks')
    op.drop_table('rag_eval_results')
    op.drop_table('units')
    op.drop_table('task_skill_requirements')
    op.drop_index('idx_unavailable_staff_time', table_name='staff_unavailability')
    op.drop_table('staff_unavailability')
    op.drop_table('staff_skills')
    op.drop_index('idx_rag_queries_time', table_name='rag_queries')
    op.drop_table('rag_queries')
    op.drop_table('rag_eval_runs')
    op.drop_table('rag_eval_cases')
    op.drop_index('idx_rag_chunks_document', table_name='rag_chunks')
    op.drop_table('rag_chunks')
    op.drop_table('task_types')
    op.drop_table('staff')
    op.drop_table('skills')
    op.drop_table('shift_templates')
    op.drop_table('rag_indexes')
    op.drop_table('rag_eval_sets')
    op.drop_index('idx_rag_documents_validity', table_name='rag_documents')
    op.drop_table('rag_documents')
    op.drop_table('patients')
    op.drop_table('model_runs')
    op.drop_table('facilities')
    op.drop_table('audit_events')
