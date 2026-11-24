"""Aşama 6: öneri inceleme ve insan onayı servisi (UI ile U4/U6/U7 arasındaki tek katman; veritabanı yok).

- Katman A YALNIZ varsayılan politikayla (küresel ret) çalıştırılır; deneysel K1 modu bu serviste hiç çağrılmaz.
  Deneysel sonuç öneri ya da uygulanabilir plan olarak gösterilemez ve kayda alınamaz (U7 kaydı da reddeder).
- Onay, ret, iptal ve geri alma yalnız `ApprovalService` üzerinden yapılır. Yetki, güncellik (güvenlik girdileri GÜNCEL
  kaynaklardan) ve SHA-256 kontrolleri orada korunur; bu katman kuralları gevşetmez veya atlamaz.
- Onaylı Katman A taşımaları sonraki çalıştırmalara ONAYLI KİLİT olarak girer (tasarım §5.3). Geri alınan onayın kilidi
  geri alma anından itibaren etkin değildir. Bir önerinin kendi kilidi kendi güncellik denetimine katılmaz.
- Saat: sentetik yeniden oynatma saatidir (yalnız ileri gider). Gerçek kullanımda duvar saati kullanılır.
- Sistem HİÇBİR planı uygulamaz; gerçek atama yapılmaz.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from medakis.approvals.audit_log import Journal, verify_chain
from medakis.approvals.authorization import ACTIONS, ACT_REGISTER, UI_SESSION, ActorSession, AuthorizationPolicy
from medakis.approvals.plan_hash import KIND_A, KIND_B, plan_sha256, snapshot_layer_a, snapshot_layer_b
from medakis.approvals.workflow import APPROVED, EV_REGISTER, ApprovalPolicy, ApprovalService, Decision
from medakis.optimization.layer_a_contracts import MoveLock

DEMO_SOURCE = "test_sentetik: Aşama 6 prototip yetki ve süre politikası; yetkili karar değildir"


def demo_policies(known_at: datetime) -> tuple[AuthorizationPolicy, ApprovalPolicy]:
    """Prototip politikası. Gerçek kullanımda kimin neyi onaylayacağı yetkili bir kararla verilmelidir."""
    units = (1, 2, 3, 4)
    grants = {
        "demo_yonetici": frozenset((a, u) for a in ACTIONS for u in units),
        "demo_planlayici": frozenset((ACT_REGISTER, u) for u in units),  # yalnız kayda alır, onaylayamaz
        "demo_servis_A_sorumlusu": frozenset((a, 1) for a in ACTIONS),  # yalnız Yataklı A kapsamı
        "demo_gozlemci": frozenset(),
    }
    return AuthorizationPolicy(grants, DEMO_SOURCE, "test", known_at), ApprovalPolicy(30, DEMO_SOURCE, "test", known_at)


class SimulationClock:
    """Sentetik yeniden oynatma saati: yalnız ileri gider."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, minutes: int) -> datetime:
        if minutes <= 0:
            raise ValueError("saat_yalniz_ileri_gider")
        self._now += timedelta(minutes=minutes)
        return self._now

    def at_least(self, moment: datetime) -> datetime:
        if moment > self._now:
            self._now = moment
        return self._now


@dataclass
class ReviewRun:
    at: datetime
    layer_a_inputs: Any
    layer_a: dict[str, Any]
    layer_b_inputs: Any
    layer_b: dict[str, Any]
    model_versions: list[dict[str, Any]]
    plan_sha_a: str | None = None
    plan_sha_b: str | None = None
    notes: list[str] = field(default_factory=list)


class ReviewService:
    def __init__(self, scenario: Any, journal: Journal, authorization: AuthorizationPolicy | None, policy: ApprovalPolicy | None,
                 clock: SimulationClock) -> None:  # fmt: skip
        self.scenario = scenario
        self.clock = clock
        self.approvals = ApprovalService(journal, authorization, policy, clock)

    # ---- Oturum --------------------------------------------------------------------------------------------

    def actors(self) -> list[str]:
        policy = self.approvals.authorization
        return sorted(policy.grants) if policy is not None else []

    def open_session(self, actor_id: str) -> ActorSession:
        """UI oturumu: kimlik serbest metinden değil, bu çağrıyla oluşturulan oturum nesnesinden gelir."""
        return ActorSession(actor_id, uuid.uuid4().hex, UI_SESSION, self.clock())

    # ---- Değerlendirme ---------------------------------------------------------------------------------------

    def evaluate(self, at: datetime) -> ReviewRun:
        from medakis.optimization import layer_a as L
        from medakis.optimization import layer_b as B
        from medakis.approvals.plan_hash import model_versions_from_rows

        rows = self.scenario.u3_rows(at)
        locks = self.approved_locks()
        inp_a = self.scenario.layer_a_inputs(at, rows, locks)
        res_a = L.optimize(inp_a)  # varsayılan politika (kapali); deneysel mod YOK
        inp_b = self.scenario.layer_b_inputs(at, rows)
        res_b = B.check_tasks(inp_b)
        run = ReviewRun(at, inp_a, res_a, inp_b, res_b, model_versions_from_rows(rows))
        if res_a.get("deneysel_mod") is not None or res_a.get("deneysel_sonuc") is not None:  # savunma: hiçbir zaman olmamalı
            raise AssertionError("deneysel_sonuc_inceleme_ekranina_ulasamaz")
        if res_a["oneri_yayinlanabilir"]:
            run.plan_sha_a = plan_sha256(snapshot_layer_a(inp_a, res_a))
        if res_b["denetim_gosterilebilir"]:
            run.plan_sha_b = plan_sha256(snapshot_layer_b(inp_b, res_b, run.model_versions))
        return run

    def register(self, session: object, run: ReviewRun, kind: str) -> Decision:
        rid = uuid.uuid4().hex
        if kind == KIND_A:
            return self.approvals.register(session, KIND_A, run.layer_a_inputs, run.layer_a, rid)
        return self.approvals.register(session, KIND_B, run.layer_b_inputs, run.layer_b, rid, run.model_versions)

    # ---- Kararlar --------------------------------------------------------------------------------------------

    def current_sources(self, proposal_id: str) -> Any:
        prop = self.approvals.state()[1][proposal_id]
        kind = prop["anlik_goruntu"]["tur"]
        return self.scenario.current_sources(kind, self.clock(), self.approved_locks(exclude=proposal_id))

    def decide(self, action: str, session: object, proposal_id: str, presented_sha256: str, text: str = "") -> Decision:
        rid = uuid.uuid4().hex
        if action == "onayla":
            return self.approvals.approve(session, proposal_id, presented_sha256, self.current_sources(proposal_id), rid, text)
        fn = {"reddet": self.approvals.reject, "iptal_et": self.approvals.cancel, "geri_al": self.approvals.revoke}[action]
        return fn(session, proposal_id, presented_sha256, rid, text)

    def applicability(self, proposal_id: str) -> tuple[bool, list[str]]:
        return self.approvals.applicability(proposal_id, self.current_sources(proposal_id))

    # ---- Görünümler ------------------------------------------------------------------------------------------

    def proposals(self) -> list[dict[str, Any]]:
        _, props, _, _ = self.approvals.state()
        rows = []
        for p in sorted(props.values(), key=lambda x: x["sira"]):
            snap = p["anlik_goruntu"]
            last = p["kararlar"][-1] if p["kararlar"] else None
            rows.append({"kimlik": p["id"], "tur": snap["tur"], "karar_ani": snap["karar_ani"], "durum": p["durum"],
                         "plan_sha256": p["plan_sha256"], "kalem": len(snap["kalemler"]), "kaydedilme": p["kaydedilme"],
                         "son_karar_aktor": last["aktor"] if last else None, "son_karar_zaman": last["zaman"] if last else None})  # fmt: skip
        return rows

    def proposal(self, proposal_id: str) -> Mapping[str, Any]:
        return self.approvals.state()[1][proposal_id]

    def history(self) -> tuple[list[dict[str, Any]], list[str]]:
        events = self.approvals.journal.events()
        rows = [{"sira": e["sira"], "zaman": e["zaman"], "eylem": e["eylem"], "denenen": e.get("denenen_eylem"), "aktor": e.get("aktor"),
                 "oneri": e.get("varlik_kimligi"), "plan_sha256": (e.get("plan_sha256") or "")[:12],
                 "neden": e.get("neden") or ", ".join(e.get("nedenler") or []), "olay_ozeti": e["olay_ozeti"][:12]} for e in events]  # fmt: skip
        return rows, verify_chain(events)

    def approved_locks(self, exclude: str | None = None) -> tuple[MoveLock, ...]:
        """Onaylı Katman A taşımalarından onaylı kilitler (geri alınanlar geri alma anıyla kapanır)."""
        events, props, _, issues = self.approvals.state()
        if issues:
            return ()
        out = []
        for p in props.values():
            if p["id"] == exclude or p["anlik_goruntu"]["tur"] != KIND_A:
                continue
            approved = next((d for d in p["kararlar"] if d["karar"] == APPROVED), None)
            if approved is None:
                continue
            revoked = next((d for d in p["kararlar"] if d["karar"] == "geri_alindi"), None)
            for item in p["anlik_goruntu"]["kalemler"]:
                if item["tur"] != "tasima":
                    continue
                out.append(MoveLock(item["personel"], item["hedef_servis"], datetime.fromisoformat(item["baslangic"]),
                                    datetime.fromisoformat(item["bitis"]), p["id"], datetime.fromisoformat(approved["zaman"]),
                                    datetime.fromisoformat(revoked["zaman"]) if revoked else None))  # fmt: skip
        return tuple(sorted(out, key=lambda k: (k.staff_id, k.starts_at)))

    def registered_ids(self) -> set[str]:
        return {e["varlik_kimligi"] for e in self.approvals.journal.events() if e["eylem"] == EV_REGISTER}

    # ---- Salt okunur görünümler (Aşama 1–6 tamamlayıcı; yeni hesap yok, yalnız doğrulanmış sonuçların gösterimi) ----

    @staticmethod
    def needs_table(run: ReviewRun) -> list[dict[str, Any]]:
        """U3 ihtiyaç–kapasite satırları (servis × rol): ŷ, tampon, D, etkin kapasite, açık/fazla, durum."""
        rows = []
        for r in run.layer_a_inputs.u3_rows:
            rows.append({"servis": r.get("servis_adi") or r.get("servis_id"), "rol": r.get("rol"), "nokta_tahmin_dk": r.get("nokta_tahmin_dk"),
                         "tampon_dk": r.get("tampon_dk"), "tamponlu_talep_dk": r.get("tamponlu_talep_dk"),
                         "etkin_kapasite_dk": r.get("etkin_kapasite_dk"), "acik_dk": r.get("acik_dk"), "fazla_dk": r.get("fazla_dk"),
                         "durum": r.get("durum"), "nedenler": ", ".join(list(r.get("nedenler") or [])[:3])})  # fmt: skip
        return rows

    @staticmethod
    def layer_b_details(run: ReviewRun) -> dict[str, Any] | None:
        """Katman B doğrulanmış denetim ayrıntıları. Doğrulanmamış sonuçta None (ayrıntı gösterilmez)."""
        res = run.layer_b
        if not res.get("denetim_gosterilebilir") or res.get("cozum") is None:
            return None
        sol = res["cozum"]
        load: dict[tuple[Any, Any, Any], int] = {}
        for a in sol["atamalar"]:
            key = (a["servis"], a["rol"], a["personel"])
            load[key] = load.get(key, 0) + a["sure_dk"]
        return {
            "atamalar": [{k: a[k] for k in ("gorev", "personel", "servis", "rol", "gorev_turu", "baslangic", "bitis", "sure_dk", "son_tarih")}
                         for a in sol["atamalar"]],
            "kisi_yuku": [{"servis": u, "rol": r, "personel": s, "atanan_dk": m} for (u, r, s), m in sorted(load.items(), key=str)],
            "atanmayanlar": [dict(g) for g in sol["atanmayanlar"]],
            "rezerv": [dict(x) for x in sol["rezerv"]],
            "amac_degerleri": dict(sol["amac_degerleri"]), "kapsam": dict(sol["kapsam"]), "durum": res["durum"],
            "sozluk_sirali_tam": res["sozluk_sirali_tam"],
            "cozucu_sure_siniri_sn": {"katman_a": run.layer_a_inputs.config.time_limit_s, "katman_b": run.layer_b_inputs.config.time_limit_s},
        }  # fmt: skip

    def people_and_tasks(self, run: ReviewRun) -> dict[str, Any]:
        """Karar anında BİLİNEN personel yetkinlikleri, uygunsuzluk aralıkları ve bekleyen görevler (salt okunur).

        Kaynak yalnız çalıştırmanın kendi girdileridir (Katman B: plan, personel, yetkinlik, görev). Pencere, çözücülerle
        aynı [t, t+60) aralığıdır. Sonradan bildirilen kayıt görünmez (known_view). Bakım değerlendirmesini sağlamayan
        senaryoda bakım alanları boş kalır ve nedeni yazılır; değer uydurulmaz.
        """
        from medakis.domain.planning import ACTIVE_STATUSES, known_view
        from medakis.domain.time import Interval
        from medakis.optimization.layer_a_contracts import WINDOW_MINUTES

        inp = run.layer_b_inputs
        at = inp.at
        window = Interval(at, at + timedelta(minutes=WINDOW_MINUTES))
        view = known_view(inp.plan, at)
        units_of: dict[int, set[int]] = {}
        for sh in view.shifts:
            if sh.status in ACTIVE_STATUSES and sh.unit_id in set(inp.units) and sh.interval.overlaps(window):
                units_of.setdefault(sh.staff_id, set()).add(sh.unit_id)
        skills_of: dict[int, list[str]] = {}
        for sk in inp.skills:
            if sk.valid_from <= at and (sk.valid_until is None or sk.valid_until > at):
                skills_of.setdefault(sk.staff_id, []).append(sk.skill)
        staff_rows = []
        for sid in sorted(units_of):
            person = inp.staff.get(sid)
            staff_rows.append({"personel": person.synthetic_code if person else str(sid), "rol": person.role if person else None,
                               "servisler": sorted(units_of[sid]), "yetkinlikler": sorted(set(skills_of.get(sid, [])))})  # fmt: skip
        unav_rows = [
            {"personel": inp.staff[u.staff_id].synthetic_code if u.staff_id in inp.staff else str(u.staff_id), "neden": u.reason,
             "tur": u.kind, "baslangic": u.starts_at, "bitis": u.ends_at, "bildirildi": u.recorded_at}
            for u in sorted(view.unavailability, key=lambda u: (u.starts_at, u.staff_id))
            if u.staff_id in units_of and Interval(u.starts_at, u.ends_at).overlaps(window)
        ]  # fmt: skip
        provider = getattr(self.scenario, "acuity_known_at", None)
        acuity = provider([t.task_id for t in inp.tasks], at) if provider is not None else None
        notes = [] if acuity is not None else ["Bu senaryo bakım değerlendirmesi sağlamıyor; bakım düzeyi ve katsayısı boş bırakıldı."]
        task_rows = []
        for t in inp.tasks:
            a = (acuity or {}).get(t.task_id)
            task_rows.append({"gorev": t.task_id, "servis": t.unit_id, "rol": t.role, "gorev_turu": t.task_type,
                              "tahmini_dk": t.estimated_minutes, "son_tarih": t.due_at, "gerekli_yetkinlik": list(t.required_skills),
                              "bakim_duzeyi": a["duzey"] if a else None, "bakim_katsayisi": a["katsayi"] if a else None,
                              "bakim_degerlendirme_ani": a["degerlendirme_ani"] if a else None})  # fmt: skip
        if acuity is not None and any(r["bakim_duzeyi"] is None for r in task_rows):
            notes.append("Bazı görevler için karar anında bilinen bakım değerlendirmesi yok; bu satırlar boş gösterilir.")
        return {"pencere": window, "personel": staff_rows, "uygunsuzluk": unav_rows, "gorevler": task_rows,
                "belirsiz_gorevler": list(inp.uncertain_task_ids), "notlar": notes}  # fmt: skip

    def export_proposal(self, proposal_id: str, fmt: str = "json") -> tuple[bytes, str, dict[str, Any]]:
        """Yalnız KAYITLI öneri dışa aktarılır. İz bozuksa ya da kayıtlı görüntünün özeti tutmuyorsa reddedilir.

        Dosya: anlık görüntü, plan SHA-256 (kayıtlı ve yeniden hesaplanan), durum ve karar geçmişi, dışa aktarım anındaki
        geçerlilik sonucu ve açık etiket. Onaylı ve geçerli olmayan öneri hiçbir zaman "geçerli plan" diye etiketlenmez.
        Dosyanın kendi SHA-256 özeti meta veride döner (dosya kendi özetini içeremez). Sistem planı uygulamaz.
        """
        events, props, _, issues = self.approvals.state()
        if issues:
            raise ValueError("denetim_izi_bozuk: " + ", ".join(issues[:3]))
        prop = props.get(proposal_id)
        if prop is None:
            raise ValueError("oneri_bulunamadi")
        snap = prop["anlik_goruntu"]
        recomputed = plan_sha256(snap)
        if recomputed != prop["plan_sha256"]:
            raise ValueError("kayitli_anlik_goruntu_bozuk")
        if snap["tur"] == KIND_A and snap.get("calisma_yapilandirmasi", {}).get("dondurma_politikasi") != "kapali":
            raise ValueError("deneysel_sonuc_disa_aktarilamaz")  # savunma: deneysel sonuç zaten kayda alınamaz
        valid, codes = self.applicability(proposal_id) if prop["durum"] == APPROVED else (False, [f"onayli_degil:{prop['durum']}"])
        if prop["durum"] == APPROVED and valid:
            label = "GECERLI_ONAYLI_PLAN"
        elif prop["durum"] == APPROVED:
            label = "ONAYLI_AMA_ARTIK_GECERSIZ"
        else:
            label = "GECERLI_PLAN_DEGIL"
        doc = {
            "bicim": "medakis-oneri-disa-aktarim/1", "disa_aktarim_ani": self.clock(), "oneri_kimligi": proposal_id,
            "tur": snap["tur"], "durum": prop["durum"], "etiket": label, "gecerli_plan_mi": label == "GECERLI_ONAYLI_PLAN",
            "gecerlilik": {"gecerli": valid, "nedenler": codes}, "plan_sha256": prop["plan_sha256"],
            "plan_sha256_yeniden_hesaplanan": recomputed, "kararlar": prop["kararlar"],
            "denetim_izi": {"olay_sayisi": len(events), "zincir_sorunu": []},
            "notlar": ["Yalnız sentetik prototip; sistem hiçbir planı uygulamaz ve gerçek atama yapmaz.",
                       "Kimlik ve yetkiler test_sentetik prototip değerleridir; doğrulanmış kurumsal kimlik değildir."],
            "anlik_goruntu": snap,
        }  # fmt: skip
        if fmt == "json":
            data = json.dumps(doc, ensure_ascii=False, indent=1, default=str).encode("utf-8")
        elif fmt == "csv":
            buf = io.StringIO()
            meta = {"oneri_kimligi": proposal_id, "plan_sha256": prop["plan_sha256"], "durum": prop["durum"], "etiket": label}
            items = [{"satir_turu": "kalem", **k} for k in snap["kalemler"]]
            items += [{"satir_turu": "atanmayan", "gorev": g[0], "neden": g[1], "acik_dk": g[2]} for g in snap.get("atanmayanlar", [])]
            fields = list(meta) + ["satir_turu"] + sorted({k for row in items for k in row} - {"satir_turu"})
            writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in items or [{"satir_turu": "kalem_yok"}]:
                writer.writerow({**meta, **row})
            data = buf.getvalue().encode("utf-8")
        else:
            raise ValueError(f"bilinmeyen_bicim:{fmt}")
        name = f"medakis_oneri_{proposal_id}_{label.lower()}.{fmt}"
        return data, name, {"dosya_sha256": hashlib.sha256(data).hexdigest(), "etiket": label, "plan_sha256": prop["plan_sha256"]}


def evaluation_summary(root: Any) -> dict[str, Any]:
    """Aşama 4 değerlendirme özeti (salt okunur; test bölümü, tüm servisler). Dosya yoksa açık 'eksik' bilgisi."""
    base = Path(root) / "docs" / "veri"
    out: dict[str, Any] = {"secim": None, "test": [], "son_dort": [], "eksik": []}
    main_file, ref_file = base / "asama4_sonuclar.json", base / "asama4_son_dort_baseline.json"
    if main_file.exists():
        d = json.loads(main_file.read_text(encoding="utf-8"))
        out["secim"] = d["dogrulama_secimi"]
        rows = {"baseline": d["test"]["baseline"], "random_forest": d["test"]["random_forest"], "baseline_bilgi": d["bilgi_baseline"]["test"]}
        for model, recs in rows.items():
            for r in recs:
                if r["servis"] == "tum":
                    out["test"].append({"hedef": r["hedef"], "model": model, "mae": r["mae"], "rmse": r["rmse"], "wape": r["wape"], "n": r["n"]})
    else:
        out["eksik"].append(main_file.as_posix())
    if ref_file.exists():
        d = json.loads(ref_file.read_text(encoding="utf-8"))
        for target, models in d["test"].items():
            for model, recs in models.items():
                r = next(x for x in recs if x["servis"] == "tum")
                out["son_dort"].append({"hedef": target, "model": model, "mae": r["mae"], "rmse": r["rmse"], "wape": r["wape"], "n": r["n"],
                                        "kapsama": r.get("kapsama")})  # fmt: skip
    else:
        out["eksik"].append(ref_file.as_posix())
    return out
