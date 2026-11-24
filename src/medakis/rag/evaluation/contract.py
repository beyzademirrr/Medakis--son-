"""D0 ölçüm sözleşmesi: her değerlendirme vakası TEK ve AYRIK bir sonuç sınıfına düşer.

- Teknik hata (`unavailable`), güvenlik reddi (`rejected`) ve kaynak çatışması (`conflicting_sources`) kaçınma SAYILMAZ;
  ayrı sınıflardır.
- Cevaplanabilir soruda `answered`, insan onaylı beklenen iddia yoksa `CEVAP_INCELEME_BEKLIYOR` olur: DOĞRU SAYILMAZ.
  `DOGRU_CEVAP` / `YANLIS_KANITLI_CEVAP` yalnız insan etiketiyle verilir; anlamsal doğruluk oranı insan etiketi olmadan
  hesaplanmaz (`None` ve gerekçe).
- Eski ölçüler (doğru kaçınma = `answered` dışındaki her sonuç) geriye dönük karşılaştırma için ayrıca hesaplanır.
Bu modül yalnız değerlendirme içindir; hiçbir karar, eşik ya da kayıt değiştirmez.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum


class OutcomeClass(StrEnum):
    TEKNIK_HATA = "TEKNIK_HATA"
    GUVENLIK_REDDI = "GUVENLIK_REDDI"
    KAYNAK_CATISMASI = "KAYNAK_CATISMASI"
    BILINCLI_KACINMA = "BILINCLI_KACINMA"
    YANLIS_KABUL = "YANLIS_KABUL"
    YANLIS_RET = "YANLIS_RET"
    CEVAP_INCELEME_BEKLIYOR = "CEVAP_INCELEME_BEKLIYOR"
    # Gemma'sız mimari: cevaplanabilir soruda doğrulanmış kaynak parçası sunuldu. CEVAP DEĞİLDİR; doğru sayılmaz.
    KAYNAK_SUNULDU_INCELEME_BEKLIYOR = "KAYNAK_SUNULDU_INCELEME_BEKLIYOR"
    DOGRU_CEVAP = "DOGRU_CEVAP"
    YANLIS_KANITLI_CEVAP = "YANLIS_KANITLI_CEVAP"


# Yanlış ret alt nedenleri (öncelik sırasıyla değerlendirilir)
GOLD_NOT_IN_POOL = "aday_havuzunda_gold_yok"
BELOW_THRESHOLD = "esik_alti"
EMPTY_QUOTE = "bos_alinti"
FAKE_QUOTE = "sahte_alinti"
INVALID_SENTENCE_ID = "gecersiz_cumle_kimligi"  # D3 M3: istemde olmayan cümle kimliği
GATE_REJECTED = "kapi_reddi"  # D5 mimari: reranker kabul/ret kapısı reddetti (Gemma çağrılmadı)
INSUFFICIENT = "kanit_yetersiz"
OTHER = "diger"
NOT_RECORDED = "kayitta_yok"  # eski kayıtta bilgi yok: tahminle doldurulmaz

HUMAN_CORRECT, HUMAN_WRONG = "dogru", "yanlis"
NO_HUMAN_REVIEW = "insan onaylı expected_claims yok; anlamsal doğruluk hesaplanmadı"


@dataclass(frozen=True)
class Classified:
    klass: OutcomeClass
    reason: str | None = None  # yalnız YANLIS_RET için alt neden


def classify(answerable: bool, outcome: str, warnings: Iterable[str] = (), gold_in_candidates: bool | None = None,
             human_label: str | None = None) -> Classified:  # fmt: skip
    """`outcome` RagOutcome değeri ya da Gemma'sız mimaride "kaynak_sunuldu"; `gold_in_candidates` kanıt denetçisine
    verilen adaylarda gold var mı (bilinmiyorsa None). Kaynak sunumu kaynak dışı soruda yanlış kabuldür; cevaplanabilir
    soruda doğru cevap SAYILMAZ (inceleme bekler)."""
    C = OutcomeClass
    if outcome == "kaynak_sunuldu":
        return Classified(C.KAYNAK_SUNULDU_INCELEME_BEKLIYOR if answerable else C.YANLIS_KABUL)
    if outcome == "unavailable":
        return Classified(C.TEKNIK_HATA)
    if outcome == "rejected":
        return Classified(C.GUVENLIK_REDDI)
    if outcome == "conflicting_sources":
        return Classified(C.KAYNAK_CATISMASI)
    if outcome not in ("answered", "no_source"):
        raise ValueError(f"bilinmeyen sonuç: {outcome}")
    if not answerable:
        return Classified(C.YANLIS_KABUL if outcome == "answered" else C.BILINCLI_KACINMA)
    if outcome == "answered":
        if human_label == HUMAN_CORRECT:
            return Classified(C.DOGRU_CEVAP)
        if human_label == HUMAN_WRONG:
            return Classified(C.YANLIS_KANITLI_CEVAP)
        if human_label is not None:
            raise ValueError(f"bilinmeyen insan etiketi: {human_label}")
        return Classified(C.CEVAP_INCELEME_BEKLIYOR)
    return Classified(C.YANLIS_RET, false_rejection_reason(warnings, gold_in_candidates))


def false_rejection_reason(warnings: Iterable[str], gold_in_candidates: bool | None) -> str:
    w = list(warnings)
    if GATE_REJECTED in w:
        return GATE_REJECTED
    if "esik_alti_aday_yok" in w:
        return BELOW_THRESHOLD
    if gold_in_candidates is False:
        return GOLD_NOT_IN_POOL
    if any(x == "sahte_alinti:alinti_yok" for x in w):
        return EMPTY_QUOTE
    if any(x.startswith("sahte_alinti:") and x.endswith(":" + INVALID_SENTENCE_ID) for x in w):
        return INVALID_SENTENCE_ID
    if any(x.startswith("sahte_alinti:") for x in w):
        return FAKE_QUOTE
    if "kanit_yetersiz" in w:
        return INSUFFICIENT
    if gold_in_candidates is None:
        return NOT_RECORDED
    return OTHER


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 3)


_OUT = ("answered", "kaynak_sunuldu")  # kullanıcıya bir şey sunulan (kaçınılmayan) sonuçlar


def summarize_contract(rows: Sequence[dict]) -> dict:
    """`rows`: {"ans": bool, "sinif": OutcomeClass değeri, "alt_neden": str|None, "sonuc": RagOutcome değeri}."""
    C = OutcomeClass
    oos = [r for r in rows if not r["ans"]]
    ans = [r for r in rows if r["ans"]]
    count = Counter(r["sinif"] for r in rows)
    oos_c = Counter(r["sinif"] for r in oos)
    ans_c = Counter(r["sinif"] for r in ans)
    oos_decided = len(oos) - oos_c[C.TEKNIK_HATA] - oos_c[C.GUVENLIK_REDDI] - oos_c[C.KAYNAK_CATISMASI]
    ans_decided = len(ans) - ans_c[C.TEKNIK_HATA] - ans_c[C.GUVENLIK_REDDI] - ans_c[C.KAYNAK_CATISMASI]
    labelled = ans_c[C.DOGRU_CEVAP] + ans_c[C.YANLIS_KANITLI_CEVAP]
    return {
        "vaka": len(rows), "kaynak_disi": len(oos), "cevaplanabilir": len(ans),
        "sinif_sayilari": {k.value: count[k] for k in C},
        "bilincli_kacinma_orani_karar_verilen": _rate(oos_c[C.BILINCLI_KACINMA], oos_decided),
        "bilincli_kacinma_orani_tum_kaynak_disi": _rate(oos_c[C.BILINCLI_KACINMA], len(oos)),
        "yanlis_kabul_orani": _rate(oos_c[C.YANLIS_KABUL], len(oos)),
        "teknik_hata_orani": _rate(count[C.TEKNIK_HATA], len(rows)),
        "yanlis_ret_orani_karar_verilen": _rate(ans_c[C.YANLIS_RET], ans_decided),
        "yanlis_ret_orani_tum_cevaplanabilir": _rate(ans_c[C.YANLIS_RET], len(ans)),
        "yanlis_ret_alt_nedenleri": dict(sorted(Counter(r["alt_neden"] for r in ans if r["sinif"] == C.YANLIS_RET).items())),
        "cevap_inceleme_bekliyor": ans_c[C.CEVAP_INCELEME_BEKLIYOR],
        "kaynak_sunuldu_inceleme_bekliyor": ans_c[C.KAYNAK_SUNULDU_INCELEME_BEKLIYOR],
        "anlamsal_dogruluk": _rate(ans_c[C.DOGRU_CEVAP], labelled) if labelled else None,
        "anlamsal_dogruluk_gerekce": None if labelled else NO_HUMAN_REVIEW,
        # Eski ölçüler (geriye dönük): `answered` dışındaki her sonuç kaçınma sayılır (teknik hata DAHİL).
        "eski_dogru_kacinma": _rate(sum(r["sonuc"] not in _OUT for r in oos), len(oos)),
        "eski_yanlis_kacinma": _rate(sum(r["sonuc"] not in _OUT for r in ans), len(ans)),
    }  # fmt: skip
