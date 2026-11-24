"""Güvenli parçalama (Aşama 7.2).

- Parça hiçbir zaman bölüm sınırını aşmaz. Hedef ~320 token, örtüşme ≤ 40 token (cümle düzeyinde).
- Parça metni belgenin birebir alt dizgisidir: `belge[char_start:char_end] == parça.text` (alıntı aralığı).
- Embedding'e giden metin = belge başlığı + bölüm yolu + parça metni. Bu metnin tahmini tokenı hedefi, kesin
  (modelden ölçülen) tokenı `max_input_tokens` sınırını aşamaz; aşarsa hata verilir, sessiz kesme yapılmaz.
- Kimlikler deterministiktir: aynı belge ve yapılandırma aynı parça kimliklerini üretir.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from medakis.rag.chunking.markdown_sections import INTRO, parse_sections
from medakis.rag.chunking.token_counter import ApproxTokenCounter, TokenCounter
from medakis.rag.errors import ChunkingError
from medakis.rag.ingestion.corpus_loader import LoadedDocument

_SENTENCE = re.compile(r"[^.!?\n]*[.!?]+|[^.!?\n]+")
_WORDS = re.compile(r"\S+")


@dataclass(frozen=True)
class ChunkConfig:
    target_tokens: int = 320
    overlap_tokens: int = 40
    max_input_tokens: int = 512  # modelden ölçülen kesin sınır (hafif yedek modelin 512 bağlamı)
    counter: str = ApproxTokenCounter.name
    version: str = "parcalama_v1"
    # v2 (S3 deneyi): belge başlığı ile ilk bölüm arasındaki "Giriş" şablon notu indekse girmez. Varsayılan False:
    # mevcut v1 indeks manifestleri (bu alan yokken yazıldı) aynı parçaları ve aynı kimliği üretmeye devam eder.
    exclude_intro: bool = False

    def __post_init__(self) -> None:
        if not (0 < self.target_tokens < self.max_input_tokens) or not (0 <= self.overlap_tokens < self.target_tokens):
            raise ValueError("Parçalama yapılandırması geçersiz.")
        if self.exclude_intro and self.version == "parcalama_v1":
            raise ValueError("Giriş dışlaması ayrı sürüm adı ister (ör. parcalama_v2).")

    def as_dict(self) -> dict:
        data = asdict(self)
        if not self.exclude_intro:
            data.pop("exclude_intro")  # v1 kimliği değişmesin: alan yalnız v2'de kimliğe girer
        return data


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_uid: str
    source_key: str
    version: str
    ordinal: int
    section: str
    text: str
    char_start: int
    char_end: int
    sha256: str
    embed_text: str
    embed_sha256: str
    est_tokens: int

    def map_entry(self) -> dict:
        return {k: getattr(self, k) for k in ("chunk_id", "doc_uid", "source_key", "version", "ordinal", "section",
                                              "char_start", "char_end", "sha256", "embed_sha256")}  # fmt: skip


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    out = []
    for m in _SENTENCE.finditer(text, start, end):
        s, e = m.start(), m.end()
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
    return out


def _embed_text(title: str, section: str, body: str) -> str:
    return f"{title}\n{section}\n{body}"


def chunk_document(doc: LoadedDocument, config: ChunkConfig = ChunkConfig(), counter: TokenCounter | None = None) -> list[Chunk]:
    counter = counter or ApproxTokenCounter()
    text = doc.text
    title, sections = parse_sections(text)
    pieces: list[tuple[str, int, int]] = []
    for sec in sections:
        if config.exclude_intro and sec.heading_path == (INTRO,):
            continue
        header = _embed_text(title, sec.name, "")
        budget = config.target_tokens - counter.count(header)
        if budget <= 0:
            raise ChunkingError("baslik_cok_uzun", sec.name)
        units: list[tuple[int, int]] = []
        for s, e in _spans(text, sec.start, sec.end):
            if counter.count(text[s:e]) <= budget:
                units.append((s, e))
                continue
            current: tuple[int, int] | None = None  # bütçeyi aşan cümle sözcük sınırında bölünür
            for w in _WORDS.finditer(text, s, e):
                cand = (current[0], w.end()) if current else (w.start(), w.end())
                if current and counter.count(text[cand[0]:cand[1]]) > budget:
                    units.append(current)
                    current = (w.start(), w.end())
                else:
                    current = cand
                if counter.count(text[current[0]:current[1]]) > budget:
                    raise ChunkingError("sozcuk_cok_uzun", sec.name)
            if current:
                units.append(current)
        i, last = 0, -1  # last: önceki parçanın son birimi
        while i < len(units):
            j = i
            while j + 1 < len(units) and counter.count(text[units[i][0]:units[j + 1][1]]) <= budget:
                j += 1
            if j <= last:  # örtüşmeyle başlayan parça yeni birim alamadı: örtüşmesiz devam et
                i = last + 1
                continue
            pieces.append((sec.name, units[i][0], units[j][1]))
            last = j
            # Örtüşme: sonraki parça, bu parçanın sonundaki toplamı ≤ overlap_tokens olan cümlelerle başlar.
            i = next((k for k in range(i + 1, j + 1)
                      if counter.count(text[units[k][0]:units[j][1]]) <= config.overlap_tokens), j + 1)  # fmt: skip
    chunks = []
    for ordinal, (section, s, e) in enumerate(pieces):
        body = text[s:e]
        embed = _embed_text(title, section, body)
        est = counter.count(embed)
        if est > config.target_tokens:
            raise ChunkingError("parca_hedefi_asti", section)
        sha = sha256_text(body)
        cid = hashlib.sha256(f"{doc.record.uid}|{ordinal}|{sha}".encode()).hexdigest()[:32]
        chunks.append(Chunk(cid, doc.record.uid, doc.record.source_key, doc.record.version, ordinal, section, body, s, e,
                            sha, embed, sha256_text(embed), est))  # fmt: skip
    return chunks


def chunk_corpus(documents: Iterable[LoadedDocument], config: ChunkConfig = ChunkConfig()) -> list[Chunk]:
    out: list[Chunk] = []
    for doc in sorted(documents, key=lambda d: d.record.uid):
        out.extend(chunk_document(doc, config))
    return out


def corpus_sha256(chunks: Iterable[Chunk]) -> str:
    rows = sorted((c.map_entry() for c in chunks), key=lambda r: (r["doc_uid"], r["ordinal"]))
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
