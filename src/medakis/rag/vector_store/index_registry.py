"""Dosya tabanlı indeks kaydı (Aşama 7.2; PostgreSQL `rag_indexes`/`rag_index_chunks` karşılığı S17'ye kadar).

- Her indeksin kimlik manifesti (`indeksler/<key>.json`) bir kez yazılır ve DEĞİŞMEZ: spec, parçalama
  yapılandırması, corpus özeti ve parça eşlemesi (parça kimliği = vektör kimliği, hash, karakter aralığı).
- Durum olayları ekleme-yalnız `indeks_olaylari.jsonl` dosyasındadır. Geçişler:
  (yok|failed|building) → building → ready | failed; ready → retired. `retired` son durumdur.
- Yalnız `ready` indeks sorgulanabilir. Eşik (dev kalibrasyonu) yalnız `ready` indekse kaydedilir.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from medakis.rag.contracts.documents import require_utc
from medakis.rag.errors import IndexError_

BUILDING, READY, FAILED, RETIRED, THRESHOLD = "building", "ready", "failed", "retired", "esik"
_ALLOWED = {BUILDING: {None, FAILED, BUILDING}, READY: {BUILDING}, FAILED: {BUILDING}, RETIRED: {READY}}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class IndexRegistry:
    def __init__(self, store_dir: Path) -> None:
        self.root = Path(store_dir)
        self.manifests = self.root / "indeksler"
        self.events_path = self.root / "indeks_olaylari.jsonl"

    # ---- Manifest -------------------------------------------------------------------------------------------

    def write_manifest(self, key: str, payload: dict[str, Any]) -> None:
        path = self.manifests / f"{key}.json"
        text = _dump({"index_key": key, **payload})
        if path.exists():
            if path.read_text(encoding="utf-8") != text:
                raise IndexError_("indeks_manifesti_degismez", key)
            return
        self.manifests.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def manifest(self, key: str) -> dict[str, Any]:
        path = self.manifests / f"{key}.json"
        if not path.exists():
            raise IndexError_("indeks_manifesti_yok", key)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("index_key") != key:
            raise IndexError_("indeks_manifesti_bozuk", key)
        return data

    # ---- Olaylar --------------------------------------------------------------------------------------------

    def events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        out = []
        for n, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines()):
            ev = json.loads(line)
            if ev.get("sira") != n:
                raise IndexError_("indeks_olaylari_bozuk", f"satir {n}")
            out.append(ev)
        return out

    def _append(self, event: dict[str, Any]) -> dict[str, Any]:
        event = {"sira": len(self.events()), **event}
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(_dump(event) + "\n")
        return event

    def state(self, key: str) -> str | None:
        states = [e["durum"] for e in self.events() if e["index_key"] == key and e["durum"] != THRESHOLD]
        return states[-1] if states else None

    def record(self, key: str, status: str, at: datetime, **details: Any) -> dict[str, Any]:
        if status not in _ALLOWED:
            raise IndexError_("durum_gecersiz", status)
        current = self.state(key)
        if current not in _ALLOWED[status]:
            raise IndexError_("gecersiz_indeks_gecisi", f"{current}->{status}")
        return self._append({"index_key": key, "durum": status, "zaman": _iso(at), **details})

    def last(self, key: str, status: str) -> dict[str, Any] | None:
        found = [e for e in self.events() if e["index_key"] == key and e["durum"] == status]
        return found[-1] if found else None

    def ready_keys(self, model: str | None = None) -> list[str]:
        keys = []
        for e in self.events():
            if e["durum"] == READY and e["index_key"] not in keys:
                keys.append(e["index_key"])
        out = [k for k in keys if self.state(k) == READY]
        if model is not None:
            out = [k for k in out if self.manifest(k)["embedding"]["model"] == model]
        return out

    def keys_in(self, status: str) -> list[str]:
        return sorted({e["index_key"] for e in self.events() if self.state(e["index_key"]) == status})

    # ---- Eşik (yalnız dev kalibrasyonu) -----------------------------------------------------------------------

    def set_threshold(self, key: str, max_distance: float, at: datetime, source: dict[str, Any]) -> dict[str, Any]:
        if self.state(key) != READY:
            raise IndexError_("esik_yalniz_hazir_indekse", key)
        if source.get("kume") != "dev":
            raise IndexError_("esik_yalniz_dev_kumesinden")
        if not (0.0 <= max_distance <= 2.0):
            raise IndexError_("esik_gecersiz")
        return self._append({"index_key": key, "durum": THRESHOLD, "zaman": _iso(at), "en_fazla_uzaklik": max_distance,
                             "kaynak": source})  # fmt: skip

    def threshold(self, key: str) -> float | None:
        ev = self.last(key, THRESHOLD)
        return None if ev is None else float(ev["en_fazla_uzaklik"])


def _iso(at: datetime) -> str:
    return require_utc(at, "Zaman").astimezone(UTC).isoformat().replace("+00:00", "Z")
