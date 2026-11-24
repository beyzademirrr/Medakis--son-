"""U7: Optimizasyon önerileri için insan onayı, ret, iptal ve geri alma akışı (veritabanısız ilk sürüm).

Durum makinesi: beklemede → onaylandı | reddedildi | iptal edildi; onaylandı → geri alındı.
- Reddedildi, iptal edildi ve geri alındı son durumlardır. Şartname Bölüm 11'e göre plan yeniden incelenir:
  bu, yeni bir çalıştırma ve yeni bir kayıt demektir. Aynı kayıt yeniden onaylanmaz.
- Durum yalnız denetim izinden (event sourcing) yeniden kurulur. Karar ve denetim olayı aynı kayıttır, ayrı yazılmaz.
- Zaman, servise verilen saatten okunur; çağıran taraf zaman beyan edemez. Saat geriye giderse işlem reddedilir.
- Her işlem için: yetki, oturum, özet bütünlüğü, durum geçişi, süre, girdi değişikliği, daha yeni öneri,
  çakışan onaylı plan ve istek kimliği (idempotency) denetlenir. Reddedilen her deneme de denetim izine yazılır.
- Sistem HİÇBİR planı uygulamaz ve gerçek atama yapmaz. `applicability` yalnız insan için bir geçerlilik kapısıdır.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from medakis.approvals.audit_log import ConcurrencyConflict, Journal, verify_chain
from medakis.approvals.authorization import (
    ACT_APPROVE,
    ACT_CANCEL,
    ACT_REGISTER,
    ACT_REJECT,
    ACT_REVOKE,
    IDENTITY_NOTE,
    ActorSession,
    AuthorizationPolicy,
    authorize,
)
from medakis.approvals.plan_hash import (
    KIND_A,
    KIND_B,
    SafetyInputs,
    plan_sha256,
    sha256_of,
    snapshot_layer_a,
    snapshot_layer_b,
    window_fingerprint,
)
from medakis.domain.time import Interval

PENDING, APPROVED, REJECTED, CANCELLED, REVOKED = "beklemede", "onaylandi", "reddedildi", "iptal_edildi", "geri_alindi"
TERMINAL = (REJECTED, CANCELLED, REVOKED)
EV_REGISTER, EV_REFUSED = "oneri_kaydedildi", "islem_reddedildi"
EVENT_OF = {ACT_APPROVE: APPROVED, ACT_REJECT: REJECTED, ACT_CANCEL: CANCELLED, ACT_REVOKE: REVOKED}
# Veritabanı karşılığı (plan_approvals.decision): approved / rejected / revoked; iptal için şemaya 'cancelled' önerilir
DB_DECISION = {APPROVED: "approved", REJECTED: "rejected", REVOKED: "revoked", CANCELLED: "cancelled"}
ENTITY = "optimizasyon_onerisi"


@dataclass(frozen=True)
class ApprovalPolicy:
    """Zaman kuralı: karar anından sonra en geç karar süresi (dk). Pencere bitişini hiçbir zaman aşmaz. Varsayılan YOK."""

    max_decision_delay_minutes: int
    source: str
    approved_by: str
    known_at: datetime

    def problems(self) -> list[str]:
        out = []
        if not self.source.strip() or not self.approved_by.strip():
            out.append("onay_politikasi_kaynaksiz")
        if not isinstance(self.max_decision_delay_minutes, int) or self.max_decision_delay_minutes <= 0:
            out.append("onay_politikasi_sure_gecersiz")
        return out


@dataclass(frozen=True)
class Decision:
    ok: bool
    codes: list[str]
    proposal_id: str | None
    state: str | None
    event: dict[str, Any] | None


def _dt(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


class ApprovalService:
    def __init__(self, journal: Journal, authorization: AuthorizationPolicy | None, policy: ApprovalPolicy | None,
                 clock: Callable[[], datetime]) -> None:  # fmt: skip
        self.journal = journal
        self.authorization = authorization
        self.policy = policy
        self.clock = clock

    # ---- Durum (yalnız denetim izinden) -------------------------------------------------------------

    def state(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
        events = self.journal.events()
        issues = verify_chain(events)
        proposals: dict[str, dict[str, Any]] = {}
        requests: dict[str, dict[str, Any]] = {}
        for e in events:
            rid = e.get("istek_kimligi")
            if rid:
                requests.setdefault(rid, e)
            pid = e.get("varlik_kimligi")
            if e["eylem"] == EV_REGISTER:
                proposals[pid] = {"id": pid, "anlik_goruntu": e["anlik_goruntu"], "plan_sha256": e["plan_sha256"], "sira": e["sira"],
                                  "kaydedilme": e["zaman"], "durum": PENDING, "kararlar": []}  # fmt: skip
            elif e["eylem"] in EVENT_OF.values() and pid in proposals:
                proposals[pid]["durum"] = e["eylem"]
                proposals[pid]["kararlar"].append({"karar": e["eylem"], "aktor": e["aktor"], "zaman": e["zaman"], "sira": e["sira"],
                                                   "plan_sha256": e["plan_sha256"], "neden": e.get("neden", "")})  # fmt: skip
        return events, proposals, requests, issues

    def _now(self, events: Sequence[Mapping[str, Any]]) -> tuple[datetime, list[str]]:
        now = self.clock()
        if events and now < _dt(events[-1]["zaman"]):
            return now, ["saat_geriye_gitti"]
        return now, []

    def _append(self, event: dict[str, Any], seq: int) -> tuple[dict[str, Any] | None, list[str]]:
        try:
            return self.journal.append(event, seq), []
        except ConcurrencyConflict:
            return None, ["eszamanli_degisiklik"]

    def _refuse(self, session: object, action: str, pid: str | None, sha: str | None, rid: str | None, codes: list[str],
                now: datetime, seq: int) -> Decision:  # fmt: skip
        actor = session.actor_id if isinstance(session, ActorSession) else None
        event = {"eylem": EV_REFUSED, "denenen_eylem": action, "zaman": now, "aktor": actor, "oturum": getattr(session, "session_id", None),
                 "kimlik_notu": IDENTITY_NOTE, "varlik_turu": ENTITY, "varlik_kimligi": pid, "plan_sha256": sha,
                 "istek_kimligi": None, "reddedilen_istek_kimligi": rid, "nedenler": sorted(set(codes))}  # fmt: skip
        sealed, conflict = self._append(event, seq)
        return Decision(False, sorted(set(codes + conflict)), pid, None, sealed)

    # ---- Kayıt -------------------------------------------------------------------------------------

    def register(self, session: object, kind: str, inp: Any, result: Mapping[str, Any], request_id: str,
                 model_versions: Sequence[Mapping[str, Any]] = ()) -> Decision:  # fmt: skip
        """Yalnız yayımlanabilir ve bağımsız doğrulayıcıdan YENİDEN geçen sonuç kayda alınır."""
        events, proposals, requests, issues = self.state()
        seq = len(events)
        if issues:
            return Decision(False, ["denetim_izi_bozuk", *issues], None, None, None)
        now, clock_codes = self._now(events)
        codes = list(clock_codes)
        snapshot, eligibility = self._eligible_snapshot(kind, inp, result, model_versions)
        codes += eligibility
        units = list(getattr(inp, "units", ()))
        codes += authorize(self.authorization, session, ACT_REGISTER, units, now)
        if not eligibility:
            window = Interval(result["pencere_baslangic"], result["pencere_bitis"])
            if now < result["karar_ani"]:
                codes.append("zaman_karar_anindan_once")
            if now >= window.end:
                codes.append("oneri_suresi_doldu")
        sha = plan_sha256(snapshot) if snapshot else None
        pid = sha256_of({"tur": kind, "karar_ani": result.get("karar_ani"), "plan_sha256": sha})[:20] if sha else None
        replay = self._replay(requests, request_id, ACT_REGISTER, pid, sha)
        if replay is not None:
            return replay
        if codes:
            return self._refuse(session, ACT_REGISTER, pid, sha, request_id, codes, now, seq)
        if pid in proposals:  # aynı plan aynı karar anında ikinci kez kaydedilmez
            return self._refuse(session, ACT_REGISTER, pid, sha, request_id, ["oneri_zaten_kayitli"], now, seq)
        event = {"eylem": EV_REGISTER, "zaman": now, "aktor": session.actor_id, "oturum": session.session_id,  # type: ignore[union-attr]
                 "kimlik_notu": IDENTITY_NOTE, "varlik_turu": ENTITY, "varlik_kimligi": pid, "plan_sha256": sha,
                 "istek_kimligi": request_id, "anlik_goruntu": snapshot, "sistem_uygulamaz": True}  # fmt: skip
        sealed, conflict = self._append(event, seq)
        return Decision(not conflict, conflict, pid, PENDING if not conflict else None, sealed)

    @staticmethod
    def _eligible_snapshot(kind: str, inp: Any, result: Mapping[str, Any], model_versions: Sequence[Mapping[str, Any]]):
        from medakis.optimization import layer_b_verifier as BV
        from medakis.optimization import verifier as VA

        if kind == KIND_A:
            ok = (result.get("oneri_yayinlanabilir") is True and result.get("deneysel_mod") is None and result.get("cozum") is not None
                  and result.get("dogrulama", {}).get("gecti") is True and result.get("insan_onayi_gerekli") is True)  # fmt: skip
            if not ok:
                return None, ["oneri_yayinlanabilir_degil"]
            check = VA.verify(inp, result["cozum"])
            if not check.passed:
                return None, ["yeniden_dogrulama_basarisiz", *check.violations[:5]]
            return snapshot_layer_a(inp, result), []
        if kind == KIND_B:
            ok = (result.get("denetim_gosterilebilir") is True and result.get("cozum") is not None
                  and result.get("dogrulama", {}).get("gecti") is True and result.get("gercek_atama_yok") is True)  # fmt: skip
            if not ok:
                return None, ["denetim_gosterilebilir_degil"]
            check = BV.verify_tasks(inp, result["cozum"])
            if not check.passed:
                return None, ["yeniden_dogrulama_basarisiz", *check.violations[:5]]
            return snapshot_layer_b(inp, result, model_versions), []
        return None, [f"bilinmeyen_oneri_turu:{kind}"]

    # ---- Kararlar ----------------------------------------------------------------------------------

    def approve(self, session: object, proposal_id: str, presented_sha256: str, current: SafetyInputs | None, request_id: str,
                note: str = "") -> Decision:  # fmt: skip
        """`current`: güvenlik girdileri GÜNCEL kaynaklardan (plan, yetkinlik, takvim, kilit / görev). Eksikse onay yok."""
        return self._decide(ACT_APPROVE, session, proposal_id, presented_sha256, request_id, note, current)

    def reject(self, session: object, proposal_id: str, presented_sha256: str, request_id: str, reason: str) -> Decision:
        return self._decide(ACT_REJECT, session, proposal_id, presented_sha256, request_id, reason, None)

    def cancel(self, session: object, proposal_id: str, presented_sha256: str, request_id: str, reason: str) -> Decision:
        return self._decide(ACT_CANCEL, session, proposal_id, presented_sha256, request_id, reason, None)

    def revoke(self, session: object, proposal_id: str, presented_sha256: str, request_id: str, reason: str) -> Decision:
        """Geri alma: onaylı planın onayını kaldırır. Plan yeniden incelenir (yeni çalıştırma gerekir)."""
        return self._decide(ACT_REVOKE, session, proposal_id, presented_sha256, request_id, reason, None)

    @staticmethod
    def _replay(requests: Mapping[str, Mapping[str, Any]], rid: str, action: str, pid: str | None, sha: str | None) -> Decision | None:
        """Aynı istek kimliği: aynı işlemse önceki sonuç döner (yeni olay yok); farklı işlemse reddedilir."""
        if not isinstance(rid, str) or not rid.strip():
            return Decision(False, ["istek_kimligi_yok"], pid, None, None)
        prev = requests.get(rid)
        if prev is None:
            return None
        same_action = prev["eylem"] == (EV_REGISTER if action == ACT_REGISTER else EVENT_OF[action])
        if same_action and prev.get("varlik_kimligi") == pid and prev.get("plan_sha256") == sha:
            return Decision(True, ["istek_tekrari_onceki_sonuc"], pid, None, dict(prev))
        return Decision(False, ["istek_kimligi_yeniden_kullanildi"], pid, None, None)

    def _decide(self, action: str, session: object, pid: str, sha: str, rid: str, text: str, current: SafetyInputs | None) -> Decision:
        events, proposals, requests, issues = self.state()
        seq = len(events)
        if issues:
            return Decision(False, ["denetim_izi_bozuk", *issues], pid, None, None)
        replay = self._replay(requests, rid, action, pid, sha)
        if replay is not None:
            return replay
        now, codes = self._now(events)
        prop = proposals.get(pid)
        if prop is None:
            return self._refuse(session, action, pid, sha, rid, codes + ["oneri_bulunamadi"], now, seq)
        snap = prop["anlik_goruntu"]
        codes += authorize(self.authorization, session, action, snap["servisler"], now)
        if plan_sha256(snap) != prop["plan_sha256"]:
            codes.append("kayitli_anlik_goruntu_bozuk")
        if sha != prop["plan_sha256"]:
            codes.append("plan_ozeti_uyusmuyor")  # aktörün gördüğü plan kayıtlı plan değil
        allowed_from = {ACT_APPROVE: (PENDING,), ACT_REJECT: (PENDING,), ACT_CANCEL: (PENDING,), ACT_REVOKE: (APPROVED,)}[action]
        if prop["durum"] not in allowed_from:
            if action == ACT_APPROVE and prop["durum"] == APPROVED:
                codes.append("zaten_onaylandi")  # aynı öneri iki kez onaylanamaz
            else:
                codes.append(f"gecersiz_durum_gecisi:{prop['durum']}->{EVENT_OF[action]}")
        if action != ACT_APPROVE and not (isinstance(text, str) and text.strip()):
            codes.append("gerekce_zorunlu")
        if action == ACT_APPROVE:
            codes += self._approval_window(prop, proposals, now, current)
        if codes:
            return self._refuse(session, action, pid, sha, rid, codes, now, seq)
        event = {"eylem": EVENT_OF[action], "zaman": now, "aktor": session.actor_id, "oturum": session.session_id,  # type: ignore[union-attr]
                 "kimlik_notu": IDENTITY_NOTE, "varlik_turu": ENTITY, "varlik_kimligi": pid, "plan_sha256": sha,
                 "istek_kimligi": rid, "veritabani_karari": DB_DECISION[EVENT_OF[action]], "neden": text or "",
                 "sistem_uygulamaz": True}  # fmt: skip
        sealed, conflict = self._append(event, seq)
        return Decision(not conflict, conflict, pid, EVENT_OF[action] if not conflict else None, sealed)

    def _approval_window(self, prop: Mapping[str, Any], proposals: Mapping[str, Mapping[str, Any]], now: datetime,
                         current: SafetyInputs | None) -> list[str]:  # fmt: skip
        """Eski önerinin onaylanmasını engeller: süre, girdi değişikliği, daha yeni öneri ve çakışan onaylı plan."""
        out = self._freshness(prop, now, current)
        snap = prop["anlik_goruntu"]
        start, end = _dt(snap["pencere"][0]), _dt(snap["pencere"][1])
        for other in proposals.values():
            if other["id"] == prop["id"] or other["anlik_goruntu"]["tur"] != snap["tur"]:
                continue
            o_start, o_end = _dt(other["anlik_goruntu"]["pencere"][0]), _dt(other["anlik_goruntu"]["pencere"][1])
            overlap = o_start < end and start < o_end and set(other["anlik_goruntu"]["servisler"]) & set(snap["servisler"])
            if not overlap:
                continue
            if other["sira"] > prop["sira"]:
                out.append(f"daha_yeni_oneri_var:{other['id']}")
            if other["durum"] == APPROVED:
                out.append(f"cakisan_onayli_plan:{other['id']}")  # alternatif çalıştırmaların planı birleştirilmez
        return out

    def _freshness(self, prop: Mapping[str, Any], now: datetime, current: SafetyInputs | None) -> list[str]:
        out = []
        policy = self.policy
        if policy is None:
            return ["onay_politikasi_yok"]
        out += policy.problems()
        if policy.known_at > now:
            out.append("onay_politikasi_islem_aninda_bilinmiyor")
        snap = prop["anlik_goruntu"]
        at, start, end = _dt(snap["karar_ani"]), _dt(snap["pencere"][0]), _dt(snap["pencere"][1])
        delay = policy.max_decision_delay_minutes if isinstance(policy.max_decision_delay_minutes, int) else 0
        deadline = min(end, at + timedelta(minutes=max(delay, 0)))
        if now < at:
            out.append("zaman_karar_anindan_once")
        if now >= deadline:
            out.append("oneri_eskimis:sure_doldu")
        out += input_changes(snap, current, now, "oneri_eskimis:girdi_degisti")
        return out

    # ---- Geçerlilik kapısı (uygulama değil) -----------------------------------------------------------

    def applicability(self, proposal_id: str, current: SafetyInputs | None) -> tuple[bool, list[str]]:
        """Onaylı plan şu an hâlâ geçerli mi? Sistem uygulamaz; bu yalnız insan için eskime/değişiklik kapısıdır."""
        events, proposals, _, issues = self.state()
        if issues:
            return False, ["denetim_izi_bozuk", *issues]
        now, codes = self._now(events)
        prop = proposals.get(proposal_id)
        if prop is None:
            return False, ["oneri_bulunamadi"]
        if prop["durum"] != APPROVED:
            codes.append(f"onayli_degil:{prop['durum']}")
        if plan_sha256(prop["anlik_goruntu"]) != prop["plan_sha256"]:
            codes.append("kayitli_anlik_goruntu_bozuk")
        last = prop["kararlar"][-1] if prop["kararlar"] else None
        if last is not None and last["plan_sha256"] != prop["plan_sha256"]:
            codes.append("karar_ozeti_uyusmuyor")
        snap = prop["anlik_goruntu"]
        if now >= _dt(snap["pencere"][1]):
            codes.append("pencere_doldu")
        codes += input_changes(snap, current, now, "girdi_degisti_yeniden_inceleme_gerekli")
        return not codes, codes


def input_changes(snap: Mapping[str, Any], current: SafetyInputs | None, now: datetime, prefix: str) -> list[str]:
    """Güvenlik girdilerini GÜNCEL kaynaklardan yeniden doğrular. Eksik kaynak ya da değişen bileşen → ret nedeni."""
    window = Interval(_dt(snap["pencere"][0]), _dt(snap["pencere"][1]))
    total, parts, missing = window_fingerprint(snap["tur"], current, now, window, snap["servisler"])
    if missing:
        return [f"guncel_{m}" for m in missing]
    if total == snap["girdi_parmak_izi"]:
        return []
    old = snap.get("girdi_bilesenleri") or {}
    changed = sorted(name for name in parts if old.get(name) != parts[name]) or ["bilinmeyen"]
    return [prefix] + [f"{prefix}:{name}" for name in changed]
