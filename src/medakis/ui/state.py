"""Arayüzün ortak durumu: tek motor, kullanıcı oturumu ve merkezi simülasyon saati.

Bütün ekranlar aynı `snapshot()` sonucunu kullanır; saat, senaryo artışı ya da onaylı plan değiştiğinde kameralar,
yoğunluk, ısı haritası, vardiya durumu, tahmin, olaylar, optimizasyon ve agent birlikte güncellenir.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import streamlit as st

from medakis.agent import tools as T
from medakis.twin.hub import Engine, Session

DEFAULT_MOMENT = datetime.fromisoformat("2027-01-19T06:30:00+00:00")  # doğrulanmış (OPTIMAL) karar anı; demo başlangıcı
SURGE_PRESETS = {"Normal akış": {}, "Acil yoğunluk artışı (+%100)": {3: 1.0}, "Acil kritik yoğunluk (+%150)": {3: 1.5},
                 "Poliklinik düşüşü (−%40)": {4: -0.4}}  # fmt: skip
LINK_PRESETS = {"acil": {3: 1.0}, "acil_kritik": {3: 1.5}, "poliklinik_dusus": {4: -0.4}}  # paylaşılabilir demo bağlantısı: ?senaryo=acil_kritik


PAGES: dict[str, Any] = {}  # sayfa anahtarı → st.Page (hızlı geçiş düğmeleri için; command_center doldurur)


@st.cache_resource(show_spinner=False)
def engine() -> Engine:
    return Engine.build()


def ss() -> Any:
    return st.session_state


def init() -> None:
    s = ss()
    if "sess" in s:
        return
    eng = engine()
    s.sess = Session.open(eng)
    s.moments = sorted(eng.data.moments())
    s.mi = s.moments.index(DEFAULT_MOMENT) if DEFAULT_MOMENT in s.moments else 0
    s.minute = 0
    s.surges = dict(LINK_PRESETS.get(st.query_params.get("senaryo", ""), {}))
    s.playing = False
    s.speed = 5
    s.view = "hepsi"
    s.chat = []
    s.auto_draft = True
    s.drafted = set()
    s.whatif_log = []


def reset() -> None:
    for k in [*("sess", "moments", "mi", "minute", "surges", "playing", "chat", "drafted", "whatif_log", "snap_key", "snap"),
              *(k for k in ss() if str(k).startswith("surge_"))]:
        ss().pop(k, None)
    init()
    ss().sess.log("simulasyon_sifirlama")


def at() -> datetime:
    return ss().moments[ss().mi]


def clock_text() -> str:
    eng = engine()
    return (eng.data.local(at()) + timedelta(minutes=ss().minute)).strftime("%d.%m.%Y %H:%M")


def tick() -> None:
    """Oynatma: her adımda `speed` dakika ilerler; pencere sonunda bir sonraki karar anına geçer."""
    s = ss()
    s.minute += s.speed
    if s.minute >= 60:
        s.minute = 0
        s.mi = min(s.mi + 1, len(s.moments) - 1)
    s.sess.sync_clock(at(), s.minute)


def snapshot(view: str | None = None) -> dict:
    """Geçerli saat için ortak durum (aynı anahtar için bir kez hesaplanır)."""
    s, eng = ss(), engine()
    s.sess.sync_clock(at(), s.minute)
    moves = s.sess.approved_moves(at())
    key = (s.mi, s.minute, tuple(sorted(s.surges.items())), view or s.view, tuple(sorted(moves.items())))
    if s.get("snap_key") == key:
        return s.snap
    tw = eng.twin
    dens = tw.density(at(), s.minute, s.surges, view or s.view)
    rows = tw.forecast(at())
    snap = {"at": at(), "minute": s.minute, "dens": dens, "staff": tw.staffing(at(), moves), "rows": rows, "moves": moves,
            "events": tw.events(at(), dens, rows), "signal": tw.density_signal(dens)}  # fmt: skip
    for e in snap["events"]:
        if e["seviye"] == "kirmizi":
            ekey = ("olay", s.mi, e["birim"])
            if ekey not in s.drafted:
                s.drafted.add(ekey)
                s.sess.log("yogunluk_olayi", birim=e["birim"], metin=e["metin"], karar_ani=at())
    s.snap_key, s.snap = key, snap
    return snap


def tool_ctx() -> T.ToolContext:
    s = ss()
    return T.ToolContext(engine().twin, at(), s.minute, dict(s.surges), create_proposal=lambda o: s.sess.create_proposal(o, "agent"),
                         create_request=s.sess.create_request, approval_status=s.sess.proposals, request_status=s.sess.request_list)  # fmt: skip


def unit_names() -> dict[int, str]:
    return {u.id: u.name for u in engine().data.units.values()}


def staff_code(staff_id: int) -> str:
    return engine().data.staff[staff_id]["kod"]


def local_hm(t: datetime | str) -> str:
    if isinstance(t, str):
        t = datetime.fromisoformat(t)
    return engine().data.local(t).strftime("%H:%M")
