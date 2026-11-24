"""Ortak operasyon merkezi: bütün ekranların paylaştığı veri, motor ve kayıt servisleri.

- `Engine` (süreç başına bir kez): sentetik veri, mevcut tahmin/optimizasyon senaryosu ve dijital ikiz motoru.
- `Session` (kullanıcı oturumu başına): insan onayı servisi (plan SHA-256, güncellik), Telegram bildirimi (varsayılan
  dry-run), talepler ve operasyon denetim izi. Bütün izler özet zincirlidir (hash chain).
- Kayıt dizini `MEDAKIS_DATA_DIR` ortam değişkeniyle seçilir; verilmezse depo içindeki `data/` (git dışında).
- Agent, öneriyi yalnız KAYDA ALMA yetkisi olan planlayıcı oturumuyla oluşturur; onay yetkisi yalnız yönetici
  oturumundadır. Onaylanan plan gerçek atama değildir; etkisi yalnız dijital ikizde simüle edilir.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from medakis.approvals.audit_log import FileJournal, Journal, MemoryJournal, verify_chain
from medakis.approvals.plan_hash import KIND_A
from medakis.notifications.telegram import TelegramNotifier
from medakis.twin.data import ROOT, TwinData
from medakis.twin.engine import Twin

AGENT_ACTOR, MANAGER_ACTOR = "demo_planlayici", "demo_yonetici"


def data_dir() -> Path:
    return Path(os.environ.get("MEDAKIS_DATA_DIR") or ROOT / "data")


@dataclass
class Engine:
    data: TwinData
    twin: Twin
    scenario: Any | None
    scenario_error: str | None

    @staticmethod
    def build(load_models: bool = True) -> Engine:
        scenario, err = None, None
        if load_models:
            try:
                from medakis.services.review_scenario import SyntheticReviewScenario

                scenario = SyntheticReviewScenario(ROOT)
                ds = scenario._bundle["ds"]  # aynı üretici çıktısı: ikiz ve tahmin tek veri kümesini paylaşır
            except Exception as exc:  # noqa: BLE001 — model dosyası yoksa tahmin/optimizasyon gösterilmez
                scenario, ds, err = None, None, f"{type(exc).__name__}: {str(exc)[:160]}"
        else:
            ds, err = None, "Model yükleme kapalı (yalnız veri ve kamera)."
        data = TwinData(ds)
        return Engine(data, Twin(data, scenario), scenario, err)


@dataclass
class Session:
    engine: Engine
    ops: Journal
    requests: Journal
    notifier: TelegramNotifier
    review: Any | None
    runs: dict[str, dict] = field(default_factory=dict)  # öneri → kaydedilen optimizasyon çalıştırması (girdi + sonuç + sinyal)
    started: datetime = field(default_factory=lambda: datetime.now(UTC))

    @staticmethod
    def open(engine: Engine, persist: bool = True) -> Session:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = data_dir()

        def journal(name: str) -> Journal:
            return FileJournal(base / "izler" / f"{name}-{stamp}.jsonl") if persist else MemoryJournal()

        review = None
        if engine.scenario is not None:
            from medakis.services.review_service import ReviewService, SimulationClock, demo_policies

            moments = engine.data.moments()
            authz, policy = demo_policies(min(moments) - timedelta(days=30))
            review = ReviewService(engine.scenario, journal("onay"), authz, policy, SimulationClock(min(moments)))
        return Session(engine, journal("operasyon"), journal("talep"), TelegramNotifier(journal("bildirim")), review)

    # ---- Operasyon denetim izi --------------------------------------------------------------------------------

    def log(self, kind: str, **fields: Any) -> None:
        ev = self.ops.events()
        self.ops.append({"olay": kind, "zaman": datetime.now(UTC).isoformat(), **_jsonable(fields)}, len(ev))

    def audit(self) -> tuple[list[dict], list[str]]:
        rows = []
        for name, j in (("operasyon", self.ops), ("talep", self.requests), ("bildirim", self.notifier.journal)):
            for e in j.events():
                rows.append({"iz": name, **e})
        if self.review is not None:
            hist, _ = self.review.history()
            rows += [{"iz": "onay", **h} for h in hist]
        issues = []
        for name, j in (("operasyon", self.ops), ("talep", self.requests), ("bildirim", self.notifier.journal)):
            issues += [f"{name}:{i}" for i in verify_chain(j.events())]
        if self.review is not None:
            issues += [f"onay:{i}" for i in self.review.history()[1]]
        return sorted(rows, key=lambda r: str(r.get("zaman"))), issues

    # ---- Öneri ve onay (mevcut insan onayı servisi) --------------------------------------------------------------

    def sync_clock(self, at: datetime, minute: int) -> None:
        if self.review is not None:
            self.review.clock.at_least(at + timedelta(minutes=minute))

    def create_proposal(self, opt: dict, source: str) -> dict:
        """Doğrulanmış Katman A sonucunu yönetici onayına TASLAK olarak kaydeder (planlayıcı oturumu; onay yetkisi yok)."""
        if self.review is None:
            return {"olusturuldu": False, "neden": "Onay servisi yok (model dosyaları gerekli)."}
        sess = self.review.open_session(AGENT_ACTOR)
        dec = self.review.approvals.register(sess, KIND_A, opt["girdi"], opt["sonuc"], uuid.uuid4().hex)
        self.log("oneri_olusturma", kaynak=source, basarili=dec.ok, oneri=dec.proposal_id, nedenler=dec.codes, sinyal=opt.get("sinyal"))
        if not dec.ok:
            return {"olusturuldu": False, "neden": ", ".join(dec.codes), "oneri": dec.proposal_id}
        self.runs[dec.proposal_id] = opt
        return {"olusturuldu": True, "oneri": dec.proposal_id, "durum": dec.state}

    def decide(self, action: str, pid: str, sha: str, text: str = "") -> Any:
        sess = self.review.open_session(MANAGER_ACTOR)
        dec = self.review.decide(action, sess, pid, sha, text)
        self.log("onay_karari", eylem=action, oneri=pid, basarili=dec.ok, nedenler=dec.codes)
        return dec

    def proposals(self) -> list[dict]:
        return self.review.proposals() if self.review is not None else []

    def approved_moves(self, at: datetime) -> dict[int, int]:
        """Onaylı taşımaların `at` anındaki simülasyon etkisi: personel → hedef birim (gerçek atama değildir)."""
        if self.review is None:
            return {}
        return {k.staff_id: k.target_unit_id for k in self.review.approved_locks()
                if k.starts_at <= at < k.lock_until and (k.revoked_at is None or k.revoked_at > at)}  # fmt: skip

    def proposal_lines(self, pid: str) -> list[str]:
        """Öneri kalemlerinin idari özeti: personel kodu, kaynak → hedef birim, yerel saat aralığı."""
        d = self.engine.data
        names = {u.id: u.name for u in d.units.values()}
        out = []
        for k in self.review.proposal(pid)["anlik_goruntu"]["kalemler"]:
            if k["tur"] != "tasima":
                continue
            s0, s1 = (d.local(datetime.fromisoformat(str(x))).strftime("%H:%M") for x in (k["baslangic"], k["bitis"]))
            out.append(f"Personel: {d.staff[k['personel']]['kod']} | {names.get(k['kaynak_servis'], k['kaynak_servis'])} → "
                       f"{names.get(k['hedef_servis'], k['hedef_servis'])} | {s0}–{s1}")  # fmt: skip
        return out

    def notify(self, pid: str, sha: str) -> Any:
        res = self.notifier.notify_approved({**self.review.proposal(pid), "id": pid}, sha, self.proposal_lines(pid))
        self.log("bildirim_denemesi", oneri=pid, durum=res.status, ayrinti=res.detail)
        return res

    # ---- Talepler ---------------------------------------------------------------------------------------------

    def create_request(self, req: dict) -> dict:
        ev = self.requests.events()
        rid = "T-" + uuid.uuid4().hex[:6].upper()
        self.requests.append({"olay": "talep", "talep": rid, "durum": "beklemede", "zaman": datetime.now(UTC).isoformat(), **_jsonable(req)}, len(ev))
        return {"olusturuldu": True, "talep": rid}

    def decide_request(self, rid: str, state: str, note: str) -> None:
        ev = self.requests.events()
        self.requests.append({"olay": "talep_karar", "talep": rid, "durum": state, "not": note, "aktor": MANAGER_ACTOR,
                              "zaman": datetime.now(UTC).isoformat()}, len(ev))  # fmt: skip

    def request_list(self) -> list[dict]:
        out: dict[str, dict] = {}
        for e in self.requests.events():
            if e["olay"] == "talep":
                out[e["talep"]] = {k: v for k, v in e.items() if k not in ("sira", "onceki_ozet", "olay_ozeti")}
            elif e["olay"] == "talep_karar" and e["talep"] in out:
                out[e["talep"]]["durum"] = e["durum"]
                out[e["talep"]]["karar_notu"] = e.get("not")
        return list(out.values())


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)
