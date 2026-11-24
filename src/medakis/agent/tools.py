"""MedAkis asistanının araçları (kontrollü araç sözleşmeleri) ve görevlendirme öneri/onay servisi.

Hiçbir araç personel atamasını uygulamaz. Öneri yalnız `create_transfer_proposal` ile oluşturulur; onay YALNIZ yönetici
arayüzündeki `TransferService.decide` ile verilir (asistanın onay aracı yoktur). Bildirim yalnız onaylı öneri için hazırlanır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from medakis.approvals.audit_log import Journal, verify_chain
from medakis.approvals.plan_hash import sha256_of
from medakis.density import staffing as S
from medakis.density.simulator import AREAS, Simulator
from medakis.notifications.telegram import TelegramNotifier
from medakis.retrieval_demo import search_sources

PENDING, APPROVED, REJECTED = "beklemede", "onaylandi", "reddedildi"


@dataclass
class TransferService:
    journal: Journal
    notifier: TelegramNotifier
    clock: Any = field(default=lambda: datetime.now(UTC))

    def _append(self, event: dict) -> dict:
        events = self.journal.events()
        return self.journal.append({**event, "zaman": self.clock().isoformat()}, len(events))

    def proposals(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for e in self.journal.events():
            if e["olay"] == "oneri_olusturuldu":
                out[e["oneri_id"]] = {"id": e["oneri_id"], "plan": e["plan"], "plan_sha256": e["plan_sha256"], "durum": PENDING,
                                      "kaynak": e["kaynak"], "kararlar": [], "olusturma": e["zaman"]}  # fmt: skip
            elif e["olay"] in (APPROVED, REJECTED) and e["oneri_id"] in out:
                out[e["oneri_id"]]["durum"] = e["olay"]
                out[e["oneri_id"]]["kararlar"].append({"karar": e["olay"], "aktor": e["aktor"], "zaman": e["zaman"]})
        return out

    def create(self, plan: dict, source: str) -> dict:
        content = {"tur": "gecici_gorevlendirme", "plan": plan}
        sha = sha256_of(content)
        pid = "G-" + sha[:8]
        if pid in self.proposals():
            return self.proposals()[pid]
        self._append({"olay": "oneri_olusturuldu", "oneri_id": pid, "plan": plan, "plan_sha256": sha, "kaynak": source})
        return self.proposals()[pid]

    def decide(self, pid: str, presented_sha: str, approve: bool, actor: str, reason: str = "") -> tuple[bool, str]:
        prop = self.proposals().get(pid)
        if prop is None:
            return False, "Öneri bulunamadı."
        if prop["durum"] != PENDING:
            return False, f"Öneri zaten {prop['durum']}."
        if presented_sha != prop["plan_sha256"]:
            return False, "Plan özeti uyuşmuyor; karar kaydedilmedi."
        if not approve and not reason.strip():
            return False, "Ret için gerekçe zorunlu."
        self._append({"olay": APPROVED if approve else REJECTED, "oneri_id": pid, "plan_sha256": presented_sha, "aktor": actor, "neden": reason})
        return True, "Karar kaydedildi."

    def audit(self) -> tuple[list[dict], list[str]]:
        ev = self.journal.events()
        return ev, verify_chain(ev)


# ---- Araç sözleşmeleri (asistan bunları çağırır) ------------------------------------------------------------------


def get_service_density(sim: Simulator) -> dict:
    staff = S.roster()
    return {"zaman_adimi": sim.t, "senaryo": sim.scenario, "alanlar": S.area_status(sim.counts(), staff)}


def get_staff_availability(area: str | None = None) -> list[dict]:
    return [{"id": s.id, "rol": s.role, "alan": S.AREA_NAMES[s.area], "yetkinlikler": sorted(s.skills), "vardiyada": s.on_shift,
             "aktif_gorevde": s.busy} for s in S.roster() if area is None or s.area == area]  # fmt: skip


def get_workload_forecast(sim: Simulator, steps: int = 6) -> dict:
    """Simülasyon ileri oynatılarak kısa vadeli doluluk projeksiyonu (makine öğrenmesi tahmini DEĞİLDİR)."""
    probe = Simulator(sim.scenario, sim.seed)
    for _ in range(sim.t):
        probe.step()
    series = []
    for _ in range(steps):
        series.append(probe.step())
    return {"yontem": "simülasyon projeksiyonu", "adimlar": series}


def optimize_staff_allocation(sim: Simulator, target: str | None = None, pinned: tuple[str, ...] = ()) -> dict:
    staff = S.roster()
    plan = S.optimize(sim.counts(), staff, target=target, pinned=pinned)
    if pinned:
        plan["yonetici_istegi"] = True
    plan["dogrulama"] = S.verify(plan, sim.counts(), staff) if plan.get("aktarimlar") else []
    return plan


def retrieve_policy_evidence(question: str) -> dict:
    return search_sources(question)


def create_transfer_proposal(svc: TransferService, plan: dict, source: str = "asistan") -> dict:
    if plan.get("durum") != "oneri" or plan.get("dogrulama"):
        return {"olusturuldu": False, "neden": "Doğrulanmış geçerli plan yok; öneri oluşturulmadı."}
    return {"olusturuldu": True, "oneri": svc.create({k: plan[k] for k in ("hedef", "aktarimlar", "gerekce", "acik")}, source)}


def get_approval_status(svc: TransferService) -> list[dict]:
    return [{"id": p["id"], "durum": p["durum"], "hedef": S.AREA_NAMES.get(p["plan"]["hedef"], p["plan"]["hedef"])} for p in svc.proposals().values()]


def prepare_approved_notification(svc: TransferService, pid: str) -> dict:
    prop = svc.proposals().get(pid)
    r = svc.notifier.notify_approved(prop, prop["plan_sha256"] if prop else "")
    return {"durum": r.status, "mesaj": r.message, "ayrinti": r.detail}


def area_key(text: str) -> str | None:
    t = text.lower()
    for a in AREAS:
        if a.key in t or a.name.lower().split()[0] in t:
            return a.key
    return None
