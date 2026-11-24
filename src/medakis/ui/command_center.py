"""MedAkis Komuta Merkezi: bütünleşik ürün arayüzü (Sentetik Demo; PostgreSQL ve Ollama gerektirmez).

Bütün ekranlar tek motoru (sentetik veri + mevcut RF/kalibrasyon + Katman A CP-SAT + doğrulayıcı) ve oturumdaki tek
simülasyon saatini paylaşır. Göstergeler hesaplanır, sabit yazılmaz. Hiçbir ekran gerçek atama yapmaz.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from medakis.ui import pages_admin as A
from medakis.ui import pages_ai as AI
from medakis.ui import pages_analysis as AN
from medakis.ui import pages_ops as O
from medakis.ui import pages_staff as P
from medakis.ui import state as S
from medakis.ui import theme

ICON = Path(__file__).resolve().parent / "assets" / "favicon.png"


def _sidebar() -> None:
    s, eng = S.ss(), S.engine()
    with st.sidebar:
        st.markdown(f'<div style="display:flex;gap:10px;align-items:center">{theme.LOGO}<div><b style="font-size:18px">MedAkis</b><br>'
                    '<span class="ma-foot">Hospital Command Center</span></div></div>', unsafe_allow_html=True)  # fmt: skip
        st.markdown(f"🕒 **{S.clock_text()}** · {'▶ oynatılıyor' if s.playing else '⏸'}")
        reds = sum(d["seviye"] == "kirmizi" for d in S.snapshot()["dens"])
        pend = sum(p["durum"] == "beklemede" for p in s.sess.proposals())
        st.markdown(" ".join([theme.chip(f"{reds} kritik alan", "crit" if reds else "ok"), theme.chip(f"{pend} onay bekliyor", "warn" if pend else "")]),
                    unsafe_allow_html=True)  # fmt: skip
        if s.surges:
            st.caption("Senaryo: " + ", ".join(f"{S.unit_names()[u]} {'+' if v > 0 else ''}{round(100 * v)}%" for u, v in s.surges.items()))
        st.markdown(f'<div class="ma-foot">Sentetik veri · gerçek hasta/personel yok<br>{"Tahmin + CP-SAT hazır" if eng.scenario else "Model dosyası yok"}</div>',
                    unsafe_allow_html=True)  # fmt: skip


def main() -> None:
    st.set_page_config(page_title="MedAkis", page_icon=str(ICON) if ICON.exists() else "🏥", layout="wide", initial_sidebar_state="expanded")
    st.markdown(theme.css(st.context.theme.type or "dark"), unsafe_allow_html=True)
    with st.spinner("MedAkis motoru hazırlanıyor (sentetik veri, tahmin modelleri, kalibrasyon; ilk açılış ~40 sn)…"):
        S.init()
    pages = {
        "Operasyon": [st.Page(O.page_control, title="Kontrol Merkezi", icon="🏥", url_path="kontrol", default=True),
                      st.Page(O.page_twin, title="Dijital Hastane İkizi", icon="🧬", url_path="ikiz")],
        "Analiz": [st.Page(AN.page_camera, title="Kamera ve Yoğunluk", icon="📹", url_path="kamera"),
                   st.Page(AN.page_forecast, title="İş Yükü Tahmini", icon="📈", url_path="tahmin"),
                   st.Page(AN.page_heatmap, title="Isı Haritası", icon="🌡️", url_path="isi")],
        "Personel": [st.Page(P.page_staff, title="Personel Yönetimi", icon="👥", url_path="personel"),
                     st.Page(P.page_shifts, title="Vardiyalar", icon="🗓️", url_path="vardiya"),
                     st.Page(P.page_optimization, title="Optimizasyon ve Yönlendirme", icon="⚙️", url_path="optimizasyon")],
        "Yapay Zekâ": [st.Page(AI.page_agent, title="MedAkis AI Agent", icon="🤖", url_path="agent"),
                       st.Page(AI.page_rag, title="Kurallar ve Belgeler", icon="📚", url_path="belgeler")],
        "Yönetim": [st.Page(A.page_approvals, title="Öneriler ve Onaylar", icon="✅", url_path="onaylar"),
                    st.Page(A.page_orders, title="Emirler ve Bildirimler", icon="🔔", url_path="bildirimler"),
                    st.Page(A.page_requests, title="Talepler", icon="📝", url_path="talepler"),
                    st.Page(A.page_audit, title="Denetim ve Raporlar", icon="🧾", url_path="denetim"),
                    st.Page(A.page_settings, title="Ayarlar", icon="⚙", url_path="ayarlar")],
    }  # fmt: skip
    S.PAGES.update({"opt": pages["Personel"][2], "onay": pages["Yönetim"][0], "agent": pages["Yapay Zekâ"][0], "emir": pages["Yönetim"][1]})
    nav = st.navigation(pages, expanded=True)
    _sidebar()
    nav.run()
