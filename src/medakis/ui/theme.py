"""MedAkis görsel teması: koyu (lacivert/antrasit) ve açık tema, logo, favicon ve güvenli sabit HTML parçaları.

Bütün metinler `html.escape` ile kaçışlanır. Renkler CSS değişkenleridir; tema Streamlit ayarlarından (Ayarlar → Tema)
değiştirildiğinde değişken seti de değişir.
"""

from __future__ import annotations

import html

ACCENT, BLUE, PURPLE, CRIT, WARN, OK, ORANGE = "#14B8A6", "#38BDF8", "#8B5CF6", "#EF4444", "#F59E0B", "#22C55E", "#F97316"
LEVEL_COLOR = {"normal": OK, "sari": WARN, "turuncu": ORANGE, "kirmizi": CRIT}

TOKENS = {
    "dark": {"bg": "#0B1220", "panel": "#111C2E", "panel2": "#0E1729", "line": "#1E2A40", "text": "#E2E8F0", "muted": "#94A3B8",
             "strong": "#F8FAFC"},
    "light": {"bg": "#F6F8FB", "panel": "#FFFFFF", "panel2": "#F1F5F9", "line": "#E2E8F0", "text": "#0F172A", "muted": "#64748B",
              "strong": "#0B1220"},
}  # fmt: skip


def css(kind: str) -> str:
    t = TOKENS.get(kind, TOKENS["dark"])
    v = ";".join(f"--ma-{k}:{c}" for k, c in t.items())
    return f"""<style>
:root {{ {v}; }}
.block-container {{ padding-top: 1rem; padding-bottom: 1rem; max-width: 1560px; }}
section[data-testid="stSidebar"] {{ border-right: 1px solid var(--ma-line); }}
.ma-header {{ display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; background:var(--ma-panel);
  border:1px solid var(--ma-line); border-radius:14px; padding:10px 16px; margin-bottom:12px; }}
.ma-brand {{ display:flex; align-items:center; gap:12px; }}
.ma-brand b {{ font-size:20px; color:var(--ma-strong); }} .ma-brand span {{ color:var(--ma-muted); font-size:12px; }}
.ma-chips {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
.ma-chip {{ border-radius:999px; padding:3px 10px; font-size:12px; border:1px solid var(--ma-line); color:var(--ma-text); background:var(--ma-panel2); }}
.ma-chip.warn {{ color:{WARN}; border-color:rgba(245,158,11,.45); background:rgba(245,158,11,.10); }}
.ma-chip.ok {{ color:{OK}; border-color:rgba(34,197,94,.45); background:rgba(34,197,94,.10); }}
.ma-chip.info {{ color:{BLUE}; border-color:rgba(56,189,248,.45); background:rgba(56,189,248,.10); }}
.ma-chip.crit {{ color:{CRIT}; border-color:rgba(239,68,68,.45); background:rgba(239,68,68,.10); }}
.ma-chip.dev {{ color:{PURPLE}; border-color:rgba(139,92,246,.45); background:rgba(139,92,246,.10); }}
.ma-card {{ background:var(--ma-panel); border:1px solid var(--ma-line); border-radius:14px; padding:12px 14px; height:100%; }}
.ma-card .k {{ color:var(--ma-muted); font-size:11px; text-transform:uppercase; letter-spacing:.6px; }}
.ma-card .v {{ color:var(--ma-strong); font-size:26px; font-weight:700; margin-top:2px; line-height:1.2; }}
.ma-card .s {{ font-size:12px; margin-top:2px; }}
.ma-panel {{ background:var(--ma-panel); border:1px solid var(--ma-line); border-radius:14px; padding:12px 14px; margin-bottom:10px; }}
.ma-alert {{ border-left:3px solid; padding:7px 10px; margin:6px 0; background:var(--ma-panel2); border-radius:8px; color:var(--ma-text); font-size:13.5px; }}
.ma-alert small {{ color:var(--ma-muted); }}
.ma-step {{ display:flex; gap:10px; align-items:flex-start; padding:8px 10px; border:1px solid var(--ma-line); border-radius:10px; margin:6px 0; background:var(--ma-panel2); }}
.ma-step .n {{ min-width:24px; height:24px; border-radius:999px; background:{ACCENT}; color:#0B1220; font-weight:700; display:flex; align-items:center; justify-content:center; font-size:12px; }}
.ma-step .t {{ color:var(--ma-text); font-size:13.5px; }} .ma-step .t small {{ color:var(--ma-muted); }}
.ma-cam {{ font-size:12px; color:var(--ma-muted); margin-top:-4px; }}
.ma-foot {{ color:var(--ma-muted); font-size:11.5px; }}
div[data-testid="stMetric"] {{ background:var(--ma-panel); border:1px solid var(--ma-line); border-radius:14px; padding:10px 14px; }}
@media (max-width: 760px) {{ .ma-card .v {{ font-size:20px; }} .ma-header {{ padding:8px 10px; }} }}
</style>"""


LOGO = f"""<svg width="34" height="34" viewBox="0 0 34 34" aria-label="MedAkis"><defs><linearGradient id="mag" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="{ACCENT}"/><stop offset="1" stop-color="{BLUE}"/></linearGradient></defs>
<rect width="34" height="34" rx="10" fill="url(#mag)"/><path d="M10 8h14v18H10z" fill="none" stroke="#0B1220" stroke-width="2.2" stroke-linejoin="round"/>
<path d="M17 11v6M14 14h6" stroke="#0B1220" stroke-width="2.4" stroke-linecap="round"/>
<path d="M6 23h5l2-3 3 5 2-3h10" stroke="#F8FAFC" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

FAVICON = "assets/favicon.png"


def esc(x: object) -> str:
    return html.escape(str(x))


def header(chips: list[tuple[str, str]]) -> str:
    c = "".join(f'<span class="ma-chip {k}">{esc(t)}</span>' for t, k in chips)
    return (f'<div class="ma-header"><div class="ma-brand">{LOGO}<div><b>MedAkis</b><br><span>Intelligent Hospital Operations '
            f'&amp; Workforce Optimization</span></div></div><div class="ma-chips">{c}</div></div>')  # fmt: skip


def chip(text: str, kind: str = "") -> str:
    return f'<span class="ma-chip {kind}">{esc(text)}</span>'


def kpi(label: str, value: object, sub: str = "", color: str = "var(--ma-muted)") -> str:
    return (f'<div class="ma-card"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div>'
            f'<div class="s" style="color:{color}">{esc(sub)}</div></div>')  # fmt: skip


def alert(text: str, level: str, small: str = "") -> str:
    extra = f"<br><small>{esc(small)}</small>" if small else ""
    return f'<div class="ma-alert" style="border-color:{LEVEL_COLOR.get(level, BLUE)}">{esc(text)}{extra}</div>'


def step(n: int, text: str, small: str = "") -> str:
    extra = f"<br><small>{esc(small)}</small>" if small else ""
    return f'<div class="ma-step"><div class="n">{n}</div><div class="t">{esc(text)}{extra}</div></div>'


def floor_plan(dens: list[dict], units: dict, thresholds: dict[str, float], width: int = 720, height: int = 420) -> str:
    """Kat planı yoğunluk haritası (uzamsal): renk eşikleri yapılandırmadan gelir."""
    by = {d["birim"]: d for d in dens}
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Kat planı yoğunluk haritası" '
             f'style="background:var(--ma-panel2);border-radius:12px;border:1px solid var(--ma-line)">']  # fmt: skip
    parts.append(f'<text x="360" y="203" text-anchor="middle" fill="var(--ma-muted)" font-size="11">KORİDOR</text>')
    for u in units.values():
        d = by.get(u.id)
        if d is None:
            continue
        x, y, w, h = u.rect
        col = LEVEL_COLOR[d["seviye"]]
        op = 0.18 + 0.55 * min(1.0, d["doluluk"])
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{col}" fill-opacity="{op:.2f}" stroke="{col}" stroke-width="2"/>')
        parts.append(f'<text x="{x + 14}" y="{y + 26}" fill="var(--ma-strong)" font-size="15" font-weight="700">{esc(u.name)}</text>')
        parts.append(f'<text x="{x + 14}" y="{y + 44}" fill="var(--ma-muted)" font-size="11">{esc(u.camera)} · {esc(u.area.split("·")[-1].strip())}</text>')
        parts.append(f'<text x="{x + 14}" y="{y + 84}" fill="var(--ma-strong)" font-size="30" font-weight="700">%{round(100 * d["doluluk"])}</text>')
        parts.append(f'<text x="{x + 14}" y="{y + 106}" fill="var(--ma-text)" font-size="12.5">{d["tespit"]}/{d["kapasite"]} kişi (tespit)</text>')
    lx = 20
    for name, key in (("Normal", "normal"), (f"≥%{round(100 * thresholds['sari'])}", "sari"), (f"≥%{round(100 * thresholds['turuncu'])}", "turuncu"),
                      (f"≥%{round(100 * thresholds['kirmizi'])}", "kirmizi")):  # fmt: skip
        parts.append(f'<rect x="{lx}" y="{height - 16}" width="10" height="10" rx="2" fill="{LEVEL_COLOR[key]}"/>')
        parts.append(f'<text x="{lx + 14}" y="{height - 7}" fill="var(--ma-muted)" font-size="11">{esc(name)}</text>')
        lx += 90
    parts.append("</svg>")
    return "".join(parts)
