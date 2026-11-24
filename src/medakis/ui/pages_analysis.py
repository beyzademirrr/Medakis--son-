"""Analiz ekranları: Kamera ve Yoğunluk, İş Yükü Tahmini, Isı Haritası (uzamsal + zamansal).

Kamera yoğunluğu (kişi / alan kapasitesi) ile personel iş yükü oranı (görev talebi / etkin kapasite) ayrı ölçülerdir ve
ayrı gösterilir. Kamera yoğunluğu Random Forest modelinin girdisi değildir; yalnız etiketli ek talep sinyalidir.
"""

from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from medakis.twin.engine import LEVEL_TR
from medakis.ui import common as C
from medakis.ui import state as S
from medakis.ui import theme

VIEWS = {"hepsi": "Video + kutu + ısı", "video": "Ham video", "kutular": "Tespit kutuları", "isi": "Yoğunluk ısısı"}


def page_camera() -> None:
    C.top("Kamera ve Yoğunluk", "Sentetik kamera akışı · bağlı bileşen sayımı · yüz/kimlik/biyometri işlenmez")
    C.clock_controls("kamera")
    s, eng = S.ss(), S.engine()
    names = S.unit_names()
    c1, c2 = st.columns([1.4, 1])
    s.view = c1.radio("Görünüm", list(VIEWS), format_func=VIEWS.get, horizontal=True, index=list(VIEWS).index(s.view), key="view_radio")
    focus = c2.selectbox("Birim", [0, *names], format_func=lambda u: "Bütün kameralar" if u == 0 else names[u], key="cam_focus")

    def body() -> None:
        snap = S.snapshot()
        rows = {r["servis_id"]: r for r in snap["rows"] or [] if r["rol"] == "nurse"}
        dens = [d for d in snap["dens"] if focus in (0, d["birim"])]
        per_row = 2 if focus == 0 else 1
        for i in range(0, len(dens), per_row):
            cols = st.columns(per_row)
            for col, d in zip(cols, dens[i:i + per_row]):
                with col:
                    st.image(d["goruntu"], width="stretch")
                    r = rows.get(d["birim"], {})
                    need = r.get("gerekli_personel_dk")
                    st.markdown(f"{C.level_badge(d['seviye'])} {theme.chip(d['kamera'])} {theme.chip(f'Tespit {d['tespit']} / simülasyon {d['gercek']}', 'info')} "
                                f"{theme.chip(f'Kapasite {d['kapasite']} · %{round(100 * d['doluluk'])}')} "
                                f"{theme.chip(f'Vardiyada {snap['staff'][d['birim']]['toplam']} personel')} "
                                + (theme.chip(f"Tahmini ihtiyaç ≈ {need / 60:.1f} hemşire", "warn") if need else ""), unsafe_allow_html=True)  # fmt: skip
        st.caption("Tespit: açık renkli figür pikselleri → bağlı bileşen → kutu. Yalnız sentetik karede çalışır; tespit ve simülasyon "
                   "gerçeği yan yana gösterilir. Bu yöntem gerçek kamerada doğrulanmış insan tespiti değildir.")  # fmt: skip

    C.live(body)
    snap = S.snapshot()
    a, b = st.columns([1.3, 1])
    with a:
        st.markdown("#### Pencere içi yoğunluk geçmişi (tespit)")
        hist = []
        for m in range(0, snap["minute"] + 1, 5):
            for u in eng.data.units:
                if focus in (0, u):
                    d = eng.twin.camera(u, snap["at"], m, s.surges.get(u, 0.0), "video")
                    hist.append({"Saat": eng.twin.clock(snap["at"], m), "Birim": names[u], "Doluluk %": round(100 * d["doluluk"])})
        st.altair_chart(alt.Chart(pd.DataFrame(hist)).mark_line(point=True).encode(x=alt.X("Saat:N", sort=None), y="Doluluk %:Q", color="Birim:N")
                        .properties(height=230), width="stretch")  # fmt: skip
    with b:
        st.markdown("#### Kritik olaylar")
        ev = [e for e in snap["events"] if focus in (0, e["birim"])]
        for e in ev:
            st.markdown(theme.alert(f"{e['tur']}: {e['metin']}", e["seviye"]), unsafe_allow_html=True)
        if not ev:
            st.markdown(theme.alert("Kritik olay yok.", "normal"), unsafe_allow_html=True)
        st.caption(f"Eşikler: sarı ≥%{round(100 * eng.twin.thresholds['sari'])}, turuncu ≥%{round(100 * eng.twin.thresholds['turuncu'])}, "
                   f"kırmızı ≥%{round(100 * eng.twin.thresholds['kirmizi'])} (Ayarlar).")  # fmt: skip


def page_forecast() -> None:
    C.top("İş Yükü Tahmini", "Mevcut Random Forest + U1 kalibrasyonlu tampon · 30/60 dk · personel-dakika (kişi sayısı değildir)")
    C.clock_controls("tahmin")
    snap = S.snapshot()
    eng = S.engine()
    if snap["rows"] is None:
        C.no_model()
        return
    names = S.unit_names()
    role = st.radio("Rol", ("nurse", "support"), format_func={"nurse": "Hemşire", "support": "Destek personeli"}.get, horizontal=True)
    r30 = {(r["servis_id"], r["rol"]): r for r in eng.twin.forecast(snap["at"], 30) or []}
    table = []
    for r in snap["rows"]:
        if r["rol"] != role:
            continue
        h = r30.get((r["servis_id"], r["rol"]), {})
        sig = snap["signal"].get(r["servis_id"], 0.0) if role == "nurse" else 0.0
        table.append({"Birim": names[r["servis_id"]], "Model": r.get("model"), "30 dk tahmin (dk)": h.get("nokta_tahmin_dk"),
                      "60 dk tahmin (dk)": r.get("nokta_tahmin_dk"), "Tampon (dk)": r.get("tampon_dk"), "Tamponlu talep (dk)": r.get("tamponlu_talep_dk"),
                      "Kamera ek sinyali (dk)": sig, "Kullanılabilir (personel-dk)": r.get("kullanilabilir_personel_dk"),
                      "Etkin kapasite (dk)": r.get("etkin_kapasite_dk"), "Açık (personel-dk)": r.get("acik_personel_dk"),
                      "Rezerv (dk)": r.get("fazla_dk"), "Takvim": r.get("takvim_durumu"), "Durum": r.get("durum")})  # fmt: skip
    num = {k: st.column_config.NumberColumn(format="%.0f") for k in table[0] if "(dk)" in k or "personel-dk" in k} if table else {}
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch", column_config=num)
    st.caption("Model sürümü: " + ", ".join(sorted({str(r.get('model_surumu')) for r in snap["rows"] if r.get("model_surumu")})) +
               " · Kamera ek sinyali modele girmez; yalnız yoğunluğa duyarlı optimizasyonda ayrı etiketle eklenir.")  # fmt: skip
    warn = [r for r in snap["rows"] if r["rol"] == role and (r.get("acik_personel_dk") or 0) > 0]
    for r in warn:
        st.markdown(theme.alert(f"Erken uyarı (60 dk): {names[r['servis_id']]} — {round(r['acik_personel_dk'])} personel-dk açık "
                                f"(talep {round(r['tamponlu_talep_dk'])} dk > etkin kapasite {round(r['etkin_kapasite_dk'])} dk)", "turuncu"), unsafe_allow_html=True)  # fmt: skip

    st.markdown("#### Tahmin ve gerçekleşen (bu gün değerlendirilen karar anları)")
    day = eng.data.local(snap["at"]).date()
    pts = []
    for m in S.ss().moments:
        if eng.data.local(m).date() != day:
            continue
        rows = eng.twin.forecast(m)
        for r in rows or []:
            if r["rol"] != role or r.get("nokta_tahmin_dk") is None:
                continue
            real = sum(t["estimated_minutes"] for t in eng.data.tasks_window(r["servis_id"], m, m + timedelta(minutes=60), role))
            hm = eng.data.local(m).strftime("%H:%M")
            pts += [{"Saat": hm, "Birim": names[r["servis_id"]], "Seri": "RF tahmini", "Dakika": r["nokta_tahmin_dk"]},
                    {"Saat": hm, "Birim": names[r["servis_id"]], "Seri": "Tamponlu talep", "Dakika": r["tamponlu_talep_dk"]},
                    {"Saat": hm, "Birim": names[r["servis_id"]], "Seri": "Gerçekleşen görev dk (sonradan)", "Dakika": real},
                    {"Saat": hm, "Birim": names[r["servis_id"]], "Seri": "Etkin kapasite", "Dakika": r["etkin_kapasite_dk"]}]  # fmt: skip
    if pts:
        st.altair_chart(alt.Chart(pd.DataFrame(pts)).mark_line(point=True).encode(x=alt.X("Saat:N", sort=None), y="Dakika:Q", color="Seri:N",
                        facet=alt.Facet("Birim:N", columns=4)).properties(height=170, width=230), width="content")  # fmt: skip
        st.caption("Gerçekleşen görev dakikası karar anında bilinmez; yalnız karşılaştırma için sonradan hesaplanır (sentetik).")


def _role_param() -> str | None:
    return st.radio("Rol filtresi", ("nurse", "support", "tum"), format_func={"nurse": "Hemşire", "support": "Destek", "tum": "Tümü"}.get,
                    horizontal=True, key="hm_role").replace("tum", "") or None  # fmt: skip


def page_heatmap() -> None:
    C.top("Isı Haritası", "Uzamsal (kamera yoğunluğu) ve zamansal (iş yükü / etkin kapasite) haritalar ayrı ölçülerdir")
    C.clock_controls("isi")
    eng, snap = S.engine(), S.snapshot()
    names = S.unit_names()
    t1, t2 = st.tabs(["🗺️ Uzamsal · kat planı", "🕒 Zamansal · birim × saat"])
    with t1:
        st.markdown(theme.floor_plan(snap["dens"], eng.data.units, eng.twin.thresholds), unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"Birim": d["ad"], "Alan": d["alan"], "Tespit": d["tespit"], "Kapasite": d["kapasite"],
                                    "Doluluk %": round(100 * d["doluluk"]), "Seviye": LEVEL_TR[d["seviye"]]} for d in snap["dens"]]),
                     hide_index=True, width="stretch")  # fmt: skip
    with t2:
        c1, c2, c3 = st.columns([1, 1.4, 1])
        cur_day = eng.data.local(snap["at"]).date()
        mode = c1.radio("Görünüm", ("gun", "hafta"), format_func={"gun": "Gün", "hafta": "Hafta"}.get, horizontal=True)
        day: date = c1.date_input("Gün", cur_day, key="hm_day")
        with c2:
            role = _role_param()
        metric = c3.radio("Ölçü", ("oran", "acik_dk"), format_func={"oran": "İş yükü oranı", "acik_dk": "Açık (dk)"}.get, horizontal=True)
        if mode == "gun":
            frame = pd.DataFrame(eng.twin.hourly(day, role))
            frame["Birim"] = frame["birim"].map(names)
            x = alt.X("saat:O", title="Yerel saat")
        else:
            start = day - timedelta(days=day.weekday())
            parts = []
            for i in range(7):
                d = start + timedelta(days=i)
                f = pd.DataFrame(eng.twin.hourly(d, role))
                f["gun"] = d.strftime("%a %d.%m")
                parts.append(f)
            frame = pd.concat(parts)
            frame = frame.groupby(["gun", "birim"], as_index=False).agg(talep_dk=("talep_dk", "sum"), etkin_kapasite_dk=("etkin_kapasite_dk", "sum"),
                                                                        acik_dk=("acik_dk", "sum"), rezerv_dk=("rezerv_dk", "sum"), personel=("personel", "max"))  # fmt: skip
            frame["oran"] = (frame["talep_dk"] / frame["etkin_kapasite_dk"].where(frame["etkin_kapasite_dk"] > 0)).round(3)
            frame["Birim"] = frame["birim"].map(names)
            frame["saat"] = frame["gun"]
            x = alt.X("saat:O", title="Gün", sort=None)
        sel = alt.selection_point(fields=["birim", "saat"], name="hucre")
        scale = alt.Scale(domain=[0, 0.6, 0.85, 1.0, 1.6], range=["#0F766E", theme.OK, theme.WARN, theme.ORANGE, theme.CRIT], clamp=True) if metric == "oran" \
            else alt.Scale(scheme="orangered")  # fmt: skip
        chart = alt.Chart(frame).mark_rect(stroke="#0B1220", strokeWidth=0.6).encode(
            x=x, y=alt.Y("Birim:N", title=None), color=alt.Color(f"{metric}:Q", scale=scale, title="Oran" if metric == "oran" else "Açık dk"),
            opacity=alt.condition(sel, alt.value(1), alt.value(0.55)),
            tooltip=["Birim", "saat", "talep_dk", "etkin_kapasite_dk", "oran", "acik_dk", "rezerv_dk", "personel"]).add_params(sel).properties(height=230)  # fmt: skip
        layers = chart
        if mode == "gun" and day == cur_day:
            now = pd.DataFrame({"saat": [eng.data.local(snap["at"]).hour]})
            layers = chart + alt.Chart(now).mark_rule(color="#F8FAFC", strokeWidth=2, strokeDash=[4, 2]).encode(x="saat:O")
        ev = st.altair_chart(layers, width="stretch", on_select="rerun", selection_mode="hucre", key=f"hm_{mode}_{day}_{role}_{metric}")
        st.caption("Talep = son tarihi o saatte olan sentetik görevlerin tahmini dakikası; etkin kapasite = vardiya personel-dakikası − mola/izin, × 0,85. "
                   "Beyaz kesik çizgi: geçerli karar anı.")  # fmt: skip
        st.markdown("##### En riskli pencereler")
        top = frame.dropna(subset=["oran"]).sort_values("oran", ascending=False).head(5)
        st.dataframe(top[["Birim", "saat", "talep_dk", "etkin_kapasite_dk", "oran", "acik_dk", "personel"]].rename(
            columns={"saat": "Saat/Gün", "talep_dk": "Talep dk", "etkin_kapasite_dk": "Etkin kap. dk", "oran": "Oran", "acik_dk": "Açık dk", "personel": "Personel"}),
            hide_index=True, width="stretch")  # fmt: skip
        pts = (ev.selection.get("hucre") if ev and ev.selection else None) or []
        if pts:
            p = pts[0]
            row = frame[(frame["birim"] == p["birim"]) & (frame["saat"].astype(str) == str(p["saat"]))]
            if not row.empty:
                r = row.iloc[0]
                st.markdown(f"##### Hücre ayrıntısı · {names[int(r['birim'])]} · {r['saat']}")
                k = st.columns(5)
                for col, (lab, val) in zip(k, (("Talep dk", r["talep_dk"]), ("Etkin kapasite dk", r["etkin_kapasite_dk"]), ("Oran", r["oran"]),
                                              ("Açık dk", r["acik_dk"]), ("Rezerv dk", r["rezerv_dk"]))):  # fmt: skip
                    col.metric(lab, "—" if pd.isna(val) else f"{val:.2f}" if lab == "Oran" else f"{val:.0f}")
                if mode == "gun":
                    _cell_routing(day, int(r["saat"]))


def _cell_routing(day: date, hour: int) -> None:
    eng = S.engine()
    hits = [m for m in S.ss().moments if eng.data.local(m).date() == day and eng.data.local(m).hour == hour]
    if not hits:
        st.caption("Bu saatte değerlendirilmiş karar anı yok; yönlendirme önerisi hesaplanmadı.")
        return
    m = hits[0]
    with st.spinner("Bu karar anı için mevcut CP-SAT planı çözülüyor…"):
        opt = eng.twin.optimize(m, {})
    if opt is None:
        C.no_model()
        return
    res, names = opt["sonuc"], S.unit_names()
    if not res.get("oneri_yayinlanabilir"):
        st.info(f"{eng.data.local(m):%H:%M} karar anında yayınlanabilir plan yok (durum: {res.get('durum')}).")
        return
    moves = res.get("tasimalar") or []
    st.success(f"{eng.data.local(m):%H:%M} karar anı CP-SAT planı (doğrulayıcı geçti): " +
               ("; ".join(f"{S.staff_code(t['personel'])} {names[t['ev_servis']]} → {names[t['hedef_servis']]}" for t in moves) or "taşıma gerekmiyor"))  # fmt: skip
