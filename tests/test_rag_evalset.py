"""Aşama 7.6: değerlendirme seti (dev), mühürlü test kümesi ve güvenlik vakaları.

Mühürlü test dosyası bu testlerde AÇILMAZ: yalnız baytlarının SHA-256'sı mühür kaydıyla karşılaştırılır.
"""

import hashlib
import json

import pytest
import rag_helpers as H

from medakis.rag.chunking.markdown_sections import parse_sections
from medakis.rag.errors import SealedSplitError
from medakis.rag.evaluation.eval_set import load_dev_set
from medakis.rag.ingestion.corpus_loader import load_corpus

EVAL = H.ROOT / "evaluation" / "asama7"
SEAL = json.loads((EVAL / "muhur.json").read_text(encoding="utf-8"))
DEV = load_dev_set(EVAL / "dev.jsonl")
DOCS = load_corpus(H.CORPUS).by_uid()
CURRENT_SOURCES = {u for u, d in DOCS.items() if d.record.purpose == "kaynak" and u != "SYN-YET-GECERLILIK@1.0"}


def test_seal_matches_sealed_file_bytes_without_opening_content():
    data = (EVAL / "muhurlu_test.jsonl").read_bytes()
    assert hashlib.sha256(data).hexdigest() == SEAL["sha256"]  # dosya mühürden sonra değişmedi
    assert SEAL["durum"] == "gelistirme_amacli_ayrildi_kor_test_degil"  # Git'te: yalnız bütünlük, gizlilik yok
    assert "Gizlilik sağlamaz" in SEAL["muhur_anlami"] and (SEAL["cevaplanabilir"], SEAL["kaynak_disi"]) == (42, 14)
    assert len(SEAL["dev_sha256"]) == 64  # mühür anındaki dev özeti (tarihsel); dev etiketleri sonra denetimle düzeltildi


def test_dev_loader_refuses_sealed_test_file():
    with pytest.raises(SealedSplitError):
        load_dev_set(EVAL / "muhurlu_test.jsonl")


def test_totals_60_answerable_20_out_of_scope_with_30_70_split():
    dev_ans = sum(c.answerable for c in DEV.cases)
    assert (dev_ans, len(DEV.cases) - dev_ans) == (18, 6)
    assert dev_ans + SEAL["cevaplanabilir"] == 60 and (len(DEV.cases) - dev_ans) + SEAL["kaynak_disi"] == 20
    assert 0.25 <= len(DEV.cases) / (len(DEV.cases) + SEAL["vaka"]) <= 0.35


def test_dev_gold_points_to_exact_spans_in_current_approved_sources():
    for c in DEV.cases:
        for g in c.gold:
            assert g.doc_uid in CURRENT_SOURCES, g.doc_uid  # tarihsel sürüm ve fikstürler gold olamaz
            text = DOCS[g.doc_uid].text
            secs = parse_sections(text)[1]
            assert any(s.start <= g.char_start < g.char_end <= s.end for s in secs)  # aralık tek bölüm içinde
            assert text[g.char_start:g.char_end].strip()


def test_dev_covers_every_document_type_and_has_unique_questions():
    raw = [json.loads(x) for x in (EVAL / "dev.jsonl").read_text(encoding="utf-8").splitlines()]
    types = {r["doc_type"] for r in raw if r["answerable"]}
    assert types == {"procedure", "job_description", "competency", "workflow"}
    assert len({r["question"] for r in raw}) == len(raw)
    assert all(r["etiket_durumu"] == "taslak_proje_sahibi_incelemesi_bekliyor" for r in raw)


def test_security_cases_at_least_ten_with_explicit_expectations():
    cases = [json.loads(x) for x in (EVAL / "guvenlik.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(cases) >= 10 and all(c["beklenen"] for c in cases)
    kinds = {c["tur"] for c in cases}
    assert {"enjeksiyon_getirilen", "kural_catismasi", "suresi_dolmus_surum_guncel", "emekli_belge", "tarihsel_sorgu"} <= kinds


def test_no_code_reads_sealed_test_content():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (H.ROOT / "src").rglob("*.py"))
    assert "muhurlu_test" not in src
    lines = [ln for p in (H.ROOT / "scripts").rglob("*.py") for ln in p.read_text(encoding="utf-8").splitlines() if "muhurlu_test" in ln]
    assert lines and all("sha_file(" in ln or ln.strip().startswith(("-", "#", '"')) or "Mühürlü" in ln for ln in lines), lines


# ---- Genişletilmiş dev (dev_ek.jsonl) -------------------------------------------------------------------------


EK = load_dev_set(EVAL / "dev_ek.jsonl")


def test_dev_extension_is_dev_only_paraphrases_and_new_out_of_scope():
    base = {c.case_key: c for c in DEV.cases}
    raw = {json.loads(x)["case_key"]: json.loads(x) for x in (EVAL / "dev_ek.jsonl").read_text(encoding="utf-8").splitlines()}
    paras = [c for c in EK.cases if c.answerable]
    assert len(paras) == 12 and sum(not c.answerable for c in EK.cases) == 24
    for c in paras:  # yalnız mevcut dev sorularının yeniden ifadesi: aynı grup, aynı gold
        src = base[raw[c.case_key]["kaynak_vaka"]]
        assert c.paraphrase_group == src.paraphrase_group and c.gold == src.gold and c.question != src.question
    assert all(not c.gold for c in EK.cases if not c.answerable)


def test_expanded_dev_totals_and_unique_questions():
    cases = DEV.cases + EK.cases
    assert (sum(c.answerable for c in cases), sum(not c.answerable for c in cases)) == (30, 30)
    assert len({c.question for c in cases}) == len(cases) and len({c.case_key for c in cases}) == len(cases)


def test_review_table_lists_every_dev_case():
    table = (H.ROOT / "docs" / "raporlar" / "ASAMA_7_DEV_SETI_INCELEME_TABLOSU.md").read_text(encoding="utf-8")
    assert all(f"| {c.case_key} |" in table for c in DEV.cases + EK.cases)
    assert "muhurlu_test" not in table


def test_label_corrections_are_documented_and_keep_paraphrase_gold_equal():
    raw = [json.loads(x) for n in ("dev.jsonl", "dev_ek.jsonl") for x in (EVAL / n).read_text(encoding="utf-8").splitlines()]
    fixed = {r["paraphrase_group"] for r in raw if "etiket_duzeltmesi" in r}
    assert fixed == {"C001", "C021", "C026", "C031", "C042"}
    by_group = {}
    for r in raw:
        if r["answerable"]:
            by_group.setdefault(r["paraphrase_group"], []).append(sorted((g["doc_uid"], g["char_start"]) for g in r["gold"]))
    assert all(all(g == gs[0] for g in gs) for gs in by_group.values())  # aynı gruptaki sorular aynı gold
    assert all(len(r["gold"]) >= 2 for r in raw if r["paraphrase_group"] in fixed)
