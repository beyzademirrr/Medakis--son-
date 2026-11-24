"""Deneysel kanıt denetimleri (kapsama ve cevap türü uyumu): birim testleri ve akışa bağlı olmadıkları."""

import rag_helpers as H

from medakis.rag.retrieval import support as S


def test_turkish_folding_and_stems():
    assert S.fold("İLAÇ IŞIK") == "ilaç ışık"
    assert S.stems("Yetkinlikler nasıl geçerli olmalıdır?") == ["yetki", "geçer"]  # soru/işlev sözcükleri atılır


def test_coverage_ignores_generic_terms_and_fails_closed_without_terms():
    stats = S.TermStats.from_chunks(["görev sistem plan", "görev sistem rezerv", "görev sistem açık"])
    assert S.content_terms("Sistem görev planı nedir?", stats, 0.5) == ["planı"]  # genel terimler (df 1,0) atılır
    # Kaba kök (ilk 5 karakter) sınırlaması: "planı" ile "plan" eşleşmez; analizde bilinen zayıflık olarak raporlandı.
    assert S.coverage("Rezerv nasıl hesaplanır?", ["rezerv şöyle hesaplanır"], stats, 0.5) == 1.0
    assert S.coverage("Karar ağacı sayısı?", ["rezerv"], stats, 0.5) == 0.0  # corpus'ta olmayan terim: kanıt yok
    assert S.coverage("Nedir?", ["her şey"], stats, 0.5) == 0.0  # ayırt edici terim yok → fail-closed


def test_answer_type_requires_number_only_for_quantity_questions():
    assert S.asks_quantity("Kaç yatak var?") and S.asks_quantity("Ücret ne kadar?") and S.asks_quantity("Doğruluk yüzdesi")
    assert not S.asks_quantity("Rezerv nasıl hesaplanır?")
    assert S.answer_type_ok("Tahmin ufukları kaç dakikadır?", ["Tahmin ufukları otuz ve altmış dakikadır."])
    assert S.answer_type_ok("Pencere 15 dakika mı?", ["metin"])  # sayı sormayan soru kısıtlanmaz
    assert not S.answer_type_ok("Kaç karar ağacı kullanılır?", ["Random Forest modeli karşılaştırılır."])
    assert S.has_number("max(0, x)") and not S.has_number("rezerv hesaplanır")


def test_experimental_checks_not_wired_into_normal_retrieval():
    src = (H.ROOT / "src" / "medakis" / "rag" / "retrieval" / "retriever.py").read_text(encoding="utf-8")
    assert "support" not in src  # onay olmadan normal sorgu akışına bağlanmaz
