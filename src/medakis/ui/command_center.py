"""MedAkis Komuta Merkezi: ürün arayüzü (Sentetik Demo; PostgreSQL ve Ollama gerektirmez).

Bütün göstergeler oturumdaki sentetik simülasyon ve sentetik personel motorundan hesaplanır; sabit değer yazılmaz.
Personel aktarımı uygulanmaz: öneri → yönetici onayı → (dry-run) bildirim → denetim kaydı.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from medakis.agent import tools as T
from medakis.agent.orchestrator import answer
from medakis.approvals.audit_log import FileJournal
from medakis.density import staffing as S
from medakis.density.simulator import AREAS, SCENARIOS, Simulator, count_from_frame
from medakis.notifications.telegram import TelegramNotifier
from medakis.retrieval_demo import search_sources
from medakis.ui import theme

ROOT = Path(__file__).resolve().parents[3]
USER = "demo_yonetici (prototip)"
PAGES = ["🏥 Kontrol Merkezi", "📹 Yoğunluk Analizi", "📈 İş Yükü Tahmini", "👥 Personel Yönetimi", "🤖 MedAkis AI",
         "✅ Öneriler ve Onaylar", "🔔 Bildirimler", "🧾 Denetim ve Raporlar", "⚙️ Ayarlar"]  # fmt: skip


def _state() -> None:
    ss = st.session_state
    if "sim" not in ss:
        ss.sim = Simulator("acil_yogunluk")
        ss.history = [{"t": 0, **ss.sim.counts()}]
        ss.running = False
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        notifier = TelegramNotifier(FileJournal(ROOT / "data" / "bildirim_izi" / f"bildirim-{stamp}.jsonl"))
        ss.svc = T.TransferService(FileJournal(ROOT / "data" / "gorevlendirme_izi" / f"gorevlendirme-{stamp}.jsonl"), notifier)
        ss.chat = []


def _tick() -> None:
    ss = st.session_state
    counts = ss.sim.step()
    ss.history.append({"t": ss.sim.t, **counts})
    ss.history = ss.history[-120:]


def _rows() -> list[dict]:
    return S.area_status(st.session_state.sim.counts(), S.roster())


def _controls(key: str) -> None:
    ss = st.session_state
    c1, c2, c3, c4 = st.columns([1.3, 1, 1, 1])
    scen = c1.selectbox("Senaryo", list(SCENARIOS), format_func=SCENARIOS.get, index=list(SCENARIOS).index(ss.sim.scenario), key=f"senaryo_{key}")
    if scen != ss.sim.scenario:
        ss.sim = Simulator(scen)
        ss.history = [{"t": 0, **ss.sim.counts()}]
    if c2.button("⏸ Durdur" if ss.running else "▶ Simülasyonu başlat", key=f"baslat_{key}", width="stretch"):
        ss.running = not ss.running
        st.rerun()
    if c3.button("⏭ Bir adım", key=f"adim_{key}", width="stretch", disabled=ss.running):
        _tick()
    if c4.button("↺ Sıfırla", key=f"sifirla_{key}", width="stretch"):
        ss.sim = Simulator(ss.sim.scenario)
        ss.history = [{"t": 0, **ss.sim.counts()}]
        ss.running = False
        st.rerun()


def _live(body) -> None:  # noqa: ANN001
    """Simülasyon çalışıyorsa bölüm her saniye bir adım ilerleyip yeniden çizilir."""
    if st.session_state.running:
        def run():
            _tick()
            body()
        st.fragment(run_every=1.0)(run)()
    else:
        body()


def page_control() -> None:
    st.markdown("### Kontrol Merkezi")
    _controls("kontrol")

    def body():
        rows = _rows()
        staff = S.roster()
        props = st.session_state.svc.proposals().values()
        total_cap = sum(r["kapasite"] for r in rows)
        total = sum(r["kisi"] for r in rows)
        crit = [r for r in rows if r["seviye"] == "kritik"]
        need_gap = sum(max(0, r["gereken_personel"] - r["aktif_personel"]) for r in rows)
        cols = st.columns(6)
        cards = [("Aktif personel", str(sum(s.on_shift for s in staff)), f"{len(staff)} kayıtlı", theme.ACCENT2),
                 ("Hastane yoğunluğu", f"%{round(100 * total / total_cap)}", f"{total}/{total_cap} kişi", theme.ACCENT),
                 ("Kritik alan", str(len(crit)), ", ".join(r["ad"] for r in crit) or "yok", theme.CRIT if crit else theme.OK),
                 ("Bekleyen görev", str(sum(r["bekleyen_gorev"] for r in rows)), "simülasyon göstergesi", theme.WARN),
                 ("Personel açığı", str(need_gap), "gereken − aktif", theme.CRIT if need_gap else theme.OK),
                 ("Onay bekleyen", str(sum(p["durum"] == "beklemede" for p in props)), "öneri", theme.ACCENT2)]  # fmt: skip
        for col, (k, v, s, c) in zip(cols, cards):
            col.markdown(theme.kpi(k, v, s, c), unsafe_allow_html=True)
        left, right = st.columns([1.35, 1])
        with left:
            st.markdown("#### Hizmet alanı yoğunluk haritası")
            st.markdown(theme.heatmap_svg(rows, AREAS), unsafe_allow_html=True)
            st.caption("Kaynak: sentetik kamera simülasyonu (gerçek görüntü değildir).")
        with right:
            st.markdown("#### Yoğunluk değişimi")
            hist = pd.DataFrame(st.session_state.history).set_index("t").rename(columns=S.AREA_NAMES)
            st.line_chart(hist, height=210)
            st.markdown("#### Personel kapasitesi")
            st.bar_chart(pd.DataFrame({"Aktif": [r["aktif_personel"] for r in rows], "Gereken": [r["gereken_personel"] for r in rows]},
                                      index=[r["ad"] for r in rows]), height=200, stack=False)  # fmt: skip
        a, b = st.columns([1, 1.2])
        with a:
            st.markdown("#### Operasyonel uyarılar")
            for r in sorted(rows, key=lambda r: -r["doluluk"]):
                if r["seviye"] != "normal":
                    st.markdown(theme.alert(f"{r['ad']}: doluluk %{round(100 * r['doluluk'])}, personel başına {r['personel_basina_gorev']} bekleyen görev",
                                            r["seviye"]), unsafe_allow_html=True)  # fmt: skip
            if all(r["seviye"] == "normal" for r in rows):
                st.markdown(theme.alert("Bütün alanlar normal seviyede.", "normal"), unsafe_allow_html=True)
        with b:
            st.markdown("#### Karar motoru önerisi")
            plan = T.optimize_staff_allocation(st.session_state.sim)
            if plan.get("aktarimlar"):
                for m in plan["aktarimlar"]:
                    st.markdown(f"**{m['staff_id']}** · {S.AREA_NAMES[m['source']]} → **{S.AREA_NAMES[m['target']]}**")
                st.caption(plan["gerekce"] + (" · Doğrulayıcı: ihlal yok." if not plan["dogrulama"] else " · Doğrulayıcı: " + ", ".join(plan["dogrulama"])))
            else:
                st.caption(plan["gerekce"])

    _live(body)
    plan = T.optimize_staff_allocation(st.session_state.sim)
    if plan.get("aktarimlar") and not plan["dogrulama"] and st.button("📝 Öneriyi onaya gönder", key="kc_oneri"):
        res = T.create_transfer_proposal(st.session_state.svc, plan, "yonetici_ekrani")
        st.success(f"Öneri {res['oneri']['id']} oluşturuldu; 'Öneriler ve Onaylar' ekranında yönetici onayı bekliyor.")


def page_density() -> None:
    st.markdown("### Yoğunluk Analizi")
    st.markdown('<span class="ma-tag">Sentetik kamera simülasyonu</span> <span class="ma-tag">Gerçek görüntü, yüz ya da kimlik işlenmez</span>',
                unsafe_allow_html=True)  # fmt: skip
    _controls("yogunluk")

    def body():
        sim = st.session_state.sim
        frame = sim.frame()
        img_counts = count_from_frame(frame)
        rows = _rows()
        c1, c2 = st.columns([1.25, 1])
        c1.image(frame, caption=f"Sentetik kamera görüntüsü · adım {sim.t} · {SCENARIOS[sim.scenario]}", width="stretch")
        c2.markdown(theme.heatmap_svg(rows, AREAS), unsafe_allow_html=True)
        table = pd.DataFrame([{"Alan": r["ad"], "Simülasyon sayımı": r["kisi"], "Görüntüden sayım": img_counts[r["alan"]], "Kapasite": r["kapasite"],
                               "Doluluk %": round(100 * r["doluluk"]), "Seviye": theme.LEVEL_LABEL[r["seviye"]]} for r in rows])  # fmt: skip
        st.dataframe(table, hide_index=True, width="stretch")
        st.caption("Görüntüden sayım: sentetik karedeki figür renginin bağlantılı bölgeleri sayılır (renk tabanlı sayım; gerçek insan tespiti değildir).")
        hist = pd.DataFrame(st.session_state.history).set_index("t").rename(columns=S.AREA_NAMES)
        st.line_chart(hist, height=220)
        for r in rows:
            if r["seviye"] == "kritik":
                st.markdown(theme.alert(f"KRİTİK YOĞUNLUK — {r['ad']}: %{round(100 * r['doluluk'])} ({r['kisi']}/{r['kapasite']})", "kritik"), unsafe_allow_html=True)

    _live(body)


def page_forecast() -> None:
    st.markdown("### İş Yükü Tahmini")
    t1, t2 = st.tabs(["Kısa vadeli simülasyon projeksiyonu", "Makine öğrenmesi modeli (Random Forest)"])
    with t1:
        st.caption("Simülasyon ileri oynatılarak elde edilen projeksiyon. Makine öğrenmesi tahmini değildir.")
        fc = T.get_workload_forecast(st.session_state.sim, steps=12)
        st.line_chart(pd.DataFrame(fc["adimlar"]).rename(columns=S.AREA_NAMES), height=280)
    with t2:
        model_dir = ROOT / "artifacts" / "forecasting" / "asama4_seed42"
        if not model_dir.exists():
            st.warning("Random Forest model dosyaları bu bilgisayarda yok. Hazırlamak için: `baslat.ps1 -ModelHazirla` "
                       "(sentetik veriden yerel eğitim; birkaç dakika). Model yokken sahte tahmin gösterilmez.")  # fmt: skip
        else:
            st.success("Random Forest modelleri yüklü (sentetik veriyle yerel eğitildi; 30/60 dakika ufuk).")
            res = ROOT / "docs" / "veri" / "asama4_sonuclar.json"
            if res.exists():
                st.caption("Ortak test diliminde değerlendirme özeti (`docs/veri/asama4_sonuclar.json`).")
                st.json(json.loads(res.read_text(encoding="utf-8")), expanded=False)
            st.caption("Modelin vardiya planına uygulandığı inceleme akışı: 'Öneriler ve Onaylar' → Vardiya planı (CP-SAT) incelemesi.")


def page_staff() -> None:
    st.markdown("### Personel Yönetimi")
    staff = S.roster()
    st.dataframe(pd.DataFrame(T.get_staff_availability()), hide_index=True, width="stretch", height=260)
    st.markdown("#### Geçici görevlendirme planı (CP-SAT + bağımsız doğrulayıcı)")
    c1, c2 = st.columns([1, 2])
    target = c1.selectbox("Hedef alan", [None] + [a.key for a in AREAS], format_func=lambda k: "Otomatik (destek gerektiren alan)" if k is None else S.AREA_NAMES[k])
    pinned = c2.multiselect("Yönetici tarafından seçilen personel (isteğe bağlı)", [s.id for s in staff])
    if st.button("⚙️ Planı hesapla", key="plan_hesapla"):
        st.session_state.last_plan = T.optimize_staff_allocation(st.session_state.sim, target=target, pinned=tuple(pinned))
    plan = st.session_state.get("last_plan")
    if plan:
        if plan.get("aktarimlar"):
            rows = _rows()
            by = {r["alan"]: r for r in rows}
            tgt = plan["aktarimlar"][0]["target"]
            st.dataframe(pd.DataFrame([{"Personel": m["staff_id"], "Kaynak": S.AREA_NAMES[m["source"]], "Hedef": S.AREA_NAMES[m["target"]]}
                                       for m in plan["aktarimlar"]]), hide_index=True)  # fmt: skip
            st.markdown(f"**Gerekçe:** {plan['gerekce']}")
            st.markdown(f"**Kapasite:** {S.AREA_NAMES[tgt]} aktif {by[tgt]['aktif_personel']} → beklenen {by[tgt]['aktif_personel'] + len(plan['aktarimlar'])} "
                        f"(gereken {by[tgt]['gereken_personel']})")  # fmt: skip
            if plan["dogrulama"]:
                st.error("Doğrulayıcı planı reddetti: " + ", ".join(plan["dogrulama"]))
            else:
                st.success("Doğrulayıcı: rol, yetkinlik, vardiya, aktif görev, asgari kapasite ve tek atama kuralları sağlandı.")
                if st.button("📝 Öneriyi onaya gönder", key="ps_oneri"):
                    res = T.create_transfer_proposal(st.session_state.svc, plan, "yonetici_ekrani")
                    st.success(f"Öneri {res['oneri']['id']} yönetici onayına gönderildi.")
        else:
            st.warning(plan["gerekce"])


def page_ai() -> None:
    st.markdown("### MedAkis AI")
    t1, t2 = st.tabs(["Operasyon asistanı", "Kaynaklı bilgi asistanı (deneysel)"])
    with t1:
        st.caption("Kural tabanlı araç yönlendirmeli demo asistanı: bir dil modeli değildir; yanıtlar yalnız çağrılan araçların çıktılarından "
                   "oluşur. Asistan öneri oluşturabilir, onay veremez.")  # fmt: skip
        examples = ["Acil servis neden yoğun?", "Hangi servisten personel desteği alabiliriz?", "Acil servise uygun iki personel öner",
                    "Personel aktarımının risklerini açıkla", "Onay durumu nedir?"]  # fmt: skip
        cols = st.columns(len(examples))
        clicked = None
        for c, e in zip(cols, examples):
            if c.button(e, key=f"ornek_{e}", width="stretch"):
                clicked = e
        msg = st.chat_input("MedAkis'e bir soru sorun") or clicked
        if msg:
            r = answer(msg, st.session_state.sim, st.session_state.svc)
            st.session_state.chat.append((msg, r))
        for q, r in reversed(st.session_state.chat[-6:]):
            with st.chat_message("user"):
                st.write(q)
            with st.chat_message("assistant"):
                st.markdown(r.text)
                st.caption("Çağrılan araçlar: " + ", ".join(r.tools))
    with t2:
        st.caption("Anahtar kelime (BM25) tabanlı kaynak arama: embedding ya da dil modeli kullanılmaz. Kaynak bulunamazsa cevap üretilmez. "
                   "BGE-M3 + reranker + Gemma hattı ayrı geliştirme kipindedir ve kabul kriteri henüz karşılanmadığı için kullanıcıya kapalıdır.")  # fmt: skip
        q = st.text_input("Soru", value="Katman B'de rezerv nasıl hesaplanır?", key="rag_soru")
        if st.button("🔎 Kaynaklarda ara", key="rag_ara"):
            res = search_sources(q)
            if not res["bulundu"]:
                st.warning(res["mesaj"])
            for k in res["kaynaklar"]:
                st.markdown(f"**{k['belge']}** · `{k['kimlik']}` sürüm {k['surum']} · bölüm: {k['bolum']} · skor {k['skor']}")
                st.markdown(f"> {k['alinti']}")


def page_approvals() -> None:
    st.markdown("### Öneriler ve Onaylar")
    svc = st.session_state.svc
    props = list(svc.proposals().values())
    if not props:
        st.info("Henüz görevlendirme önerisi yok. Kontrol Merkezi, Personel Yönetimi ya da MedAkis AI üzerinden oluşturabilirsiniz.")
    for p in reversed(props):
        with st.container(border=True):
            plan = p["plan"]
            st.markdown(f"**{p['id']}** · hedef **{S.AREA_NAMES[plan['hedef']]}** · durum: **{p['durum']}** · kaynak: {p['kaynak']}")
            st.markdown("\n".join(f"- {m['staff_id']}: {S.AREA_NAMES[m['source']]} → {S.AREA_NAMES[m['target']]}" for m in plan["aktarimlar"]))
            st.caption(f"Gerekçe: {plan['gerekce']} · Plan SHA-256: {p['plan_sha256'][:16]}… · Riskler: kaynak alan asgari sınıra yaklaşır; sentetik veri.")
            if p["durum"] == "beklemede":
                c1, c2, c3 = st.columns([1, 1, 2])
                reason = c3.text_input("Ret gerekçesi", key=f"gerekce_{p['id']}")
                if c1.button("✅ Onayla", key=f"onay_{p['id']}"):
                    ok, m = svc.decide(p["id"], p["plan_sha256"], True, USER)
                    (st.success if ok else st.error)(m)
                    st.rerun()
                if c2.button("✖ Reddet", key=f"ret_{p['id']}"):
                    ok, m = svc.decide(p["id"], p["plan_sha256"], False, USER, reason)
                    (st.success if ok else st.error)(m)
            elif p["durum"] == "onaylandi":
                if st.button("🔔 Bildirimi hazırla", key=f"bildir_{p['id']}"):
                    r = T.prepare_approved_notification(svc, p["id"])
                    {"gonderildi": st.success, "dry_run": st.info}.get(r["durum"], st.warning)(f"{r['durum']}: {r['ayrinti']}")
                    if r["mesaj"]:
                        st.code(r["mesaj"], language="text")
    with st.expander("Vardiya planı (CP-SAT) incelemesi ve onayı — makine öğrenmesi tahmini + Katman A/B"):
        from medakis.ui import review_panel

        review_panel.render()


def page_notifications() -> None:
    st.markdown("### Bildirimler")
    n = st.session_state.svc.notifier
    st.markdown(theme.alert("Kip: CANLI (tek demo sohbeti)" if n.config.live_ready else
                            "Kip: DRY-RUN — ağ isteği yapılmaz; Telegram'a gönderim yapılmadı. Gerçek gönderim için ortam değişkenleri gerekir.",
                            "uyari"), unsafe_allow_html=True)  # fmt: skip
    ev = n.journal.events()
    st.dataframe(pd.DataFrame([{k: e.get(k) for k in ("sira", "zaman", "kip", "durum", "oneri_id", "ayrinti")} for e in ev]) if ev else
                 pd.DataFrame({"bilgi": ["Henüz bildirim yok."]}), hide_index=True, width="stretch")  # fmt: skip


def page_audit() -> None:
    st.markdown("### Denetim ve Raporlar")
    ev, issues = st.session_state.svc.audit()
    (st.error("Denetim zinciri bozuk: " + ", ".join(issues)) if issues else st.success(f"Özet zinciri doğrulandı ({len(ev)} olay)."))
    if ev:
        st.dataframe(pd.DataFrame([{"sıra": e["sira"], "zaman": e["zaman"], "olay": e["olay"], "öneri": e.get("oneri_id"), "aktör": e.get("aktor", "—"),
                                    "özet": e["olay_ozeti"][:12] + "…"} for e in ev]), hide_index=True, width="stretch")  # fmt: skip
        st.download_button("⬇ Denetim kaydını indir (JSON)", json.dumps(ev, ensure_ascii=False, indent=1), file_name="medakis_denetim.json")


def page_settings() -> None:
    st.markdown("### Ayarlar")
    n = st.session_state.svc.notifier
    st.markdown(f"""
- **Çalışma modu:** Sentetik Demo (veritabanı ve yerel dil modeli gerektirmez).
- **Telegram:** `{n.config.mode}` · gerçek gönderim için `MEDAKIS_TELEGRAM_MODE=live`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` ortam değişkenleri.
- **Kaynaklı RAG (BGE-M3 + reranker + Gemma):** geliştirme kipi; kabul kriteri henüz karşılanmadı, kullanıcıya kapalı.
- **Veri:** tamamı sentetik; gerçek hasta ya da personel verisi yok. Gerçek hastanede doğrulanmamıştır.
- **Klasik inceleme ekranı:** `.\\.venv\\Scripts\\python.exe -m streamlit run app.py -- --demo`
""")


def main() -> None:
    st.set_page_config(page_title="MedAkis", page_icon="🩺", layout="wide")
    st.markdown(theme.CSS, unsafe_allow_html=True)
    _state()
    st.markdown(theme.header("Sentetik Demo", USER), unsafe_allow_html=True)
    with st.sidebar:
        st.markdown(theme.LOGO + " <b style='font-size:18px;color:#F8FAFC'>MedAkis</b>", unsafe_allow_html=True)
        page = st.radio("Menü", PAGES, label_visibility="collapsed", key="sayfa")
        st.caption("● Simülasyon çalışıyor" if st.session_state.running else "○ Simülasyon durdu")
    {PAGES[0]: page_control, PAGES[1]: page_density, PAGES[2]: page_forecast, PAGES[3]: page_staff, PAGES[4]: page_ai,
     PAGES[5]: page_approvals, PAGES[6]: page_notifications, PAGES[7]: page_audit, PAGES[8]: page_settings}[page]()  # fmt: skip
