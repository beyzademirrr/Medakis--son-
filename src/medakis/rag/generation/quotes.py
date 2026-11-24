"""Deterministik alıntı doğrulaması (Aşama 8.2). Modele güvenilmez; karar bu denetimle verilir.

Kurallar (hepsi sağlanmazsa karar `supported=False`; tek bir bozuk alıntı bütün kararı düşürür = fail-closed):
1. Parça kimliği BU SORGUDA getirilen (ve retrieval'da doğrulanmış) parçalardan biri olmalı.
2. Alıntı metni o parçanın metninde BİREBİR geçmeli. Yalnız boşluk dizileri tek boşluğa indirgenerek karşılaştırılır;
   büyük/küçük harf, noktalama ve sözcük farkı kabul edilmez.
3. Alıntı en az `min_chars` karakter olmalı (tek sözcüklük "alıntı" kanıt değildir).
4. Belgedeki karakter aralığı hesaplanır: parçanın belge içi başlangıcı + parça içi konum. Döndürülen metin modelin
   yazdığı değil, belgedeki birebir metindir.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from medakis.rag.contracts.generation import EvidenceVerdict, QuoteClaim, VerifiedQuote
from medakis.rag.contracts.retrieval import RetrievedChunk

MIN_CHARS = 20
_WS = re.compile(r"\s+")


def _normalized_with_map(text: str) -> tuple[str, list[int]]:
    """Boşluk dizilerini tek boşluğa indirger; normalleştirilmiş her karakterin özgün konumunu döndürür."""
    out, pos = [], []
    i = 0
    while i < len(text):
        if text[i].isspace():
            j = i
            while j < len(text) and text[j].isspace():
                j += 1
            out.append(" ")
            pos.append(i)
            i = j
        else:
            out.append(text[i])
            pos.append(i)
            i += 1
    return "".join(out), pos


def locate(claim: str, chunk_text: str) -> tuple[int, int] | None:
    """Alıntının parça içindeki [başlangıç, bitiş) aralığı; yoksa ya da birden çok yerde geçiyorsa ilk geçiş."""
    needle = _WS.sub(" ", claim).strip()
    if not needle:
        return None
    hay, pos = _normalized_with_map(chunk_text)
    k = hay.find(needle)
    if k < 0:
        return None
    start = pos[k]
    end = pos[k + len(needle) - 1] + 1
    return start, end


def verify_quotes(claims: Sequence[QuoteClaim], chunks: Sequence[RetrievedChunk], min_chars: int = MIN_CHARS,
                  claimed_supported: bool = True, missing: str = "") -> EvidenceVerdict:  # fmt: skip
    if not claimed_supported:
        return EvidenceVerdict(False, (), (), missing)
    if not claims:
        return EvidenceVerdict(False, (), ("alinti_yok",), missing)
    by_id = {c.chunk_id: c for c in chunks}
    verified: list[VerifiedQuote] = []
    problems: list[str] = []
    for n, cl in enumerate(claims):
        chunk = by_id.get(cl.chunk_id)
        if chunk is None:
            problems.append(f"{n}:parca_getirilmedi")
            continue
        if len(_WS.sub(" ", cl.text or "").strip()) < min_chars:
            problems.append(f"{n}:alinti_cok_kisa")
            continue
        span = locate(cl.text, chunk.text)
        if span is None:
            problems.append(f"{n}:alinti_parcada_yok")
            continue
        s, e = span
        verified.append(VerifiedQuote(chunk.chunk_id, chunk.source_key, chunk.version, chunk.section,
                                      chunk.quote_start + s, chunk.quote_start + e, chunk.text[s:e]))  # fmt: skip
    if problems:
        return EvidenceVerdict(False, (), tuple(problems), missing)
    return EvidenceVerdict(True, tuple(verified), (), missing)
