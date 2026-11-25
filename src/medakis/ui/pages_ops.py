"""Operasyon ekranları: Kontrol Merkezi ve Dijital Hastane İkizi (senaryo, what-if, onaylı etkinin simülasyonu)."""

from __future__ import annotations

from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

from medakis.twin import whatif as W
from medakis.twin.engine import LEVEL_TR
from medakis.ui import common as C
from medakis.ui import state as S
from medakis.ui import theme


def _kpis(snap: dict) -> list[tuple[str, str, str, str]]:
    eng, s = S.engine(), S.ss()
    dens, staff, rows = snap["dens"], snap["staff"], snap["rows"]
    total, cap = sum(d["tespit"] for d in dens), sum(d["kapasite"] for d in dens)
    crit = [d for d in dens if d["seviye"] in ("turuncu", "kirmizi")]
    at = snap["at"]
    pending_tasks = sum(len(eng.data.tasks_window(u, at, at + timedelta(minutes=60))) for u in eng.data.units)
    gap = sum(r["eksik_hemsire"] for r in rows or [] if r["rol"] == "nurse") if rows else None
    gap_units = {r["servis_id"] for r in rows or [] if r["rol"] == "nurse" and r["eksik_hemsire"] > 0}
    reserve = [c for c in eng.data.staff_on_duty(at) if c["rol"] == "nurse" and not c["izinli"] and c["kullanilabilir_dk"] >= 60
               and c["birim"] not in gap_units]  # fmt: skip
    pend = sum(p["durum"] == "beklemede" for p in s.sess.proposals())
    return [("Aktif personel", str(sum(v["toplam"] for v in staff.values())), f"{sum(v['molada'] for v in staff.values())} molada · {sum(v['izinli'] for v in staff.values())} izinli", theme.BLUE),
            ("Hastane yoğunluğu", f"%{round(100 * total / cap)}", f"{total}/{cap} kişi (kamera tespiti)", theme.ACCENT),
            ("Kritik birim", str(len(crit)), ", ".join(d["ad"] for d in crit) or "yok", theme.CRIT if crit else theme.OK),
            ("Bekleyen görev (60 dk)", str(pending_tasks), "son tarihi pencerede", theme.WARN),
            ("Eksik hemşire (60 dk)", "—" if gap is None else str(gap), "tahmin + tampon · birim bazında", theme.CRIT if gap else theme.OK),
            ("Uygun rezerv", str(len(reserve)), "açığı olmayan birimlerde tam pencere hemşire", theme.ACCENT),
            ("Onay bekleyen", str(pend), "öneri", theme.PURPLE),
            ("Aktif uyarı", str(len(snap["events"])), "yoğunluk + erken uyarı", theme.ORANGE)]  # fmt: skip


def _todo(snap: dict) -> list[tuple[str, str]]:
    s = S.ss()
    out = []
    reds = [e for e in snap["events"] if e["seviye"] == "kirmizi"]
    for e in reds:
        out.append((f"{e['metin']} — yoğunluk kritik.", "Optimizasyon ve Yönlendirme ekranında yoğunluğa duyarlı planı inceleyin."))
    warn = [e for e in snap["events"] if e["tur"].startswith("Erken")]
    if warn:
        out.append((f"{len(warn)} birimde 60 dk içinde hemşire açığı öngörülüyor.", "; ".join(e["metin"] for e in warn[:3])))
    props = s.sess.proposals()
    pend = [p for p in props if p["durum"] == "beklemede"]
    if pend:
        out.append((f"{len(pend)} öneri yönetici onayı bekliyor.", "Öneriler ve Onaylar ekranında plan özetini kontrol edip karar verin."))
    approved = [p for p in props if p["durum"] == "onaylandi"]
    sent = {e.get("oneri_id") for e in s.sess.notifier.journal.events()}
    for p in approved:
        if p["kimlik"] not in sent:
            out.append((f"Onaylı öneri {p['kimlik'][:10]}… için bildirim hazırlanmadı.", "Emirler ve Bildirimler ekranından bildirimi hazırlayın."))
    if not out:
        out.append(("Kritik durum yok.", "Saat ilerledikçe kameralar, tahmin ve olaylar güncellenir."))
    return out


def page_control() -> None:
    C.top("Kontrol Merkezi", "Yoğunluğu algıla · iş yükünü öngör · personeli optimize et · kararları insan denetiminde yönet")
    C.clock_controls("kontrol")

    def body() -> None:
        snap = S.snapshot()
        cards = _kpis(snap)
        for row in (cards[:4], cards[4:]):
            cols = st.columns(4)
            for col, (k, v, sub, c) in zip(cols, row):
                col.markdown(theme.kpi(k, v, sub, c), unsafe_allow_html=True)
        st.write("")
        left, right = st.columns([1.35, 1])
        with left:
            st.markdown("#### Kat planı yoğunluk haritası")
            st.markdown(theme.floor_plan(snap["dens"], S.engine().data.units, S.engine().twin.thresholds), unsafe_allow_html=True)
            st.caption("Kaynak: sentetik kamera + bağlı bileşen sayımı (gerçek görüntü ya da doğrulanmış insan tespiti değildir).")
        with right:
            st.markdown("#### Şimdi ne yapmalıyım?")
            st.markdown("".join(theme.step(i + 1, t, sm) for i, (t, sm) in enumerate(_todo(snap))), unsafe_allow_html=True)
            st.markdown("#### Son olaylar")
            for e in snap["events"][:6]:
                st.markdown(theme.alert(f"{e['tur']}: {e['metin']}", e["seviye"]), unsafe_allow_html=True)
            if not snap["events"]:
                st.markdown(theme.alert("Olay yok; bütün alanlar eşik altında.", "normal"), unsafe_allow_html=True)

    C.live(body)
    snap = S.snapshot()
    a, b = st.columns(2)
    with a:
        st.markdown("#### Alan doluluğu (kamera) ve kapasite")
        d = pd.DataFrame([{"Birim": x["ad"], "Ölçü": "Tespit edilen kişi", "Değer": x["tespit"]} for x in snap["dens"]]
                         + [{"Birim": x["ad"], "Ölçü": "Alan kapasitesi", "Değer": x["kapasite"]} for x in snap["dens"]])  # fmt: skip
        st.altair_chart(alt.Chart(d).mark_bar().encode(x=alt.X("Birim:N", title=None), xOffset="Ölçü:N", y="Değer:Q",
                        color=alt.Color("Ölçü:N", scale=alt.Scale(domain=["Tespit edilen kişi", "Alan kapasitesi"], range=[theme.ACCENT, "#64748B"]))).properties(height=230), width="stretch")  # fmt: skip
    with b:
        st.markdown("#### İş yükü ve etkin kapasite (hemşire, 60 dk)")
        if snap["rows"] is None:
            C.no_model()
        else:
            names = S.unit_names()
            d = pd.DataFrame([{"Birim": names[r["servis_id"]], "Ölçü": "Tamponlu talep (dk)", "Değer": r["tamponlu_talep_dk"]} for r in snap["rows"] if r["rol"] == "nurse"]
                             + [{"Birim": names[r["servis_id"]], "Ölçü": "Etkin kapasite (dk)", "Değer": r["etkin_kapasite_dk"]} for r in snap["rows"] if r["rol"] == "nurse"])  # fmt: skip
            st.altair_chart(alt.Chart(d).mark_bar().encode(x=alt.X("Birim:N", title=None), xOffset="Ölçü:N", y="Değer:Q",
                            color=alt.Color("Ölçü:N", scale=alt.Scale(domain=["Tamponlu talep (dk)", "Etkin kapasite (dk)"], range=[theme.WARN, theme.BLUE]))).properties(height=230), width="stretch")  # fmt: skip
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Gün içi iş yükü (görev dakikası / etkin kapasite)")
        day = S.engine().data.local(snap["at"]).date()
        hm = pd.DataFrame(S.engine().twin.hourly(day, "nurse"))
        names = S.unit_names()
        hm["Birim"] = hm["birim"].map(names)
        now_h = S.engine().data.local(snap["at"]).hour
        line = alt.Chart(hm).mark_line(point=True).encode(x=alt.X("saat:Q", title="Yerel saat"), y=alt.Y("oran:Q", title="Talep / etkin kapasite"),
                                                         color="Birim:N", tooltip=["Birim", "saat", "talep_dk", "etkin_kapasite_dk", "oran"])  # fmt: skip
        rule = alt.Chart(pd.DataFrame({"saat": [now_h]})).mark_rule(color=theme.CRIT, strokeDash=[4, 3]).encode(x="saat:Q")
        one = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(color="#64748B").encode(y="y:Q")
        st.altair_chart((line + rule + one).properties(height=240), width="stretch")
        st.caption("Gerçekleşen sentetik görev dakikaları ve vardiya kapasitesi (kullanım oranı 0,85). Kırmızı çizgi: karar anı.")
    with c2:
        st.markdown("#### Yapay zekâ önerisi ve bekleyen yönlendirmeler")
        opt = S.engine().twin.optimize(snap["at"], snap["signal"]) if snap["rows"] is not None else None
        if opt is None:
            C.no_model()
        else:
            res = opt["sonuc"]
            if res.get("oneri_yayinlanabilir"):
                for t in res.get("tasimalar") or []:
                    st.markdown(f"**{S.staff_code(t['personel'])}** · {names[t['ev_servis']]} → **{names[t['hedef_servis']]}** · {S.local_hm(t['baslangic'])}–{S.local_hm(t['bitis'])}")
                st.caption(f"CP-SAT durum: {res['durum']} · doğrulayıcı: {'geçti' if res['dogrulama']['gecti'] else 'geçmedi'}"
                           + (f" · yoğunluk sinyali: {opt['sinyal']}" if opt["sinyal"] else ""))  # fmt: skip
            else:
                st.info(f"Bu karar anında yayınlanabilir plan yok (durum: {res.get('durum')}). Güvenli ret: kısıtlar gevşetilmez.")
        pend = [p for p in S.ss().sess.proposals() if p["durum"] == "beklemede"]
        st.caption(f"Onay bekleyen öneri: {len(pend)}")
        q1, q2, q3 = st.columns(3)
        if q1.button("⚙️ Optimizasyon", width="stretch"):
            st.switch_page(S.PAGES["opt"])
        if q2.button("✅ Onaylar", width="stretch"):
            st.switch_page(S.PAGES["onay"])
        if q3.button("🤖 Agent", width="stretch"):
            st.switch_page(S.PAGES["agent"])


def page_twin() -> None:
    C.top("Dijital Hastane İkizi", "Merkezi simülasyon saati, senaryolar ve what-if. Hiçbir senaryo gerçek atama yapmaz.")
    C.clock_controls("ikiz")
    s, eng = S.ss(), S.engine()
    names = S.unit_names()
    st.markdown("#### Senaryo")
    c1, c2 = st.columns([1.2, 2])
    preset = c1.selectbox("Hazır senaryo", list(S.SURGE_PRESETS), key="preset")
    if c1.button("Senaryoyu uygula", width="stretch"):
        s.surges = dict(S.SURGE_PRESETS[preset])
        for u in eng.data.units:
            s[f"surge_{u}"] = int(round(100 * s.surges.get(u, 0.0)))
        s.sess.log("senaryo", ad=preset, artis=s.surges)
        st.rerun()
    with c2:
        cols = st.columns(4)
        new = {}
        for col, u in zip(cols, eng.data.units.values()):
            s.setdefault(f"surge_{u.id}", int(round(100 * s.surges.get(u.id, 0.0))))
            new[u.id] = col.slider(f"{u.name} yoğunluk %", -50, 250, step=10, key=f"surge_{u.id}") / 100
        new = {u: v for u, v in new.items() if v}
        if new != s.surges:
            s.surges = new
            s.sess.log("senaryo", ad="özel", artis=new)
            st.rerun()
    snap = S.snapshot()
    st.markdown("#### İkizin anlık durumu")
    rows = {r["servis_id"]: r for r in snap["rows"] or [] if r["rol"] == "nurse"}
    table = []
    for d in snap["dens"]:
        u = d["birim"]
        r = rows.get(u, {})
        table.append({"Birim": d["ad"], "Kamera (tespit/kapasite)": f"{d['tespit']}/{d['kapasite']}", "Yoğunluk": LEVEL_TR[d["seviye"]],
                      "Vardiyada personel": snap["staff"][u]["toplam"], "Hemşire": snap["staff"][u]["nurse"],
                      "Ek talep sinyali (dk)": snap["signal"].get(u, 0.0), "RF tahmin (dk)": r.get("nokta_tahmin_dk"),
                      "Etkin kapasite (dk)": r.get("etkin_kapasite_dk"), "Eksik hemşire": r.get("eksik_hemsire")})  # fmt: skip
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch",
                 column_config={"RF tahmin (dk)": st.column_config.NumberColumn(format="%.0f"), "Etkin kapasite (dk)": st.column_config.NumberColumn(format="%.0f")})  # fmt: skip
    if snap["moves"]:
        st.success("Onaylı plan dijital ikizde SİMÜLE ediliyor (gerçek atama değildir): " +
                   "; ".join(f"{S.staff_code(p)} → {names[t]}" for p, t in snap["moves"].items()))  # fmt: skip

    st.markdown("#### What-if: yönlendirme öncesi etki analizi")
    if snap["rows"] is None:
        C.no_model()
        return
    duty = [x for x in eng.data.staff_on_duty(snap["at"]) if x["rol"] == "nurse" and not x["izinli"]]
    w1, w2, w3, w4 = st.columns(4)
    mv = w1.selectbox("Taşınacak hemşire", ["—"] + [f"{x['kod']} · {names[x['birim']]}" for x in duty], key="wi_mv")
    tgt = w2.selectbox("Hedef birim", list(names), format_func=names.get, index=2, key="wi_tgt")
    leave = w3.multiselect("İzne çıkan personel", [x["kod"] for x in duty], key="wi_leave", max_selections=3)
    ex_u = w4.selectbox("Ek personel birimi", [0, *names], format_func=lambda u: "—" if u == 0 else names[u], key="wi_ex")
    sc = W.Scenario(move=(mv.split(" · ")[0], tgt) if mv != "—" else None, leave=tuple(leave), extra={ex_u: 1} if ex_u else {})
    res = W.evaluate(eng.twin, snap["at"], snap["minute"], s.surges, sc)
    if res is None:
        return
    st.caption(f"Senaryo: {res['senaryo']} · gerçek atama yok")
    frame = pd.DataFrame(res["satirlar"])
    frame["degisim"] = frame["eksik_sonra"] - frame["eksik_once"]
    frame = frame[["ad", "talep_once", "talep_sonra", "kapasite_once", "kapasite_sonra", "eksik_once", "eksik_sonra", "degisim"]].rename(
        columns={"ad": "Birim", "talep_once": "Talep dk (mevcut)", "talep_sonra": "Talep dk (senaryo)", "kapasite_once": "Etkin kapasite dk (mevcut)",
                 "kapasite_sonra": "Etkin kapasite dk (senaryo)", "eksik_once": "Eksik hemşire (mevcut)", "eksik_sonra": "Eksik hemşire (senaryo)",
                 "degisim": "Değişim (kişi)"})  # fmt: skip
    st.dataframe(frame, hide_index=True, width="stretch")
    for name, ok, why in res["kontroller"]:
        st.markdown(theme.alert(f"{'✔' if ok else '✖'} {name}: {why}", "normal" if ok else "kirmizi"), unsafe_allow_html=True)
    if (sc.move or sc.leave or sc.extra) and st.button("What-if sonucunu denetim izine kaydet"):
        s.sess.log("what_if", senaryo=res["senaryo"], satirlar=res["satirlar"], kontroller=res["kontroller"])
        st.toast("What-if kaydedildi.")

