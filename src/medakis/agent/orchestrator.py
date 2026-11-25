"""MedAkis AI Agent: araç tabanlı, yerel orkestrasyon (planla → araçları çağır → doğrula → yanıtla).

Niyet belirleme deterministiktir (sözcük başı eşleşme). Yanıttaki her sayı araç çıktısından gelir; serbest sayı ya da
personel uydurulmaz. Agent'ın yazma yetkisi yalnız TASLAK öneri ve TASLAK talep oluşturmaktır; onay, gerçek atama ve
bildirim yönetici işlemidir. Bu orkestrasyon yerel bir prototiptir (LangGraph/MCP değildir).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from medakis.agent import tools as T
from medakis.twin.data import UTILIZATION
from medakis.twin.engine import LEVEL_TR, Twin

UNIT_WORDS = ((3, ("acil",)), (4, ("poliklini", "ayaktan", "klini")), (2, ("servis b", "b servis", "yataklı b", "yatakli b")),
              (1, ("servis a", "a servis", "yataklı a", "yatakli a")))  # fmt: skip
EXAMPLE_GROUPS: dict[str, tuple[str, ...]] = {
    "Kamera ve yoğunluk": ("Şu an hangi alanlarda yoğunluk var?", "60 dk sonra hangi alanlarda yoğunluk bekleniyor?",
                           "Acil Servis yoğunluğu için kamera verisine göre ne yapalım?"),
    "Personel yönlendirme (yönetici komutu)": ("STAFF0002'yi Acil Servis'e yönlendir", "Servis A'dan 1 hemşireyi Acil Servis'e gönder",
                                               "Acil Servis için taslak emirleri oluştur", "Acil Servis için uygun adaylar kimler?",
                                               "Alternatif öneri var mı?", "Bu yönlendirmenin riski ne?"),
    "60 dk öngörü": ("Önümüzdeki 60 dakikada talep ve kapasite durumu nedir?", "Acil Serviste önümüzdeki 60 dakikada kaç hemşire eksik?"),
    "Vardiya ve plan": ("Bugün hangi birimlerde personel açığı var?", "Servis B bugün 20:00–24:00 arasındaki açığı nasıl kapatalım?",
                        "Acil Serviste şu an kimler çalışıyor?", "Birimlerin personel kapasitesi nedir?", "STAFF0002 yetkinlikleri"),
    "Talepler ve onaylar": ("Acil için ek personel talebi oluştur", "Taleplerin durumu ne?", "Onay bekleyen kararlar neler?"),
    "Kurallar": ("Destek personeli hemşire görevine atanabilir mi?", "Onaylanmamış öneri kilit midir?", "Vardiya sorumlusunun görevi nedir?",
                 "Yetkinlik geçerliliği nasıl belirlenir?", "Tahmin tamponu nasıl hesaplanır?"),
}  # fmt: skip
EXAMPLES = tuple(q for group in EXAMPLE_GROUPS.values() for q in group)
MOVE_VERBS = ("yönlendir", "yonlendir", "gönder", "gonder", "görevlendir", "gorevlendir", "kaydır", "kaydir", "taşı", "tasi", "aktar")
RULE_WORDS = ("kural", "gerektir", "politika", "yönetmelik", "yonetmelik", "prosedür", "prosedur", "atanabilir", "izin verilir",
              "ne diyor", "fazla mesai", "kilit", "görevi nedir", "gorevi nedir", "görev tanım", "gorev tanim", "geçerlili", "gecerlili",
              "nasıl hesaplan", "nasil hesaplan", "dışa aktar", "disa aktar")  # fmt: skip
SHIFT_TR = {"GUNDUZ": "gündüz", "GECE": "gece", "AKSAM": "akşam", "SABAH": "sabah"}
_SRC_SUFFIX = re.compile(r"[dt][ae]n$")


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


def _word_re(w: str) -> str:
    # 'servis a' gibi tek harfle biten adlar sözcük sınırı ister ('servis alanı' A servisi değildir)
    return r"(?<!\w)" + re.escape(w) + (r"(?![a-zçğıöşü])" if re.search(r" [ab]$", w) else "")


def unit_of(m: str) -> int | None:
    for u, words in UNIT_WORDS:
        if any(re.search(_word_re(w), m) for w in words):
            return u
    return None


def unit_roles(m: str) -> tuple[int | None, int | None]:
    """(kaynak, hedef): '-dan/-den/-tan/-ten' ekli birim kaynaktır, diğeri hedef."""
    last: dict[int, int] = {}
    for u, words in UNIT_WORDS:
        for w in words:
            if w == "ayaktan":
                continue
            for mt in re.finditer(_word_re(w), m):
                last[u] = max(last.get(u, 0), mt.end())
    src = dst = None
    for u, end in sorted(last.items(), key=lambda x: x[1]):
        nxt = re.match(r"(\S*)(?:\s+(?:servis|klini|birim|alan)\S*?('?[a-zçğıöşü]*))?", m[end:])
        tail = nxt.group(1) if nxt and nxt.group(1) else (nxt.group(2) or "" if nxt else "")
        if _SRC_SUFFIX.search(tail) and src is None:
            src = u
        elif dst is None:
            dst = u
    return src, dst


def _fmt_moves(twin: Twin, res: dict) -> str:
    names = {u.id: u.name for u in twin.data.units.values()}
    codes = {s["id"]: s["kod"] for s in twin.data.staff.values()}
    return "\n".join(f"- **{codes.get(t['personel'], t['personel'])}**: {names.get(t['ev_servis'])} → **{names.get(t['hedef_servis'])}** "
                     f"({t['sure_dk']} dk; gerekli yetkinlik: {', '.join(t.get('gerekli_yetkinlikler') or []) or '-'})"
                     for t in res.get("tasimalar") or [])  # fmt: skip


def help_text() -> str:
    parts = [f"**{g}**\n" + "\n".join(f"- {q}" for q in qs) for g, qs in EXAMPLE_GROUPS.items()]
    return ("Soru sorabilir, istek ya da doğrudan talimat yazabilirsiniz. Agent veriyi araçlarla okur, TASLAK öneri ve TASLAK talep "
            "oluşturur; onay, gerçek atama ve bildirim yöneticidedir.\n\n" + "\n\n".join(parts))  # fmt: skip


def _hours(m: str) -> tuple[int, int] | None:
    mt = re.search(r"(\d{1,2})(?:[:.]\d{2})?\s*[-–—]\s*(\d{1,2})(?:[:.]\d{2})?", m)
    if not mt:
        return None
    a, b = int(mt.group(1)), int(mt.group(2))
    b = 24 if b == 0 else b
    return (a, b) if 0 <= a < b <= 24 else None


def answer(message: str, ctx: T.ToolContext) -> Reply:
    m = _norm(message)
    unit = unit_of(m)
    twin = ctx.twin
    names = {u.id: u.name for u in twin.data.units.values()}

    def done(text: str, plan: list[str], **kw) -> Reply:
        return Reply(text, plan, list(ctx.calls), **kw)

    code = re.search(r"staff\d{4}", m)
    moving = _has(m, *MOVE_VERBS)

    # ---- Yardım -------------------------------------------------------------------------------------------------
    if _has(m, "yardım", "yardim", "ne yapabilir", "neler yapabilir", "komutlar", "örnek soru", "ornek soru") or m.strip() in ("/start", "/yardim", "/help"):
        return done(help_text(), ["Yetenek listesini göster"])

    # ---- Doğrudan talimat: belirli personeli birime yönlendir (TASLAK talep) -------------------------------------
    if code and moving:
        _, dst = unit_roles(m)
        if dst is None:
            return done("Hedef birimi anlayamadım. Örnek: \"STAFF0002'yi Acil Servis'e yönlendir\".", ["Talimatı çözümle"])
        kod = code.group().upper()
        res = T.call(ctx, "create_transfer_request", code=kod, target=dst, reason=f"Yönetici talimatı (agent): {message.strip()[:160]}")
        if not res.get("olusturuldu"):
            return done(f"**{kod} → {names[dst]}** yönlendirmesi için talep AÇILMADI: {res.get('neden')}.\n\nUygun adayları görmek için: "
                        f"\"{names[dst]} için uygun adaylar kimler?\"", ["Uygunluğu denetle (rol, yetkinlik, kural kapsamı, süre)"])  # fmt: skip
        return done(f"**{kod} → {names[dst]}** geçici görevlendirme talebi TASLAK olarak açıldı ({res['talep']}). Uygunluk denetimi geçti "
                    "(rol, yetkinlik, destek kuralı kapsamı, kullanılabilir süre). Gerçek atama değildir; karar 'Talepler' ekranında "
                    "yöneticidedir.", ["Uygunluğu denetle", "Geçici görevlendirme talebi taslağı aç"])  # fmt: skip

    # ---- Doğrudan talimat: X biriminden N hemşireyi Y birimine gönder ---------------------------------------------
    src, dst = unit_roles(m)
    if moving and src is not None and dst is not None and _has(m, "hemşire", "hemsire", "personel", "kişi", "kisi"):
        found = re.search(r"(\d+)\s*(?:hemşire|hemsire|personel|kişi|kisi)", m)
        n = int(found.group(1)) if found else 1
        cands = T.call(ctx, "get_staff_availability", target=dst)
        from_src = [c for c in cands if c["birim"] == names[src]]
        eligible = [c for c in from_src if c["uygun_veri"]]
        lines, opened = [], 0
        for c in eligible:
            if opened == n:
                break
            r = T.call(ctx, "create_transfer_request", code=c["kod"], target=dst, reason=f"Yönetici talimatı (agent): {message.strip()[:160]}")
            opened += bool(r.get("olusturuldu"))
            lines.append(f"- {c['kod']} ({c['yetkinlikler']}): " + (f"talep {r['talep']} açıldı" if r.get("olusturuldu") else f"açılmadı ({r.get('neden')})"))
        head = f"**{names[src]} → {names[dst]}**, istenen {n} kişi; {names[src]} biriminden uygun {len(eligible)} aday var."
        if not eligible:
            why = sorted({c["gerekce"] for c in from_src})[:4]
            return done(head + " Talep AÇILMADI.\n\nElenme gerekçeleri:\n" + ("\n".join(f"- {w}" for w in why) or "- Kaynak birimde vardiyada personel yok."),
                        ["Kaynak birimdeki adayları listele", "Uygunluğu denetle"])  # fmt: skip
        tail = "" if opened >= n else f"\n\nYalnız {opened} yeni talep açılabildi; kalanlar için ek personel talebi önerilir."
        return done(head + "\n\nGeçici görevlendirme talepleri (TASLAK):\n" + "\n".join(lines) + tail +
                    "\n\nGerçek atama değildir; karar 'Talepler' ekranında yöneticidedir.", ["Adayları listele", "Uygunluğu denetle", "Talep taslakları aç"])  # fmt: skip

    # ---- Kurallar (kaynak belgeden) ------------------------------------------------------------------------------
    if _has(m, *RULE_WORDS) and not code:
        return _policy(message, ctx, done)

    # ---- Onaylar ve talepler -------------------------------------------------------------------------------------
    if _has(m, "onay"):
        st = T.call(ctx, "get_approval_status")
        if _has(m, "bekle"):
            st = [p for p in st if p["durum"] == "beklemede"]
        lines = "\n".join(f"- {p['kimlik']}: **{p['durum']}** ({p['kalem']} kalem, plan {p['plan_sha256'][:10]}…)" for p in st)
        return done(("Öneri durumları:\n" + lines) if lines else "Bu kriterde kayıtlı öneri yok.", ["Onay kayıtlarını oku"])
    if (_has(m, "talep", "taleb") and _has(m, "oluştur", "olustur", "aç", "iste", "ekle")) or (_has(m, "ek personel") and _has(m, "iste", "lazım", "lazim", "gerek")):
        u = unit or 3
        dens = T.call(ctx, "get_density")
        d = next(x for x in dens if x["birim"] == u)
        res = T.call(ctx, "create_staff_request", unit=u, reason=f"Agent taslağı: {d['alan']} {d['tespit']}/{d['kapasite']} kişi")
        return done(f"**{names[u]}** için ek personel talebi TASLAĞI oluşturuldu ({res.get('talep', '-')}). Talep yönetici kararını "
                    "bekliyor; agent talebi onaylayamaz.", ["Yoğunluğu oku", "Talep taslağı oluştur"])  # fmt: skip
    if _has(m, "talepler", "talebin", "taleplerin", "talep listesi", "talep durumu", "bekleyen talep"):
        reqs = T.call(ctx, "get_request_status")
        lines = "\n".join(f"- {r['talep']}: {r['tur']} · {names.get(r.get('birim'), '-')}" + (f" · {r['personel']}" if r.get("personel") else "")
                          + f" · **{r['durum']}**" for r in reqs)  # fmt: skip
        return done(("Talepler:\n" + lines) if lines else "Kayıtlı talep yok.", ["Talep kayıtlarını oku"])

    # ---- Personel bilgisi ----------------------------------------------------------------------------------------
    if code:
        q = T.call(ctx, "get_staff_qualifications", code=code.group().upper())
        if q is None:
            return done("Bu kodla kayıtlı personel yok.", ["Personel yetkinliklerini oku"])
        return done(f"**{q['kod']}** ({q['rol']}) — karar anında geçerli yetkinlikler: {', '.join(q['yetkinlikler']) or 'yok'}.",
                    ["Personel yetkinliklerini oku"])  # fmt: skip

    # ---- Günlük açık (saatlik matris) ----------------------------------------------------------------------------
    hrs = _hours(m)
    if _has(m, "açı", "acı", "acig", "açık", "acik", "eksik") and (_has(m, "bugün", "bugun", "gün", "gun", "vardiya") or hrs):
        h0, h1 = hrs or (0, 24)
        gaps = T.call(ctx, "get_daily_gaps", unit=unit, start_hour=h0, end_hour=h1)
        day = twin.data.local(ctx.at).strftime("%d.%m.%Y")
        scope = (names[unit] + " · ") if unit else ""
        if not gaps:
            return done(f"{scope}{day} {h0:02d}:00–{h1:02d}:00 arasında hemşire açığı yok (talep ≤ etkin kapasite).", ["Saatlik matrisi oku"])
        by: dict[int, list[dict]] = {}
        for r in gaps:
            by.setdefault(r["birim"], []).append(r)
        lines = []
        for u, rows in by.items():
            worst = max(rows, key=lambda r: r["eksik_hemsire"])
            hours = ", ".join(f"{r['saat']:02d}:00 ({r['eksik_hemsire']})" for r in sorted(rows, key=lambda r: r["saat"]))
            lines.append(f"- **{names[u]}**: en çok {worst['eksik_hemsire']} hemşire eksik ({worst['saat']:02d}:00, vardiyada {worst['personel']} hemşire). "
                         f"Eksik olan saatler: {hours}")  # fmt: skip
        text = f"{scope}{day} {h0:02d}:00–{h1:02d}:00 saatlik eksik hemşire sayısı (iş yükü, kullanım oranı %{round(100 * UTILIZATION)} ile):\n" + "\n".join(lines)
        plan = ["Saatlik talep/kapasite matrisini oku", "Açığı olan saatleri seç"]
        if _has(m, "nasıl", "nasil", "kapat", "çöz", "coz", "ne yap"):
            peak = max(gaps, key=lambda r: r["eksik_hemsire"])
            text += (f"\n\nKapatma seçenekleri (en yüksek saat {peak['saat']:02d}:00 için {peak['eksik_hemsire']} ek hemşire gerekir):\n"
                     f"- Ek personel talebi: \"{names[peak['birim']]} için ek personel talebi oluştur\"\n"
                     "- Destek kuralı kapsamındaki birimden geçici görevlendirme: \"Servis A'dan 1 hemşireyi … gönder\"\n"
                     "- Karar anında CP-SAT planı: Optimizasyon ve Yönlendirme ekranı (kısıtlar gevşetilmez)")  # fmt: skip
            plan.append("Seçenekleri hazırla")
        return done(text, plan)

    # ---- 60 dk sonrası yoğunluk ----------------------------------------------------------------------------------
    if _has(m, "sonra", "bekleni", "olacak") and _has(m, "yoğun", "yogun", "kalabalık", "kalabalik", "alan", "kişi", "kisi"):
        now = {d["birim"]: d for d in T.call(ctx, "get_density")}
        ahead = T.call(ctx, "get_density_ahead", minutes=60)
        lines = [f"- **{d['alan']}**: şimdi {now[d['birim']]['tespit']}/{d['kapasite']} → 60 dk sonra ~{d['tespit']}/{d['kapasite']} "
                 f"(%{round(100 * d['doluluk'])}, {LEVEL_TR[d['seviye']]})" for d in ahead if unit is None or d["birim"] == unit]  # fmt: skip
        hot = [d["alan"] for d in ahead if d["seviye"] in ("turuncu", "kirmizi")]
        return done(("Yoğunluk beklenen alanlar: " + ", ".join(hot) if hot else "60 dk sonra turuncu/kırmızı alan beklenmiyor.") + "\n\n" + "\n".join(lines)
                    + "\n\nNot: 60 dk sonrası kamera görüntüsü değil, aktif hasta kaydı projeksiyonudur (senaryo artışı dahil).",
                    ["Şimdiki yoğunluğu oku", "60 dk sonrasını projekte et"])  # fmt: skip

    # ---- 60 dk iş yükü tahmini -----------------------------------------------------------------------------------
    if _has(m, "tahmin", "öngör", "ongor", "önümüzdeki", "onumuzdeki", "tampon", "60 dakika", "eksik"):
        rows = T.call(ctx, "get_workload_forecast")
        if rows is None:
            return done("Tahmin modeli dosyaları yüklü değil; tahmin üretilmedi (sahte tahmin gösterilmez).", ["Tahmini oku"])
        lines = [f"- **{names[r['servis_id']]}**: " + (f"**{r['eksik_hemsire']} hemşire eksik**" if r["eksik_hemsire"] else "eksik yok")
                 + f" (iş yükü tahmini {round(r['nokta_tahmin_dk'])} dk + tampon {round(r['tampon_dk'] or 0)} dk; etkin kapasite {round(r['etkin_kapasite_dk'])} dk)"
                 for r in rows if r["rol"] == "nurse" and r.get("nokta_tahmin_dk") is not None and (unit is None or r["servis_id"] == unit)]  # fmt: skip
        return done("Önümüzdeki 60 dakika için hemşire iş yükü (Random Forest + kalibrasyonlu tampon):\n" + ("\n".join(lines) or "- Bu birim için tahmin satırı yok."),
                    ["Tahmin satırlarını oku"])  # fmt: skip
    if _has(m, "risk"):
        sim = T.call(ctx, "simulate_transfer")
        if sim is None:
            return done("Optimizasyon için model dosyaları gerekli; risk hesaplanamadı.", ["Taşımayı simüle et"])
        lines = [f"- {names[u]}: eksik hemşire {sim['once'][u]['eksik_hemsire']} → {sim['sonra'][u]['eksik_hemsire']}"
                 for u in sim["sonra"]]  # fmt: skip
        return done("Yoğunluk sinyalli plan ile sinyalsiz plan karşılaştırması (CP-SAT sonrası kalan eksik hemşire):\n" + "\n".join(lines) +
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
        return done(f"{names[u]} için uygun ama çözücünün seçmediği adaylar. Talimatla talep açılabilir, ör. \"{cands[0]['kod']}'yi "
                    f"{names[u]}'e yönlendir\":\n{lines}", ["Planı çöz", "Uygun adayları listele"])  # fmt: skip
    if _has(m, "aday", "uygun personel", "uygun hemşire", "uygun hemsire"):
        u = unit or 3
        cands = T.call(ctx, "get_staff_availability", target=u)
        ok = [c for c in cands if c["uygun_veri"]]
        lines = "\n".join(f"- {c['kod']} ({c['birim']}; {c['yetkinlikler'] or '-'})" for c in ok[:8]) or "- Uygun aday yok."
        return done(f"**{names[u]}** için uygun adaylar ({len(ok)} uygun, {len(cands) - len(ok)} elendi):\n{lines}\n\nElenme gerekçeleri "
                    "Optimizasyon ekranındaki aday tablosundadır.", ["Uygun adayları listele"])  # fmt: skip

    # ---- Kamera verisine göre ne yapılmalı (salt okunur öneri, kayıt yok) ----------------------------------------
    if _has(m, "ne yap", "nasıl", "nasil") and _has(m, "yoğun", "yogun", "kamera", "kalabalık", "kalabalik"):
        u = unit or 3
        dens = T.call(ctx, "get_density")
        d = next(x for x in dens if x["birim"] == u)
        sig = twin.density_signal([d])
        text = f"**{d['alan']}**: {d['tespit']}/{d['kapasite']} kişi (%{round(100 * d['doluluk'])}, {LEVEL_TR[d['seviye']]})."
        if not sig:
            return done(text + " Kapasite aşılmadığı için ek talep sinyali yok; izlemeye devam önerilir.", ["Yoğunluğu oku"])
        opt = T.call(ctx, "optimize_allocation")
        text += f" Kapasite aşımı ek talep sinyali: +{sig[u]} hemşire-dk."
        if opt and opt["sonuc"].get("oneri_yayinlanabilir"):
            text += f"\n\nCP-SAT planı (henüz KAYDEDİLMEDİ):\n{_fmt_moves(twin, opt['sonuc']) or '- Taşıma gerekmiyor.'}"
        text += (f"\n\nSeçenekler:\n- Taslak öneri kaydet: \"{names[u]} için taslak emirleri oluştur\"\n- Ek personel talebi: "
                 f"\"{names[u]} için ek personel talebi oluştur\"\n- Belirli personeli yönlendir: \"STAFF…'yi {names[u]}'e yönlendir\"")  # fmt: skip
        return done(text, ["Yoğunluğu oku", "Ek talep sinyalini hesapla", "CP-SAT ile çöz (kayıt yok)", "Seçenekleri hazırla"])

    # ---- Öneri / taslak emir (CP-SAT + doğrulayıcı + TASLAK kayıt) ------------------------------------------------
    if _has(m, "öner", "oner", "destek", "yönlendir", "yonlendir", "görevlendir", "gorevlendir", "plan", "optimiz", "emir", "taslak"):
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
                    + (f"Yoğunluk ek talep sinyali: {sig}.\n\n" if sig else "") + tail + " Agent onay veremez; onay 'Öneriler ve Onaylar' ekranındadır. "
                    "Personel emri (bildirim) yalnız onaydan sonra hazırlanır.", plan, proposal=prop)  # fmt: skip

    # ---- Vardiyadaki personel ve kapasite ------------------------------------------------------------------------
    if _has(m, "kimler", "kim var", "çalışıyor", "calisiyor", "vardiyada", "personel listesi"):
        rows = T.call(ctx, "get_shifts", unit=unit)
        groups: dict[int, list[str]] = {}
        for r in rows:
            groups.setdefault(r["birim"], []).append(f"{r['kod']} ({SHIFT_TR.get(r['vardiya'], str(r['vardiya']).lower())})")
        lines = [f"- **{names[u]}** ({len(v)}): " + ", ".join(sorted(v)[:12]) + (" …" if len(v) > 12 else "") for u, v in sorted(groups.items())]
        return done("Karar anından itibaren 60 dk içinde vardiyası olan personel:\n" + ("\n".join(lines) or "- Kayıt yok."), ["Vardiyaları oku"])
    if _has(m, "kapasite"):
        cap = T.call(ctx, "get_capacity")
        lines = [f"- **{names[u]}**: {c['toplam']} personel ({c['nurse']} hemşire, {c['support']} destek, {c['supervisor']} sorumlu; "
                 f"{c['molada']} molada, {c['izinli']} izinli)" for u, c in cap.items() if unit is None or u == unit]  # fmt: skip
        return done("Karar anında birim personel kapasitesi:\n" + "\n".join(lines), ["Kapasiteyi oku"])

    # ---- Yoğunluk / durum ----------------------------------------------------------------------------------------
    if _has(m, "yoğun", "yogun", "durum", "doluluk", "kalabalık", "kalabalik", "kamera"):
        dens = T.call(ctx, "get_density")
        status = {s["birim"]: s for s in T.call(ctx, "get_service_status")}
        rows = [d for d in dens if unit is None or d["birim"] == unit]
        lines = []
        for d in rows:
            s = status[d["birim"]]
            gap = s["eksik_hemsire"]
            lines.append(f"- **{d['alan']}**: {d['tespit']}/{d['kapasite']} kişi (%{round(100 * d['doluluk'])}, {LEVEL_TR[d['seviye']]}); "
                         f"vardiyada {s['personel']} personel ({s['hemsire']} hemşire, {s['molada']} molada)"
                         + (f"; 60 dk içinde {gap} hemşire eksik" if gap else "; 60 dk içinde eksik yok" if gap == 0 else ""))  # fmt: skip
        hot = [d["alan"] for d in rows if d["seviye"] in ("turuncu", "kirmizi")]
        head = ("Yoğunluk olan alanlar: " + ", ".join(hot) + "\n\n") if hot and unit is None else ""
        return done(head + "Sentetik kamera ve vardiya verisine göre güncel durum:\n" + "\n".join(lines) +
                    "\n\nNot: kamera yoğunluğu (kişi/alan kapasitesi) ile personel iş yükü (talep/etkin kapasite) ayrı ölçülerdir.",
                    ["Yoğunluğu oku", "Birim durumunu oku"])  # fmt: skip
    return _policy(message, ctx, done)


def _policy(message: str, ctx: T.ToolContext, done: Callable[..., Reply]) -> Reply:
    ev = T.call(ctx, "retrieve_policy_evidence", question=message)
    if not ev["bulundu"]:
        return done(ev["mesaj"] + " Ne sorabileceğinizi görmek için \"yardım\" yazın. Örnek: " + "; ".join(EXAMPLES[:3]) + ".", ["Kaynak belgede ara"])
    k = ev["kaynaklar"][0]
    return done(f"Kaynak belgede bulunan alıntı ({ev['yontem']}):\n\n> {k['alinti'][:700]}\n\n— {k['belge']} ({k['kimlik']} sürüm {k['surum']}, "
                f"bölüm: {k['bolum']}, skor {k['skor']})", ["Kaynak belgede ara", "Alıntıyı birebir göster"], sources=ev["kaynaklar"])  # fmt: skip
