-- MedAkis schema v2.0 | 2026-11-04
-- Reconstructed specification: synthetic administrative decision support.
-- Run on a NEW database. Deliberately fail on an existing schema; not a migration.
-- Every connection MUST enable foreign_keys before BEGIN.
-- UTC timestamps: fixed-width YYYY-MM-DDTHH:MM:SSZ, validated by application.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

-- Sağlık tesisi ve yerel saat dilimi
CREATE TABLE facilities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'Europe/Istanbul'
);

-- Servis ve senaryo türü
CREATE TABLE units (
    id INTEGER PRIMARY KEY,
    facility_id INTEGER NOT NULL REFERENCES facilities(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('inpatient','emergency','outpatient')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    UNIQUE(facility_id,name)
);

-- Servis içindeki oda
CREATE TABLE rooms (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id),
    code TEXT NOT NULL,
    UNIQUE(unit_id,code),
    UNIQUE(id,unit_id)
);

-- Oda içindeki yatak
CREATE TABLE beds (
    id INTEGER PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    code TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    UNIQUE(room_id,code)
);

-- Yalnızca sentetik hasta kimliği
CREATE TABLE patients (
    id INTEGER PRIMARY KEY,
    synthetic_code TEXT NOT NULL UNIQUE,
    is_synthetic INTEGER NOT NULL DEFAULT 1 CHECK(is_synthetic=1)
);

-- Sentetik başvuru ve servis yatışı
CREATE TABLE encounters (
    id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL REFERENCES patients(id),
    unit_id INTEGER NOT NULL REFERENCES units(id),
    bed_id INTEGER REFERENCES beds(id),
    admitted_at TEXT NOT NULL,
    discharged_at TEXT,
    CHECK(discharged_at IS NULL OR discharged_at>admitted_at),
    UNIQUE(id,unit_id)
);

-- Bakım gereksinimi değerlendirme geçmişi
CREATE TABLE acuity_assessments (
    id INTEGER PRIMARY KEY,
    encounter_id INTEGER NOT NULL REFERENCES encounters(id),
    assessed_at TEXT NOT NULL,
    level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 5),
    multiplier REAL NOT NULL CHECK(multiplier>0),
    UNIQUE(encounter_id,assessed_at)
);

-- Sentetik personel ve görev rolü
CREATE TABLE staff (
    id INTEGER PRIMARY KEY,
    synthetic_code TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK(role IN ('nurse','supervisor','support')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);

-- Yetkinlik sözlüğü
CREATE TABLE skills (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

-- Personel yetkinliği ve geçerlilik aralığı
CREATE TABLE staff_skills (
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    skill_id INTEGER NOT NULL REFERENCES skills(id),
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    PRIMARY KEY(staff_id,skill_id,valid_from),
    CHECK(valid_until IS NULL OR valid_until>valid_from)
);

-- Yerel saatle vardiya şablonu
CREATE TABLE shift_templates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    start_local TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK(duration_minutes BETWEEN 1 AND 1440)
);

-- UTC başlangıç bitiş ile gerçekleşecek vardiya
CREATE TABLE staff_shifts (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    unit_id INTEGER NOT NULL REFERENCES units(id),
    template_id INTEGER REFERENCES shift_templates(id),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    break_minutes INTEGER NOT NULL DEFAULT 0 CHECK(break_minutes>=0),
    CHECK(ends_at>starts_at),
    UNIQUE(staff_id,starts_at)
);

-- İzin mola veya diğer uygunsuzluk aralığı
CREATE TABLE staff_unavailability (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    CHECK(ends_at>starts_at)
);

-- İşlem türü ve değiştirilebilir sentetik süre
CREATE TABLE task_types (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    base_minutes REAL NOT NULL CHECK(base_minutes>0),
    required_role TEXT NOT NULL CHECK(required_role IN ('nurse','supervisor','support')),
    duration_source TEXT NOT NULL DEFAULT 'synthetic_assumption' CHECK(duration_source='synthetic_assumption')
);

-- İşlem türünün tümü gerekli yetkinlikleri
CREATE TABLE task_skill_requirements (
    task_type_id INTEGER NOT NULL REFERENCES task_types(id),
    skill_id INTEGER NOT NULL REFERENCES skills(id),
    PRIMARY KEY(task_type_id,skill_id)
);

-- Planlanmış veya tamamlanan bakım işleri
CREATE TABLE care_tasks (
    id INTEGER PRIMARY KEY,
    encounter_id INTEGER NOT NULL,
    unit_id INTEGER NOT NULL,
    task_type_id INTEGER NOT NULL REFERENCES task_types(id),
    created_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    estimated_minutes REAL NOT NULL CHECK(estimated_minutes>0),
    actual_minutes REAL CHECK(actual_minutes>0),
    status TEXT NOT NULL CHECK(status IN ('pending','completed','cancelled')),
    completed_at TEXT,
    FOREIGN KEY(encounter_id,unit_id) REFERENCES encounters(id,unit_id),
    CHECK((status='completed' AND completed_at IS NOT NULL AND actual_minutes IS NOT NULL) OR (status IN ('pending','cancelled') AND completed_at IS NULL)),
    CHECK(completed_at IS NULL OR completed_at>=created_at)
);

-- Servis bazında 15 dakikalık gerçekleşen iş yükü
CREATE TABLE workload_observations (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id),
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    observed_minutes REAL NOT NULL CHECK(observed_minutes>=0),
    patient_count INTEGER NOT NULL CHECK(patient_count>=0),
    available_at TEXT NOT NULL,
    CHECK(window_end>window_start),
    CHECK(available_at>=window_end),
    UNIQUE(unit_id,window_start,window_end)
);

-- Model sürümü eğitim sınırları ve test metrikleri
CREATE TABLE model_runs (
    id INTEGER PRIMARY KEY,
    model_name TEXT NOT NULL CHECK(model_name IN ('baseline','random_forest')),
    created_at TEXT NOT NULL,
    train_start TEXT NOT NULL,
    train_end TEXT NOT NULL,
    test_start TEXT NOT NULL,
    test_end TEXT NOT NULL,
    horizon_minutes INTEGER NOT NULL CHECK(horizon_minutes IN (30,60)),
    mae REAL NOT NULL CHECK(mae>=0),
    rmse REAL NOT NULL CHECK(rmse>=0),
    artifact_path TEXT NOT NULL,
    feature_spec TEXT NOT NULL,
    seed INTEGER NOT NULL,
    CHECK(train_start<train_end AND train_end<=test_start AND test_start<test_end)
);

-- Tahmin anı ve ufku başına toplam personel dakikası
CREATE TABLE workload_forecasts (
    id INTEGER PRIMARY KEY,
    model_run_id INTEGER NOT NULL REFERENCES model_runs(id),
    unit_id INTEGER NOT NULL REFERENCES units(id),
    issued_at TEXT NOT NULL,
    horizon_minutes INTEGER NOT NULL CHECK(horizon_minutes IN (30,60)),
    predicted_minutes REAL NOT NULL CHECK(predicted_minutes>=0),
    UNIQUE(model_run_id,unit_id,issued_at,horizon_minutes)
);

-- Çözücü çalıştırması ve açıklanabilir sonuç
CREATE TABLE optimization_runs (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id),
    forecast_id INTEGER REFERENCES workload_forecasts(id),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('OPTIMAL','FEASIBLE','INFEASIBLE','UNKNOWN','MODEL_INVALID')),
    objective_value REAL,
    config_json TEXT NOT NULL,
    solver_seconds REAL NOT NULL CHECK(solver_seconds>=0),
    CHECK(ends_at>starts_at)
);

-- Bir çözüm içindeki iş personel ataması
CREATE TABLE assignments (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES optimization_runs(id),
    task_id INTEGER NOT NULL REFERENCES care_tasks(id),
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    CHECK(ends_at>starts_at),
    UNIQUE(run_id,task_id)
);

-- Atanmayan iş ve açık personel dakikası
CREATE TABLE staffing_gaps (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES optimization_runs(id),
    task_id INTEGER REFERENCES care_tasks(id),
    required_role TEXT NOT NULL CHECK(required_role IN ('nurse','supervisor','support')),
    missing_minutes REAL NOT NULL CHECK(missing_minutes>0),
    reason TEXT NOT NULL,
    UNIQUE(run_id,task_id)
);

-- Hassas veri içermeyen uygulama işlem izi
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    detail_json TEXT NOT NULL
);

CREATE INDEX idx_tasks_unit_due ON care_tasks(unit_id,due_at,status);
CREATE INDEX idx_encounters_unit ON encounters(unit_id,admitted_at);
CREATE INDEX idx_shifts_staff_time ON staff_shifts(staff_id,starts_at,ends_at);
CREATE INDEX idx_unavailable_staff_time ON staff_unavailability(staff_id,starts_at,ends_at);
CREATE INDEX idx_observations_unit_time ON workload_observations(unit_id,window_start);
CREATE INDEX idx_forecasts_unit_time ON workload_forecasts(unit_id,issued_at);
CREATE INDEX idx_assignments_staff_time ON assignments(staff_id,starts_at,ends_at);

-- v2 additions: preserve all original 23 business tables unchanged.
CREATE TABLE plan_approvals (
 id INTEGER PRIMARY KEY,
 run_id INTEGER NOT NULL REFERENCES optimization_runs(id),
 decision TEXT NOT NULL CHECK(decision IN ('approved','rejected','revoked')),
 manager_actor TEXT NOT NULL CHECK(length(trim(manager_actor))>0),
 decided_at TEXT NOT NULL,
 plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256)=64),
 note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_approvals_run ON plan_approvals(run_id,decided_at,id);

CREATE TABLE rag_documents (
 id INTEGER PRIMARY KEY,
 source_key TEXT NOT NULL,
 source_name TEXT NOT NULL,
 doc_type TEXT NOT NULL CHECK(doc_type IN ('procedure','job_description','competency','workflow')),
 version TEXT NOT NULL,
 valid_from TEXT NOT NULL,
 valid_until TEXT,
 status TEXT NOT NULL CHECK(status IN ('draft','approved','retired')),
 approved_by TEXT,
 approved_at TEXT,
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 local_path TEXT NOT NULL,
 is_synthetic INTEGER NOT NULL DEFAULT 1 CHECK(is_synthetic=1),
 CHECK(valid_until IS NULL OR valid_until>valid_from),
 CHECK(status!='approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
 UNIQUE(source_key,version)
);
CREATE TABLE rag_chunks (
 id INTEGER PRIMARY KEY,
 document_id INTEGER NOT NULL REFERENCES rag_documents(id),
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 section TEXT NOT NULL,
 text_content TEXT NOT NULL CHECK(length(trim(text_content))>0),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 token_count INTEGER NOT NULL CHECK(token_count>0),
 UNIQUE(document_id,ordinal)
);
CREATE TABLE rag_indexes (
 id INTEGER PRIMARY KEY,
 collection_name TEXT NOT NULL UNIQUE,
 embedding_model TEXT NOT NULL,
 model_digest TEXT NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions>0),
 distance_metric TEXT NOT NULL CHECK(distance_metric='cosine'),
 chunk_config_json TEXT NOT NULL,
 corpus_sha256 TEXT NOT NULL CHECK(length(corpus_sha256)=64),
 state TEXT NOT NULL CHECK(state IN ('building','ready','failed','retired')),
 created_at TEXT NOT NULL
);
CREATE TABLE rag_index_chunks (
 index_id INTEGER NOT NULL REFERENCES rag_indexes(id),
 chunk_id INTEGER NOT NULL REFERENCES rag_chunks(id),
 vector_id TEXT NOT NULL,
 PRIMARY KEY(index_id,chunk_id),
 UNIQUE(index_id,vector_id)
);
CREATE TABLE rag_queries (
 id INTEGER PRIMARY KEY,
 index_id INTEGER NOT NULL REFERENCES rag_indexes(id),
 asked_at TEXT NOT NULL,
 as_of TEXT NOT NULL,
 question TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('answered','no_source','conflicting_sources','unavailable','rejected')),
 answer TEXT,
 generator_model TEXT,
 generator_digest TEXT,
 prompt_version TEXT NOT NULL,
 retrieval_ms REAL NOT NULL CHECK(retrieval_ms>=0),
 generation_ms REAL NOT NULL CHECK(generation_ms>=0),
 total_ms REAL NOT NULL CHECK(total_ms>=0),
 config_json TEXT NOT NULL,
 CHECK(outcome!='answered' OR (answer IS NOT NULL AND length(trim(answer))>0))
);
CREATE TABLE rag_retrieval_hits (
 query_id INTEGER NOT NULL REFERENCES rag_queries(id),
 chunk_id INTEGER NOT NULL REFERENCES rag_chunks(id),
 rank INTEGER NOT NULL CHECK(rank>0),
 cosine_distance REAL NOT NULL CHECK(cosine_distance>=0 AND cosine_distance<=2.00001),
 cited INTEGER NOT NULL DEFAULT 0 CHECK(cited IN (0,1)),
 PRIMARY KEY(query_id,rank),
 UNIQUE(query_id,chunk_id)
);
CREATE TABLE rag_eval_sets (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL,
 version TEXT NOT NULL,
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 created_at TEXT NOT NULL,
 UNIQUE(name,version)
);
CREATE TABLE rag_eval_cases (
 id INTEGER PRIMARY KEY,
 set_id INTEGER NOT NULL REFERENCES rag_eval_sets(id),
 case_key TEXT NOT NULL,
 split TEXT NOT NULL CHECK(split IN ('dev','test')),
 category TEXT NOT NULL,
 question TEXT NOT NULL,
 as_of TEXT NOT NULL,
 answerable INTEGER NOT NULL CHECK(answerable IN (0,1)),
 relevant_chunk_ids_json TEXT NOT NULL,
 expected_claims_json TEXT NOT NULL,
 UNIQUE(set_id,case_key)
);
CREATE TABLE rag_eval_runs (
 id INTEGER PRIMARY KEY,
 set_id INTEGER NOT NULL REFERENCES rag_eval_sets(id),
 index_id INTEGER NOT NULL REFERENCES rag_indexes(id),
 created_at TEXT NOT NULL,
 generator_model TEXT,
 config_json TEXT NOT NULL,
 metrics_json TEXT NOT NULL
);
CREATE TABLE rag_eval_results (
 run_id INTEGER NOT NULL REFERENCES rag_eval_runs(id),
 case_id INTEGER NOT NULL REFERENCES rag_eval_cases(id),
 query_id INTEGER REFERENCES rag_queries(id),
 recall_at_5 REAL CHECK(recall_at_5 BETWEEN 0 AND 1),
 reciprocal_rank_at_10 REAL CHECK(reciprocal_rank_at_10 BETWEEN 0 AND 1),
 citation_accuracy REAL CHECK(citation_accuracy BETWEEN 0 AND 1),
 faithfulness REAL CHECK(faithfulness BETWEEN 0 AND 1),
 latency_ms REAL NOT NULL CHECK(latency_ms>=0),
 abstention_correct INTEGER CHECK(abstention_correct IN (0,1)),
 reviewer TEXT,
 review_note TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(run_id,case_id)
);
CREATE INDEX idx_rag_documents_validity ON rag_documents(status,valid_from,valid_until);
CREATE INDEX idx_rag_chunks_document ON rag_chunks(document_id,ordinal);
CREATE INDEX idx_rag_queries_time ON rag_queries(asked_at);

PRAGMA user_version = 2;
COMMIT;
