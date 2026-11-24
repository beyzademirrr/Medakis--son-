"""MedAkis AI Agent: araç tabanlı, yerel orkestrasyon (planla → araçları çağır → doğrula → yanıtla).

Niyet belirleme deterministiktir (sözcük başı eşleşme). Yanıttaki her sayı araç çıktısından gelir; serbest sayı ya da
personel uydurulmaz. Agent'ın yazma yetkisi yalnız TASLAK öneri ve TASLAK talep oluşturmaktır; onay, gerçek atama ve
bildirim yönetici işlemidir. Bu orkestrasyon yerel bir prototiptir (LangGraph/MCP değildir).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from medakis.agent import tools as T
from medakis.twin.engine import LEVEL_TR, Twin

UNIT_WORDS = ((3, ("acil",)), (4, ("poliklinik", "ayaktan", "klinik")), (2, ("servis b", "b servis", "yataklı b", "yatakli b")),
              (1, ("servis a", "a servis", "yataklı a", "yatakli a")))  # fmt: skip
EXAMPLES = ("Acil serviste durum ne?", "Acil servise personel öner", "Alternatif öneri var mı?", "Bu yönlendirmenin riski ne?",
            "60 dakikalık tahmin nedir?", "STAFF0002 yetkinlikleri", "Acil için ek personel talebi oluştur", "Onay durumu",
            "Mola kuralı ne diyor?")  # fmt: skip


@dataclass
class Reply:
    text: str
    plan: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    proposal: dict | None = None
    sources: list[dict] = field(default_factory=list)
    engine: str = "Deterministik yerel orkestrasyon"


def _norm(m: str) -> str:
    return m.replace("İ", "i").replace("I", "ı").lower()


def _has(m: str, *prefixes: str) -> bool:
    """Sözcük BAŞINDA eşleşme (ör. 'plan' → 'planı' eşleşir, 'hesaplanır' eşleşmez)."""
    return any(re.search(r"(?<!\w)" + re.escape(p), m) for p in prefixes)


def unit_of(m: str) -> int | None:
    for u, words in UNIT_WORDS:
        if any(w in m for w in words):
            return u
    return None


def _fmt_moves(twin: Twin, res: dict) -> str:
    names = {u.id: u.name for u in twin.data.units.values()}
    codes = {s["id"]: s["kod"] for s in twin.data.staff.values()}
    return "\n".join(f"- **{codes.get(t['personel'], t['personel'])}**: {names.get(t['ev_servis'])} → **{names.get(t['hedef_servis'])}** "
                     f"({t['sure_dk']} dk; gerekli yetkinlik: {', '.join(t.get('gerekli_yetkinlikler') or []) or '-'})"
                     for t in res.get("tasimalar") or [])  # fmt: skip


def answer(message: str, ctx: T.ToolContext) -> Reply:
    m = _norm(message)
    unit = unit_of(m)
    twin = ctx.twin
    names = {u.id: u.name for u in twin.data.units.values()}

    def done(text: str, plan: list[str], **kw) -> Reply:
        return Reply(text, plan, list(ctx.calls), **kw)

    if _has(m, "onay"):
        st = T.call(ctx, "get_approval_status")
        lines = "\n".join(f"- {p['kimlik']}: **{p['durum']}** ({p['kalem']} kalem, plan {p['plan_sha256'][:10]}…)" for p in st) or "Kayıtlı öneri yok."
        return done("Öneri durumları:\n" + lines, ["Onay kayıtlarını oku"])
    if _has(m, "talep", "talebi"):
        u = unit or 3
        dens = T.call(ctx, "get_density")
        d = next(x for x in dens if x["birim"] == u)
        res = T.call(ctx, "create_staff_request", unit=u, reason=f"Agent taslağı: {d['alan']} {d['tespit']}/{d['kapasite']} kişi")
        return done(f"**{names[u]}** için ek personel talebi TASLAĞI oluşturuldu ({res.get('talep', '-')}). Talep yönetici kararını "
                    "bekliyor; agent talebi onaylayamaz.", ["Yoğunluğu oku", "Talep taslağı oluştur"])  # fmt: skip
    code = re.search(r"staff\d{4}", m)
    if code:
        q = T.call(ctx, "get_staff_qualifications", code=code.group().upper())
        if q is None:
            return done("Bu kodla kayıtlı personel yok.", ["Personel yetkinliklerini oku"])
        return done(f"**{q['kod']}** ({q['rol']}) — karar anında geçerli yetkinlikler: {', '.join(q['yetkinlikler']) or 'yok'}.",
                    ["Personel yetkinliklerini oku"])  # fmt: skip
    if _has(m, "tahmin", "öngör", "ongor"):
        rows = T.call(ctx, "get_workload_forecast")
        if rows is None:
            return done("Tahmin modeli dosyaları yüklü değil; tahmin üretilmedi (sahte tahmin gösterilmez).", ["Tahmini oku"])
        lines = [f"- **{names[r['servis_id']]}**: RF nokta tahmini {round(r['nokta_tahmin_dk'])} dk + tampon {round(r['tampon_dk'] or 0)} dk → "
                 f"etkin kapasite {round(r['etkin_kapasite_dk'])} dk, açık {round(r['acik_personel_dk'] or 0)} personel-dk"
                 for r in rows if r["rol"] == "nurse" and r.get("nokta_tahmin_dk") is not None]  # fmt: skip
        return done("Önümüzdeki 60 dakika için hemşire iş yükü (Random Forest + kalibrasyonlu tampon):\n" + "\n".join(lines),
                    ["Tahmin satırlarını oku"])  # fmt: skip
    if _has(m, "risk"):
        sim = T.call(ctx, "simulate_transfer")
        if sim is None:
            return done("Optimizasyon için model dosyaları gerekli; risk hesaplanamadı.", ["Taşımayı simüle et"])
        lines = [f"- {names[u]}: açık {round(sim['once'][u]['acik_dk'] or 0)} → {round(sim['sonra'][u]['acik_dk'] or 0)} personel-dk"
                 for u in sim["sonra"]]  # fmt: skip
        return done("Yoğunluk sinyalli plan ile sinyalsiz plan karşılaştırması (CP-SAT sonrası kalan açıklar):\n" + "\n".join(lines) +
                    "\n\nRiskler: kaynak birim asgari kişi kuralı ve kaynak açığı doğrulayıcıda denetlenir; sentetik veridir, "
                    "gerçek hastanede doğrulanmamıştır.", ["Mevcut planı çöz", "Yoğunluk sinyalli planı çöz", "Karşılaştır"])  # fmt: skip
    if _has(m, "alternatif"):
        u = unit or 3
        opt = T.call(ctx, "optimize_allocation")
        chosen = {t["personel"] for t in (opt or {}).get("sonuc", {}).get("tasimalar") or []}
        cands = [c for c in T.call(ctx, "get_staff_availability", target=u) if c["uygun_veri"] and c["id"] not in chosen]
        if not cands:
            return done(f"{names[u]} için çözücünün seçmediği uygun aday yok; diğer adaylar veri kurallarıyla elendi.",
                        ["Planı çöz", "Uygun adayları listele"])  # fmt: skip
        lines = "\n".join(f"- {c['kod']} ({c['birim']}; {c['yetkinlikler']})" for c in cands[:5])
        return done(f"{names[u]} için uygun ama çözücünün seçmediği adaylar (alternatif yalnız manuel yönlendirme talebi olarak "
                    f"Optimizasyon ekranında yeniden doğrulanabilir):\n{lines}", ["Planı çöz", "Uygun adayları listele"])  # fmt: skip
    if _has(m, "öner", "oner", "destek", "yönlendir", "yonlendir", "görevlendir", "gorevlendir", "plan", "optimiz"):
        dens = T.call(ctx, "get_density")
        T.call(ctx, "get_workload_forecast")
        T.call(ctx, "get_staff_availability", target=unit or 3)
        opt = T.call(ctx, "optimize_allocation")
        plan = ["Yoğunluğu oku", "Tahmini oku", "Uygun personeli listele", "CP-SAT ile çöz", "Bağımsız doğrula", "Taslak öneri oluştur"]
        if opt is None:
            return done("Optimizasyon için model dosyaları gerekli; öneri üretilmedi.", plan)
        res = opt["sonuc"]
        crit = [f"{d['alan']} {d['tespit']}/{d['kapasite']} ({LEVEL_TR[d['seviye']]})" for d in dens if d["seviye"] in ("turuncu", "kirmizi")]
        head = ("Yoğunluk: " + "; ".join(crit) + "\n\n") if crit else ""
        sig = ", ".join(f"{names[u]} +{v} dk" for u, v in opt["sinyal"].items())
        if not res.get("oneri_yayinlanabilir"):
            why = ", ".join((res.get("dogrulama") or {}).get("ihlaller") or []) or res.get("durum")
            return done(head + f"CP-SAT bu karar anında yayınlanabilir plan üretmedi (durum: {res.get('durum')}; {why}). Güvenli ret: "
                        "kısıtlar gevşetilmez. Alternatif: ek personel talebi ya da manuel yönlendirme talebi.", plan)  # fmt: skip
        prop = T.call(ctx, "create_transfer_proposal")
        moves = _fmt_moves(twin, res) or "- Taşıma gerekmiyor (mevcut kapasite yeterli)."
        if prop.get("olusturuldu"):
            tail = f"Öneri **{prop['oneri']}** yönetici onayına TASLAK olarak kaydedildi."
        elif "oneri_zaten_kayitli" in str(prop.get("neden")):
            tail = f"Aynı plan zaten kayıtlı (öneri {prop.get('oneri')}); ikinci kez kaydedilmedi."
        else:
            tail = f"Taslak kaydedilmedi: {prop.get('neden')}."
        return done(head + f"CP-SAT planı (doğrulayıcı: {'geçti' if res['dogrulama']['gecti'] else 'geçmedi'}):\n{moves}\n\n"
                    + (f"Yoğunluk ek talep sinyali: {sig}.\n\n" if sig else "") + tail + " Agent onay veremez; onay 'Öneriler ve Onaylar' ekranındadır.",
                    plan, proposal=prop)  # fmt: skip
    if _has(m, "yoğun", "yogun", "durum", "doluluk", "kalabalık", "kalabalik", "kamera"):
        dens = T.call(ctx, "get_density")
        status = {s["birim"]: s for s in T.call(ctx, "get_service_status")}
        rows = [d for d in dens if unit is None or d["birim"] == unit]
        lines = []
        for d in rows:
            s = status[d["birim"]]
            gap = s["acik_personel_dk"]
            lines.append(f"- **{d['alan']}**: {d['tespit']}/{d['kapasite']} kişi (%{round(100 * d['doluluk'])}, {LEVEL_TR[d['seviye']]}); "
                         f"vardiyada {s['personel']} personel ({s['hemsire']} hemşire, {s['molada']} molada)"
                         + (f"; 60 dk tahmini açık {round(gap)} personel-dk" if gap is not None else ""))  # fmt: skip
        return done("Sentetik kamera ve vardiya verisine göre güncel durum:\n" + "\n".join(lines) +
                    "\n\nNot: kamera yoğunluğu (kişi/alan kapasitesi) ile personel iş yükü (talep/etkin kapasite) ayrı ölçülerdir.",
                    ["Yoğunluğu oku", "Birim durumunu oku"])  # fmt: skip
    ev = T.call(ctx, "retrieve_policy_evidence", question=message)
    if not ev["bulundu"]:
        return done(ev["mesaj"] + " Örnek istekler: " + "; ".join(EXAMPLES[:5]) + ".", ["Kaynak belgede ara"])
    k = ev["kaynaklar"][0]
    return done(f"Kaynak belgede bulunan alıntı ({ev['yontem']}):\n\n> {k['alinti'][:700]}\n\n— {k['belge']} ({k['kimlik']} sürüm {k['surum']}, "
                f"bölüm: {k['bolum']}, skor {k['skor']})", ["Kaynak belgede ara", "Alıntıyı birebir göster"], sources=ev["kaynaklar"])  # fmt: skip
