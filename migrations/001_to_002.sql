-- Apply only when user_version=1; back up first. Not rerunnable.
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;

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

PRAGMA user_version=2;
COMMIT;
