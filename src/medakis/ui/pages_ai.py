"""Yapay Zekâ ekranları: MedAkis AI Agent ve Kurallar ve Belgeler (kaynaklı arama)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from medakis.agent import llm
from medakis.agent import tools as T
from medakis.agent.orchestrator import EXAMPLES, answer
from medakis.retrieval_demo import CORPUS, search_sources
from medakis.ui import common as C
from medakis.ui import state as S
from medakis.ui import theme

GRAPH = """digraph { rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fillcolor="#111C2E", color="#14B8A6", fontcolor="#E2E8F0", fontname="Helvetica"];
edge [color="#38BDF8"]; soru [label="Kullanıcı sorusu"]; niyet [label="Niyet + birim"]; plan [label="Araç planı"];
araclar [label="Araç çağrıları\\n(yoğunluk, tahmin, personel,\\nCP-SAT, doğrulayıcı, kaynak)"]; dogrula [label="Doğrulama\\n(doğrulayıcı / sayı denetimi)"];
taslak [label="TASLAK öneri / talep", fillcolor="#1E1B4B", color="#8B5CF6"]; yanit [label="Yanıt + kaynak + araç izi"];
onay [label="Yönetici onayı (agent yetkisiz)", fillcolor="#3F1D1D", color="#EF4444"];
soru -> niyet -> plan -> araclar -> dogrula -> yanit; dogrula -> taslak -> onay [style=dashed]; }"""


@st.cache_data(ttl=60, show_spinner=False)
def _llm_status() -> llm.LlmStatus:
    return llm.status()


def _run(q: str, auto: bool = False) -> None:
    s = S.ss()
    ctx = S.tool_ctx()
    r = answer(q, ctx)
    text, engine = r.text, r.engine
    if s.get("use_llm"):
        new, why = llm.rephrase(r.text)
        if new:
            text, engine = new, "Gemma 3 4B yeniden ifade (sayı denetimi geçti) · araçlar deterministik"
        else:
            engine = f"Deterministik (Gemma çıktısı kullanılmadı: {why})"
    s.chat.append({"rol": "kullanici", "metin": q, "otomatik": auto})
    s.chat.append({"rol": "agent", "metin": text, "plan": r.plan, "cagrilar": r.calls, "kaynaklar": r.sources, "oneri": r.proposal, "motor": engine})
    s.sess.log("agent_cagri", soru=q, otomatik=auto, araclar=[{"arac": c["arac"], "sure_ms": c["sure_ms"]} for c in r.calls],
               oneri=(r.proposal or {}).get("oneri"), motor=engine)  # fmt: skip


def page_agent() -> None:
    C.top("MedAkis AI Agent", "Araç tabanlı agent · her sayı araç çıktısından · agent onay veremez, atama yapamaz, bildirim gönderemez")
    C.clock_controls("agent")
    s = S.ss()
    snap = S.snapshot()
    st_llm = _llm_status()
    k = st.columns([1.2, 1, 1])
    k[0].markdown(theme.kpi("Orkestrasyon", "Yerel", "planla → araç → doğrula → yanıtla", theme.ACCENT), unsafe_allow_html=True)
    k[1].markdown(theme.kpi("Dil modeli", "Hazır" if st_llm.available else "Yok", st_llm.detail, theme.OK if st_llm.available else theme.WARN), unsafe_allow_html=True)
    k[2].markdown(theme.kpi("Araç", len(T.TOOLS), f"{len(T.WRITE_TOOLS)} yalnız TASLAK yazar", theme.PURPLE), unsafe_allow_html=True)
    s.use_llm = st.toggle("Yanıtı Gemma 3 4B ile yeniden ifade et (sayı denetimli)", value=bool(s.get("use_llm")) and st_llm.available,
                          disabled=not st_llm.available, help="Model yoksa agent deterministik kipte çalışır.")  # fmt: skip
    reds = [e for e in snap["events"] if e["seviye"] == "kirmizi"]
    if s.auto_draft and reds:
        key = ("agent_taslak", s.mi, tuple(sorted(s.surges.items())))
        if key not in s.drafted:
            s.drafted.add(key)
            unit = S.unit_names()[reds[0]["birim"]]
            with st.spinner("Kritik yoğunluk olayı: agent otomatik taslak hazırlıyor…"):
                _run(f"{unit} için personel öner", auto=True)
    left, right = st.columns([1.6, 1])
    with left:
        for m in s.chat[-16:]:
            with st.chat_message("user" if m["rol"] == "kullanici" else "assistant", avatar="🧑‍⚕️" if m["rol"] == "kullanici" else "🤖"):
                if m["rol"] == "kullanici":
                    st.markdown(("**[Otomatik · yoğunluk olayı]** " if m.get("otomatik") else "") + m["metin"])
                    continue
                st.markdown(m["metin"])
                st.caption(f"Motor: {m['motor']}")
                with st.expander(f"Plan ve araç izi ({len(m['cagrilar'])} çağrı, {sum(c['sure_ms'] for c in m['cagrilar']):.0f} ms)"):
                    st.markdown(" → ".join(m["plan"]))
                    st.dataframe(pd.DataFrame([{"Araç": c["arac"], "Parametre": str(c["parametre"]) if c["parametre"] else "-", "Süre (ms)": c["sure_ms"]}
                                               for c in m["cagrilar"]]), hide_index=True, width="stretch")  # fmt: skip
                    for src in m["kaynaklar"]:
                        st.caption(f"Kaynak: {src['belge']} · {src['kimlik']} v{src['surum']} · {src['bolum']} · skor {src['skor']}")
        q = st.chat_input("Örn. Acil servise personel öner")
        if q:
            _run(q)
            st.rerun()
    with right:
        st.markdown("#### Örnek sorular")
        for i, ex in enumerate(EXAMPLES):
            if st.button(ex, key=f"ex_{i}", width="stretch"):
                _run(ex)
                st.rerun()
        st.markdown("#### Bekleyen onaylar")
        pend = [p for p in s.sess.proposals() if p["durum"] == "beklemede"]
        for p in pend:
            st.markdown(theme.alert(f"{p['kimlik'][:12]}… · {p['kalem']} kalem", "sari", "Onay: Öneriler ve Onaylar (yönetici)"), unsafe_allow_html=True)
        if not pend:
            st.caption("Bekleyen öneri yok.")
        if st.button("Sohbeti temizle", width="stretch"):
            s.chat = []
            st.rerun()
    st.markdown("#### Orkestrasyon akışı")
    st.graphviz_chart(GRAPH, width="stretch")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Araç sözleşmeleri")
        st.dataframe(pd.DataFrame([{"Araç": n, "Yetki": "TASLAK yazar" if n in T.WRITE_TOOLS else "Salt okunur"} for n in T.TOOLS]),
                     hide_index=True, width="stretch", height=300)  # fmt: skip
    with c2:
        st.markdown("#### Entegrasyon durumu")
        st.markdown(theme.alert("Yerel orkestrasyon: çalışıyor (bu ekrandaki bütün yanıtlar).", "normal"), unsafe_allow_html=True)
        st.markdown(theme.alert("LangGraph: Geliştiriliyor — Prototip. Paket kurulu değil; akış yukarıdaki yerel orkestrasyonla aynı düğümleri izler.", "sari"), unsafe_allow_html=True)
        st.markdown(theme.alert("MCP sunucusu: Geliştiriliyor — Prototip. Araç sözleşmeleri tanımlı (salt okunur / yalnız taslak); "
                                "MCP üzerinden çağrı yapılmaz ve MCP kaydı üretilmez.", "sari"), unsafe_allow_html=True)  # fmt: skip
        st.markdown(theme.alert("Otomatik taslak: kritik yoğunluk olayında agent taslak öneri hazırlar (Ayarlar'dan kapatılabilir). Onay her zaman yöneticidedir.",
                                "normal" if s.auto_draft else "sari"), unsafe_allow_html=True)  # fmt: skip


def page_rag() -> None:
    C.top("Kurallar ve Belgeler", "Kontrollü sentetik kaynak belgeler · kaynaklı arama · bulunamazsa cevap üretilmez")
    from medakis.rag.ingestion.corpus_loader import load_corpus

    corpus = load_corpus(CORPUS)
    docs = [d.record for d in corpus.documents]
    st.markdown(theme.alert("Bu ekranda sınırlı anahtar kelime (BM25) araması çalışır; sonuç dil modeli cevabı DEĞİLDİR. BGE-M3 + yeniden "
                            "sıralayıcı + Gemma hattı geliştirme kipindedir ve kabul ölçütü (A6) karşılanmadığı için normal kullanıcıya kapalıdır.",
                            "sari"), unsafe_allow_html=True)  # fmt: skip
    t1, t2 = st.tabs(["🔎 Soru ve kaynak", "📚 Belge listesi"])
    with t1:
        q = st.text_input("Soru", "Acil serviste hangi yetkinlik aranır?", key="rag_q")
        ex = st.columns(3)
        for col, sample in zip(ex, ("Destek kuralı hangi birimleri kapsar?", "Kullanım oranı nedir?", "Hastaya hangi ilaç verilmeli?")):
            if col.button(sample, width="stretch"):
                st.session_state["rag_q"] = sample
                st.rerun()
        if q:
            res = search_sources(q)
            S.ss().sess.log("belge_arama", soru=q, bulundu=res["bulundu"], kaynak=[k["kimlik"] for k in res["kaynaklar"]])
            if not res["bulundu"]:
                st.error(res["mesaj"] + " (Desteklenmeyen ya da kaynakta olmayan soru: güvenli ret.)")
            texts = {d.record.uid: d.text for d in corpus.documents}
            uid_of = {d.record.source_key: d.record.uid for d in corpus.documents}
            for k in res["kaynaklar"]:
                verified = k["alinti"].strip()[:120] in texts.get(uid_of.get(k["kimlik"], ""), "")
                with st.container(border=True):
                    st.markdown(f"**{k['belge']}** · {theme.chip(k['kimlik'])} {theme.chip('sürüm ' + str(k['surum']))} "
                                f"{theme.chip('skor ' + str(k['skor']), 'info')} {theme.chip('alıntı belgede doğrulandı' if verified else 'alıntı doğrulanamadı', 'ok' if verified else 'crit')}",
                                unsafe_allow_html=True)  # fmt: skip
                    st.caption(f"Bölüm: {k['bolum']}")
                    st.markdown(f"> {k['alinti']}")
    with t2:
        st.dataframe(pd.DataFrame([{"Kimlik": r.source_key, "Belge": r.source_name, "Sürüm": r.version, "Amaç": r.purpose} for r in docs]),
                     hide_index=True, width="stretch", height=420)  # fmt: skip
        st.caption(f"{len(docs)} belge · kontrollü sentetik corpus (yetkili klinik kaynak değildir). Güvenlik fikstürleri aramaya katılmaz.")
