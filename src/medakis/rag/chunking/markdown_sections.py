"""Markdown bölüm ayrıştırma: başlık sınırları korunur, her bölümün belgedeki karakter aralığı tutulur.

- `# ` belge başlığıdır (tek). `## ` bölüm, `### ` alt bölümdür (başlık yolu "Bölüm > Alt bölüm").
- Başlıktan sonra ilk bölüme kadarki metin "Giriş" bölümüdür.
- Bölüm gövdesi baştaki/sondaki boşluklardan arındırılmış aralıktır; `text[start:end]` gövdeyi birebir verir.
- Gövdesi boş bölüm (eksik bölüm) hatadır; sessizce atlanmaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from medakis.rag.errors import ChunkingError

_HEADING = re.compile(r"^(#{1,3}) +(\S.*?)\s*$", re.M)
INTRO = "Giriş"


@dataclass(frozen=True)
class Section:
    heading_path: tuple[str, ...]
    start: int  # gövde başlangıcı (dahil)
    end: int  # gövde bitişi (hariç)

    @property
    def name(self) -> str:
        return " > ".join(self.heading_path)


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def parse_sections(text: str) -> tuple[str, list[Section]]:
    """(belge başlığı, bölümler). Biçim hatasında ChunkingError."""
    heads = list(_HEADING.finditer(text))
    if not heads or len(heads[0].group(1)) != 1:
        raise ChunkingError("belge_basligi_yok")
    if sum(len(h.group(1)) == 1 for h in heads) != 1:
        raise ChunkingError("birden_cok_belge_basligi")
    title = heads[0].group(2)
    sections: list[Section] = []
    parent: str | None = None
    boundaries = [*heads[1:], None]
    s, e = _trim(text, heads[0].end(), heads[1].start() if len(heads) > 1 else len(text))
    if e > s:
        sections.append(Section((INTRO,), s, e))
    for h, nxt in zip(heads[1:], boundaries[1:]):
        level, name = len(h.group(1)), h.group(2)
        if level == 2:
            parent, path = name, (name,)
        else:
            if parent is None:
                raise ChunkingError("alt_bolum_ust_bolumsuz")
            path = (parent, name)
        s, e = _trim(text, h.end(), nxt.start() if nxt is not None else len(text))
        if e <= s:
            if level == 2 and nxt is not None and len(nxt.group(1)) == 3:
                continue  # alt bölümleri olan üst bölümün kendi gövdesi boş olabilir
            raise ChunkingError("bos_bolum", " > ".join(path))
        sections.append(Section(path, s, e))
    if not sections:
        raise ChunkingError("bolum_yok")
    return title, sections
