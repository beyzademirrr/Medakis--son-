"""Aşama 5 / U7: insan onayı, ret, iptal, geri alma, plan özeti ve denetim izi (veritabanısız; test_sentetik).

Gerçek atama yok, veritabanı yok. Öneriler U4 (Katman A) ve U6 (Katman B) test fikstürlerinden üretilir ve bağımsız
doğrulayıcıdan geçer. Yetki politikası ve onay süresi test yapılandırmasıdır; yetkili değer değildir.
"""

import ast
import copy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
import test_layer_a_u4 as UA
import test_layer_b_u6 as UB

from medakis.approvals import audit_log as AL
from medakis.approvals import plan_hash as PH
from medakis.approvals import workflow as W
from medakis.approvals.authorization import ACTIONS, ACT_REGISTER, UI_SESSION, ActorSession, AuthorizationPolicy
from medakis.domain.planning import PlannedUnavailability
from medakis.optimization import layer_a as L
from medakis.optimization import layer_b as B

T, M, H, KNOWN, SRC = UA.T, UA.M, UA.H, UA.KNOWN, "test_sentetik"
UNITS = sorted(UA.UNITS)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def session(actor, origin=UI_SESSION):
    return ActorSession(actor, f"oturum-{actor}", origin, T - H)


POLICY = AuthorizationPolicy(
    {"yonetici": frozenset((a, u) for a in ACTIONS for u in UNITS), "sistem": frozenset((ACT_REGISTER, u) for u in UNITS),
     "gozlemci": frozenset(), "yalniz_A": frozenset((a, UA.A) for a in ACTIONS)},
    SRC, "test", KNOWN)  # fmt: skip
APPROVAL = W.ApprovalPolicy(30, SRC, "test", KNOWN)


def CUR(inp):  # noqa: N802  # güncel güvenlik girdileri (testte: öneriyi üreten girdiler)
    return PH.SafetyInputs.from_layer_a(inp)


def service(journal=None, clock=None, authz=POLICY, policy=APPROVAL):
    return W.ApprovalService(journal if journal is not None else AL.MemoryJournal(), authz, policy, clock or Clock(T + 2 * M))


def proposal_a(nurse=None, at=T):
    inp = UA.build(nurse or {UA.A: 30, UA.B: 120}, at=at)
    res = UA.published(L.optimize(inp))
    return inp, res


@pytest.fixture()
def registered():
    inp, res = proposal_a()
    svc = service()
    d = svc.register(session("sistem"), PH.KIND_A, inp, res, "kayit-1")
    assert d.ok and d.state == W.PENDING, d.codes
    return svc, inp, res, d.proposal_id, d.event["plan_sha256"]


def refused_events(svc):
    return [e for e in svc.journal.events() if e["eylem"] == W.EV_REFUSED]


# ---- Plan özeti --------------------------------------------------------------------------------------------


def test_plan_hash_contents_and_determinism():
    inp, res = proposal_a()
    snap = PH.snapshot_layer_a(inp, res)
    assert snap["karar_ani"] == T and snap["cozum_surumu"]["cozum_ozeti"] == res["cozum"]["ozet_sha256"]
    assert snap["cozum_surumu"]["cozucu"].startswith("ortools-cp-sat-")
    assert snap["model_surumleri"] == [{"model": "random_forest", "model_surumu": "rf@t", "model_sha256": "t"}]  # U3 satırlarından
    assert {k["tur"] for k in snap["kalemler"]} == {"tasima"} and all({"personel", "baslangic", "bitis"} <= set(k) for k in snap["kalemler"])
    assert snap["calisma_yapilandirmasi"]["dondurma_politikasi"] == L.FREEZE_OFF
    assert PH.plan_sha256(snap) == PH.plan_sha256(PH.snapshot_layer_a(inp, res)) and len(PH.plan_sha256(snap)) == 64
    moved = copy.deepcopy(snap)
    moved["kalemler"][0]["hedef_servis"] = UA.ED
    assert PH.plan_sha256(moved) != PH.plan_sha256(snap)
    assert PH.canonical_json({"b": 1, "a": T}) == '{"a":"%s","b":1}' % T.isoformat()


def test_model_versions_tracked_from_u3_rows():
    rows = [{"model": "random_forest", "model_surumu": "rf@abc", "model_sha256": "h1"}, {"model": "random_forest", "model_surumu": "rf@abc",
            "model_sha256": "h1"}, {"model": "baseline_bilgi", "model_surumu": "b@1", "model_sha256": None}, {"model": None}]  # fmt: skip
    assert PH.model_versions_from_rows(rows) == [{"model": "baseline_bilgi", "model_surumu": "b@1", "model_sha256": None},
                                                 {"model": "random_forest", "model_surumu": "rf@abc", "model_sha256": "h1"}]  # fmt: skip


def test_window_fingerprint_sensitive_only_to_window_relevant_later_info():
    inp, _ = proposal_a()
    window = UA.Interval(T, T + 60 * M)
    fp = lambda src, known: PH.window_fingerprint(PH.KIND_A, src, known, window, UNITS)[0]  # noqa: E731
    base = fp(CUR(inp), T)
    outside = PlannedUnavailability(900, 11, T + 3 * H, T + 4 * H, "izin", T + 5 * M, "ani")  # pencere dışında
    inside = PlannedUnavailability(901, 11, T + 10 * M, T + 40 * M, "devamsizlik", T + 5 * M, "ani")  # pencereye dokunuyor
    with_out = replace(CUR(inp), plan=replace(inp.plan, unavailability=inp.plan.unavailability + (outside,)))
    with_in = replace(CUR(inp), plan=replace(inp.plan, unavailability=inp.plan.unavailability + (inside,)))
    assert fp(with_out, T + 10 * M) == base
    assert fp(with_in, T + 10 * M) != base
    assert fp(with_in, T) == base  # karar anında bilinmiyordu


# ---- Mutlu yol ve izlenebilirlik ----------------------------------------------------------------------------


def test_register_approve_trace(registered):
    svc, inp, res, pid, sha = registered
    d = svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-1", "uygun")
    assert d.ok and d.state == W.APPROVED and d.event["veritabani_karari"] == "approved"
    events = svc.journal.events()
    assert [e["eylem"] for e in events] == [W.EV_REGISTER, W.APPROVED] and AL.verify_chain(events) == []
    reg, appr = events
    assert reg["anlik_goruntu"]["karar_ani"] == T.isoformat() and reg["plan_sha256"] == appr["plan_sha256"] == sha
    assert appr["aktor"] == "yonetici" and appr["oturum"] == "oturum-yonetici" and appr["kimlik_notu"].startswith("yerel_prototip")
    assert appr["sistem_uygulamaz"] is True and appr["zaman"] == (T + 2 * M).isoformat() and appr["onceki_ozet"] == reg["olay_ozeti"]
    ok, codes = svc.applicability(pid, CUR(inp))
    assert ok and codes == []


def test_no_patient_data_in_trail(registered):
    svc, inp, _, pid, sha = registered
    svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-1")
    text = AL.canonical_json(svc.journal.events()).lower()
    assert "hasta" not in text and "patient" not in text and "encounter" not in text


# ---- Çift onay, idempotency, eşzamanlılık --------------------------------------------------------------------


def test_double_approval_refused_and_logged(registered):
    svc, inp, _, pid, sha = registered
    assert svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-1").ok
    d = svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-2")
    assert not d.ok and "zaten_onaylandi" in d.codes
    assert refused_events(svc)[-1]["denenen_eylem"] == "onayla" and "zaten_onaylandi" in refused_events(svc)[-1]["nedenler"]
    assert sum(e["eylem"] == W.APPROVED for e in svc.journal.events()) == 1


def test_same_request_id_is_idempotent_and_not_reusable(registered):
    svc, inp, _, pid, sha = registered
    first = svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-1")
    n = len(svc.journal.events())
    again = svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-1")
    assert again.ok and again.codes == ["istek_tekrari_onceki_sonuc"] and len(svc.journal.events()) == n
    assert again.event["olay_ozeti"] == first.event["olay_ozeti"]
    reused = svc.revoke(session("yonetici"), pid, sha, "onay-1", "neden")
    assert not reused.ok and reused.codes == ["istek_kimligi_yeniden_kullanildi"]
    assert not svc.approve(session("yonetici"), pid, sha, CUR(inp), "  ").ok


class RacingJournal(AL.MemoryJournal):
    """İlk eklemeden hemen önce başka bir yazıcının araya girdiğini taklit eder."""

    def __init__(self):
        super().__init__()
        self.raced = False

    def append(self, event, expected_seq):
        if not self.raced and expected_seq > 0:
            self.raced = True
            super().append({"eylem": "baska_islem", "zaman": event["zaman"]}, expected_seq)
        return super().append(event, expected_seq)


def test_concurrent_writer_conflict_prevents_double_write():
    inp, res = proposal_a()
    journal = RacingJournal()
    svc = service(journal)
    pid = svc.register(session("sistem"), PH.KIND_A, inp, res, "kayit-1").proposal_id  # ilk ekleme (sıra 0) yarışmaz
    d = svc.approve(session("yonetici"), pid, svc.state()[1][pid]["plan_sha256"], CUR(inp), "onay-1")
    assert not d.ok and "eszamanli_degisiklik" in d.codes
    assert not any(e["eylem"] == W.APPROVED for e in journal.events())


# ---- Eski öneri -----------------------------------------------------------------------------------------------


def test_stale_by_time_refused(registered):
    svc, inp, _, pid, sha = registered
    svc.clock.t = T + 30 * M  # karar anı + 30 dk = politika sınırı
    d = svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-1")
    assert not d.ok and "oneri_eskimis:sure_doldu" in d.codes


def test_stale_by_later_absence_refused_and_applicability_drops(registered):
    svc, inp, _, pid, sha = registered
    absent = PlannedUnavailability(901, 11, T + 10 * M, T + 40 * M, "devamsizlik", T + M, "ani")
    changed = replace(CUR(inp), plan=replace(inp.plan, unavailability=inp.plan.unavailability + (absent,)))
    d = svc.approve(session("yonetici"), pid, sha, changed, "onay-1")
    assert not d.ok and "oneri_eskimis:girdi_degisti" in d.codes
    missing = svc.approve(session("yonetici"), pid, sha, None, "onay-2")  # güncel kaynak yoksa onay yok
    assert not missing.ok and "guncel_guvenlik_girdisi_eksik:hepsi" in missing.codes
    assert svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-3").ok
    svc.clock.t = T + 5 * M
    ok, codes = svc.applicability(pid, changed)
    assert not ok and "girdi_degisti_yeniden_inceleme_gerekli" in codes
    svc.clock.t = T + 61 * M
    ok, codes = svc.applicability(pid, CUR(inp))
    assert not ok and "pencere_doldu" in codes


def test_newer_proposal_supersedes_and_alternatives_not_merged():
    inp1, res1 = proposal_a()
    inp2, res2 = proposal_a({UA.A: 30, UA.B: 150}, at=T + 15 * M)
    svc = service(clock=Clock(T + 16 * M))
    p1 = svc.register(session("sistem"), PH.KIND_A, inp1, res1, "k1")
    p2 = svc.register(session("sistem"), PH.KIND_A, inp2, res2, "k2")
    old = svc.approve(session("yonetici"), p1.proposal_id, p1.event["plan_sha256"], CUR(inp1), "o1")
    assert not old.ok and f"daha_yeni_oneri_var:{p2.proposal_id}" in old.codes
    assert svc.approve(session("yonetici"), p2.proposal_id, p2.event["plan_sha256"], CUR(inp2), "o2").ok
    inp3, res3 = proposal_a({UA.A: 30, UA.B: 180}, at=T + 30 * M)
    svc.clock.t = T + 31 * M
    p3 = svc.register(session("sistem"), PH.KIND_A, inp3, res3, "k3")
    alt = svc.approve(session("yonetici"), p3.proposal_id, p3.event["plan_sha256"], CUR(inp3), "o3")
    assert not alt.ok and f"cakisan_onayli_plan:{p2.proposal_id}" in alt.codes  # önce mevcut onay geri alınmalı
    assert svc.revoke(session("yonetici"), p2.proposal_id, p2.event["plan_sha256"], "g2", "yeni plan incelenecek").ok
    assert svc.approve(session("yonetici"), p3.proposal_id, p3.event["plan_sha256"], CUR(inp3), "o3b").ok


# ---- Yetkisiz işlemler ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("sess,code", [
    (session("gozlemci"), "yetkisiz:onayla:1"),
    (session("yabanci"), "yetkisiz:onayla:1"),
    (session("yalniz_A"), "yetkisiz:onayla:2"),  # kapsamdaki BÜTÜN servisler için yetki gerekir
    ("yonetici", "oturum_yok_kimlik_metinden_alinamaz"),  # serbest metin / RAG çıktısı kimlik değildir
    (session("yonetici", origin="rag_metni"), "oturum_kokeni_gecersiz:rag_metni"),
    (ActorSession("yonetici", "s", UI_SESSION, T + H), "oturum_gelecekte_basliyor"),
    (ActorSession(" ", "s", UI_SESSION, T - H), "oturum_kimligi_bos"),
])  # fmt: skip
def test_unauthorized_approval_refused_and_logged(registered, sess, code):
    svc, inp, _, pid, sha = registered
    d = svc.approve(sess, pid, sha, CUR(inp), "onay-x")
    assert not d.ok and code in d.codes and svc.state()[1][pid]["durum"] == W.PENDING
    assert code in refused_events(svc)[-1]["nedenler"]


@pytest.mark.parametrize("authz,policy,code", [
    (None, APPROVAL, "yetki_politikasi_yok"),
    (replace(POLICY, known_at=T + H), APPROVAL, "yetki_politikasi_islem_aninda_bilinmiyor"),
    (replace(POLICY, source=" "), APPROVAL, "yetki_politikasi_kaynaksiz"),
    (POLICY, None, "onay_politikasi_yok"),
    (POLICY, replace(APPROVAL, known_at=T + H), "onay_politikasi_islem_aninda_bilinmiyor"),
    (POLICY, replace(APPROVAL, max_decision_delay_minutes=0), "onay_politikasi_sure_gecersiz"),
])  # fmt: skip
def test_missing_or_future_policies_fail_closed(registered, authz, policy, code):
    svc, inp, _, pid, sha = registered
    svc.authorization, svc.policy = authz, policy
    d = svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-x")
    assert not d.ok and code in d.codes


def test_presented_hash_must_match(registered):
    svc, inp, _, pid, sha = registered
    d = svc.approve(session("yonetici"), pid, "0" * 64, CUR(inp), "onay-1")
    assert not d.ok and "plan_ozeti_uyusmuyor" in d.codes


def test_register_requires_permission():
    inp, res = proposal_a()
    d = service().register(session("gozlemci"), PH.KIND_A, inp, res, "k")
    assert not d.ok and "yetkisiz:kaydet:1" in d.codes


# ---- Ret, iptal, geri alma ----------------------------------------------------------------------------------


def test_reject_needs_reason_and_is_terminal(registered):
    svc, inp, _, pid, sha = registered
    assert "gerekce_zorunlu" in svc.reject(session("yonetici"), pid, sha, "r0", " ").codes
    d = svc.reject(session("yonetici"), pid, sha, "r1", "kapsam uygun değil")
    assert d.ok and d.event["veritabani_karari"] == "rejected"
    again = svc.approve(session("yonetici"), pid, sha, CUR(inp), "o1")
    assert not again.ok and "gecersiz_durum_gecisi:reddedildi->onaylandi" in again.codes


def test_cancel_only_pending(registered):
    svc, inp, _, pid, sha = registered
    assert svc.cancel(session("yonetici"), pid, sha, "c1", "girdi değişti").ok
    assert not svc.approve(session("yonetici"), pid, sha, CUR(inp), "o1").ok
    assert "gecersiz_durum_gecisi:iptal_edildi->iptal_edildi" in svc.cancel(session("yonetici"), pid, sha, "c2", "x").codes


def test_revoke_only_after_approval_and_blocks_reuse(registered):
    svc, inp, _, pid, sha = registered
    early = svc.revoke(session("yonetici"), pid, sha, "g0", "erken")
    assert not early.ok and "gecersiz_durum_gecisi:beklemede->geri_alindi" in early.codes
    assert svc.approve(session("yonetici"), pid, sha, CUR(inp), "o1").ok
    assert not svc.revoke(session("gozlemci"), pid, sha, "g1", "yetkisiz").ok
    d = svc.revoke(session("yonetici"), pid, sha, "g2", "yeniden inceleme")
    assert d.ok and d.state == W.REVOKED and d.event["veritabani_karari"] == "revoked"
    assert not svc.applicability(pid, CUR(inp))[0]
    assert "gecersiz_durum_gecisi:geri_alindi->onaylandi" in svc.approve(session("yonetici"), pid, sha, CUR(inp), "o2").codes


# ---- Kayıt uygunluğu ------------------------------------------------------------------------------------------


def test_only_publishable_verified_results_registered():
    inp = UA.build({UA.A: 30, UA.B: 120, UA.ED: 400}, review=[(UA.ED, "nurse")])
    svc = service()
    review = L.optimize(inp)
    assert "oneri_yayinlanabilir_degil" in svc.register(session("sistem"), PH.KIND_A, inp, review, "k1").codes
    exp = L.optimize(inp, freeze_policy=L.FREEZE_TWO_WAY, experimental_mode="test")
    assert exp["deneysel_sonuc"] is not None
    assert "oneri_yayinlanabilir_degil" in svc.register(session("sistem"), PH.KIND_A, inp, exp, "k2").codes  # deneysel asla
    ok_inp, ok = proposal_a()
    forged = copy.deepcopy(ok)
    forged["cozum"]["tasimalar"].clear()
    forged["cozum"]["ozet_sha256"] = L.solution_hash(forged["cozum"])
    assert "yeniden_dogrulama_basarisiz" in svc.register(session("sistem"), PH.KIND_A, ok_inp, forged, "k3").codes
    assert "bilinmeyen_oneri_turu:x" in svc.register(session("sistem"), "x", ok_inp, ok, "k4").codes


def test_register_time_bounds_and_duplicate():
    inp, res = proposal_a()
    assert "zaman_karar_anindan_once" in service(clock=Clock(T - M)).register(session("sistem"), PH.KIND_A, inp, res, "k").codes
    assert "oneri_suresi_doldu" in service(clock=Clock(T + 60 * M)).register(session("sistem"), PH.KIND_A, inp, res, "k").codes
    svc = service()
    assert svc.register(session("sistem"), PH.KIND_A, inp, res, "k1").ok
    assert "oneri_zaten_kayitli" in svc.register(session("sistem"), PH.KIND_A, inp, res, "k2").codes


def test_layer_b_check_registered_and_approved():
    inp = UB.build([UB.task(1), UB.task(2, 20.0)])
    res = B.check_tasks(inp)
    svc = W.ApprovalService(AL.MemoryJournal(), AuthorizationPolicy({"yonetici": frozenset((a, u) for a in ACTIONS for u in (UB.A, UB.BB))},
                                                                     SRC, "test", UB.KNOWN), APPROVAL, Clock(UB.T + M))  # fmt: skip
    b_session = ActorSession("yonetici", "oturum-b", UI_SESSION, UB.T - H)
    d = svc.register(b_session, PH.KIND_B, inp, res, "k1", model_versions=[{"model": "rf", "model_surumu": "rf@1", "model_sha256": "h"}])
    assert d.ok, d.codes
    snap = d.event["anlik_goruntu"]
    assert snap["tur"] == PH.KIND_B and {k["gorev"] for k in snap["kalemler"]} == {1, 2} and snap["model_surumleri"][0]["model"] == "rf"
    assert svc.approve(b_session, d.proposal_id, d.event["plan_sha256"], PH.SafetyInputs.from_layer_b(inp), "o1").ok


# ---- Denetim izi bütünlüğü ve dosya deposu --------------------------------------------------------------------


@pytest.mark.parametrize("tamper", [
    lambda ev: ev[1].update(aktor="baskasi"),
    lambda ev: ev.pop(0),
    lambda ev: ev.reverse(),
    lambda ev: ev[0]["anlik_goruntu"]["kalemler"].clear(),
])  # fmt: skip
def test_tampered_trail_blocks_every_operation(registered, tamper):
    svc, inp, _, pid, sha = registered
    svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-1")
    tamper(svc.journal._events)
    assert svc.revoke(session("yonetici"), pid, sha, "g1", "x").codes[0] == "denetim_izi_bozuk"
    assert svc.applicability(pid, CUR(inp))[1][0] == "denetim_izi_bozuk"


def test_clock_going_backwards_refused(registered):
    svc, inp, _, pid, sha = registered
    svc.clock.t = T + M  # kayıt T+2 dk'da yapıldı
    d = svc.approve(session("yonetici"), pid, sha, CUR(inp), "onay-1")
    assert not d.ok and "saat_geriye_gitti" in d.codes


def test_file_journal_persists_and_detects_partial_write(tmp_path):
    path = tmp_path / "onay_izi.jsonl"
    inp, res = proposal_a()
    svc = service(AL.FileJournal(path))
    d = svc.register(session("sistem"), PH.KIND_A, inp, res, "k1")
    assert svc.approve(session("yonetici"), d.proposal_id, d.event["plan_sha256"], CUR(inp), "o1").ok
    reopened = service(AL.FileJournal(path))  # yeni süreç: durum yalnız dosyadan
    assert reopened.state()[1][d.proposal_id]["durum"] == W.APPROVED and reopened.state()[3] == []
    assert reopened.applicability(d.proposal_id, CUR(inp))[0]
    assert not reopened.approve(session("yonetici"), d.proposal_id, d.event["plan_sha256"], CUR(inp), "o2").ok
    with path.open("ab") as fh:
        fh.write(b'{"eylem":"yarim"')  # çökme: yarım satır
    with pytest.raises(ValueError, match="yarim_satir"):
        AL.FileJournal(path).events()
    with pytest.raises(AL.ConcurrencyConflict):
        AL.FileJournal(tmp_path / "x.jsonl").append({"eylem": "x", "zaman": T}, 3)


def test_approvals_package_has_no_db_or_rag_or_solver_writes():
    src = Path(W.__file__).parent
    mods = set()
    for py in src.glob("*.py"):
        for n in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module)
            elif isinstance(n, ast.Import):
                mods |= {a.name for a in n.names}
    forbidden = ("sqlalchemy", "psycopg", "sqlite3", "medakis.repositories", "medakis.rag", "ortools", "subprocess")
    assert not [m for m in mods if m.startswith(forbidden)], mods


# ---- U7 güvenlik düzeltmesi: genişletilmiş girdi parmak izi -------------------------------------------------


def _approve_with(svc, pid, sha, current, rid="onay-g"):
    return svc.approve(session("yonetici"), pid, sha, current, rid)


def test_snapshot_stores_component_fingerprints(registered):
    svc, inp, _, pid, _ = registered
    snap = svc.state()[1][pid]["anlik_goruntu"]
    assert set(snap["girdi_bilesenleri"]) == {"plan", "yetkinlik", "takvim", "kilit"}


def test_skill_revoked_after_decision_makes_proposal_stale(registered):
    svc, inp, res, pid, sha = registered
    mover = res["tasimalar"][0]["personel"]
    cut = tuple(replace(k, valid_until=T + 20 * M) if k.staff_id == mover else k for k in inp.skills)  # sonradan kısaltıldı
    d = _approve_with(svc, pid, sha, replace(CUR(inp), skills=cut))
    assert not d.ok and "oneri_eskimis:girdi_degisti:yetkinlik" in d.codes
    assert _approve_with(svc, pid, sha, CUR(inp), "onay-2").ok
    ok, codes = svc.applicability(pid, replace(CUR(inp), skills=cut))
    assert not ok and "girdi_degisti_yeniden_inceleme_gerekli:yetkinlik" in codes


def test_calendar_change_makes_proposal_stale(registered):
    svc, inp, _, pid, sha = registered
    without_a = tuple(c for c in inp.calendars if c.unit_id != UA.A)  # A'nın takvimi artık doğrulanamıyor
    d = _approve_with(svc, pid, sha, replace(CUR(inp), calendars=without_a))
    assert not d.ok and "oneri_eskimis:girdi_degisti:takvim" in d.codes


def test_new_or_revoked_lock_after_decision_makes_proposal_stale():
    lock = UA.MoveLock(11, UA.B, T - 30 * M, T + 90 * M, "onay-1", T - 30 * M)
    inp = UA.build({UA.A: 10, UA.B: 60}, locks=[lock])
    res = UA.published(L.optimize(inp))
    svc = service()
    d = svc.register(session("sistem"), PH.KIND_A, inp, res, "k1")
    pid, sha = d.proposal_id, d.event["plan_sha256"]
    revoked = replace(CUR(inp), locks=(replace(lock, revoked_at=T + M),))  # kilit t'den sonra geri alındı
    assert "oneri_eskimis:girdi_degisti:kilit" in _approve_with(svc, pid, sha, revoked).codes
    extra = replace(CUR(inp), locks=(lock, UA.MoveLock(12, UA.B, T, T + 90 * M, "onay-yeni", T + M)))  # yeni kilit sonradan onaylandı
    assert "oneri_eskimis:girdi_degisti:kilit" in _approve_with(svc, pid, sha, extra, "onay-3").codes
    assert _approve_with(svc, pid, sha, CUR(inp), "onay-4").ok


@pytest.mark.parametrize("field,code", [
    ("skills", "guncel_guvenlik_girdisi_eksik:yetkinlik"),
    ("calendars", "guncel_guvenlik_girdisi_eksik:takvim"),
    ("locks", "guncel_guvenlik_girdisi_eksik:kilit"),
    ("plan", "guncel_guvenlik_girdisi_eksik:plan"),
])  # fmt: skip
def test_missing_current_source_fails_closed(registered, field, code):
    svc, inp, _, pid, sha = registered
    d = _approve_with(svc, pid, sha, replace(CUR(inp), **{field: None}))
    assert not d.ok and code in d.codes
    assert _approve_with(svc, pid, sha, CUR(inp), "onay-2").ok
    ok, codes = svc.applicability(pid, replace(CUR(inp), **{field: None}))
    assert not ok and code in codes


def test_empty_lock_source_is_not_missing(registered):
    svc, inp, _, pid, sha = registered
    assert inp.locks == () and _approve_with(svc, pid, sha, replace(CUR(inp), locks=())).ok  # "kilit yok" ≠ "kaynak yok"


@pytest.fixture()
def registered_b():
    inp = UB.build([UB.task(1), UB.task(2, 20.0), UB.task(3, due=UB.T + 3 * H)])
    res = B.check_tasks(inp)
    authz = AuthorizationPolicy({"yonetici": frozenset((a, u) for a in ACTIONS for u in (UB.A, UB.BB))}, SRC, "test", UB.KNOWN)
    svc = W.ApprovalService(AL.MemoryJournal(), authz, APPROVAL, Clock(UB.T + M))
    sess = ActorSession("yonetici", "oturum-b", UI_SESSION, UB.T - H)
    d = svc.register(sess, PH.KIND_B, inp, res, "k1")
    assert d.ok, d.codes
    return svc, sess, inp, d.proposal_id, d.event["plan_sha256"]


@pytest.mark.parametrize("change", [
    lambda tasks: tasks[1:],  # görev t'den sonra tamamlandı / iptal edildi (artık bekleyen değil)
    lambda tasks: tasks + (UB.task(9, created=UB.T + M),),  # t'den sonra yeni görev geldi
    lambda tasks: (replace(tasks[0], estimated_minutes=35.0),) + tasks[1:],  # süre tahmini değişti
    lambda tasks: (replace(tasks[0], due_at=UB.T + 15 * M),) + tasks[1:],  # son tarih değişti
])  # fmt: skip
def test_layer_b_task_state_change_makes_check_stale(registered_b, change):
    svc, sess, inp, pid, sha = registered_b
    current = replace(PH.SafetyInputs.from_layer_b(inp), tasks=change(tuple(inp.tasks)))
    d = svc.approve(sess, pid, sha, current, "o1")
    assert not d.ok and "oneri_eskimis:girdi_degisti:gorev" in d.codes


def test_layer_b_later_window_task_and_uncertain_ids(registered_b):
    svc, sess, inp, pid, sha = registered_b
    uncertain = replace(PH.SafetyInputs.from_layer_b(inp), uncertain_task_ids=(2,))  # iptal edildi, zamanı bilinmiyor
    d = svc.approve(sess, pid, sha, uncertain, "o1")
    assert not d.ok and "oneri_eskimis:girdi_degisti:gorev" in d.codes
    later_only = replace(PH.SafetyInputs.from_layer_b(inp), tasks=tuple(inp.tasks) + (UB.task(10, due=UB.T + 4 * H),))
    assert svc.approve(sess, pid, sha, later_only, "o2").ok  # son tarihi pencere dışı: Katman B kapsamını etkilemez


def test_layer_b_missing_tasks_fails_closed(registered_b):
    svc, sess, inp, pid, sha = registered_b
    d = svc.approve(sess, pid, sha, replace(PH.SafetyInputs.from_layer_b(inp), tasks=None), "o1")
    assert not d.ok and "guncel_guvenlik_girdisi_eksik:gorev" in d.codes
