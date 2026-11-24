"""Ekranlar arası ortak parçalar: üst bilgi, saat denetimleri, tablo biçimleri."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import streamlit as st

from medakis.notifications.telegram import TelegramConfig
from medakis.twin.data import SOURCE_LABEL
from medakis.twin.engine import LEVEL_TR
from medakis.ui import state as S
from medakis.ui import theme


def top(title: str, subtitle: str = "") -> None:
    eng = S.engine()
    tg = TelegramConfig.from_env()
    chips = [(SOURCE_LABEL, "info"), ("Sentetik prototip · gerçek hasta/personel yok", "warn"), (f"🕒 {S.clock_text()}", ""),
             ("Tahmin+CP-SAT hazır" if eng.scenario else "Model dosyası yok: tahmin/optimizasyon kapalı", "ok" if eng.scenario else "crit"),
             ("Telegram: canlı" if tg.live_ready else "Telegram: dry-run", "crit" if tg.live_ready else "")]  # fmt: skip
    st.markdown(theme.header(chips), unsafe_allow_html=True)
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)


def clock_controls(key: str) -> None:
    """Merkezi simülasyon saati: karar anı + pencere dakikası + oynatma. Bütün ekranlar aynı saati kullanır."""
    s = S.ss()
    eng = S.engine()
    labels = [eng.data.local(m).strftime("%d.%m.%Y %H:%M") for m in s.moments]
    cols = st.columns([2.2, 1.6, 0.9, 0.9, 0.9])
    mi = cols[0].selectbox("Karar anı (sentetik takvim)", range(len(s.moments)), index=s.mi, format_func=lambda i: labels[i], key=f"mi_{key}_{s.mi}")
    if mi != s.mi:
        s.mi, s.minute = mi, 0
        s.sess.log("saat_degisti", karar_ani=S.at())
        st.rerun()
    minute = cols[1].slider("Pencere dakikası", 0, 59, s.minute, 1, key=f"dk_{key}_{s.mi}_{s.minute}")
    if minute != s.minute:
        s.minute = minute
        st.rerun()
    if cols[2].button("⏸ Durdur" if s.playing else "▶ Oynat", key=f"play_{key}", width="stretch"):
        s.playing = not s.playing
        st.rerun()
    s.speed = cols[3].selectbox("Hız (dk/sn)", (1, 5, 10, 15), index=(1, 5, 10, 15).index(s.speed), key=f"hiz_{key}")
    if cols[4].button("↺ Sıfırla", key=f"reset_{key}", width="stretch", help="Senaryo, saat ve bu oturumun kayıtlarını baştan başlatır"):
        S.reset()
        st.rerun()


def live(body: Callable[[], None]) -> None:
    """Oynatma açıkken bölüm her saniye `speed` dakika ilerleyip yeniden çizilir (yalnız bu bölüm)."""
    if S.ss().playing:
        def run() -> None:
            S.tick()
            body()
        st.fragment(run_every=1.0)(run)()
    else:
        body()


def level_badge(level: str) -> str:
    kind = {"normal": "ok", "sari": "warn", "turuncu": "warn", "kirmizi": "crit"}[level]
    return theme.chip(LEVEL_TR[level], kind)


def df(rows: list[dict], cols: dict[str, str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if cols and not frame.empty:
        frame = frame[[c for c in cols if c in frame.columns]].rename(columns=cols)
    return frame


def no_model() -> None:
    eng = S.engine()
    st.warning("Tahmin ve optimizasyon için yerel model dosyaları gerekli (artifacts/forecasting). Sahte tahmin ya da plan "
               "gösterilmez. Hazırlık: `./baslat.ps1 -ModelHazirla`." + (f" Ayrıntı: {eng.scenario_error}" if eng.scenario_error else ""))  # fmt: skip
