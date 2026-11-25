"""Dijital Hastane İkizi motoru: ortak simülasyon saati, kamera yoğunluğu, tahmin, optimizasyon ve what-if.

- Saat: veri setindeki değerlendirilmiş karar anları (15 dk çözünürlük) ve her anın 60 dakikalık penceresindeki dakika.
- Kamera yoğunluğu (kişi/alan kapasitesi) ile personel iş yükü oranı (talep/etkin kapasite) AYRI kavramlardır.
- Tahmin: mevcut Random Forest + U1 kalibrasyonu (`SyntheticReviewScenario.u3_rows`). Model dosyası yoksa tahmin ve
  optimizasyon GÖSTERİLMEZ (sahte sonuç üretilmez); durum açıkça bildirilir.
- Optimizasyon: mevcut Katman A CP-SAT ve bağımsız doğrulayıcı. Kamera yoğunluğu modelin girdisi değildir; yalnız
  yoğunluğa duyarlı senaryoda, kapasite üstü kişi başına yapılandırılmış dakika kadar hemşire talebine EK SİNYAL olarak
  eklenir ve sonuçta ayrı gösterilir. Kısıtlar gevşetilmez. Hiçbir sonuç gerçek atama değildir.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from medakis.density import camera as C
from medakis.twin.data import KIND_TR, ROLE_TR, TwinData, nurses_short

LEVELS = ("normal", "sari", "turuncu", "kirmizi")
LEVEL_TR = {"normal": "Normal", "sari": "Yükseliyor", "turuncu": "Yüksek", "kirmizi": "Kritik"}
REQ_SKILLS = {1: ("MON", "MED"), 2: ("MON", "MED"), 3: ("TRIAGE",)}  # senaryodaki birim yetkinlik gereksinimleri (Katman A)
FLOAT_SCOPE = {1: (1, 2), 2: (1, 2), 3: (1, 2, 3)}  # hedef birim → kaynak olabilecek birimler (FloatRule kapsamı)


def level_of(ratio: float, th: dict[str, float]) -> str:
    if ratio >= th["kirmizi"]:
        return "kirmizi"
    if ratio >= th["turuncu"]:
        return "turuncu"
    return "sari" if ratio >= th["sari"] else "normal"


@dataclass
class Twin:
    data: TwinData
    scenario: Any | None = None  # SyntheticReviewScenario; model dosyaları yoksa None
    _rows: dict = field(default_factory=dict)
    _opt: dict = field(default_factory=dict)
    _census: dict = field(default_factory=dict)

    # ---- Saat ve kamera -------------------------------------------------------------------------------------

    @property
    def thresholds(self) -> dict[str, float]:
        return self.data.config["yogunluk_esikleri"]

    def clock(self, at: datetime, minute: int) -> str:
        return (self.data.local(at) + timedelta(minutes=minute)).strftime("%H:%M")

    def people_target(self, unit_id: int, at: datetime, minute: int, surge: float = 0.0) -> int:
        """Simülasyon gerçeği: aktif hasta kaydı × alan katsayısı × (1 + senaryo artışı), pencere boyunca yumuşak geçiş."""
        u = self.data.units[unit_id]
        key0, key1 = (unit_id, at), (unit_id, at + timedelta(minutes=60))
        for k in (key0, key1):
            if k not in self._census:
                self._census[k] = self.data.census(unit_id, k[1])
        base = self._census[key0] + (self._census[key1] - self._census[key0]) * minute / 60
        wobble = C._rng("dalga", unit_id, at.isoformat(), minute // 5).uniform(-0.08, 0.08)
        return max(0, round(base * u.per_patient * (1 + wobble) * (1 + surge)))

    def camera(self, unit_id: int, at: datetime, minute: int, surge: float = 0.0, view: str = "hepsi") -> dict:
        u = self.data.units[unit_id]
        n = self.people_target(unit_id, at, minute, surge)
        img = C.render(f"{u.camera} · {u.area}", str(unit_id), at.isoformat(), minute, n, self.clock(at, minute))
        det = C.detect(img)
        ratio = det.count / u.capacity
        return {"birim": unit_id, "ad": u.name, "alan": u.area, "kamera": u.camera, "gercek": n, "tespit": det.count, "kapasite": u.capacity,
                "doluluk": ratio, "seviye": level_of(ratio, self.thresholds), "goruntu": C.overlay(img, det, view)}  # fmt: skip

    def density(self, at: datetime, minute: int, surges: dict[int, float] | None = None, view: str = "hepsi") -> list[dict]:
        surges = surges or {}
        return [self.camera(u, at, minute, surges.get(u, 0.0), view) for u in self.data.units]

    # ---- Personel ve iş yükü ----------------------------------------------------------------------------------

    def staffing(self, at: datetime, moves: dict[int, int] | None = None, removed: frozenset[int] = frozenset(),
                 extra: dict[int, int] | None = None) -> dict[int, dict]:  # fmt: skip
        """Birim personel durumu. `moves`: onaylı/simüle taşımalar (personel → hedef); `removed`: what-if izinli; `extra`: ek personel."""
        moves = moves or {}
        duty = self.data.staff_on_duty(at)
        out = {u: {"toplam": 0, "nurse": 0, "support": 0, "supervisor": 0, "izinli": 0, "molada": 0, "personel_dk": 0.0} for u in self.data.units}
        for u, n in (extra or {}).items():
            out[u]["toplam"] += n
            out[u]["nurse"] += n
            out[u]["personel_dk"] += 60.0 * n
        for s in duty:
            if s["id"] in removed:
                out[s["birim"]]["izinli"] += 1
                continue
            o = out[moves.get(s["id"], s["birim"])]
            if s["izinli"]:
                o["izinli"] += 1
                continue
            o["toplam"] += 1
            o[s["rol"]] += 1
            o["molada"] += int(s["molada"])
            o["personel_dk"] += s["kullanilabilir_dk"]
        return out

    def forecast(self, at: datetime, horizon: int = 60) -> list[dict] | None:
        """Mevcut RF + kalibrasyonlu tampon satırları. Optimizasyon yalnız 60 dk ufku kullanır; 30 dk gösterim içindir."""
        if self.scenario is None:
            return None
        key = at if horizon == 60 else (at, horizon)
        if key not in self._rows:
            rows = self.scenario.u3_rows(at) if horizon == 60 else self.scenario.u3_rows(at, horizon)
            # Açık personel-dakikadır; yöneticiye eksik hemşire sayısı olarak gösterilir (pencere = tahmin ufku)
            self._rows[key] = [{**r, "eksik_hemsire": nurses_short(r.get("acik_personel_dk"), int(r.get("ufuk_dk") or horizon))} for r in rows]
        return self._rows[key]

    def hourly(self, day: Any, role: str | None) -> list[dict]:
        key = ("saatlik", day, role)
        if key not in self._rows:
            self._rows[key] = self.data.hourly_matrix(day, role)
        return self._rows[key]

    # ---- Yoğunluğa duyarlı optimizasyon -------------------------------------------------------------------------

    def density_signal(self, dens: list[dict]) -> dict[int, float]:
        per = float(self.data.config["ek_talep_sinyali"]["kisi_basi_dk"])
        return {d["birim"]: round((d["tespit"] - d["kapasite"]) * per, 1) for d in dens if d["tespit"] > d["kapasite"]}

    def optimize(self, at: datetime, signal: dict[int, float] | None = None) -> dict | None:
        """Mevcut Katman A (varsayılan politika) + bağımsız doğrulayıcı. `signal`: birim → hemşire talebine ek dakika."""
        rows = self.forecast(at)
        if rows is None:
            return None
        signal = {u: v for u, v in (signal or {}).items() if v > 0}
        key = (at, tuple(sorted(signal.items())))
        if key not in self._opt:
            from medakis.optimization import layer_a as L

            mod = copy.deepcopy(rows)
            applied = {}
            for r in mod:
                extra = signal.get(r["servis_id"], 0.0)
                if extra and r["rol"] == "nurse" and r.get("takvim_durumu") == "acik" and r.get("tamponlu_talep_dk") is not None:
                    r["tamponlu_talep_dk"] = float(r["tamponlu_talep_dk"]) + extra
                    r["ek_talep_sinyali_dk"] = extra
                    applied[r["servis_id"]] = extra
            inp = self.scenario.layer_a_inputs(at, mod)
            self._opt[key] = {"girdi": inp, "sonuc": L.optimize(inp), "sinyal": applied}
        return self._opt[key]

    @staticmethod
    def gaps(result: dict) -> dict[int, dict]:
        out = {}
        for r in result["sonuc"].get("servis_rol") or []:
            if r["rol"] == "nurse":
                out[r["servis_id"]] = {"ihtiyac_dk": r.get("ihtiyac_personel_dk_tam"), "kapasite_dk": r.get("kapasite_personel_dk_tam"),
                                       "acik_dk": r.get("acik_personel_dk_tam"), "fazla_dk": r.get("fazla_personel_dk_tam"),
                                       "eksik_hemsire": nurses_short(r.get("acik_personel_dk_tam"))}  # fmt: skip
        return out

    def candidates(self, at: datetime, target: int, chosen: set[int] = frozenset()) -> list[dict]:
        """Hedef birim için diğer birimlerdeki hemşireler ve uygunluk/elenme gerekçeleri (veri gerçekleri; karar CP-SAT'indir)."""
        req = REQ_SKILLS.get(target, ())
        rows = []
        for s in self.data.staff_on_duty(at):
            if s["birim"] == target:
                continue
            reasons = []
            if s["rol"] != "nurse":
                reasons.append(f"rol uyumsuz ({ROLE_TR[s['rol']]})")
            if s["izinli"]:
                reasons.append("izinli")
            if s["birim"] not in FLOAT_SCOPE.get(target, ()):
                reasons.append("birimler arası destek kuralı kapsamı dışında")
            missing = [k for k in req if k not in s["yetkinlikler"]]
            if missing:
                reasons.append("geçerli yetkinlik yok: " + ", ".join(missing))
            if s["kullanilabilir_dk"] < 60:
                reasons.append(f"pencerede {int(s['kullanilabilir_dk'])} dk kullanılabilir (mola/vardiya)")
            rows.append({"id": s["id"], "kod": s["kod"], "rol": ROLE_TR[s["rol"]], "birim": self.data.units[s["birim"]].name,
                         "yetkinlikler": ", ".join(s["yetkinlikler"]), "uygun_veri": not reasons, "secildi": s["id"] in chosen,
                         "gerekce": "; ".join(reasons) or ("CP-SAT tarafından seçildi" if s["id"] in chosen else "uygun aday; çözücü seçmedi")})  # fmt: skip
        return sorted(rows, key=lambda r: (not r["secildi"], not r["uygun_veri"], r["kod"]))

    # ---- Olaylar ------------------------------------------------------------------------------------------------

    def events(self, at: datetime, dens: list[dict], rows: list[dict] | None) -> list[dict]:
        ev = []
        for d in dens:
            if d["seviye"] in ("turuncu", "kirmizi"):
                ev.append({"tur": "Yoğunluk olayı" if d["seviye"] == "kirmizi" else "Yoğunluk uyarısı", "seviye": d["seviye"], "birim": d["birim"],
                           "metin": f"{d['alan']}: {d['tespit']}/{d['kapasite']} kişi (%{round(100 * d['doluluk'])})"})  # fmt: skip
        for r in rows or []:
            if r["rol"] == "nurse" and r.get("eksik_hemsire", 0) > 0:
                ev.append({"tur": "Erken uyarı (60 dk)", "seviye": "turuncu", "birim": r["servis_id"],
                           "metin": f"{r['servis_adi']}: 60 dk içinde {r['eksik_hemsire']} hemşire eksik (tahmin + tampon)"})  # fmt: skip
        return ev

    @staticmethod
    def unit_label(kind: str) -> str:
        return KIND_TR.get(kind, kind)
