"""MedAkis agent araç katmanı: dijital ikiz üzerinde kontrollü, sınırlı yetkili araçlar.

Bütün araçlar ortak `Twin` durumunu okur; personel, kapasite ya da sayı uydurmaz. Yazma yetkisi yalnız TASLAK öneri ve
TASLAK personel talebi oluşturmaktır. Onay verme, gerçek atama ya da bildirim gönderme aracı YOKTUR. Her çağrı süresiyle
birlikte `ToolContext.calls` listesine yazılır (gerçek ölçüm).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from medakis.retrieval_demo import search_sources
from medakis.twin.data import ROLE_TR
from medakis.twin.engine import Twin


@dataclass
class ToolContext:
    twin: Twin
    at: datetime
    minute: int
    surges: dict[int, float]
    create_proposal: Callable[[dict], dict] | None = None  # yönetici onayına TASLAK öneri (insan onayı servisi)
    create_request: Callable[[dict], dict] | None = None
    approval_status: Callable[[], list[dict]] | None = None
    request_status: Callable[[], list[dict]] | None = None
    calls: list[dict] = field(default_factory=list)

    def dens(self) -> list[dict]:
        return [{k: v for k, v in d.items() if k != "goruntu"} for d in self.twin.density(self.at, self.minute, self.surges)]


# ---- Araçlar (ham fonksiyonlar) ----------------------------------------------------------------------------------


def get_density(ctx: ToolContext) -> list[dict]:
    return ctx.dens()


def get_service_status(ctx: ToolContext) -> list[dict]:
    st = ctx.twin.staffing(ctx.at)
    rows = {r["servis_id"]: r for r in (ctx.twin.forecast(ctx.at) or []) if r["rol"] == "nurse"}
    out = []
    for u in ctx.twin.data.units.values():
        r = rows.get(u.id, {})
        out.append({"birim": u.id, "ad": u.name, "hemsire": st[u.id]["nurse"], "personel": st[u.id]["toplam"], "molada": st[u.id]["molada"],
                    "tamponlu_talep_dk": r.get("tamponlu_talep_dk"), "etkin_kapasite_dk": r.get("etkin_kapasite_dk"),
                    "acik_personel_dk": r.get("acik_personel_dk"), "eksik_hemsire": r.get("eksik_hemsire")})  # fmt: skip
    return out


def get_staff(ctx: ToolContext, unit: int | None = None) -> list[dict]:
    return [s for s in ctx.twin.data.staff_on_duty(ctx.at) if unit is None or s["birim"] == unit]


def get_staff_availability(ctx: ToolContext, target: int) -> list[dict]:
    return ctx.twin.candidates(ctx.at, target)


def get_staff_qualifications(ctx: ToolContext, code: str) -> dict | None:
    s = next((x for x in ctx.twin.data.staff.values() if x["kod"] == code), None)
    if s is None:
        return None
    return {"kod": code, "rol": ROLE_TR[s["rol"]], "yetkinlikler": ctx.twin.data.skills_valid(s["id"], ctx.at)}


def get_shifts(ctx: ToolContext, unit: int | None = None) -> list[dict]:
    return [{"kod": s["kod"], "birim": s["birim"], "vardiya": s["vardiya"], "baslangic": s["baslangic"], "bitis": s["bitis"]}
            for s in get_staff(ctx, unit)]  # fmt: skip


def get_workload_forecast(ctx: ToolContext) -> list[dict] | None:
    return ctx.twin.forecast(ctx.at)


def get_density_ahead(ctx: ToolContext, minutes: int = 60) -> list[dict]:
    """`minutes` sonrası beklenen kişi sayısı: aktif hasta kaydı projeksiyonu × alan katsayısı × senaryo (kamera görüntüsü değildir)."""
    t = ctx.at + timedelta(minutes=minutes)
    return [{k: v for k, v in d.items() if k != "goruntu"} for d in ctx.twin.density(t, ctx.minute, ctx.surges)]


def get_daily_gaps(ctx: ToolContext, unit: int | None = None, start_hour: int = 0, end_hour: int = 24) -> list[dict]:
    """Karar gününün saatlik hemşire talep/etkin kapasite matrisi; yalnız açığı olan saatler (talep > etkin kapasite)."""
    day = ctx.twin.data.local(ctx.at).date()
    return [r for r in ctx.twin.hourly(day, "nurse") if r["acik_dk"] > 0 and (unit is None or r["birim"] == unit)
            and start_hour <= r["saat"] < end_hour]  # fmt: skip


def get_capacity(ctx: ToolContext) -> dict:
    return ctx.twin.staffing(ctx.at)


def optimize_allocation(ctx: ToolContext, density_aware: bool = True) -> dict | None:
    return ctx.twin.optimize(ctx.at, ctx.twin.density_signal(ctx.dens()) if density_aware else {})


def validate_transfer(ctx: ToolContext, code: str, target: int) -> dict:
    row = next((c for c in ctx.twin.candidates(ctx.at, target) if c["kod"] == code), None)
    if row is None:
        return {"uygun": False, "gerekce": "Personel bu karar anında vardiyada değil ya da zaten hedef birimde."}
    if not row["uygun_veri"]:
        return {"uygun": False, "gerekce": row["gerekce"]}
    return {"uygun": True, "gerekce": "Rol, yetkinlik, kural kapsamı ve kullanılabilir süre uygun; son karar CP-SAT ve doğrulayıcınındır."}


def simulate_transfer(ctx: ToolContext) -> dict | None:
    base = ctx.twin.optimize(ctx.at, {})
    if base is None:
        return None
    dens = ctx.twin.optimize(ctx.at, ctx.twin.density_signal(ctx.dens()))
    return {"once": Twin.gaps(base), "sonra": Twin.gaps(dens), "sinyal": dens["sinyal"]}


def retrieve_policy_evidence(ctx: ToolContext, question: str) -> dict:
    return search_sources(question)


def create_transfer_proposal(ctx: ToolContext) -> dict:
    if ctx.create_proposal is None:
        return {"olusturuldu": False, "neden": "Onay servisi bu modda yok (model dosyaları gerekli)."}
    opt = optimize_allocation(ctx)
    if opt is None or not opt["sonuc"].get("oneri_yayinlanabilir"):
        return {"olusturuldu": False, "neden": "Doğrulanmış ve yayınlanabilir plan yok (güvenli ret)."}
    return ctx.create_proposal(opt)


def create_staff_request(ctx: ToolContext, unit: int, reason: str) -> dict:
    if ctx.create_request is None:
        return {"olusturuldu": False, "neden": "Talep servisi yok."}
    return ctx.create_request({"tur": "ek_personel", "birim": unit, "gerekce": reason, "kaynak": "agent"})


def create_transfer_request(ctx: ToolContext, code: str, target: int, reason: str) -> dict:
    """Belirli personel için geçici görevlendirme TALEBİ (taslak). Önce uygunluk denetlenir; uygun değilse talep açılmaz.
    Gerçek atama değildir; yönetici kararı ve CP-SAT/doğrulayıcı denetimi gerekir."""
    check = validate_transfer(ctx, code, target)
    if not check["uygun"]:
        return {"olusturuldu": False, "neden": check["gerekce"]}
    if ctx.create_request is None:
        return {"olusturuldu": False, "neden": "Talep servisi yok."}
    dup = next((r for r in get_request_status(ctx) if r.get("personel") == code and r.get("birim") == target and r.get("durum") == "beklemede"), None)
    if dup is not None:
        return {"olusturuldu": False, "neden": f"aynı personel ve birim için bekleyen talep zaten var ({dup['talep']})", "talep": dup["talep"]}
    staff = next(s for s in ctx.twin.data.staff_on_duty(ctx.at) if s["kod"] == code)
    return ctx.create_request({"tur": "gecici_gorevlendirme", "birim": target, "personel": code, "kaynak_birim": staff["birim"],
                               "gerekce": reason, "kaynak": "agent"})  # fmt: skip


def get_approval_status(ctx: ToolContext) -> list[dict]:
    return ctx.approval_status() if ctx.approval_status else []


def get_request_status(ctx: ToolContext) -> list[dict]:
    return ctx.request_status() if ctx.request_status else []


TOOLS: dict[str, Callable[..., Any]] = {f.__name__: f for f in (
    get_density, get_service_status, get_staff, get_staff_availability, get_staff_qualifications, get_shifts, get_workload_forecast,
    get_capacity, get_density_ahead, get_daily_gaps, optimize_allocation, validate_transfer, simulate_transfer, retrieve_policy_evidence,
    create_transfer_proposal, create_staff_request, create_transfer_request, get_approval_status, get_request_status)}  # fmt: skip
WRITE_TOOLS = ("create_transfer_proposal", "create_staff_request", "create_transfer_request")  # yalnız TASLAK üretir; onay/atama/bildirim aracı yok


def call(ctx: ToolContext, name: str, **kwargs: Any) -> Any:
    """Aracı çalıştırır ve gerçek süresini kaydeder. Tanımsız araç adı reddedilir."""
    if name not in TOOLS:
        raise KeyError(f"tanimsiz_arac:{name}")
    t0 = time.perf_counter()
    out = TOOLS[name](ctx, **kwargs)
    ctx.calls.append({"arac": name, "parametre": {k: v for k, v in kwargs.items()}, "sure_ms": round((time.perf_counter() - t0) * 1000, 1)})
    return out
