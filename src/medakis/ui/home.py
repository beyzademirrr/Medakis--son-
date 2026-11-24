"""Ana ekran: prototip uyarısı, genel bakış, servis paneli (Aşama 3), öneri inceleme ve onay (Aşama 6), mimari ve yapılandırma.

Çalışma modu (ui/mode.py) sekmelerden ÖNCE belirlenir. Sentetik Demo modunda Servis paneli çizilmez: veritabanı
servisi, repository ve bağlantı fabrikası hiç çağrılmaz. Normal mod önceki davranışı aynen korur.
"""

from __future__ import annotations

import os
import sys

import streamlit as st

from medakis.config import ConfigError, load_settings
from medakis.services import system_info
from medakis.ui import mode as run_mode
from medakis.ui import review_panel, workload_panel

DEMO_NOTICE = (
    "**Sentetik Demo Modu** — veritabanına bağlanılmaz; Servis paneli bu modda kapalıdır. "
    "Öneri inceleme ve onay ekranı yerel sentetik senaryoyla çalışır. Kimlik ve yetkiler **`test_sentetik`** prototip "
    "değerleridir; aktör seçimi doğrulanmış kimlik değildir. Gerçek personel ataması veya otomatik uygulama yapılmaz."
)
DEMO_PANEL_NOTICE = (
    "Servis paneli veritabanlı (Aşama 3) bir ekrandır. Sentetik Demo Modunda veritabanı bağlantısı kurulmaz ve bu panel "
    "çizilmez. Veritabanlı paneli kullanmak için kenar çubuğundan **Normal (veritabanlı)** modu seçin."
)

DISCLAIMER = (
    "**Prototip — yalnız sentetik veri.** MedAkis idari karar destek prototipidir; "
    "klinik ürün değildir. Gerçek hasta verisi kullanılmaz. Görev süreleri ve bakım "
    "katsayıları değiştirilebilir simülasyon varsayımlarıdır, klinik standart değildir. "
    "Öneriler otomatik personel emri değildir; yönetici incelemesi gerektirir."
)


def render_disclaimer() -> None:
    st.warning(DISCLAIMER, icon="⚠️")


def select_mode() -> str:
    """Kenar çubuğu seçicisi; başlangıç değeri URL, ortam ya da başlatma bayrağından gelir (varsayılan Normal)."""
    initial, source = run_mode.resolve_mode(st.session_state.get("calisma_modu"), st.query_params.to_dict(), os.environ, sys.argv)
    options = [run_mode.NORMAL, run_mode.DEMO]
    st.sidebar.radio("Çalışma modu", options, index=options.index(initial), format_func=run_mode.LABELS.get, key="calisma_modu")
    st.sidebar.caption(f"Mod kaynağı: {source}. Demo için: `streamlit run app.py -- --demo` ya da `?mod=demo`.")
    return st.session_state["calisma_modu"]


def main() -> None:
    st.set_page_config(page_title="MedAkis", page_icon="🩺", layout="wide")
    st.title("MedAkis")
    st.caption(
        "Yapay zekâ destekli sağlık tesisi iş yükü tahmini ve personel kaynak optimizasyonu"
    )
    render_disclaimer()

    mode = select_mode()
    if mode == run_mode.DEMO:
        st.info(DEMO_NOTICE, icon="🧪")

    try:
        settings = load_settings()
    except ConfigError as exc:
        st.error(f"Yapılandırma hatası: {exc}")
        return

    tab_overview, tab_panel, tab_review, tab_arch, tab_config = st.tabs(
        ["Genel bakış", "Servis paneli", "Öneri inceleme ve onay", "Mimari ve aşamalar", "Yapılandırma"]
    )

    with tab_overview:
        st.subheader("Birincil senaryo")
        st.markdown(
            "- Yataklı servis, **gece vardiyası** hemşire iş yükü ve personel planlaması\n"
            "- İş yükü **personel-dakikası** olarak ölçülür (hasta sayısı tek başına ölçü değildir)\n"
            "- Gelecek **30 ve 60 dakika** iş yükü tahmini\n"
            "- Zorunlu kurallar korunur; uygun personel yoksa **eksik kapasite** gösterilir\n"
            "- Acil servis ve ayaktan tedavi ikincil sentetik senaryolardır"
        )
        st.info(
            "**Tamamlanan özellikler (sentetik prototip):**\n"
            "- **Aşama 3 — Servis paneli:** gerçekleşen iş yükü ve personel kapasitesi (veritabanlı; demo modunda kapalı).\n"
            "- **Aşama 4 — Tahmin:** 30/60 dakikalık iş yükü tahmini (Random Forest, bilgi baseline'ı karşılaştırmalı). "
            "Tahmin sayıları Servis panelinde gösterilmez; Öneri inceleme ekranında ihtiyaç hesabına girer.\n"
            "- **Aşama 5 — Optimizasyon:** U1 kalibrasyonu ve güvenli tahmin, U3 ihtiyaç–kapasite karşılaştırması, "
            "CP-SAT Katman A kadro önerisi ve Katman B görev denetimi; bağımsız doğrulayıcı, belirsizlikte güvenli ret.\n"
            "- **Aşama 6 — İnsan onayı:** plan SHA-256 özeti, onay/ret/iptal/geri alma, yetki ve güncellik kontrolleri, "
            "zincirli denetim geçmişi, veritabanısız Sentetik Demo Modu.\n\n"
            "Sistem hiçbir planı uygulamaz; her öneri yönetici incelemesi gerektirir. RAG (Aşama 7+) henüz yoktur."
        )

    with tab_panel:
        if mode == run_mode.DEMO:
            st.info(DEMO_PANEL_NOTICE)  # bağlantısız açıklama; workload_panel hiç çağrılmaz
        else:
            workload_panel.render()

    with tab_review:
        review_panel.render()

    with tab_arch:
        st.subheader("Geliştirme aşamaları")
        st.dataframe(system_info.stage_rows(), hide_index=True, width="stretch")
        st.subheader("Modüller (modüler monolit)")
        st.dataframe(system_info.module_rows(), hide_index=True, width="stretch")
        st.caption(
            "RAG yalnız kaynaklı açıklama DTO'su döndürür; atama, onay veya iş tablosu "
            "yazma yetkisi yoktur."
        )

    with tab_config:
        st.subheader("Merkezi yapılandırma")
        st.dataframe(system_info.settings_rows(settings), hide_index=True, width="stretch")
        st.subheader("RAG çalışma profilleri (planlanan)")
        try:
            rows, measured = system_info.rag_profile_rows(settings)
        except ConfigError as exc:
            st.error(f"RAG profilleri okunamadı: {exc}")
        else:
            st.dataframe(rows, hide_index=True, width="stretch")
            if not measured:
                st.caption(
                    "Bu profiller aday yapılandırmalardır; bu cihazda henüz ölçülmemiştir. "
                    "Aşama 1'de model indirilmez ve Ollama'ya bağlanılmaz."
                )
