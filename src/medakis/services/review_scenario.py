"""Aşama 6: sentetik inceleme senaryosu (veritabanı yok, salt okunur).

Kaynak:
- üretici 2.1 planlı kadrosu ve üretici 2.0.0 görevleri;
- Aşama 4 Random Forest modeli ile U1 bilgi baseline'ı ve kalibrasyonu (yeniden eğitim veya ayar YOK);
- U5 değerlendirmesindeki `test_sentetik` fikstürleri: asgari personel, destek kuralları, servis yetkinlik gereksinimi,
  takvim ve optimizasyon yapılandırması.

Bu değerler YETKİLİ KAYNAK DEĞİLDİR; yalnız prototip incelemesi içindir. Yeni klinik kural tanımlanmaz.
Karar anları, Aşama 5 güvenli iyileştirme değerlendirmesinin 104 anıdır. Model dosyaları (artifacts/, Git dışı) yoksa
senaryo kullanılamaz ve nedeni bildirilir.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import datetime, timedelta
from datetime import time as dtime
from functools import cached_property
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from medakis.approvals.plan_hash import KIND_A, KIND_B, SafetyInputs

SRC = "test_sentetik: Aşama 6 prototip senaryosu (U5 fikstürleri); yetkili kaynak değildir"
UNITS = (1, 2, 3, 4)
MOMENTS_FILE = Path("docs/veri/asama5_guvenli_duzeltme2.json")
ARTIFACTS = {
    "random_forest": Path("artifacts/forecasting/asama4_seed42/random_forest"),
    "baseline_bilgi": Path("artifacts/forecasting/asama5_u1/baseline_bilgi"),
    "kalibrasyon": Path("artifacts/forecasting/asama5_u1/kalibrasyon.json"),
}


RESOLUTION = (
    "Model dosyaları Git dışındadır (artifacts/). Proje kökünde sırayla üretin:\n"
    "  .\\.venv\\Scripts\\python.exe scripts\\asama4_egit.py --cikti artifacts\\forecasting\\asama4_seed42\n"
    "  .\\.venv\\Scripts\\python.exe scripts\\asama5_u1_kalibrasyon.py\n"
    "Ya da mevcut artifacts/forecasting klasörünü aynı yollarla kopyalayın. Sahte model çıktısı üretilmez; "
    "dosyalar yoksa veya özetleri uyuşmazsa inceleme ekranı çalışmaz."
)


class ScenarioUnavailable(RuntimeError):
    """Senaryo girdileri (model dosyaları, karar anı listesi) bulunamadı ya da özetleri uyuşmuyor."""

    def __init__(self, codes: list[str] | str) -> None:
        self.codes = [codes] if isinstance(codes, str) else list(codes)
        super().__init__(", ".join(self.codes))


class SyntheticReviewScenario:
    def __init__(self, root: Path, solver_time_limit_s: float | None = None) -> None:
        self.root = Path(root)
        # Çözücü süre sınırı merkezi yapılandırmadan (config.Settings.solver_time_limit_seconds) okunur; ortam
        # değişkeniyle değiştirilemez (env={}). Plan SHA-256 özetine girer: değer 10.0 kaldıkça özetler aynıdır.
        if solver_time_limit_s is None:
            from medakis.config import load_settings

            solver_time_limit_s = load_settings(self.root, env={}).solver_time_limit_seconds
        self.solver_time_limit_s = float(solver_time_limit_s)

    def problems(self) -> list[str]:
        out = [f"dosya_yok:{p.as_posix()}" for p in (*ARTIFACTS.values(), MOMENTS_FILE) if not (self.root / p).exists()]
        return out

    def moments(self) -> list[dict[str, Any]]:
        """Değerlendirilmiş karar anları ve varsayılan politikadaki Katman A durumları (yalnız bilgi)."""
        path = self.root / MOMENTS_FILE
        if not path.exists():
            raise ScenarioUnavailable(f"dosya_yok:{MOMENTS_FILE.as_posix()}")
        runs = json.loads(path.read_text(encoding="utf-8"))["calistirmalar"]
        return [{"an": datetime.fromisoformat(r["an"]), "yerel": r["yerel"], "katman_a_varsayilan": r["kapali"]["durum"],
                 "katman_a_yayin": bool(r["kapali"]["yayin"])} for r in runs]  # fmt: skip

    # ---- Ağır kaynaklar (bir kez yüklenir) ------------------------------------------------------------------

    def preflight(self) -> None:
        """Ağır senaryoyu yükler ve doğrular. Eksik dosya ya da özet uyuşmazlığında ScenarioUnavailable."""
        problems = self.problems()
        if problems:
            raise ScenarioUnavailable(problems)
        self._bundle  # noqa: B018  # yükle ve özetleri denetle

    @cached_property
    def _bundle(self) -> dict[str, Any]:
        problems = self.problems()
        if problems:
            raise ScenarioUnavailable(problems)
        from medakis.domain.planning import FloatRule, PlanningConfig, StaffingRule
        from medakis.domain.service_calendar import always_open, weekly_calendar
        from medakis.domain.time import Interval
        from medakis.forecasting import calibration as C
        from medakis.forecasting.registry import load_model
        from medakis.optimization.layer_a_contracts import OptimizationConfig, UnitSkillRequirement, staff_skills_from_tables
        from medakis.optimization.u3_flow import units_of
        from medakis.synthetic.v2.calendar import HALF_DAY_START, day_infos
        from medakis.synthetic.v21 import generate_v21

        try:  # önce hızlı dosya denetimi: bozuk ya da uyumsuz dosya açık hata verir, sahte çıktı üretilmez
            forest, rf_m = load_model(self.root / ARTIFACTS["random_forest"])
            info, info_m = load_model(self.root / ARTIFACTS["baseline_bilgi"])
            cal = C.load_calibration((self.root / ARTIFACTS["kalibrasyon"]).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ScenarioUnavailable([f"model_yuklenemedi:{type(exc).__name__}:{exc}"]) from exc
        if not cal.sha256().startswith("02fbfbe371f5d64e"):
            raise ScenarioUnavailable(["ozet_uyusmuyor:u1_kalibrasyonu"])
        ds = generate_v21()
        base = ds.base
        mismatch = [code for ok, code in ((ds.base_content_sha256().startswith("2a1fdfb0"), "ozet_uyusmuyor:uretici_2_0_0"),
                                          (ds.plan_sha256().startswith("09c0315d"), "ozet_uyusmuyor:plan_2_1")) if not ok]  # fmt: skip
        if mismatch:
            raise ScenarioUnavailable(mismatch)
        known = base.scenario_start - timedelta(days=30)
        rules = [StaffingRule(u, role, (0, 24), n, SRC, "test", known, None, known) for u in UNITS
                 for role, n in (("nurse", 2 if u < 4 else 1), ("supervisor", 1 if u < 4 else 0), ("support", 0))]  # fmt: skip
        floats = (FloatRule("nurse", None, (1, 2), ("MON", "MED"), 60, known, None, known, SRC + " (A-B)"),
                  FloatRule("nurse", None, (3,), ("TRIAGE",), 60, known, None, known, SRC + " (acil)"))  # fmt: skip
        reqs = tuple(UnitSkillRequirement(u, "nurse", sk, SRC, "test", known) for u, sk in ((1, ("MON", "MED")), (2, ("MON", "MED")), (3, ("TRIAGE",))))
        plan = replace(ds.plan, staffing_rules=tuple(rules), float_rules=floats, config=PlanningConfig(0.85, 0.35, 1.0, self.solver_time_limit_s, SRC))
        p = base.params
        days = [(d.day, d.holiday.kind if d.holiday else "yok") for d in day_infos(p.start_day, p.days + 1)]
        cover = Interval(base.scenario_start, base.scenario_now + timedelta(days=1))
        calendars = [always_open(u, cover, SRC, "test", known) for u in (1, 2, 3)]
        calendars.append(weekly_calendar(4, days, {wd: (dtime(8), dtime(17)) for wd in range(5)}, HALF_DAY_START, p.timezone, SRC, "test", known))
        skills, catalog = staff_skills_from_tables(base.tables)
        return {
            "ds": ds, "base": base, "tz": ZoneInfo(p.timezone), "plan": plan, "calendars": tuple(calendars), "skills": skills,
            "catalog": catalog, "reqs": reqs, "staff": ds.staff(), "units": units_of(base),
            "models": {"random_forest": forest, "baseline_bilgi": info}, "mans": {"random_forest": rf_m, "baseline_bilgi": info_m},
            "cal": cal, "known": known,
            "config": OptimizationConfig({u: 1 for u in UNITS}, {u: 30 for u in UNITS}, self.solver_time_limit_s, SRC, "test", known),
        }  # fmt: skip

    @property
    def timezone(self) -> ZoneInfo:
        return self._bundle["tz"]

    def u3_rows(self, at: datetime, horizon: int = 60) -> list[dict[str, Any]]:
        from medakis.optimization.u3_flow import run_comparison

        b = self._bundle
        versions = {k: f"{k}@{m['model_sha256'][:12]}" for k, m in b["mans"].items()}
        return run_comparison(b["base"], b["plan"], b["staff"], at, b["models"], b["cal"], b["mans"], versions, list(b["calendars"]), b["tz"], horizon)

    def layer_a_inputs(self, at: datetime, rows: list[dict[str, Any]], locks: tuple = ()) -> Any:
        from medakis.optimization.layer_a_contracts import LayerAInputs

        b = self._bundle
        return LayerAInputs(at, b["plan"], b["staff"], b["units"], b["calendars"], rows, b["skills"], b["catalog"], b["reqs"],
                            tuple(locks), b["config"], b["tz"])  # fmt: skip

    def layer_b_inputs(self, at: datetime, rows: list[dict[str, Any]]) -> Any:
        from medakis.optimization.layer_b_contracts import LayerBConfig, LayerBInputs, tasks_known_at

        b = self._bundle
        forecasts = {(r["servis_id"], r["rol"]): float(r["nokta_tahmin_dk"]) for r in rows
                     if r.get("nokta_tahmin_dk") is not None and math.isfinite(float(r["nokta_tahmin_dk"]))}  # fmt: skip
        tasks, uncertain = tasks_known_at(b["base"].tables, at, UNITS)
        return LayerBInputs(at, b["ds"].plan, b["staff"], UNITS, tasks, b["skills"], b["catalog"], LayerBConfig(self.solver_time_limit_s, SRC, "test", b["known"]),
                            forecasts, uncertain)  # fmt: skip

    def acuity_known_at(self, task_ids: list[int], at: datetime) -> dict[int, dict[str, Any]]:
        """Görevin hastasına ait, `at` anında bilinen EN SON bakım değerlendirmesi (salt okunur; üretici 2.0.0 tabloları).

        Değerlendirmesi olmayan görev sonuçta yer almaz (boş gösterilir; değer uydurulmaz).
        """
        tables = self._bundle["base"].tables
        wanted = set(task_ids)
        encounter_of = {r["id"]: r["encounter_id"] for r in tables["care_tasks"] if r["id"] in wanted}
        latest: dict[int, dict[str, Any]] = {}
        needed = set(encounter_of.values())
        for a in tables["acuity_assessments"]:
            if a["encounter_id"] in needed and a["assessed_at"] <= at:
                cur = latest.get(a["encounter_id"])
                if cur is None or a["assessed_at"] > cur["degerlendirme_ani"]:
                    latest[a["encounter_id"]] = {"duzey": a["level"], "katsayi": a["multiplier"], "degerlendirme_ani": a["assessed_at"]}
        return {tid: latest[enc] for tid, enc in encounter_of.items() if enc in latest}

    def current_sources(self, kind: str, now: datetime, locks: tuple = ()) -> SafetyInputs:
        """Güvenlik girdilerinin `now` anındaki GÜNCEL hâli (ham kayıtlar; parmak izi known_view(now) ile süzer)."""
        from medakis.optimization.layer_b_contracts import tasks_known_at

        b = self._bundle
        if kind == KIND_A:
            return SafetyInputs(b["plan"], tuple(b["skills"]), tuple(b["calendars"]), tuple(locks))
        if kind == KIND_B:
            tasks, uncertain = tasks_known_at(b["base"].tables, now, UNITS)
            return SafetyInputs(b["ds"].plan, tuple(b["skills"]), None, None, tasks, uncertain)
        raise ValueError(f"bilinmeyen_tur:{kind}")
