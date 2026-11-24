"""Aşama 7.1: kontrollü sentetik corpus, belge onayı, sürüm ve geçerlilik yönetimi (ağsız)."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta

import pytest
import rag_helpers as H

from medakis.config import load_settings
from medakis.rag.contracts.documents import DOC_TYPES, DocStatus
from medakis.rag.errors import CorpusError, LedgerError, StoreUnavailable
from medakis.rag.ingestion.approval_ledger import APPROVE, RETIRE, ApprovalLedger, effective_states
from medakis.rag.ingestion.corpus_loader import load_corpus
from medakis.rag.ingestion.document_policy import eligible_documents
from medakis.rag.vector_store.location import check_store_dir

T0 = H.T0


@pytest.fixture()
def store():
    path = H.d_tmp()
    yield path
    H.remove(path)


# ---- Gerçek taslak corpus ------------------------------------------------------------------------------------


def test_controlled_corpus_loads_as_drafts_without_problems():
    corpus = load_corpus(H.CORPUS)
    assert corpus.problems == () and len(corpus.documents) == 19
    states, problems = effective_states([d.record for d in corpus.documents], [])
    assert problems == [] and all(s.status is DocStatus.DRAFT for s in states.values())  # hiçbiri onaylı değil
    sources = [d.record for d in corpus.documents if d.record.purpose == "kaynak"]
    for doc_type in DOC_TYPES:  # her belge türünden en az 3 kaynak belge
        assert sum(r.doc_type == doc_type for r in sources) >= 3, doc_type
    manifest = json.loads((H.CORPUS / "manifest.json").read_text(encoding="utf-8"))
    assert all(e["status"] == "draft" and e["approved_by"] is None and e["is_synthetic"] for e in manifest)


def test_corpus_has_version_and_safety_fixtures():
    recs = {d.record.uid: d.record for d in load_corpus(H.CORPUS).documents}
    v1, v2 = recs["SYN-YET-GECERLILIK@1.0"], recs["SYN-YET-GECERLILIK@2.0"]
    assert v1.valid_until == v2.valid_from and not v1.overlaps(v2)  # tarihsel ve güncel sürüm bitişik, örtüşmez
    assert recs["SYN-FKS-CAKISMA@1.0"].overlaps(recs["SYN-FKS-CAKISMA@1.1"])
    assert recs["SYN-FKS-KURAL@1.0"].rule_conflict and recs["SYN-FKS-TALIMAT@1.0"].purpose == "guvenlik_fiksturu"


def test_corpus_text_is_synthetic_and_carries_no_real_identifiers():
    for doc in load_corpus(H.CORPUS).documents:
        first = doc.text.splitlines()[0]
        assert first.startswith("# Sentetik") and "sentetik" in doc.text.lower()
        assert not any(w in doc.text for w in ("TC Kimlik", "telefon", "@", "http"))


def test_original_package_corpus_is_not_a_controlled_corpus():
    old = load_corpus(H.ROOT / "corpus")  # özgün paket corpus'u (smoke fikstürü) değişmedi ve kontrollü sözleşmeye girmez
    assert old.documents == () and all("eksik_alan:purpose" in p for p in old.problems if p.endswith("purpose"))
    assert any(p.endswith("eksik_alan:purpose") for p in old.problems)


# ---- Yükleyici reddeder ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("change, code", [
    ({"status": "approved", "approved_by": "x", "approved_at": "2026-11-01T00:00:00Z"}, "manifest_onay_tasiyamaz"),
    ({"path": "../disari.md"}, "yol_gecersiz"),
    ({"path": "alt/belge.md"}, "yol_gecersiz"),
    ({"doc_type": "klinik"}, "doc_type_gecersiz"),
    ({"is_synthetic": False}, "sentetik_degil"),
    ({"valid_from": "2026-11-01T00:00:00+03:00"}, "valid_from_utc_degil"),
    ({"valid_until": "2026-10-01T00:00:00Z"}, "gecerlilik_araligi_bos"),
    ({"version": "v1"}, "version_gecersiz"),
    ({"content_sha256": "0" * 63}, "content_sha256_gecersiz"),
])  # fmt: skip
def test_manifest_metadata_is_mandatory_and_validated(store, change, code):
    e, body = H.entry("SYN-DENEME")
    e.update(change)
    corpus = load_corpus(H.write_corpus(store / "c", [(e, body)]))
    assert corpus.documents == () and any(p.endswith(code) for p in corpus.problems), corpus.problems


def test_missing_field_rejected(store):
    e, body = H.entry("SYN-DENEME")
    del e["valid_until"]
    assert load_corpus(H.write_corpus(store / "c", [(e, body)])).problems == ("#0:eksik_alan:valid_until",)


@pytest.mark.parametrize("raw, code", [
    (None, "icerik_ozeti_uyusmuyor"),
    (b"# Sentetik a\n\xff\xfe", "utf8_degil"),
    ("# Sentetik a\n\x07zil\n".encode(), "denetim_karakteri"),
    ("# Deneme belgesi\n\nmetin\n".encode(), "sentetik_baslik_yok"),
    (("# Sentetik büyük\n" + "a" * 70_000).encode(), "boyut_siniri"),
])  # fmt: skip
def test_file_content_checks(store, raw, code):
    e, body = H.entry("SYN-DENEME")
    folder = H.write_corpus(store / "c", [(e, body)])
    data = raw if raw is not None else (body + "değişti").encode()
    (folder / e["path"]).write_bytes(data)
    if raw is not None:  # özeti yeni içeriğe göre güncelle: yalnız ilgili denetim tetiklensin
        import hashlib

        e["content_sha256"] = hashlib.sha256(data).hexdigest()
        (folder / "manifest.json").write_text(json.dumps([e]), encoding="utf-8")
    assert load_corpus(folder).problems == (f"SYN-DENEME@1.0:{code}",)


def test_symlink_rejected(store, monkeypatch):
    e, body = H.entry("SYN-DENEME")
    folder = H.write_corpus(store / "c", [(e, body)])
    target = folder / e["path"]
    real = type(target).is_symlink
    # Windows oturumunda sembolik bağlantı izni olmayabilir: denetim yolu sahte bağlantıyla sınanır.
    monkeypatch.setattr(type(target), "is_symlink", lambda self: self == target or real(self))
    assert load_corpus(folder).problems == ("SYN-DENEME@1.0:sembolik_baglanti",)


def test_duplicate_and_structural_errors(store):
    a = H.entry("SYN-DENEME")
    folder = H.write_corpus(store / "c", [a, a])
    assert load_corpus(folder).problems == ("SYN-DENEME@1.0:yinelenen_belge",)
    with pytest.raises(CorpusError):
        load_corpus(folder.relative_to(H.ROOT))  # göreli yol
    (folder / "manifest.json").write_text("{bozuk", encoding="utf-8")
    with pytest.raises(CorpusError, match="manifest_okunamadi"):
        load_corpus(folder)


# ---- Onay izi ve durum makinesi -------------------------------------------------------------------------------


def _ledger_corpus(store, *items):
    corpus = load_corpus(H.write_corpus(store / "c", list(items)))
    return corpus, {d.record.uid: d.record for d in corpus.documents}, ApprovalLedger(store / "belge_onaylari.jsonl")


def test_approve_retire_flow_and_terminal_states(store):
    corpus, recs, ledger = _ledger_corpus(store, H.entry("SYN-A"))
    known = list(recs.values())
    rec = recs["SYN-A@1.0"]
    with pytest.raises(LedgerError, match="yalniz_onayli"):
        ledger.append(RETIRE, rec, "sahip", "deneme", T0, known)
    ledger.append(APPROVE, rec, "sahip", "incelendi", T0, known)
    with pytest.raises(LedgerError, match="yalniz_taslak"):
        ledger.append(APPROVE, rec, "sahip", "ikinci", T0, known)
    ledger.append(RETIRE, rec, "sahip", "eskidi", T0 + timedelta(hours=1), known)
    states, problems = effective_states(known, ledger.events())
    s = states["SYN-A@1.0"]
    assert problems == [] and s.status is DocStatus.RETIRED and s.approved_by == "sahip" and s.retired_by == "sahip"
    with pytest.raises(LedgerError):
        ledger.append(APPROVE, rec, "sahip", "yeniden", T0, known)  # emekli son durumdur


def test_overlapping_approved_versions_rejected(store):
    corpus, recs, ledger = _ledger_corpus(store, H.entry("SYN-A", "1.0"), H.entry("SYN-A", "1.1", valid_from="2026-11-10T00:00:00Z"),
                                          H.entry("SYN-A", "2.0", valid_from="2027-01-01T00:00:00Z"))  # fmt: skip
    known = list(recs.values())
    ledger.append(APPROVE, recs["SYN-A@1.1"], "sahip", "ok", T0, known)
    with pytest.raises(LedgerError, match="onayli_surum_ortusuyor"):
        ledger.append(APPROVE, recs["SYN-A@1.0"], "sahip", "ok", T0, known)
    with pytest.raises(LedgerError, match="onayli_surum_ortusuyor"):
        ledger.append(APPROVE, recs["SYN-A@2.0"], "sahip", "ok", T0, known)  # 1.1 açık uçlu: 2.0 ile örtüşür


@pytest.mark.parametrize("actor, reason", [("", "ok"), ("a\nb", "ok"), ("x" * 65, "ok"), ("sahip", " ")])
def test_actor_and_reason_required(store, actor, reason):
    _, recs, ledger = _ledger_corpus(store, H.entry("SYN-A"))
    with pytest.raises(LedgerError):
        ledger.append(APPROVE, recs["SYN-A@1.0"], actor, reason, T0, list(recs.values()))
    assert ledger.events() == []


def test_tampered_chain_fails_closed(store):
    _, recs, ledger = _ledger_corpus(store, H.entry("SYN-A"), H.entry("SYN-B"))
    known = list(recs.values())
    ledger.append(APPROVE, recs["SYN-A@1.0"], "sahip", "ok", T0, known)
    ledger.append(APPROVE, recs["SYN-B@1.0"], "sahip", "ok", T0, known)
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    ledger.path.write_text(lines[0].replace('"sahip"', '"baskasi"') + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="zincir_bozuk"):
        ledger.events()
    ledger.path.write_text(lines[1] + "\n", encoding="utf-8")  # satır silme
    with pytest.raises(LedgerError, match="zincir_bozuk"):
        ledger.events()


def test_approved_content_change_makes_document_unusable(store):
    corpus, recs, ledger = _ledger_corpus(store, H.entry("SYN-A"))
    ledger.append(APPROVE, recs["SYN-A@1.0"], "sahip", "ok", T0, list(recs.values()))
    e, _ = H.entry("SYN-A", text="# Sentetik değişmiş\n\nYeni metin.\n")
    corpus2 = load_corpus(H.write_corpus(store / "c", [(e, "# Sentetik değişmiş\n\nYeni metin.\n")]))
    states, _ = effective_states([d.record for d in corpus2.documents], ledger.events())
    elig = eligible_documents(states, T0)
    assert elig.allowed == () and elig.excluded["SYN-A@1.0"] == "onayli_icerik_degisti"
    assert "onayli_icerik_degisti:SYN-A@1.0" in elig.conflicts  # düzeltme yeni sürüm olmalı; fail-closed


# ---- Geçerlilik ve filtreleme ----------------------------------------------------------------------------------


def _states(store, *items, approve=(), retire=()):
    corpus, recs, ledger = _ledger_corpus(store, *items)
    known = list(recs.values())
    for uid in approve:
        ledger.append(APPROVE, recs[uid], "sahip", "ok", T0, known)
    for uid in retire:
        ledger.append(RETIRE, recs[uid], "sahip", "eskidi", T0, known)
    return effective_states(known, ledger.events())[0]


def test_validity_boundary_start_inclusive_end_exclusive(store):
    states = _states(store, H.entry("SYN-A", "1.0", valid_until="2026-11-10T00:00:00Z"),
                     H.entry("SYN-A", "2.0", valid_from="2026-11-10T00:00:00Z"), approve=("SYN-A@1.0", "SYN-A@2.0"))  # fmt: skip
    edge = datetime(2026, 11, 10, tzinfo=UTC)
    assert eligible_documents(states, edge).allowed_uids == ("SYN-A@2.0",)
    assert eligible_documents(states, edge - timedelta(microseconds=1)).allowed_uids == ("SYN-A@1.0",)
    assert eligible_documents(states, datetime(2026, 10, 31, 23, 59, tzinfo=UTC)).allowed_uids == ()


def test_draft_and_retired_never_queryable_even_historically(store):
    states = _states(store, H.entry("SYN-A"), H.entry("SYN-B"), H.entry("SYN-C"),
                     approve=("SYN-B@1.0", "SYN-C@1.0"), retire=("SYN-C@1.0",))  # fmt: skip
    for at in (T0, datetime(2026, 11, 2, tzinfo=UTC)):  # güncel ve tarihsel
        elig = eligible_documents(states, at)
        assert elig.allowed_uids == ("SYN-B@1.0",)
        assert elig.excluded["SYN-A@1.0"] == "taslak" and elig.excluded["SYN-C@1.0"] == "emekli"


def test_query_time_version_conflict_fails_closed(store):
    states = _states(store, H.entry("SYN-A", "1.0"), H.entry("SYN-A", "1.1"), approve=("SYN-A@1.0",))
    from dataclasses import replace

    forged = dict(states)
    forged["SYN-A@1.1"] = replace(states["SYN-A@1.0"], record=states["SYN-A@1.1"].record,
                                  approved_sha256=states["SYN-A@1.1"].record.content_sha256)  # fmt: skip
    elig = eligible_documents(forged, T0)  # iz dışından gelmiş tutarsız durum (ör. elle düzenlenmiş veri)
    assert elig.conflicts and elig.conflicts[0].startswith("surum_cakismasi:")


def test_naive_or_non_utc_time_rejected(store):
    states = _states(store, H.entry("SYN-A"))
    with pytest.raises(ValueError):
        eligible_documents(states, datetime(2026, 11, 20, 12))


# ---- Depo konumu (KM29) -----------------------------------------------------------------------------------------


class _Disk:
    def __init__(self, free):
        self.free = free


def test_store_location_rules(store):
    big = lambda _p: _Disk(10**15)  # noqa: E731
    assert check_store_dir(store / "depo", min_free_gb=30, disk_usage=big).is_dir()
    with pytest.raises(StoreUnavailable, match="sistem_surucusu_yasak"):
        check_store_dir(store, min_free_gb=30, sys_drive=str(store)[:2].upper(), disk_usage=big)
    with pytest.raises(StoreUnavailable, match="mutlak_degil"):
        check_store_dir("data/rag", min_free_gb=30, disk_usage=big)
    with pytest.raises(StoreUnavailable, match="mutlak_degil"):
        check_store_dir(r"\\sunucu\paylasim\rag", min_free_gb=30, disk_usage=big)
    with pytest.raises(StoreUnavailable, match="bos_alan_yetersiz"):
        check_store_dir(store, min_free_gb=30, disk_usage=lambda _p: _Disk(1024))
    (store / "dosya").write_text("x", encoding="utf-8")
    with pytest.raises(StoreUnavailable, match="klasor_degil"):
        check_store_dir(store / "dosya", min_free_gb=30, disk_usage=big)
    with pytest.raises(StoreUnavailable, match="depo_yolu_tanimsiz"):
        check_store_dir(None, min_free_gb=30)


def test_default_store_is_absolute_under_project_and_env_override():
    s = load_settings(env={})
    assert s.rag_store_dir.is_absolute() and s.rag_store_dir == H.ROOT / "data" / "rag" and s.rag_min_free_gb == 30.0
    assert load_settings(env={"MEDAKIS_RAG_STORE": r"E:\medakis\rag"}).rag_store_dir == type(s.rag_store_dir)(r"E:\medakis\rag")


# ---- Yerel onay komutu --------------------------------------------------------------------------------------------


def _script():
    spec = importlib.util.spec_from_file_location("rag_belge_onay", H.ROOT / "scripts" / "rag_belge_onay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARGS = ["--kaynak", "SYN-A", "--surum", "1.0", "--karar", "onayla", "--aktor", "sahip", "--gerekce", "incelendi"]


def test_approval_command_requires_interactive_terminal_and_exact_phrase(store):
    script = _script()
    folder = H.write_corpus(store / "c", [H.entry("SYN-A")])
    out = []
    common = {"corpus_dir": folder, "store_dir": store / "depo", "out": out.append, "now": lambda: T0}
    assert script.run(ARGS, interactive=lambda: False, ask=lambda _: "ONAYLIYORUM SYN-A@1.0", **common) == 2
    assert script.run(ARGS, interactive=lambda: True, ask=lambda _: "evet", **common) == 1
    ledger = ApprovalLedger(store / "depo" / script.LEDGER_NAME)
    assert ledger.events() == []  # izinsiz ya da yanlış ifade: iz değişmez
    assert script.run(ARGS, interactive=lambda: True, ask=lambda _: "ONAYLIYORUM SYN-A@1.0", **common) == 0
    assert [e["karar"] for e in ledger.events()] == ["onayla"] and ledger.events()[0]["aktor"] == "sahip"


def test_approval_command_refuses_security_fixtures(store):
    script = _script()
    e, body = H.entry("SYN-FKS-DENEME", purpose="guvenlik_fiksturu")
    folder = H.write_corpus(store / "c", [(e, body)])
    out = []
    args = ["--kaynak", "SYN-FKS-DENEME", "--surum", "1.0", "--karar", "onayla", "--aktor", "sahip", "--gerekce", "x"]
    assert script.run(args, interactive=lambda: True, ask=lambda _: "ONAYLIYORUM SYN-FKS-DENEME@1.0", corpus_dir=folder,
                      store_dir=store / "depo", out=out.append, now=lambda: T0) == 2  # fmt: skip
    assert "güvenlik fikstürü" in out[-1] and not (store / "depo" / script.LEDGER_NAME).exists()


# ---- Belge içeriği: kodla uyum (belge onayı öncesi düzeltmeler) -------------------------------------------------


def _text(uid):
    return load_corpus(H.CORPUS).by_uid()[uid].text


def test_reserve_defined_as_layer_b_formula_and_separate_from_gap():
    text = _text("SYN-YET-ACIK@1.0")
    assert "max(0, tahmin edilen talep − somut görev dakikası)" in text and "rezervden ayrıdır" in text
    assert "boş kalan uygun personel süresi" not in text


def test_export_file_hash_computed_separately_and_shown():
    text = _text("SYN-PRC-DISA@1.0")
    assert "ayrıca hesaplanır ve arayüzde gösterilir" in text and "dosyanın içinde yer almaz" in text
    assert "kendi SHA-256 özetini taşır" not in text


def test_historical_competency_version_not_presented_as_current():
    old, new = _text("SYN-YET-GECERLILIK@1.0"), _text("SYN-YET-GECERLILIK@2.0")
    assert "tarihsel sürüm" in old.splitlines()[0] and "güncel kural değildir" in old
    assert "bütün süresi boyunca" in new and "tarihsel" not in new


def test_task_duration_fixed_at_creation_and_skill_names_match_catalog():
    assert "görev oluşturulduğu anda bilinen son bakım katsayısı" in _text("SYN-PRC-DEVIR@1.0")
    servis = _text("SYN-YET-SERVIS@1.0")
    assert "monitör takibi" in servis and "triaj" in servis and "yapılandırma kaydından" not in servis


def test_fixtures_are_flagged_and_current_sources_are_13():
    recs = [d.record for d in load_corpus(H.CORPUS).documents]
    assert sum(r.purpose == "kaynak" for r in recs) == 13
    assert {r.uid for r in recs if r.purpose == "surum_fiksturu"} == {"SYN-YET-GECERLILIK@1.0"}  # tarihsel sürüm fikstürü
    assert {r.source_key for r in recs if r.purpose == "guvenlik_fiksturu"} == {"SYN-FKS-EMEKLI", "SYN-FKS-TALIMAT",
                                                                               "SYN-FKS-KURAL", "SYN-FKS-CAKISMA"}  # fmt: skip


@pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
def test_approval_command_treats_missing_input_as_cancel(store, exc):
    script = _script()
    folder = H.write_corpus(store / "c", [H.entry("SYN-A")])

    def ask(_prompt):
        raise exc

    out = []
    assert script.run(ARGS, interactive=lambda: True, ask=ask, corpus_dir=folder, store_dir=store / "depo",
                      out=out.append, now=lambda: T0) == 1  # fmt: skip
    assert out[-1].startswith("VAZGEÇİLDİ") and not (store / "depo" / script.LEDGER_NAME).exists()


def test_batch_approval_script_lists_only_current_sources_and_never_feeds_input():
    ps = (H.ROOT / "scripts" / "rag_belge_onay_toplu.ps1").read_text(encoding="utf-8-sig")
    listed = ps[ps.index("$Belgeler = @("):ps.index(")", ps.index("$Belgeler = @("))]
    keys = [k.strip().strip("'") for k in listed.split("(", 1)[1].replace("\n", " ").split(",")]
    recs = {d.record.source_key: d.record for d in load_corpus(H.CORPUS).documents if d.record.purpose == "kaynak"}
    assert len(keys) == 13 and "SYN-YET-GECERLILIK|2.0" in keys and not any("FKS" in k for k in keys)
    assert {k.split("|")[0] for k in keys} == set(recs)  # 13 güncel kaynak anahtarının hepsi, başka hiçbiri
    assert "ONAYLIYORUM" not in ps.split("#>", 1)[1]  # betik onay ifadesini kendisi vermez
    assert "--karar onayla" in ps and "Proje Sahibi" in ps


def test_version_fixture_refused_by_approval_command(store):
    script = _script()
    e, body = H.entry("SYN-ESKI", purpose="surum_fiksturu")
    folder = H.write_corpus(store / "c", [(e, body)])
    out = []
    args = ["--kaynak", "SYN-ESKI", "--surum", "1.0", "--karar", "onayla", "--aktor", "sahip", "--gerekce", "x"]
    assert script.run(args, interactive=lambda: True, ask=lambda _: "ONAYLIYORUM SYN-ESKI@1.0", corpus_dir=folder,
                      store_dir=store / "depo", out=out.append, now=lambda: T0) == 2  # fmt: skip
    assert not (store / "depo" / script.LEDGER_NAME).exists()


def test_reclassification_keeps_index_identity_and_approvals():
    from medakis.rag.chunking.chunker import chunk_corpus, corpus_sha256

    chunks = chunk_corpus(load_corpus(H.CORPUS).documents)
    assert len(chunks) == 73 and corpus_sha256(chunks)  # amaç alanı parça kimliğine girmez: hazır indeksler geçerli kalır
