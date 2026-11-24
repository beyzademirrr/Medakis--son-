from pathlib import Path
import sqlite3,json
ROOT=Path(__file__).resolve().parent
def connect():
    c=sqlite3.connect(':memory:'); c.execute('PRAGMA foreign_keys=ON'); return c
def run():
    schema=(ROOT/'medakis_schema.sql').read_text(encoding='utf-8')
    migration=(ROOT/'migrations/001_to_002.sql').read_text(encoding='utf-8')
    # Derive preserved v1 prefix from v2: additive section begins at plan_approvals.
    prefix=schema.split('-- v2 additions:')[0]
    v1=prefix+'PRAGMA user_version=1;\nCOMMIT;'
    fresh=connect(); fresh.executescript(schema)
    db=connect(); db.executescript(v1)
    db.executescript("""
    INSERT INTO facilities VALUES(1,'Demo','Europe/Istanbul');
    INSERT INTO units VALUES(1,1,'Yatakli','inpatient',1);
    INSERT INTO rooms VALUES(1,1,'O1');
    INSERT INTO beds VALUES(1,1,'Y1',1);
    INSERT INTO patients VALUES(1,'SYN001',1);
    INSERT INTO encounters VALUES(1,1,1,1,'2026-11-04T17:00:00Z',NULL);
    INSERT INTO acuity_assessments VALUES(1,1,'2026-11-04T17:00:00Z',2,1.2);
    INSERT INTO staff VALUES(1,'STAFF001','nurse',1);
    INSERT INTO skills VALUES(1,'S1','Demo yetkinlik');
    INSERT INTO staff_skills VALUES(1,1,'2026-01-01T00:00:00Z',NULL);
    INSERT INTO shift_templates VALUES(1,'Gece','20:00',720);
    INSERT INTO staff_shifts VALUES(1,1,1,1,'2026-11-04T17:00:00Z','2026-11-05T05:00:00Z',30);
    INSERT INTO staff_unavailability VALUES(1,1,'2026-11-04T22:00:00Z','2026-11-04T22:30:00Z','Mola');
    INSERT INTO task_types VALUES(1,'T1','Sentetik gorev',10,'nurse','synthetic_assumption');
    INSERT INTO task_skill_requirements VALUES(1,1);
    INSERT INTO care_tasks VALUES(1,1,1,1,'2026-11-04T17:00:00Z','2026-11-04T18:00:00Z',12,NULL,'pending',NULL);
    INSERT INTO workload_observations VALUES(1,1,'2026-11-04T17:00:00Z','2026-11-04T17:15:00Z',12,1,'2026-11-04T17:15:00Z');
    INSERT INTO model_runs VALUES(1,'baseline','2026-11-04T17:00:00Z','2026-10-01T00:00:00Z','2026-10-20T00:00:00Z','2026-10-21T00:00:00Z','2026-10-31T00:00:00Z',30,1,2,'artifacts/demo','lag4',42);
    INSERT INTO workload_forecasts VALUES(1,1,1,'2026-11-04T17:15:00Z',30,24);
    INSERT INTO optimization_runs VALUES(1,1,1,'2026-11-04T17:15:00Z','2026-11-04T17:45:00Z','2026-11-04T17:15:00Z','FEASIBLE',0,'{}',0.1);
    INSERT INTO assignments VALUES(1,1,1,1,'2026-11-04T17:15:00Z','2026-11-04T17:27:00Z');
    INSERT INTO staffing_gaps VALUES(1,1,NULL,'nurse',12,'forecast_reserve');
    INSERT INTO audit_events VALUES(1,'2026-11-04T17:15:00Z','demo','optimize','run','1','{}');
    """)

    original={n:(sql,list(db.execute('SELECT * FROM '+n))) for n,sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall()}
    assert len(original)==23
    assert db.execute('PRAGMA user_version').fetchone()[0]==1
    db.executescript(migration)
    for name,(sql,rows) in original.items():
        assert db.execute("SELECT sql FROM sqlite_master WHERE name=?",(name,)).fetchone()[0]==sql
        assert list(db.execute('SELECT * FROM '+name))==rows
    assert sorted(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall())==sorted(fresh.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall())
    tables=[x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    assert len(tables)==34
    # One connected fixture for every new table.
    sha='a'*64
    db.execute("INSERT INTO plan_approvals VALUES(1,1,'approved','manager','2026-11-04T18:00:00Z',?,'demo')",(sha,))
    db.execute("INSERT INTO rag_documents VALUES(1,'P1','Demo','procedure','1','2026-11-01T00:00:00Z',NULL,'approved','manager','2026-11-01T00:00:00Z',?,'corpus/demo.md',1)",(sha,))
    db.execute("INSERT INTO rag_chunks VALUES(1,1,0,'S1','Synthetic text',?,3)",(sha,))
    db.execute("INSERT INTO rag_indexes VALUES(1,'qwen_v1','qwen3-embedding:0.6b','digest',1024,'cosine','{}',?,'ready','2026-11-04T00:00:00Z')",(sha,))
    db.execute("INSERT INTO rag_index_chunks VALUES(1,1,'vector1')")
    db.execute("INSERT INTO rag_queries VALUES(1,1,'2026-11-04T18:00:00Z','2026-11-04T18:00:00Z','Question','answered','Answer','gemma3:4b','digest','v1',1,2,3,'{}')")
    db.execute("INSERT INTO rag_retrieval_hits VALUES(1,1,1,0.2,1)")
    db.execute("INSERT INTO rag_eval_sets VALUES(1,'demo','1',?,'2026-11-04T00:00:00Z')",(sha,))
    db.execute("INSERT INTO rag_eval_cases VALUES(1,1,'Q1','test','procedure','Question','2026-11-04T18:00:00Z',1,'[1]','[]')")
    db.execute("INSERT INTO rag_eval_runs VALUES(1,1,1,'2026-11-04T18:00:00Z','gemma3:4b','{}','{}')")
    db.execute("INSERT INTO rag_eval_results VALUES(1,1,1,1,1,1,1,3,NULL,'human','demo')")
    db.commit()
    passed=[]
    def reject(label,sql):
        db.execute('SAVEPOINT bad')
        try: db.execute(sql)
        except sqlite3.IntegrityError: passed.append(label)
        else: raise AssertionError(label)
        finally: db.execute('ROLLBACK TO bad'); db.execute('RELEASE bad')
    reject('synthetic_only',"INSERT INTO patients VALUES(2,'REAL',0)")
    reject('foreign_key',"INSERT INTO rooms VALUES(2,999,'X')")
    reject('negative_task_minutes',"UPDATE task_types SET base_minutes=-1 WHERE id=1")
    reject('shift_order',"UPDATE staff_shifts SET ends_at=starts_at WHERE id=1")
    reject('unique_assignment',"INSERT INTO assignments VALUES(2,1,1,1,'2026-11-04T17:30:00Z','2026-11-04T17:40:00Z')")
    reject('invalid_horizon',"UPDATE workload_forecasts SET horizon_minutes=45 WHERE id=1")
    reject('completed_requires_duration',"UPDATE care_tasks SET status='completed' WHERE id=1")
    reject('unit_encounter_mismatch',"UPDATE care_tasks SET unit_id=2 WHERE id=1")
    reject('training_order',"UPDATE model_runs SET test_start=train_start WHERE id=1")
    reject('nonpositive_gap',"UPDATE staffing_gaps SET missing_minutes=0 WHERE id=1")
    reject('invalid_solver_status',"UPDATE optimization_runs SET status='SUCCESS' WHERE id=1")
    reject('duplicate_skill',"INSERT INTO task_skill_requirements VALUES(1,1)")
    reject('referenced_staff_delete',"DELETE FROM staff WHERE id=1")
    reject('rag_synthetic_only',"UPDATE rag_documents SET is_synthetic=0 WHERE id=1")
    reject('rag_validity',"UPDATE rag_documents SET valid_until=valid_from WHERE id=1")
    reject('rag_approval_required',"UPDATE rag_documents SET approved_by=NULL WHERE id=1")
    reject('rag_hash_length',"UPDATE rag_documents SET content_sha256='bad' WHERE id=1")
    reject('rag_chunk_fk',"UPDATE rag_chunks SET document_id=999 WHERE id=1")
    reject('empty_chunk',"UPDATE rag_chunks SET text_content='' WHERE id=1")
    reject('index_dimensions',"UPDATE rag_indexes SET dimensions=0 WHERE id=1")
    reject('duplicate_vector',"INSERT INTO rag_index_chunks VALUES(1,1,'vector2')")
    reject('invalid_query_outcome',"UPDATE rag_queries SET outcome='hallucinated' WHERE id=1")
    reject('answered_requires_text',"UPDATE rag_queries SET answer=NULL WHERE id=1")
    reject('negative_latency',"UPDATE rag_queries SET total_ms=-1 WHERE id=1")
    reject('rank_positive',"UPDATE rag_retrieval_hits SET rank=0 WHERE query_id=1")
    reject('eval_range',"UPDATE rag_eval_results SET recall_at_5=1.1 WHERE run_id=1")
    reject('eval_split',"UPDATE rag_eval_cases SET split='train' WHERE id=1")
    reject('approval_decision',"UPDATE plan_approvals SET decision='auto' WHERE id=1")
    reject('approval_manager',"UPDATE plan_approvals SET manager_actor='' WHERE id=1")
    reject('approval_hash',"UPDATE plan_approvals SET plan_sha256='bad' WHERE id=1")
    for conn in [db,fresh]:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert list(conn.execute('PRAGMA foreign_key_check'))==[]
        assert conn.execute('PRAGMA user_version').fetchone()[0]==2
    assert all(db.execute('SELECT count(*) FROM '+t).fetchone()[0]>=1 for t in tables)
    return {'sqlite_version':sqlite3.sqlite_version,'table_count':34,'preserved_v1_tables':23,'new_tables':11,'clean_install':'passed','migration':'passed','v1_ddl_and_rows_preserved':True,'all_34_tables_fixture':'passed','integrity_check':'ok','foreign_key_check':[],'negative_tests':passed,'negative_test_count':len(passed),'schema_version':2}
if __name__=='__main__': print(json.dumps(run(),ensure_ascii=False,indent=2))
