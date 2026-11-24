"""Değerlendirme seti okuma (yalnız DEV kümesi). Test kümesi Aşama 9'a kadar mühürlüdür (KM29).

- Bu modülde test kümesini açan kod YOKTUR. Dev okuyucusu `test` bölümlü bir vakayla karşılaşırsa içeriğini
  kullanmadan SealedSplitError verir; dosya adında "test" geçen yol da reddedilir.
- Gold, parça kimliğiyle değil belge + karakter aralığıyla etiketlenir (parçalama değişse de eşleme bozulmaz);
  bir indeksin parçalarına aralık kesişimiyle eşlenir.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from medakis.rag.errors import SealedSplitError
from medakis.rag.ingestion.manifest import parse_utc

SPLITS = ("dev", "test")


@dataclass(frozen=True)
class GoldRef:
    doc_uid: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class EvalCase:
    case_key: str
    split: str
    category: str
    question: str
    as_of: datetime
    answerable: bool
    gold: tuple[GoldRef, ...]
    paraphrase_group: str


@dataclass(frozen=True)
class DevSet:
    cases: tuple[EvalCase, ...]
    sha256: str  # dosyanın SHA-256'sı (kalibrasyon kaydına girer)


def load_dev_set(path: Path) -> DevSet:
    path = Path(path)
    if "test" in path.name.lower():
        raise SealedSplitError("test_kumesi_muhurlu", path.name)
    raw = path.read_bytes()
    cases = []
    for n, line in enumerate(raw.decode("utf-8").splitlines()):
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("split") != "dev":
            raise SealedSplitError("test_kumesi_muhurlu", f"satir {n}")  # içerik kullanılmadan durulur
        gold = tuple(GoldRef(g["doc_uid"], int(g["char_start"]), int(g["char_end"])) for g in item.get("gold", []))
        if bool(item["answerable"]) != bool(gold):
            raise ValueError(f"satir {n}: cevaplanabilirlik ile gold tutarsız")
        cases.append(EvalCase(item["case_key"], "dev", item["category"], item["question"], parse_utc(item["as_of"], "as_of"),
                              bool(item["answerable"]), gold, item.get("paraphrase_group", item["case_key"])))  # fmt: skip
    if len({c.case_key for c in cases}) != len(cases):
        raise ValueError("yinelenen vaka anahtarı")
    return DevSet(tuple(cases), hashlib.sha256(raw).hexdigest())


def gold_chunk_ids(gold: Iterable[GoldRef], index_parts: Iterable[dict]) -> tuple[str, ...]:
    """Gold aralıklarıyla kesişen indeks parçaları (indeks manifestindeki `parcalar`)."""
    out = []
    parts = list(index_parts)
    for g in gold:
        for p in parts:
            if p["doc_uid"] == g.doc_uid and p["char_start"] < g.char_end and g.char_start < p["char_end"]:
                out.append(p["chunk_id"])
    return tuple(dict.fromkeys(out))
