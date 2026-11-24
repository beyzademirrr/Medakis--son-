"""S3 (giriş parçalarının dışlanması, parcalama_v2), dev gold eşleşmeleri ve S4 bağımsız soru şablonu/doğrulaması."""

import importlib.util
import json

import pytest
import rag_helpers as H

from medakis.rag.chunking.chunker import ChunkConfig, chunk_corpus
from medakis.rag.chunking.markdown_sections import INTRO, parse_sections
from medakis.rag.contracts.providers import EmbedderInfo
from medakis.rag.evaluation.eval_set import gold_chunk_ids, load_dev_set
from medakis.rag.ingestion.corpus_loader import load_corpus
from medakis.rag.vector_store.index_identity import index_key

EVAL = H.ROOT / "evaluation" / "asama7"
DOCS = load_corpus(H.CORPUS)
V1, V2 = ChunkConfig(), ChunkConfig(version="parcalama_v2", exclude_intro=True)
CASES = [c for n in ("dev.jsonl", "dev_ek.jsonl") for c in load_dev_set(EVAL / n).cases]


# ---- S3: parcalama_v2 ----------------------------------------------------------------------------------------------


def test_v2_drops_only_intro_sections_and_keeps_all_other_chunks():
    v1, v2 = chunk_corpus(DOCS.documents, V1), chunk_corpus(DOCS.documents, V2)
    assert (len(v1), len(v2)) == (73, 54)
    assert {(c.doc_uid, c.text) for c in v1 if c.section != INTRO} == {(c.doc_uid, c.text) for c in v2}
    assert all(c.section != INTRO for c in v2)


def test_v2_is_a_separate_versioned_identity_and_v1_identity_unchanged():
    info = EmbedderInfo("m", "d" * 64, 8, 512, 512, "", "", "s")
    assert "exclude_intro" not in V1.as_dict() and V2.as_dict()["exclude_intro"] is True
    assert index_key(info, V1, "x" * 64) == index_key(info, ChunkConfig(**V1.as_dict()), "x" * 64)  # eski manifestler aynı anahtar
    assert index_key(info, V1, "x" * 64) != index_key(info, V2, "x" * 64)
    with pytest.raises(ValueError):
        ChunkConfig(exclude_intro=True)  # sürüm adı değişmeden giriş dışlanamaz


# ---- Dev gold düzeltmeleri ve kaynak eşleşmeleri ---------------------------------------------------------------


@pytest.mark.parametrize("config", [V1, V2], ids=["v1", "v2"])
def test_every_dev_gold_maps_to_chunks_and_never_to_intro(config):
    parts = [c.map_entry() for c in chunk_corpus(DOCS.documents, config)]
    for c in CASES:
        if c.answerable:
            ids = gold_chunk_ids(c.gold, parts)
            assert ids, c.case_key
            assert all(p["section"] != INTRO for p in parts if p["chunk_id"] in ids), c.case_key


def test_gold_spans_lie_in_named_section_of_current_source():
    by_uid = DOCS.by_uid()
    raw = [json.loads(x) for n in ("dev.jsonl", "dev_ek.jsonl") for x in (EVAL / n).read_text(encoding="utf-8").splitlines()]
    for r in raw:
        for g in r["gold"]:
            doc = by_uid[g["doc_uid"]]
            assert doc.record.purpose == "kaynak" and doc.record.version == g["version"]
            sec = next(s for s in parse_sections(doc.text)[1] if s.name == g["section"])
            assert sec.start <= g["char_start"] < g["char_end"] <= sec.end, r["case_key"]


@pytest.mark.parametrize("group, uid, section", [
    ("C021", "SYN-ROL-SORUMLU@1.0", "İnceleme"), ("C001", "SYN-ROL-SORUMLU@1.0", "Sınırlar"),
    ("C026", "SYN-ROL-DESTEK@1.0", "Görev sınırı"), ("C031", "SYN-ROL-HEMSIRE@1.0", "Görev adaylığı"),
    ("C042", "SYN-YET-ACIK@1.0", "Rezerv"),
])  # fmt: skip
def test_audit_corrections_present_in_every_case_of_group(group, uid, section):
    members = [c for c in CASES if c.paraphrase_group == group]
    assert members
    for c in members:
        assert any(g.doc_uid == uid and _section(g) == section for g in c.gold), c.case_key


def _section(g):
    text = DOCS.by_uid()[g.doc_uid].text
    return next(s.name for s in parse_sections(text)[1] if s.start <= g.char_start < s.end)


# ---- S4: bağımsız soru şablonu ve doğrulama ----------------------------------------------------------------------


def _validator():
    spec = importlib.util.spec_from_file_location("dogrula", H.ROOT / "scripts" / "rag_bagimsiz_sorular_dogrula.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_template_is_empty_and_rejected_as_data():
    rows = [json.loads(x) for x in (EVAL / "bagimsiz_kaynak_disi_sablon.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 30 and all(r["question"] == "" and r["etiket_durumu"] == "bos_sablon" for r in rows)  # soru yazılmadı
    result = _validator().validate(EVAL / "bagimsiz_kaynak_disi_sablon.jsonl")
    assert not result["gecti"] and any(p.endswith("soru_uzunlugu") for p in result["sorun"])


def test_validator_accepts_valid_file_and_never_echoes_questions(tmp_path):
    rows = [{"case_key": f"B{i:03d}", "split": "dev", "category": "no_source", "answerable": False, "gold": [],
             "question": f"Deneme biçim sorusu numara {i} geçerli uzunlukta mı?", "as_of": "2026-11-23T12:00:00Z",
             "paraphrase_group": f"B{i:03d}", "yakinlik": "alana_yakin" if i % 2 else "alana_uzak", "neden_kaynak_disi": "biçim testi",
             "hazirlayan_rolu": "rol_a", "kontrol_eden_rolu": "rol_b", "etiket_durumu": "bagimsiz_kontrol_edildi"} for i in range(1, 31)]  # fmt: skip
    path = tmp_path / "dolu.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    v = _validator()
    ok = v.validate(path)
    assert ok["gecti"] and ok["vaka"] == 30 and "Deneme biçim" not in json.dumps(ok, ensure_ascii=False)
    rows[0]["hazirlayan_rolu"] = rows[0]["kontrol_eden_rolu"]
    rows[1]["question"] = "Bana 05321234567 numarasından ulaşılabilir mi efendim?"
    rows[2]["question"] = load_dev_set(EVAL / "dev.jsonl").cases[0].question
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    bad = v.validate(path)
    assert {"B001:hazirlayan_kontrol_eden", "B002:kisisel_veri_kalibi", "B003:mevcut_dev_sorusuyla_ayni"} <= set(bad["sorun"])


def test_s3_experiment_record_keeps_real_store_untouched():
    rec = json.loads((H.ROOT / "docs" / "veri" / "asama7_s3_giris_deneyi.json").read_text(encoding="utf-8"))
    assert rec["kabul_kurali"].startswith("bge-m3") and set(rec["adaylar"]) == {"bge-m3", "qwen3-embedding:0.6b"}
    assert all(v is None for v in rec["gercek_depo_esik"].values())  # eşik yazılmadı: normal sorgu kapalı
    for r in rec["adaylar"].values():
        assert r["v1_indeks"] != r["v2_indeks"] and r["v1_indeks"] in rec["gercek_depo_hazir_indeksler"].values()


def test_guide_example_row_is_format_only_and_rejected_as_data(tmp_path):
    guide = (H.ROOT / "docs" / "raporlar" / "S4_HAZIRLAYICI_ICIN_KISA_YONERGE.md").read_text(encoding="utf-8")
    example = json.loads(next(ln for ln in guide.splitlines() if ln.startswith('{"answerable"')))
    rows = [{**example, "case_key": f"B{i:03d}", "paraphrase_group": f"B{i:03d}"} for i in range(1, 31)]
    path = tmp_path / "ornek.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    result = _validator().validate(path)
    assert not result["gecti"] and all(f"B{i:03d}:ornek_ya_da_yer_tutucu" in result["sorun"] for i in range(1, 31))
    assert not (EVAL / "bagimsiz_kaynak_disi.jsonl").exists()  # gerçek bağımsız soru dosyası henüz yok (yazılmadı)


def test_aşama8_draft_is_design_only():
    draft = (H.ROOT / "docs" / "raporlar" / "ASAMA_8_KANIT_DENETIMI_TEKNIK_TASLAGI.md").read_text(encoding="utf-8")
    assert "Uygulanmadı" in draft and "fail-closed" in draft.lower()
    rag = H.ROOT / "src" / "medakis" / "rag"
    src = "\n".join(p.read_text(encoding="utf-8") for p in rag.rglob("*.py"))
    assert "/api/generate" not in src  # Aşama 8'de yalnız kip kapılı /api/chat kullanılır (test_rag_generation)
