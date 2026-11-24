"""Aşama 5 / U5: T1–T15 test matrisi, doğrulayıcı saldırıları, O1–O4 sözlük sırası, performans dumanı.

Senaryolar tests/u5_scenarios.py içindedir (test_sentetik fikstürler; veritabanı yok). Her senaryo bağımsız
bir kanıt kaydı üretir; burada kaydın kontrolleri doğrulanır. Bütün alt senaryolar normal başarı ölçütüyle
denetlenir; beklenen-başarısızlık istisnası yoktur (T9-e U5 düzeltmesinden sonra).
"""

import pytest

import u5_scenarios as S


@pytest.fixture(scope="module")
def matrix():
    return {tid: fn() for tid, fn in S.MATRIX.items()}


@pytest.mark.parametrize("tid", list(S.MATRIX))
def test_matrix_scenario(matrix, tid):
    records = matrix[tid]
    assert records, tid
    for rec in records:
        assert rec["kimlik"] == tid and rec["kontroller"], rec
        failed = {name for name, ok in rec["kontroller"].items() if not ok}
        assert rec["sonuc"] == "gecti", (tid, rec["alt"], failed, rec["nedenler"])


@pytest.mark.parametrize("tid", list(S.FREEZE_EXPERIMENT))
def test_freeze_experiment_scenario(tid):
    """Deneysel K1 (açık deneysel mod): varsayılan matristen ayrı; hiçbir kayıt yayımlanabilir değil."""
    records = S.FREEZE_EXPERIMENT[tid]()
    assert records
    for rec in records:
        failed = {name for name, ok in rec["kontroller"].items() if not ok}
        assert rec["kimlik"] == tid and rec["sonuc"] == "gecti", (tid, rec["alt"], failed, rec["nedenler"])
        assert not rec["oneri_yayinlanabilir"] and rec["kontroller"]["deneysel_sizinti_yok"]


def test_no_unverified_or_violating_proposal_published(matrix):
    for records in matrix.values():
        for rec in records:
            if rec["oneri_yayinlanabilir"]:
                assert rec["dogrulama"] is True and rec["durum"] in ("OPTIMAL", "FEASIBLE")


def test_t9e_verifier_lists_invalid_interval(matrix):
    rec = next(r for r in matrix["T9"] if r["alt"] == "e")
    assert rec["sonuc"] == "gecti" and rec["kontroller"]["h5_kodu"] and not rec["oneri_yayinlanabilir"]


def test_verifier_attack_suite_all_rejected():
    results = S.attack_suite()
    assert len(results) >= 16
    for r in results:
        assert r["reddedildi"] and r["sonuc"] == "gecti", r
    assert all(r["ozet_yeniden_hesaplandi"] for r in results if r["ozet_yeniden_hesaplandi"] is not None
               and r["beklenen_kod"] != "ozet_uyusmuyor")  # fmt: skip


def test_lexicographic_priority_suite():
    for r in S.lexicographic_suite():
        assert r["sonuc"] == "gecti", r


@pytest.mark.parametrize("name", ["P1_mevcut_yaklasik_40", "P2_x2", "P7_x10_kisa_sure_0_05sn"])
def test_performance_profiles_smoke(name):
    m = S.measure(S.perf_inputs(**S.PERF_PROFILES[name]))
    assert m["tutarli"]
    if name.startswith("P7"):
        assert m["durum"] in ("UNKNOWN", "FEASIBLE", "OPTIMAL") and (m["durum"] == "UNKNOWN") == (m["dogrulama"] is None)
    else:
        assert m["durum"] == "OPTIMAL" and m["dogrulama"] is True
