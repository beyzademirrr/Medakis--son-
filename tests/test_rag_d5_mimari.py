"""D5 mimari deneyi: reranker kapısı, Gemma'sız kaynak sunumu ve sözleşme sınıfları (ağsız; gerçek corpus metni)."""

import math

import pytest
import test_rag_generation as G

from medakis.rag.contracts.dto import RagOutcome
from medakis.rag.contracts.retrieval import RetrievalOutcome, RetrievalResult
from medakis.rag.evaluation.contract import OutcomeClass as C
from medakis.rag.evaluation.contract import classify, summarize_contract
from medakis.rag.generation import methods as M
from medakis.rag.generation.gate import GATE_REJECTED, GATE_TECHNICAL, PRESENTATION_LABEL, SourcePresentation, apply_gate, present_sources
from medakis.rag.generation.pipeline import POLICY_CANDIDATES, EvidencePipeline

TEST = G.GenerationMode.TEST
C1, C2 = G.C1, G.C2  # C1 sıra 1, C2 sıra 2


def found(*chunks):
    return RetrievalResult(RetrievalOutcome.FOUND, "x", tuple(chunks), evaluation=True)


class Gen:
    supports_budget = True

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), 0

    def chat_json(self, messages, schema, budget_s=None):
        self.calls += 1
        return {"veri": self.responses.pop(0)}


# ---- Kapı -----------------------------------------------------------------------------------------------------


def test_gate_reorders_by_score_and_accepts_above_threshold():
    g = apply_gate(found(C1, C2), {"p1": 0.2, "p2": 0.9}, threshold_score=0.5)
    assert g.accepted and g.top_score == 0.9
    assert [(c.chunk_id, c.rank) for c in g.retrieval.chunks] == [("p2", 1), ("p1", 2)]
    assert {c.text for c in g.retrieval.chunks} == {C1.text, C2.text}  # metin değişmez; aday eklenmez


def test_gate_rejects_below_threshold_or_without_threshold():
    for th in (0.95, None):
        g = apply_gate(found(C1, C2), {"p1": 0.2, "p2": 0.9}, threshold_score=th)
        assert not g.accepted and g.retrieval.outcome is RetrievalOutcome.NO_SOURCE and GATE_REJECTED in g.retrieval.warnings


@pytest.mark.parametrize("scores", [{"p1": 0.9}, {"p1": 0.9, "p2": math.nan}, {"p1": math.inf, "p2": 0.1}])
def test_gate_missing_or_nonfinite_score_is_technical_error_not_abstention(scores):
    g = apply_gate(found(C1, C2), scores, threshold_score=0.5)
    assert g.retrieval.outcome is RetrievalOutcome.UNAVAILABLE and GATE_TECHNICAL in g.retrieval.warnings
    assert classify(True, "unavailable").klass is C.TEKNIK_HATA


@pytest.mark.parametrize("outcome", [RetrievalOutcome.CONFLICTING_SOURCES, RetrievalOutcome.REJECTED, RetrievalOutcome.UNAVAILABLE,
                                     RetrievalOutcome.NO_SOURCE])  # fmt: skip
def test_gate_and_later_layers_cannot_override_security_or_errors(outcome):
    r = RetrievalResult(outcome, "x", (C1,) if outcome is RetrievalOutcome.CONFLICTING_SOURCES else ())
    g = apply_gate(r, {"p1": 99.0}, threshold_score=0.0)
    assert not g.accepted and g.retrieval is r
    gen = Gen()
    res = EvidencePipeline(object(), gen, TEST, method=M.METHOD_STRICT).decide("Soru?", g.retrieval, POLICY_CANDIDATES)
    assert gen.calls == 0 and res.outcome.value == outcome.value
    assert present_sources(g).outcome == outcome.value and present_sources(g).chunks == ()


def test_gate_rejection_means_no_model_call():
    g = apply_gate(found(C1), {"p1": 0.1}, threshold_score=0.5)
    gen = Gen()
    res = EvidencePipeline(object(), gen, TEST, method=M.METHOD_STRICT).decide("Soru?", g.retrieval, POLICY_CANDIDATES)
    assert gen.calls == 0 and res.outcome is RagOutcome.NO_SOURCE and GATE_REJECTED in res.warnings
    assert classify(True, res.outcome.value, res.warnings, True).reason == "kapi_reddi"


def test_positive_gate_does_not_relax_quote_verification():
    g = apply_gate(found(C1, C2), {"p1": 0.9, "p2": 0.1}, threshold_score=0.5)
    gen = Gen({"destekleniyor": True, "alintilar": [{"parca": "K1", "metin": "Rezerv her zaman yüzde yirmidir ve sabittir."}]})
    res = EvidencePipeline(object(), gen, TEST, method=M.METHOD_STRICT).decide("Rezerv?", g.retrieval, POLICY_CANDIDATES)
    assert res.outcome is RagOutcome.NO_SOURCE and res.warnings[0].startswith("sahte_alinti:")  # uydurma alıntı yine reddedilir


# ---- Gemma'sız kaynak sunumu -----------------------------------------------------------------------------------


def test_presentation_is_verbatim_labelled_and_not_an_answer():
    g = apply_gate(found(C1, C2), {"p1": 0.3, "p2": 0.9}, threshold_score=0.5)
    p = present_sources(g)
    assert p.outcome == "kaynak_sunuldu" and p.label == PRESENTATION_LABEL and "cevap DEĞİLDİR" in p.label
    assert len(p.chunks) == 1 and p.chunks[0].chunk_id == "p2" and p.chunks[0].text == C2.text
    assert G.DOCS["SYN-PRC-ONAY@1.0"].text[p.chunks[0].quote_start:p.chunks[0].quote_end] == p.chunks[0].text
    assert not hasattr(p, "answer")


def test_presentation_invariants():
    with pytest.raises(ValueError):
        SourcePresentation("kaynak_sunuldu", "etiketsiz", (C1,))
    with pytest.raises(ValueError):
        SourcePresentation("no_source", "x", (C1,))


def test_presentation_classes_are_not_correct_answers():
    assert classify(True, "kaynak_sunuldu").klass is C.KAYNAK_SUNULDU_INCELEME_BEKLIYOR
    assert classify(False, "kaynak_sunuldu").klass is C.YANLIS_KABUL
    rows = []
    for ans, out in ((True, "kaynak_sunuldu"), (False, "no_source"), (False, "kaynak_sunuldu")):
        k = classify(ans, out)
        rows.append({"ans": ans, "sonuc": out, "sinif": k.klass, "alt_neden": k.reason})
    s = summarize_contract(rows)
    assert s["kaynak_sunuldu_inceleme_bekliyor"] == 1 and s["anlamsal_dogruluk"] is None
    assert s["yanlis_kabul_orani"] == 0.5 and s["eski_dogru_kacinma"] == 0.5
