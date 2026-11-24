"""MedAkis komuta merkezi görsel teması (Streamlit üzerinde CSS ve güvenli, sabit HTML parçaları)."""

from __future__ import annotations

import html

BG, PANEL, ACCENT, ACCENT2, CRIT, WARN, OK = "#0B1220", "#111C2E", "#14B8A6", "#38BDF8", "#EF4444", "#F59E0B", "#22C55E"
LEVEL_COLOR = {"kritik": CRIT, "uyari": WARN, "normal": OK}
LEVEL_LABEL = {"kritik": "Kritik", "uyari": "Uyarı", "normal": "Normal"}

CSS = f"""
<style>
.stApp {{ background: {BG}; }}
section[data-testid="stSidebar"] {{ background: #0E1729; border-right: 1px solid #1E2A40; }}
.block-container {{ padding-top: 1.2rem; padding-bottom: 1rem; max-width: 1500px; }}
h1, h2, h3, h4 {{ color: #E2E8F0; letter-spacing: .2px; }}
.ma-header {{ display:flex; align-items:center; justify-content:space-between; background:{PANEL}; border:1px solid #1E2A40;
  border-radius:12px; padding:10px 18px; margin-bottom:14px; }}
.ma-brand {{ display:flex; align-items:center; gap:12px; }}
.ma-brand b {{ font-size:22px; color:#F8FAFC; }} .ma-brand span {{ color:#94A3B8; font-size:13px; }}
.ma-badge {{ background:rgba(245,158,11,.12); color:{WARN}; border:1px solid rgba(245,158,11,.4); border-radius:999px; padding:3px 10px; font-size:12px; }}
.ma-user {{ color:#CBD5E1; font-size:13px; }}
.ma-card {{ background:{PANEL}; border:1px solid #1E2A40; border-radius:12px; padding:14px 16px; height:100%; }}
.ma-card .k {{ color:#94A3B8; font-size:12px; text-transform:uppercase; letter-spacing:.6px; }}
.ma-card .v {{ color:#F8FAFC; font-size:28px; font-weight:700; margin-top:2px; }}
.ma-card .s {{ font-size:12px; margin-top:2px; }}
.ma-panel {{ background:{PANEL}; border:1px solid #1E2A40; border-radius:12px; padding:12px 14px; margin-bottom:12px; }}
.ma-alert {{ border-left:3px solid; padding:6px 10px; margin:6px 0; background:#0E1729; border-radius:6px; color:#E2E8F0; font-size:14px; }}
.ma-tag {{ display:inline-block; font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid #334155; color:#94A3B8; }}
div[data-testid="stMetric"] {{ background:{PANEL}; border:1px solid #1E2A40; border-radius:12px; padding:10px 14px; }}
</style>
"""

LOGO = f"""<svg width="34" height="34" viewBox="0 0 34 34"><rect width="34" height="34" rx="9" fill="{ACCENT}"/>
<path d="M6 18h6l3-7 4 13 3-6h6" stroke="#0B1220" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>"""


def header(mode_label: str, user: str) -> str:
    return (f'<div class="ma-header"><div class="ma-brand">{LOGO}<div><b>MedAkis</b><br><span>Intelligent Hospital Operations '
            f'&amp; Workforce Optimization</span></div></div><div style="display:flex;gap:14px;align-items:center">'
            f'<span class="ma-badge">{html.escape(mode_label)}</span><span class="ma-user">👤 {html.escape(user)}</span></div></div>')  # fmt: skip


def kpi(label: str, value: str, sub: str = "", color: str = "#94A3B8") -> str:
    return (f'<div class="ma-card"><div class="k">{html.escape(label)}</div><div class="v">{html.escape(value)}</div>'
            f'<div class="s" style="color:{color}">{html.escape(sub)}</div></div>')  # fmt: skip


def alert(text: str, level: str) -> str:
    return f'<div class="ma-alert" style="border-color:{LEVEL_COLOR.get(level, ACCENT2)}">{html.escape(text)}</div>'


def heatmap_svg(rows: list[dict], areas, width: int = 720, height: int = 420) -> str:
    """Kat planı üzerinde alan doluluk ısı haritası (sentetik simülasyon verisinden)."""
    by = {r["alan"]: r for r in rows}
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="background:{BG};border-radius:10px">']
    for a in areas:
        r = by[a.key]
        x, y, w, h = a.rect
        col = LEVEL_COLOR[r["seviye"]]
        op = 0.25 + 0.6 * min(1.0, r["doluluk"])
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{col}" fill-opacity="{op:.2f}" stroke="{col}" stroke-width="2"/>')
        parts.append(f'<text x="{x + 12}" y="{y + 26}" fill="#F8FAFC" font-size="16" font-weight="700">{html.escape(a.name)}</text>')
        parts.append(f'<text x="{x + 12}" y="{y + 52}" fill="#E2E8F0" font-size="28" font-weight="700">%{round(100 * r["doluluk"])}</text>')
        parts.append(f'<text x="{x + 12}" y="{y + 74}" fill="#CBD5E1" font-size="13">{r["kisi"]}/{r["kapasite"]} kişi · {LEVEL_LABEL[r["seviye"]]}</text>')
    parts.append("</svg>")
    return "".join(parts)
