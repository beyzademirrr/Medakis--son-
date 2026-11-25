"""Personel ekranları: Personel Yönetimi, Vardiyalar ve yoğunluğa duyarlı Optimizasyon ve Yönlendirme."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from medakis.twin import whatif as W
from medakis.twin.data import ROLE_TR
from medakis.twin.engine import Twin
from medakis.ui import common as C
from medakis.ui import state as S
from medakis.ui import theme


def _status(x: dict) -> str:
    return "İzinli" if x["izinli"] else "Molada" if x["molada"] else "Aktif"


def page_staff() -> None:
    C.top("Personel Yönetimi", "Vardiya, rol, geçerlilik tarihli yetkinlik ve kullanılabilir süre — sentetik personel kayıtları")
    C.clock_controls("personel")
    eng, snap = S.engine(), S.snapshot()
    names = S.unit_names()
    duty = eng.data.staff_on_duty(snap["at"])
    c1, c2, c3 = st.columns(3)
    fu = c1.multiselect("Birim", list(names), format_func=names.get, key="st_u")
    fr = c2.multiselect("Rol", list(ROLE_TR), format_func=ROLE_TR.get, key="st_r")
    fs = c3.multiselect("Durum", ["Aktif", "Molada", "İzinli"], key="st_s")
    rows = []
    for x in duty:
        unit = snap["moves"].get(x["id"], x["birim"])
        if (fu and unit not in fu) or (fr and x["rol"] not in fr) or (fs and _status(x) not in fs):
            continue
        rows.append({"Kod": x["kod"], "Rol": ROLE_TR[x["rol"]], "Birim": names[unit] + (" (onaylı taşıma · simülasyon)" if x["id"] in snap["moves"] else ""),
                     "Vardiya": x["vardiya"], "Saat": f"{S.local_hm(x['baslangic'])}–{S.local_hm(x['bitis'])}", "Durum": _status(x),
                     "Mola": ", ".join(f"{S.local_hm(a)}–{S.local_hm(b)}" for a, b in x["mola"]) or "-",
                     "Yetkinlikler (geçerli)": ", ".join(x["yetkinlikler"]) or "-", "Kullanılabilir dk (60 dk)": round(x["kullanilabilir_dk"])})  # fmt: skip
    k = st.columns(4)
    k[0].markdown(theme.kpi("Vardiyada", len(duty), f"{len(eng.data.staff)} kayıtlı personel", theme.BLUE), unsafe_allow_html=True)
    k[1].markdown(theme.kpi("Aktif", sum(_status(x) == "Aktif" for x in duty), "şu an görevde", theme.OK), unsafe_allow_html=True)
    k[2].markdown(theme.kpi("Molada / izinli", f"{sum(x['molada'] for x in duty)} / {sum(x['izinli'] for x in duty)}", "mola ve izin kayıtları", theme.WARN), unsafe_allow_html=True)
    gaps = [r for r in snap["rows"] or [] if r["rol"] == "nurse" and r.get("eksik_hemsire", 0) > 0]
    k[3].markdown(theme.kpi("Kritik açık", len(gaps), ", ".join(names[r["servis_id"]] for r in gaps) or "yok", theme.CRIT if gaps else theme.OK), unsafe_allow_html=True)
    st.write("")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=380)
    a, b = st.columns(2)
    with a:
        st.markdown("#### Rezerv havuzu")
        gap_units = {r["servis_id"] for r in gaps}
        pool = [x for x in duty if x["rol"] == "nurse" and not x["izinli"] and x["kullanilabilir_dk"] >= 60 and x["birim"] not in gap_units]
        st.dataframe(C.df([{"kod": x["kod"], "birim": names[x["birim"]], "yetkinlik": ", ".join(x["yetkinlikler"])} for x in pool],
                          {"kod": "Kod", "birim": "Birim", "yetkinlik": "Yetkinlikler"}), hide_index=True, width="stretch")  # fmt: skip
        st.caption("Açığı öngörülmeyen birimlerde tam pencere kullanılabilir hemşireler; destek uygunluğu yine CP-SAT ve doğrulayıcı ile belirlenir.")
    with b:
        st.markdown("#### Bekleyen yönlendirmeler")
        pend = [p for p in S.ss().sess.proposals() if p["durum"] == "beklemede"]
        for p in pend:
            st.markdown(theme.alert(f"Öneri {p['kimlik'][:10]}… · {p['kalem']} kalem · plan {p['plan_sha256'][:10]}…", "sari"), unsafe_allow_html=True)
        if not pend:
            st.caption("Onay bekleyen yönlendirme yok.")
        code = st.selectbox("Personel ayrıntısı", [x["kod"] for x in duty], key="st_detail")
        x = next(y for y in duty if y["kod"] == code)
        all_sk = sorted(eng.data.skills.get(x["id"], []), key=lambda t: t[0])
        st.markdown(f"**{x['kod']}** · {ROLE_TR[x['rol']]} · {names[x['birim']]} · {x['vardiya']} · {_status(x)}")
        st.dataframe(C.df([{"k": c, "ad": eng.data.skill_names.get(c, c), "f": f.date(), "u": u.date() if u else "süresiz",
                            "g": "geçerli" if c in x["yetkinlikler"] else "geçersiz"} for c, f, u in all_sk],
                          {"k": "Kod", "ad": "Yetkinlik", "f": "Başlangıç", "u": "Bitiş", "g": "Karar anında"}), hide_index=True, width="stretch")  # fmt: skip


def page_shifts() -> None:
    C.top("Vardiyalar", "Günlük Gantt, haftalık matris, mola aralıkları ve saatlik kapsama")
    C.clock_controls("vardiya")
    eng, snap = S.engine(), S.snapshot()
    names = S.unit_names()
    c1, c2, c3 = st.columns(3)
    day = c1.date_input("Gün", eng.data.local(snap["at"]).date(), key="sh_day")
    unit = c2.selectbox("Birim", list(names), format_func=names.get, index=2, key="sh_unit")
    role = c3.selectbox("Rol", ["", *ROLE_TR], format_func=lambda r: ROLE_TR.get(r, "Tümü"), key="sh_role")
    d0 = datetime(day.year, day.month, day.day, tzinfo=eng.data.tz).astimezone(UTC)
    shifts = [s for s in eng.data.shifts_overlapping(d0, d0 + timedelta(days=1)) if s.unit_id == unit and (not role or eng.data.staff[s.staff_id]["rol"] == role)]
    bars, breaks = [], []
    for s in shifts:
        code = eng.data.staff[s.staff_id]["kod"]
        bars.append({"Personel": f"{code} · {ROLE_TR[eng.data.staff[s.staff_id]['rol']][:7]}", "Başlangıç": eng.data.local(s.start).replace(tzinfo=None),
                     "Bitiş": eng.data.local(s.end).replace(tzinfo=None), "Vardiya": s.template})  # fmt: skip
        for a in eng.data.absences.get(s.staff_id, []):
            if a.start < s.end and s.start < a.end:
                breaks.append({"Personel": bars[-1]["Personel"], "Başlangıç": eng.data.local(max(a.start, s.start)).replace(tzinfo=None),
                               "Bitiş": eng.data.local(min(a.end, s.end)).replace(tzinfo=None), "Tür": a.reason})  # fmt: skip
    st.markdown(f"#### Günlük Gantt · {names[unit]} · {len(bars)} vardiya")
    if bars:
        base = alt.Chart(pd.DataFrame(bars)).mark_bar(cornerRadius=4, height=14).encode(
            x=alt.X("Başlangıç:T", title=None, axis=alt.Axis(format="%H:%M")), x2="Bitiş:T", y=alt.Y("Personel:N", sort=None, title=None),
            color=alt.Color("Vardiya:N", scale=alt.Scale(range=[theme.ACCENT, theme.BLUE, theme.PURPLE, theme.WARN])), tooltip=["Personel", "Vardiya", "Başlangıç", "Bitiş"])  # fmt: skip
        layers = base
        if breaks:
            layers += alt.Chart(pd.DataFrame(breaks)).mark_bar(height=14, color="#0B1220", opacity=0.75).encode(
                x="Başlangıç:T", x2="Bitiş:T", y=alt.Y("Personel:N", sort=None), tooltip=["Personel", "Tür", "Başlangıç", "Bitiş"])  # fmt: skip
        now = eng.data.local(snap["at"] + timedelta(minutes=snap["minute"])).replace(tzinfo=None)
        if now.date() == day:
            layers += alt.Chart(pd.DataFrame({"t": [now]})).mark_rule(color=theme.CRIT, strokeDash=[4, 3]).encode(x="t:T")
        st.altair_chart(layers.properties(height=max(160, 22 * len(bars))), width="stretch")
        st.caption("Koyu bölümler: mola/izin aralıkları. Kırmızı çizgi: simülasyon saati.")
    a, b = st.columns(2)
    with a:
        st.markdown("#### Saatlik kapsama (vardiyada kullanılabilir personel)")
        hm = pd.DataFrame(eng.twin.hourly(day, role or None))
        hm["Birim"] = hm["birim"].map(names)
        st.altair_chart(alt.Chart(hm).mark_area(opacity=0.7).encode(x=alt.X("saat:Q", title="Yerel saat"), y=alt.Y("personel:Q", stack=True, title="Personel"),
                                                                     color="Birim:N").properties(height=240), width="stretch")  # fmt: skip
    with b:
        st.markdown("#### Haftalık matris")
        start = day - timedelta(days=day.weekday())
        w0 = datetime(start.year, start.month, start.day, tzinfo=eng.data.tz).astimezone(UTC)
        cells = {}
        for s in eng.data.shifts_overlapping(w0, w0 + timedelta(days=7)):
            if s.unit_id != unit or (role and eng.data.staff[s.staff_id]["rol"] != role):
                continue
            dd = eng.data.local(s.start).date()
            if start <= dd < start + timedelta(days=7):
                cells.setdefault(eng.data.staff[s.staff_id]["kod"], {})[dd.strftime("%a %d")] = {"GUNDUZ": "G", "GECE": "N", "POLIKLINIK": "P", "POLIKLINIK_YARIM": "Y"}.get(s.template, s.template[:1])
        cols = [(start + timedelta(days=i)).strftime("%a %d") for i in range(7)]
        st.dataframe(pd.DataFrame.from_dict(cells, orient="index").reindex(columns=cols).fillna("·").sort_index(), width="stretch", height=280)
        st.caption("G: gündüz · N: gece · P: poliklinik · Y: poliklinik yarım gün")


def page_optimization() -> None:
    C.top("Optimizasyon ve Yönlendirme", "Mevcut CP-SAT (Katman A) + bağımsız doğrulayıcı · kısıtlar gevşetilmez · yoğunluk tek başına gerekçe değildir")
    C.clock_controls("opt")
    s, eng, snap = S.ss(), S.engine(), S.snapshot()
    if snap["rows"] is None:
        C.no_model()
        return
    names = S.unit_names()
    aware = st.toggle("Yoğunluğa duyarlı senaryo (kamera ek talep sinyali)", value=True)
    sig = snap["signal"] if aware else {}
    with st.spinner("CP-SAT çözülüyor ve bağımsız doğrulayıcıdan geçiriliyor…"):
        base = eng.twin.optimize(snap["at"], {})
        opt = eng.twin.optimize(snap["at"], sig)
    res = opt["sonuc"]
    lkey = ("opt", s.mi, tuple(sorted(sig.items())))
    if lkey not in s.drafted:
        s.drafted.add(lkey)
        s.sess.log("optimizasyon", karar_ani=snap["at"], durum=res.get("durum"), yayinlanabilir=res.get("oneri_yayinlanabilir"),
                   dogrulayici=res.get("dogrulama", {}).get("gecti"), sinyal=opt["sinyal"], tasima=len(res.get("tasimalar") or []))  # fmt: skip
    k = st.columns(4)
    k[0].markdown(theme.kpi("Çözücü durumu", res.get("durum"), "CP-SAT (varsayılan politika)", theme.ACCENT), unsafe_allow_html=True)
    passed = res.get("dogrulama", {}).get("gecti")
    k[1].markdown(theme.kpi("Doğrulayıcı", "Geçti" if passed else "Geçmedi", f"{len(res.get('dogrulama', {}).get('ihlaller') or [])} ihlal", theme.OK if passed else theme.CRIT), unsafe_allow_html=True)
    k[2].markdown(theme.kpi("Taşıma", len(res.get("tasimalar") or []), "geçici görevlendirme önerisi", theme.BLUE), unsafe_allow_html=True)
    k[3].markdown(theme.kpi("Ek talep sinyali", f"{sum(opt['sinyal'].values()):.0f} dk", ", ".join(names[u] for u in opt["sinyal"]) or "yok", theme.PURPLE), unsafe_allow_html=True)
    st.write("")
    if not res.get("oneri_yayinlanabilir"):
        st.error(f"Yayınlanabilir plan yok (durum: {res.get('durum')}). Güvenli ret: kısıtlar gevşetilmez, öneri oluşturulmaz. "
                 "Alternatifler: ek personel talebi (Talepler) ya da aşağıdaki manuel yönlendirme denetimi.")  # fmt: skip
    moves = res.get("tasimalar") or []
    before = {r["servis_id"]: r for r in snap["rows"] if r["rol"] == "nurse"}
    gaps_b, gaps_a = Twin.gaps(base), Twin.gaps(opt)
    if moves:
        st.markdown("#### Önerilen geçici görevlendirmeler")
        st.dataframe(C.df([{"p": S.staff_code(t["personel"]), "k": names[t["ev_servis"]], "h": names[t["hedef_servis"]],
                            "s": f"{S.local_hm(t['baslangic'])}–{S.local_hm(t['bitis'])}", "d": t["sure_dk"], "y": ", ".join(t.get("gerekli_yetkinlikler") or []),
                            "g": f"hedefte {before[t['hedef_servis']]['eksik_hemsire']} hemşire eksik; kaynakta asgari kişi korunur",
                            "r": str(t.get("destek_kurali_kaynagi") or "-")[:60]} for t in moves],
                          {"p": "Personel", "k": "Kaynak", "h": "Hedef", "s": "Saat", "d": "Süre dk", "y": "Gerekli yetkinlik", "g": "Gerekçe", "r": "Destek kuralı kaynağı"}),
                     hide_index=True, width="stretch")  # fmt: skip
    st.markdown("#### Eksik hemşire: plansız, sinyalsiz plan ve bu plan")
    cmp = []
    for u in names:
        if u not in before:
            continue
        b0, a0 = gaps_a.get(u, {}), gaps_b.get(u, {})
        cmp.append({"Birim": names[u], "Tahmini ihtiyaç": b0.get("ihtiyac_dk"), "Mevcut kapasite": before[u].get("kullanilabilir_personel_dk"),
                    "Önerilen kapasite": b0.get("kapasite_dk"), "Eksik (plansız)": before[u].get("eksik_hemsire"),
                    "Eksik (sinyalsiz plan)": a0.get("eksik_hemsire"), "Eksik (bu plan)": b0.get("eksik_hemsire"), "Ek sinyal": opt["sinyal"].get(u, 0.0)})  # fmt: skip
    st.dataframe(pd.DataFrame(cmp), hide_index=True, width="stretch", column_config={c: st.column_config.NumberColumn(format="%.0f") for c in cmp[0] if c != "Birim"} if cmp else None)
    st.caption("Eksik = 60 dk pencerede karşılanamayan iş için gereken hemşire sayısı (yukarı yuvarlanır). İhtiyaç ve kapasite sütunları "
               "personel-dakikadır: ihtiyaç = tamponlu talep / 0,85. Yoğunluk sinyali yalnız açık takvimli hemşire satırına eklenir.")  # fmt: skip

    a, b = st.columns([1.3, 1])
    with a:
        target = st.selectbox("Aday ve elenme gerekçeleri — hedef birim", [1, 2, 3], format_func=names.get, index=2, key="opt_tgt")
        cands = eng.twin.candidates(snap["at"], target, {t["personel"] for t in moves if t["hedef_servis"] == target})
        st.dataframe(C.df(cands, {"kod": "Personel", "rol": "Rol", "birim": "Birim", "yetkinlikler": "Yetkinlikler", "gerekce": "Sonuç / elenme gerekçesi"}),
                     hide_index=True, width="stretch", height=300)  # fmt: skip
    with b:
        st.markdown("#### Kural kontrolleri (bağımsız doğrulayıcı)")
        rules = ("Rol ve gerekli yetkinlik", "Çakışan vardiya / çift atama yok", "Asgari personel kuralı (kaynak dahil)",
                 "Birimler arası destek kuralı kaynağı", "Onaylı kilitli yerleşimler", "Asgari kalış süresi", "Kapasite ve açık hesabı tutarlılığı")  # fmt: skip
        for name in rules:
            st.markdown(theme.alert(f"{'✔' if passed else '✖'} {name}", "normal" if passed else "kirmizi"), unsafe_allow_html=True)
        st.caption("Doğrulayıcı çözümü çözücüden bağımsız olarak bu kuralların hepsine karşı yeniden denetler; tek ihlal planı geçersiz kılar.")
        for v in (res.get("dogrulama", {}).get("ihlaller") or [])[:5]:
            st.markdown(theme.alert(f"İhlal: {v}", "kirmizi"), unsafe_allow_html=True)
        props = {p["kimlik"]: p for p in s.sess.proposals()}
        if res.get("oneri_yayinlanabilir") and st.button("📝 Planı yönetici onayına gönder", type="primary", width="stretch"):
            out = s.sess.create_proposal(opt, "optimizasyon_ekrani")
            (st.success if out["olusturuldu"] else st.warning)(f"Öneri {out.get('oneri')}: {out.get('durum') or out.get('neden')}")
        st.caption(f"Kayıtlı öneri: {len(props)} · onay yalnız 'Öneriler ve Onaylar' ekranında, yönetici oturumuyla.")

    st.markdown("#### Manuel yönlendirme talebi (yönlendirme öncesi what-if)")
    duty = [x for x in eng.data.staff_on_duty(snap["at"]) if x["rol"] == "nurse" and not x["izinli"]]
    m1, m2 = st.columns(2)
    code = m1.selectbox("Personel", [x["kod"] for x in duty], key="man_code")
    tgt = m2.selectbox("Hedef birim", list(names), format_func=names.get, index=2, key="man_tgt")
    wi = W.evaluate(eng.twin, snap["at"], snap["minute"], s.surges, W.Scenario(move=(code, tgt)))
    if wi:
        st.dataframe(pd.DataFrame(wi["satirlar"])[["ad", "eksik_once", "eksik_sonra"]].rename(
            columns={"ad": "Birim", "eksik_once": "Eksik hemşire (mevcut)", "eksik_sonra": "Eksik hemşire (yönlendirme sonrası)"}), hide_index=True, width="stretch")  # fmt: skip
        ok = all(c[1] for c in wi["kontroller"])
        for name, good, why in wi["kontroller"]:
            st.markdown(theme.alert(f"{'✔' if good else '✖'} {name}: {why}", "normal" if good else "kirmizi"), unsafe_allow_html=True)
        if st.button("Manuel yönlendirme talebi oluştur", disabled=not ok, help="Bütün kontroller geçmeden talep oluşturulamaz"):
            r = s.sess.create_request({"tur": "gecici_gorevlendirme", "personel": code, "birim": tgt, "kaynak": "yonetici_ekrani",
                                       "gerekce": "Manuel yönlendirme; what-if kontrolleri geçti", "what_if": wi["satirlar"]})  # fmt: skip
            st.success(f"Talep {r['talep']} oluşturuldu (beklemede). Gerçek atama yapılmadı.")
