"""What-if senaryoları: dijital ikizde gerçek atama yapmadan etki hesabı.

Temel: karar anındaki hemşire ihtiyaç–kapasite satırları (RF + kalibrasyonlu tampon; mevcut kurallar). Değişiklikler:
- yoğunluk artışı/azalışı: kamera kişi sayısı değişir, kapasite üstü kişi için yapılandırılmış ek talep sinyali;
- bir hemşireyi taşıma: kişinin pencerede kullanılabilir dakikası kaynaktan hedefe geçer;
- izin: seçilen personelin kullanılabilir dakikası düşülür;
- ek personel: birime 60 dakikalık kullanılabilir personel eklenir.
Güvenlik kontrolleri veri gerçekleridir (rol, geçerli yetkinlik, destek kuralı kapsamı, kaynak birimin asgari kişi
kuralı ve kaynakta açık oluşması). Onaylanabilir plan yalnız CP-SAT + bağımsız doğrulayıcıdan gelir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from medakis.twin.data import nurses_short
from medakis.twin.engine import Twin

UTIL = 0.85


@dataclass
class Scenario:
    surges: dict[int, float] = field(default_factory=dict)
    move: tuple[str, int] | None = None  # (personel kodu, hedef birim)
    leave: tuple[str, ...] = ()
    extra: dict[int, int] = field(default_factory=dict)

    def describe(self) -> str:
        parts = [f"birim {u} yoğunluk {'+' if v >= 0 else ''}{round(100 * v)}%" for u, v in self.surges.items() if v]
        if self.move:
            parts.append(f"{self.move[0]} → birim {self.move[1]}")
        if self.leave:
            parts.append("izin: " + ", ".join(self.leave))
        parts += [f"birim {u} +{n} ek personel" for u, n in self.extra.items() if n]
        return "; ".join(parts) or "değişiklik yok"


def _state(twin: Twin, at: datetime, minute: int, surges: dict[int, float]) -> dict[int, dict]:
    rows = {r["servis_id"]: r for r in twin.forecast(at) or [] if r["rol"] == "nurse"}
    sig = twin.density_signal([{k: v for k, v in d.items() if k != "goruntu"} for d in twin.density(at, minute, surges)])
    out = {}
    for u, r in rows.items():
        if r.get("tamponlu_talep_dk") is None:
            continue
        rule = min((k["asgari_kisi"] for k in r.get("asgari_kurallar") or []), default=0)
        out[u] = {"birim": u, "ad": r["servis_adi"], "talep_dk": float(r["tamponlu_talep_dk"]) + sig.get(u, 0.0), "sinyal_dk": sig.get(u, 0.0),
                  "kullanilabilir_dk": float(r["kullanilabilir_personel_dk"]), "kisi": int(r.get("kullanilabilir_kisi_en_az_atom") or 0),
                  "asgari_kisi": rule}  # fmt: skip
    return out


def _finish(s: dict) -> dict:
    eff = s["kullanilabilir_dk"] * UTIL
    return {**s, "etkin_kapasite_dk": round(eff, 1), "acik_personel_dk": round(max(0.0, s["talep_dk"] / UTIL - s["kullanilabilir_dk"]), 1),
            "rezerv_personel_dk": round(max(0.0, s["kullanilabilir_dk"] - s["talep_dk"] / UTIL), 1)}  # fmt: skip


def evaluate(twin: Twin, at: datetime, minute: int, base_surges: dict[int, float], sc: Scenario) -> dict | None:
    if twin.forecast(at) is None:
        return None
    before = _state(twin, at, minute, base_surges)
    after = _state(twin, at, minute, {**base_surges, **sc.surges})
    checks: list[tuple[str, bool, str]] = []
    duty = {s["kod"]: s for s in twin.data.staff_on_duty(at)}
    if sc.move:
        code, target = sc.move
        s = duty.get(code)
        if s is None:
            checks.append(("Personel vardiyada", False, f"{code} bu karar anında vardiyada değil"))
        else:
            cand = next((c for c in twin.candidates(at, target) if c["kod"] == code), None)
            ok = cand is not None and cand["uygun_veri"]
            checks.append(("Rol, yetkinlik, kapsam, süre", ok, "uygun" if ok else (cand["gerekce"] if cand else "zaten hedef birimde")))
            src = s["birim"]
            if src in after and target in after:
                after[src]["kullanilabilir_dk"] -= s["kullanilabilir_dk"]
                after[src]["kisi"] -= 1
                after[target]["kullanilabilir_dk"] += s["kullanilabilir_dk"]
                after[target]["kisi"] += 1
                floor_ok = after[src]["kisi"] >= after[src]["asgari_kisi"]
                checks.append(("Kaynak asgari kişi kuralı", floor_ok, f"kaynakta {after[src]['kisi']} kişi kalır (asgari {after[src]['asgari_kisi']})"))
                src_gap = _finish(after[src])["acik_personel_dk"]
                checks.append(("Kaynakta yeni açık oluşmaz", src_gap <= _finish(before[src])["acik_personel_dk"] + 1e-6,
                               f"kaynakta eksik hemşire {nurses_short(_finish(before[src])['acik_personel_dk'])} → {nurses_short(src_gap)}"))  # fmt: skip
    for code in sc.leave:
        s = duty.get(code)
        if s and s["birim"] in after:
            after[s["birim"]]["kullanilabilir_dk"] -= s["kullanilabilir_dk"]
            after[s["birim"]]["kisi"] -= 1
    for u, n in sc.extra.items():
        if u in after and n:
            after[u]["kullanilabilir_dk"] += 60.0 * n
            after[u]["kisi"] += n
    rows = []
    for u in before:
        b, a = _finish(before[u]), _finish(after[u])
        rows.append({"birim": u, "ad": b["ad"], "talep_once": round(b["talep_dk"], 1), "talep_sonra": round(a["talep_dk"], 1),
                     "kapasite_once": b["etkin_kapasite_dk"], "kapasite_sonra": a["etkin_kapasite_dk"],
                     "acik_once": b["acik_personel_dk"], "acik_sonra": a["acik_personel_dk"],
                     "degisim": round(a["acik_personel_dk"] - b["acik_personel_dk"], 1),
                     "eksik_once": nurses_short(b["acik_personel_dk"]), "eksik_sonra": nurses_short(a["acik_personel_dk"])})  # fmt: skip
    for u in after:
        if after[u]["kisi"] < after[u]["asgari_kisi"] and not any(c[0] == "Kaynak asgari kişi kuralı" for c in checks):
            checks.append((f"{after[u]['ad']} asgari kişi kuralı", False, f"{after[u]['kisi']} kişi < asgari {after[u]['asgari_kisi']}"))
    return {"senaryo": sc.describe(), "satirlar": rows, "kontroller": checks, "gercek_atama": False}
