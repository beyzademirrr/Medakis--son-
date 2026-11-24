"""U7: Yalnız ekleme yapılan, özet zinciriyle bağlı denetim izi (veritabanısız ilk sürüm).

Her olay bir öncekinin özetini taşır: olay_ozeti = SHA-256(canonical(olay − olay_ozeti)). Silme, araya ekleme,
sıra değiştirme ya da içerik değişikliği `verify_chain` ile yakalanır. Ekleme beklenen sıra numarasıyla yapılır:
durum okunduktan sonra araya bir yazım girdiyse ekleme reddedilir.

TEK YAZICILI PROTOTİP: Okuma ile ekleme arasında kilit yoktur. Çok süreçli ya da çok iş parçacıklı kullanımda
yarış durumu (TOCTOU) mümkündür; bu modül eşzamanlı yazıcılara karşı güvenlik İDDİA ETMEZ. Gerçek eşzamanlılık
güvencesi veritabanı transaction'ı ve kısıtlarıyla sağlanır (U7 raporu §6, ayrı onay).

Şartname Bölüm 7 ve 11: ilk sürümde denetim izi uygulama işlem izidir; değiştirilemez güvenlik kaydı iddiası
yoktur; özet zinciri değişikliği tespit eder, engellemez.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from medakis.approvals.plan_hash import canonical_json, sha256_of

GENESIS = "0" * 64


class ConcurrencyConflict(Exception):
    """Beklenen sıra numarası güncel değil: başka bir işlem araya girdi."""


def seal(event: Mapping[str, Any], seq: int, prev_hash: str) -> dict[str, Any]:
    body = {**{k: v for k, v in event.items() if k not in ("sira", "onceki_ozet", "olay_ozeti")}, "sira": seq, "onceki_ozet": prev_hash}
    body = json.loads(canonical_json(body))  # depolanan biçimle birebir aynı (zamanlar ISO metni)
    return {**body, "olay_ozeti": sha256_of(body)}


def verify_chain(events: list[Mapping[str, Any]]) -> list[str]:
    out = []
    prev = GENESIS
    for i, e in enumerate(events):
        if e.get("sira") != i:
            out.append(f"denetim_sira_bozuk:{i}")
        if e.get("onceki_ozet") != prev:
            out.append(f"denetim_zincir_kopuk:{i}")
        body = {k: v for k, v in e.items() if k != "olay_ozeti"}
        if e.get("olay_ozeti") != sha256_of(body):
            out.append(f"denetim_olay_degistirilmis:{i}")
        prev = e.get("olay_ozeti", "")
    return out


class Journal(Protocol):
    def events(self) -> list[dict[str, Any]]: ...

    def append(self, event: Mapping[str, Any], expected_seq: int) -> dict[str, Any]: ...


class MemoryJournal:
    """Bellek içi denetim izi (testler ve tek süreçli kullanım)."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def events(self) -> list[dict[str, Any]]:
        return [dict(e) for e in self._events]

    def append(self, event: Mapping[str, Any], expected_seq: int) -> dict[str, Any]:
        if expected_seq != len(self._events):
            raise ConcurrencyConflict(f"beklenen={expected_seq} guncel={len(self._events)}")
        prev = self._events[-1]["olay_ozeti"] if self._events else GENESIS
        sealed = seal(event, expected_seq, prev)
        self._events.append(sealed)
        return dict(sealed)


class FileJournal:
    """JSON Lines dosyası: her satır tek olay. Onay kararı ve denetim olayı AYNI satırdır (tek yazım birimi).

    Yalnız tek yazıcılı yerel prototip içindir (dosya kilidi yok; çok süreçli güvenlik iddiası yok).
    Satır sonuna kadar yazılmamış (yarım) son satır bozuk sayılır ve okuma reddedilir; sessizce atlanmaz.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes().decode("utf-8")
        if raw and not raw.endswith("\n"):
            raise ValueError("denetim_dosyasi_yarim_satir")
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    def append(self, event: Mapping[str, Any], expected_seq: int) -> dict[str, Any]:
        current = self.events()
        if expected_seq != len(current):
            raise ConcurrencyConflict(f"beklenen={expected_seq} guncel={len(current)}")
        prev = current[-1]["olay_ozeti"] if current else GENESIS
        sealed = seal(event, expected_seq, prev)
        line = canonical_json(sealed) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as fh:
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        return sealed
