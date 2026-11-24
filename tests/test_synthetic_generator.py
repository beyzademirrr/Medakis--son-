"""Sentetik üretici: tekrarlanabilirlik, kurallara uygunluk ve senaryo kararları (veritabanısız)."""

import ast
import os
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from medakis.domain.rules import PATIENT_CODE, STAFF_CODE
from medakis.repositories.schema import SEED_RESET_TABLES, legacy_columns, metadata
from medakis.synthetic.generator import generate
from medakis.synthetic.params import GENERATOR_VERSION, SyntheticParams
from medakis.synthetic.validate import validate

ROOT = Path(__file__).resolve().parents[1]
# Kurallı bir üretici değişikliğinde GENERATOR_VERSION ile birlikte bilerek güncellenir.
GOLDEN_SHA256_SEED42 = "83a619cb04371b892ed32803be9d0d16690bd72cecc6b8f560e9d59897191f42"


@pytest.fixture(scope="module")
def dataset():
    return generate(SyntheticParams())


def test_golden_content_hash_for_seed_42(dataset):
    assert GENERATOR_VERSION == "1.0.0"
    assert dataset.content_sha256() == GOLDEN_SHA256_SEED42


def test_same_seed_same_content_in_process(dataset):
    assert generate(SyntheticParams()).content_sha256() == dataset.content_sha256()


def test_same_seed_same_content_across_processes_with_different_hash_seeds():
    code = "import sys; sys.path.insert(0, 'src'); from medakis.synthetic.generator import generate; print(generate().content_sha256())"
    digests = set()
    for hash_seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        assert out.returncode == 0, out.stderr
        digests.add(out.stdout.strip())
    assert digests == {GOLDEN_SHA256_SEED42}


def test_different_seed_different_content(dataset):
    assert generate(SyntheticParams(seed=43)).content_sha256() != dataset.content_sha256()


def test_fingerprint_tracks_params_and_version():
    base = SyntheticParams()
    assert base.fingerprint() == SyntheticParams().fingerprint()
    assert base.fingerprint() != replace(base, days=27).fingerprint()


def test_dataset_passes_all_domain_rules(dataset):
    assert validate(dataset) == []


def test_scenario_decisions(dataset):
    t = dataset.tables
    assert dataset.scenario_now - dataset.scenario_start == timedelta(days=28)
    assert [u["kind"] for u in t["units"]] == ["inpatient", "emergency", "outpatient"]
    room_unit = {r["id"]: r["unit_id"] for r in t["rooms"]}
    assert sum(1 for b in t["beds"] if room_unit[b["room_id"]] == 1) == 30
    assert all(r["duration_source"] == "synthetic_assumption" for r in t["task_types"])
    assert t["workload_observations"] == []  # gözlem hesabı Aşama 3'e aittir


def test_only_synthetic_identities(dataset):
    assert all(PATIENT_CODE.fullmatch(p["synthetic_code"]) for p in dataset.tables["patients"])
    assert all(STAFF_CODE.fullmatch(s["synthetic_code"]) for s in dataset.tables["staff"])
    text_columns = {"name", "code", "reason", "synthetic_code"}
    for table in SEED_RESET_TABLES:
        for row in dataset.tables[table]:
            for column in text_columns & row.keys():
                assert not any(ch.isdigit() for ch in row[column][:1]) or column == "code", (table, column)


def test_no_future_knowledge(dataset):
    now = dataset.scenario_now
    assert all(task["created_at"] <= now for task in dataset.tables["care_tasks"])
    assert all(a["assessed_at"] <= now for a in dataset.tables["acuity_assessments"])
    statuses = Counter(task["status"] for task in dataset.tables["care_tasks"] if task["due_at"] >= now)
    assert set(statuses) == {"pending"} and statuses["pending"] > 100  # planlama ufku dolu


def test_pending_tasks_cover_next_night_shift(dataset):
    now = dataset.scenario_now
    pending = [t for t in dataset.tables["care_tasks"] if t["status"] == "pending" and t["due_at"] >= now]
    assert max(t["due_at"] for t in pending) >= now + timedelta(hours=7)


def test_shortage_periods_exist(dataset):
    # Gece daha az kadro planlanır; açık raporunun Aşama 5'te anlamlı olması için gerekli.
    t = dataset.tables
    night = [s for s in t["staff_shifts"] if s["template_id"] == 2 and s["unit_id"] == 1]
    day = [s for s in t["staff_shifts"] if s["template_id"] == 1 and s["unit_id"] == 1]
    assert len(night) < len(day)


def test_skill_expiry_inside_scenario(dataset):
    expiring = [s for s in dataset.tables["staff_skills"] if s["valid_until"] is not None]
    assert expiring and all(dataset.scenario_start < s["valid_until"] < dataset.scenario_now for s in expiring)


def test_row_volume_is_measured_and_bounded(dataset):
    counts = dataset.row_counts()
    assert 10_000 <= counts["care_tasks"] <= 25_000
    assert set(counts) == set(SEED_RESET_TABLES)


def test_rows_match_table_columns(dataset):
    for table in SEED_RESET_TABLES:
        # 1.0.0 üreticisi revizyon 0003 kolonlarını üretir; 0004 kolonları veritabanında NULL kalır.
        columns = {c.name for c in legacy_columns(metadata.tables[table])}
        for row in dataset.tables[table]:
            assert set(row) == columns, table


def test_generator_reads_no_clock_or_global_random():
    forbidden = {"now", "today", "utcnow", "time", "random"}
    for path in (ROOT / "src" / "medakis" / "synthetic").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"now", "today", "utcnow"}:
                pytest.fail(f"{path.name}: saat okunuyor")
            if isinstance(node, ast.Import):
                assert not {a.name for a in node.names} & {"random"}, path.name
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "random":
                assert node.attr in {"default_rng", "Generator"}, f"{path.name}: küresel numpy rastgeleliği"
    assert forbidden
