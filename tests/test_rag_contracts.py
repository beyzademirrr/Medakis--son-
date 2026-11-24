import dataclasses
import math
import re

import pytest

from medakis.rag.contracts import (
    Citation,
    EmbeddingProvider,
    GenerationProvider,
    RagAnswer,
    RagOutcome,
    Timings,
)


def _citation(**kw):
    base = dict(
        source_key="SYN-PROC", source_name="Sentetik vardiya devir prosedürü",
        section="devir", version="1.0", valid_from="2026-11-01T00:00:00Z",
        valid_until=None, chunk_id=1, quote_start=0, quote_end=10,
    )
    base.update(kw)
    return Citation(**base)


def test_outcomes_match_schema_check(project_root):
    sql = (project_root / "medakis_schema.sql").read_text(encoding="utf-8")
    m = re.search(r"outcome TEXT NOT NULL CHECK\(outcome IN \(([^)]*)\)\)", sql)
    assert m, "rag_queries.outcome CHECK bulunamadı"
    schema_values = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert schema_values == {o.value for o in RagOutcome}


def test_answered_requires_text_and_citation():
    with pytest.raises(ValueError):
        RagAnswer(RagOutcome.ANSWERED, "Cevap", citations=())
    with pytest.raises(ValueError):
        RagAnswer(RagOutcome.ANSWERED, "  ", citations=(_citation(),))
    ok = RagAnswer(RagOutcome.ANSWERED, "Cevap", citations=(_citation(),))
    assert ok.outcome is RagOutcome.ANSWERED


def test_no_source_cannot_carry_citations():
    with pytest.raises(ValueError):
        RagAnswer(RagOutcome.NO_SOURCE, None, citations=(_citation(),))
    assert RagAnswer(RagOutcome.NO_SOURCE, None).citations == ()


def test_dto_is_immutable():
    ans = RagAnswer(RagOutcome.UNAVAILABLE, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ans.outcome = RagOutcome.ANSWERED  # type: ignore[misc]


@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf])
def test_timings_reject_invalid(bad):
    with pytest.raises(ValueError):
        Timings(retrieval_ms=bad)


@pytest.mark.parametrize("kw", [{"quote_start": 5, "quote_end": 5}, {"quote_start": -1}, {"section": ""}])
def test_citation_validation(kw):
    with pytest.raises(ValueError):
        _citation(**kw)


def test_fake_providers_satisfy_narrow_protocols():
    class FakeEmbed:
        model_name = "fake-embed"

        def embed(self, texts):
            return [[float(len(t))] for t in texts]

    class FakeGen:
        model_name = "fake-gen"

        def generate(self, system, prompt):
            return "metin"

    assert isinstance(FakeEmbed(), EmbeddingProvider)
    assert isinstance(FakeGen(), GenerationProvider)
