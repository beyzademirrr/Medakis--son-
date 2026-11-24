"""Yönetim ekranları: Öneriler ve Onaylar, Emirler ve Bildirimler, Talepler, Denetim ve Raporlar, Ayarlar.

Onay, ret ve geri alma mevcut insan onayı servisinden geçer (yetki, plan SHA-256, güncellik). Onay gerçek hastane ataması
değildir; etkisi yalnız dijital ikizde simüle edilir. Bildirim yalnız onaylı ve özeti eşleşen öneri için hazırlanır.
"""

from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

from medakis.agent import llm
from medakis.notifications.telegram import DRY, SENT, TelegramConfig, build_message
from medakis.retrieval_demo import search_sources
from medakis.twin.engine import Twin
from medakis.twin.hub import MANAGER_ACTOR, data_dir
from medakis.ui import common as C
from medakis.ui import state as S
from medakis.ui import theme

STATE_TR = {"beklemede": "Beklemede", "onaylandi": "Onaylandı", "reddedildi": "Reddedildi", "iptal_edildi": "İptal", "geri_alindi": "Geri alındı"}
STATE_KIND = {"beklemede": "warn", "onaylandi": "ok", "reddedildi": "crit", "iptal_edildi": "", "geri_alindi": "crit"}


def _items(pid: str) -> list[dict]:
    sess, names = S.ss().sess, S.unit_names()
    snap = sess.review.proposal(pid)["anlik_goruntu"]
    return [{"Tür": "Taşıma" if k["tur"] == "tasima" else "Kilitli yerleşim", "Personel": S.staff_code(k["personel"]),
             "Kaynak": names.get(k.get("kaynak_servis"), "-"), "Hedef": names.get(k["hedef_servis"], k["hedef_servis"]),
             "Saat": f"{S.local_hm(str(k['baslangic']))}–{S.local_hm(str(k['bitis']))}" if k.get("baslangic") else "-"} for k in snap["kalemler"]]  # fmt: skip


def page_approvals() -> None:
    C.top("Öneriler ve Onaylar", "İnsan onayı: plan SHA-256 eşleşmesi, yetki ve güncellik kontrolü · onay gerçek atama değildir")
    C.clock_controls("onay")
    s, snap = S.ss(), S.snapshot()
    sess = s.sess
    if sess.review is None:
        C.no_model()
        return
    props = sess.proposals()
    k = st.columns(4)
    for col, (lab, st_) in zip(k, (("Beklemede", "beklemede"), ("Onaylandı", "onaylandi"), ("Reddedildi", "reddedildi"), ("Geri alındı", "geri_alindi"))):
        col.markdown(theme.kpi(lab, sum(p["durum"] == st_ for p in props), "öneri", theme.LEVEL_COLOR["sari"] if st_ == "beklemede" else theme.ACCENT), unsafe_allow_html=True)
    st.write("")
    if not props:
        st.info("Kayıtlı öneri yok. Optimizasyon ekranından ya da agent ile doğrulanmış planı onaya gönderin.")
        return
    st.dataframe(pd.DataFrame([{"Öneri": p["kimlik"], "Karar anı": S.engine().data.local(pd.Timestamp(p["karar_ani"]).to_pydatetime()).strftime("%d.%m %H:%M"),
                                "Durum": STATE_TR.get(p["durum"], p["durum"]), "Kalem": p["kalem"], "Plan SHA-256": p["plan_sha256"][:16] + "…",
                                "Son karar": p["son_karar_aktor"] or "-"} for p in props]), hide_index=True, width="stretch")  # fmt: skip
    pid = st.selectbox("Öneri seç", [p["kimlik"] for p in props], index=len(props) - 1, key="ap_pid")
    p = next(x for x in props if x["kimlik"] == pid)
    left, right = st.columns([1.4, 1])
    with left:
        st.markdown(f"#### Öneri {pid} {theme.chip(STATE_TR.get(p['durum'], p['durum']), STATE_KIND.get(p['durum'], ''))}", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(_items(pid)), hide_index=True, width="stretch")
        st.code(f"plan_sha256 = {p['plan_sha256']}", language=None)
        at = pd.Timestamp(p["karar_ani"]).to_pydatetime()
        opt = sess.runs.get(pid)
        if opt:
            names = S.unit_names()
            g = Twin.gaps(opt)
            rows = {r["servis_id"]: r for r in S.engine().twin.forecast(at) or [] if r["rol"] == "nurse"}
            st.markdown("##### What-if: onay öncesi ve sonrası (hemşire açığı, personel-dk)")
            st.dataframe(pd.DataFrame([{"Birim": names[u], "Mevcut açık": round(rows[u]["acik_personel_dk"] or 0) if u in rows else None,
                                        "Plan sonrası açık": round(v["acik_dk"] or 0)} for u, v in g.items()]), hide_index=True, width="stretch")  # fmt: skip
    with right:
        st.markdown("#### Karar")
        st.caption(f"Karar veren oturum: {MANAGER_ACTOR} (prototip yetki politikası). Agent bu işlemleri yapamaz.")
        presented = p["plan_sha256"]
        if p["durum"] == "beklemede":
            if st.button("✅ Onayla", type="primary", width="stretch"):
                d = sess.decide("onayla", pid, presented)
                if d.ok:
                    st.toast("Onaylandı: etki dijital ikizde simüle edilir; gerçek atama yok.")
                    st.rerun()
                st.error("Onaylanmadı: " + ", ".join(d.codes))
            reason = st.text_input("Ret / alternatif gerekçesi", key="ap_reason")
            c1, c2 = st.columns(2)
            if c1.button("✖ Reddet", width="stretch", disabled=not reason.strip()):
                d = sess.decide("reddet", pid, presented, reason)
                (st.success if d.ok else st.error)("Reddedildi." if d.ok else ", ".join(d.codes))
            if c2.button("↻ Alternatif iste", width="stretch", disabled=not reason.strip()):
                d = sess.decide("reddet", pid, presented, "Alternatif istendi: " + reason)
                if d.ok:
                    r = sess.create_request({"tur": "alternatif_oneri", "oneri": pid, "gerekce": reason, "kaynak": "yonetici"})
                    st.success(f"Öneri reddedildi ve alternatif talebi {r['talep']} açıldı.")
                else:
                    st.error(", ".join(d.codes))
        elif p["durum"] == "onaylandi":
            ok, codes = sess.review.applicability(pid)
            st.markdown(theme.alert("Plan hâlâ geçerli." if ok else "Yeniden inceleme gerekli: " + ", ".join(codes), "normal" if ok else "turuncu"), unsafe_allow_html=True)
            why = st.text_input("Geri alma gerekçesi", key="ap_rev")
            if st.button("Onayı geri al", disabled=not why.strip()):
                d = sess.decide("geri_al", pid, presented, why)
                (st.success if d.ok else st.error)("Geri alındı." if d.ok else ", ".join(d.codes))
            if st.button("🔔 Emirler ve Bildirimler", width="stretch"):
                st.switch_page(S.PAGES["emir"])
        st.caption("Onay saati sentetik takvimde yalnız ileri gider; karar anından 30 dk sonra onay eskir (prototip politikası).")


def page_orders() -> None:
    C.top("Emirler ve Bildirimler", "Öneri → doğrulama → inceleme → açık onay → bildirim → denetim · varsayılan dry-run")
    s = S.ss()
    sess = s.sess
    cfg = TelegramConfig.from_env()
    k = st.columns(3)
    k[0].markdown(theme.kpi("Simülasyon kanalı", "Etkin", "dijital ikizde etki", theme.OK), unsafe_allow_html=True)
    k[1].markdown(theme.kpi("Telegram", "Canlı" if cfg.live_ready else "Dry-run", "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ortam değişkeni" if cfg.live_ready
                            else "ağ isteği yapılmaz; yeni token girilene kadar", theme.CRIT if cfg.live_ready else theme.WARN), unsafe_allow_html=True)  # fmt: skip
    k[2].markdown(theme.kpi("n8n", "Geliştiriliyor", "Prototip · bağlantı yok", theme.PURPLE), unsafe_allow_html=True)
    st.write("")
    if sess.review is None:
        C.no_model()
        return
    approved = [p for p in sess.proposals() if p["durum"] == "onaylandi"]
    events = sess.notifier.journal.events()
    if not approved:
        st.info("Onaylı öneri yok. Bildirim yalnız yönetici onayından sonra hazırlanabilir.")
    for p in approved:
        pid = p["kimlik"]
        prop = sess.review.proposal(pid)
        mine = [e for e in events if e.get("oneri_id") == pid]
        last = mine[-1]["durum"] if mine else "hazırlanmadı"
        with st.container(border=True):
            st.markdown(f"**Öneri {pid}** {theme.chip('Onaylandı', 'ok')} {theme.chip('Bildirim: ' + last, 'info' if last == DRY else 'ok' if last == SENT else '')}",
                        unsafe_allow_html=True)  # fmt: skip
            a, b = st.columns([1.2, 1])
            with a:
                st.dataframe(pd.DataFrame(_items(pid)), hide_index=True, width="stretch")
                approver = next((d for d in reversed(prop["kararlar"]) if d["karar"] == "onaylandi"), {})
                checks = [("Öneri onaylı (yönetici kararı kayıtlı)", True), ("Plan SHA-256 onay kaydından sunulur; uyuşmazsa bildirim reddedilir", True),
                          ("Mükerrer bildirim yok", not any(e.get("durum") in (DRY, SENT) for e in mine))]  # fmt: skip
                st.caption(f"Onaylayan: {approver.get('aktor', '-')} · {approver.get('zaman', '-')} · Kanal: {'Telegram (canlı, tek demo sohbeti)' if cfg.live_ready else 'Telegram dry-run'}")
                for name, ok in checks:
                    st.markdown(theme.alert(f"{'✔' if ok else '✖'} {name}", "normal" if ok else "turuncu"), unsafe_allow_html=True)
            with b:
                st.markdown("Mesaj önizlemesi")
                st.code(build_message({**prop, "id": pid}, sess.proposal_lines(pid)), language=None, wrap_lines=True)
                if st.button("Onaylı bildirimi hazırla / gönder", key=f"nt_{pid}", type="primary", width="stretch"):
                    r = sess.notify(pid, p["plan_sha256"])
                    (st.success if r.status in (DRY, SENT) else st.warning)(r.detail)
                st.caption("Tek demo sohbeti, bütün personele bildirim gönderildiği anlamına gelmez. Gerçek gönderim olmadıysa gönderildi denmez.")
    st.markdown("#### Bildirim izi")
    st.dataframe(pd.DataFrame([{k: e.get(k) for k in ("sira", "zaman", "kanal", "kip", "durum", "oneri_id", "ayrinti")} for e in events]), hide_index=True, width="stretch")


def page_requests() -> None:
    C.top("Talepler", "Ek personel ve geçici görevlendirme talepleri · agent yalnız taslak oluşturur · izleme")
    s = S.ss()
    sess, names = s.sess, S.unit_names()
    with st.form("talep"):
        c1, c2, c3 = st.columns([1, 1, 2])
        tur = c1.selectbox("Tür", ("ek_personel", "gecici_gorevlendirme"), format_func={"ek_personel": "Ek personel", "gecici_gorevlendirme": "Geçici görevlendirme"}.get)
        unit = c2.selectbox("Birim", list(names), format_func=names.get, index=2)
        why = c3.text_input("Gerekçe")
        if st.form_submit_button("Talep oluştur") and why.strip():
            r = sess.create_request({"tur": tur, "birim": unit, "gerekce": why, "kaynak": "yonetici_ekrani"})
            st.success(f"Talep {r['talep']} oluşturuldu.")
    reqs = sess.request_list()
    st.dataframe(pd.DataFrame([{"Talep": r["talep"], "Tür": r["tur"], "Birim": names.get(r.get("birim"), "-"), "Personel": r.get("personel", "-"),
                                "Kaynak": r.get("kaynak"), "Durum": r["durum"], "Gerekçe": r.get("gerekce"), "Karar notu": r.get("karar_notu", "-")}
                               for r in reqs]), hide_index=True, width="stretch")  # fmt: skip
    pend = [r["talep"] for r in reqs if r["durum"] == "beklemede"]
    if pend:
        c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
        rid = c1.selectbox("Talep", pend)
        note = c2.text_input("Karar notu", key="rq_note")
        if c3.button("Onayla", width="stretch", disabled=not note.strip()):
            sess.decide_request(rid, "onaylandi", note)
            st.rerun()
        if c4.button("Reddet", width="stretch", disabled=not note.strip()):
            sess.decide_request(rid, "reddedildi", note)
            st.rerun()
        st.caption("Talep onayı gerçek personel ataması değildir; yalnız karar kaydıdır.")
    st.markdown("#### İzleme")
    ops = sess.ops.events()
    calls = [c for e in ops if e["olay"] == "agent_cagri" for c in e.get("araclar") or []]
    k = st.columns(4)
    k[0].markdown(theme.kpi("Agent sorusu", sum(e["olay"] == "agent_cagri" for e in ops), "bu oturum", theme.ACCENT), unsafe_allow_html=True)
    k[1].markdown(theme.kpi("Araç çağrısı", len(calls), f"ort. {sum(c['sure_ms'] for c in calls) / len(calls):.0f} ms" if calls else "—", theme.BLUE), unsafe_allow_html=True)
    k[2].markdown(theme.kpi("Optimizasyon", sum(e["olay"] == "optimizasyon" for e in ops), "kayıtlı çalıştırma", theme.PURPLE), unsafe_allow_html=True)
    k[3].markdown(theme.kpi("Yoğunluk olayı", sum(e["olay"] == "yogunluk_olayi" for e in ops), "kritik eşik", theme.CRIT), unsafe_allow_html=True)
    if calls:
        frame = pd.DataFrame(calls).groupby("arac", as_index=False).agg(cagri=("sure_ms", "size"), ort_ms=("sure_ms", "mean"), en_cok_ms=("sure_ms", "max"))
        st.dataframe(frame.rename(columns={"arac": "Araç", "cagri": "Çağrı", "ort_ms": "Ort. ms", "en_cok_ms": "En çok ms"}), hide_index=True, width="stretch")
    st.caption("Metrikler yalnız bu oturumun gerçek işlem izinden hesaplanır.")


def page_audit() -> None:
    C.top("Denetim ve Raporlar", "Özet zincirli denetim izleri · açıklanabilirlik · dışa aktarma")
    s = S.ss()
    sess = s.sess
    rows, issues = sess.audit()
    (st.success if not issues else st.error)("Bütün izlerde özet zinciri doğrulandı." if not issues else "Zincir sorunu: " + ", ".join(issues[:5]))
    kinds = sorted({str(r.get("olay") or r.get("eylem")) for r in rows})
    pick = st.multiselect("Olay türü", kinds, key="au_kind")
    show = [r for r in rows if not pick or str(r.get("olay") or r.get("eylem")) in pick]
    st.dataframe(pd.DataFrame([{"İz": r["iz"], "Sıra": r.get("sira"), "Zaman": r.get("zaman"), "Olay": r.get("olay") or r.get("eylem"),
                                "Ayrıntı": json.dumps({k: v for k, v in r.items() if k not in ("iz", "sira", "zaman", "olay", "eylem", "onceki_ozet", "olay_ozeti")},
                                                      ensure_ascii=False, default=str)[:180], "Özet": str(r.get("olay_ozeti", ""))[:12]} for r in show]),
                 hide_index=True, width="stretch", height=360)  # fmt: skip
    st.download_button("Denetim izini indir (JSON)", json.dumps(rows, ensure_ascii=False, default=str, indent=1).encode("utf-8"), "medakis_denetim.json", "application/json")
    st.markdown("#### Açıklanabilirlik")
    props = sess.proposals()
    if not props:
        st.caption("Açıklanacak öneri yok.")
        return
    pid = st.selectbox("Öneri", [p["kimlik"] for p in props], index=len(props) - 1, key="au_pid")
    snapshot = sess.review.proposal(pid)["anlik_goruntu"]
    at = pd.Timestamp(snapshot["karar_ani"]).to_pydatetime()
    eng, names = S.engine(), S.unit_names()
    rows_f = eng.twin.forecast(at) or []
    items = [k for k in snapshot["kalemler"] if k["tur"] == "tasima"]
    a, b = st.columns(2)
    with a:
        st.markdown("**Kullanılan veri**")
        st.markdown(f"- Karar anı: {eng.data.local(at):%d.%m.%Y %H:%M} · pencere 60 dk\n- Model: {', '.join(sorted({str(r.get('model_surumu')) for r in rows_f if r.get('model_surumu')}))}\n"
                    f"- Plan özeti: `{next(p['plan_sha256'] for p in props if p['kimlik'] == pid)[:24]}…`\n- Kaynak: sentetik veri üreticisi 2.1")  # fmt: skip
        st.markdown("**Uygulanan kurallar**")
        st.markdown("- Rol ve geçerlilik tarihli yetkinlik\n- Vardiya, mola ve izin takvimi\n- Asgari personel (kaynak birim dahil)\n"
                    "- Birimler arası destek kuralı\n- Onaylı kilitler\n- Bağımsız doğrulayıcı: geçti (kayıt koşulu)")  # fmt: skip
    with b:
        st.markdown("**Fayda ve risk**")
        g = {r["servis_id"]: r for r in rows_f if r["rol"] == "nurse"}
        for k in items:
            t, src = k["hedef_servis"], k.get("kaynak_servis")
            st.markdown(f"- {S.staff_code(k['personel'])}: {names.get(src)} → {names.get(t)}; hedefte plansız açık {round(g.get(t, {}).get('acik_personel_dk') or 0)} personel-dk; "
                        f"kaynakta asgari kişi doğrulayıcıda korunur.")  # fmt: skip
        st.markdown("**Kaynak belgeler**")
        ev = search_sources("birimler arası destek yetkinlik")
        for d in ev["kaynaklar"][:2]:
            st.caption(f"{d['belge']} · {d['kimlik']} v{d['surum']} · {d['bolum']} (skor {d['skor']})")
    if items:
        t = items[0]["hedef_servis"]
        if t in (1, 2, 3):
            st.markdown(f"**Elenen alternatifler ({names[t]})**")
            cands = [c for c in eng.twin.candidates(at, t, {k["personel"] for k in items}) if not c["uygun_veri"]][:10]
            st.dataframe(C.df(cands, {"kod": "Personel", "birim": "Birim", "gerekce": "Elenme gerekçesi"}), hide_index=True, width="stretch")


def page_settings() -> None:
    C.top("Ayarlar", "Eşikler, kanallar, model durumu ve oturum")
    s, eng = S.ss(), S.engine()
    th = eng.twin.thresholds
    st.markdown("#### Yoğunluk eşikleri (kamera doluluk oranı)")
    c = st.columns(3)
    new = {"sari": c[0].slider("Sarı", 0.3, 0.9, float(th["sari"]), 0.05), "turuncu": c[1].slider("Turuncu", 0.5, 1.0, float(th["turuncu"]), 0.05),
           "kirmizi": c[2].slider("Kırmızı", 0.7, 1.5, float(th["kirmizi"]), 0.05)}  # fmt: skip
    if new["sari"] < new["turuncu"] < new["kirmizi"] and new != th:
        th.update(new)
        s.pop("snap_key", None)
        s.sess.log("esik_degisti", esikler=new)
        st.rerun()
    st.caption("Eşikler bu uygulama sürecinde geçerlidir; kalıcı varsayılanlar config/dijital_ikiz.json dosyasındadır.")
    s.auto_draft = st.toggle("Kritik yoğunlukta agent otomatik taslak hazırlasın", value=s.auto_draft)
    st.markdown("#### Durum")
    cfg = TelegramConfig.from_env()
    st_llm = llm.status()
    st.dataframe(pd.DataFrame([
        {"Bileşen": "Veri kaynağı", "Durum": "Sentetik Demo (üretici 2.1, veritabanı gerekmez)"},
        {"Bileşen": "PostgreSQL kipi", "Durum": "Doğrulanmış şema ve depolar mevcut; bu arayüz varsayılan olarak sentetik demo kipindedir"},
        {"Bileşen": "Tahmin + CP-SAT", "Durum": "Hazır" if eng.scenario else f"Kapalı: {eng.scenario_error}"},
        {"Bileşen": "Dil modeli", "Durum": st_llm.detail},
        {"Bileşen": "Telegram", "Durum": "canlı (tek demo sohbeti)" if cfg.live_ready else f"dry-run (kip={cfg.mode}; token {'tanımlı' if cfg.token else 'yok'}, sohbet {'tanımlı' if cfg.chat_id else 'yok'})"},
        {"Bileşen": "Kayıt dizini", "Durum": "MEDAKIS_DATA_DIR" if os.environ.get("MEDAKIS_DATA_DIR") else f"varsayılan ({data_dir().name}/izler, git dışında)"},
    ]), hide_index=True, width="stretch")  # fmt: skip
    st.markdown("#### Tema")
    st.caption("Koyu (varsayılan) ve açık tema: sağ üstteki ⋮ menüsü → Settings → Theme. Grafikler ve kartlar seçilen temaya uyar.")
    st.markdown("#### Oturum")
    if st.button("Simülasyonu ve oturum kayıtlarını sıfırla"):
        S.reset()
        st.rerun()
