"""Streamlit theming: HCLTech-derived CSS and reusable branded UI primitives."""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

from . import config, graphics


def _data_uri(path: Path) -> str:
    if not path or not Path(path).exists():
        return ""
    mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode('ascii')}"


def inject_css(palette: dict[str, str] | None = None) -> None:
    """Inject the brand stylesheet. Safe to call on every rerun."""
    p = palette or config.HCLTECH_PALETTE
    purple = p["primary_purple"]
    blue = p["primary_blue"]
    teal = p["secondary_teal"]
    dark = p["dark_neutral"]
    accent = p.get("accent_magenta", purple)
    light_blue = p.get("light_blue", config.lighten(blue, 0.72))
    surface = p.get("surface", "#F5F8FF")
    surface_alt = p.get("surface_alt", "#EAF1FF")
    muted = p.get("text_muted", "#5A6B85")

    st.markdown(
        f"""
<style>
:root {{
  --lada-purple: {purple};
  --lada-blue: {blue};
  --lada-teal: {teal};
  --lada-dark: {dark};
  --lada-accent: {accent};
  --lada-light-blue: {light_blue};
  --lada-surface: {surface};
  --lada-surface-alt: {surface_alt};
  --lada-muted: {muted};
  --lada-grad: linear-gradient(120deg, {purple} 0%, {blue} 55%, {teal} 100%);
  --lada-grad-soft: linear-gradient(120deg, {config.lighten(purple, 0.9)} 0%, {config.lighten(blue, 0.88)} 100%);
  --lada-radius: 14px;
  --lada-shadow: 0 2px 14px rgba(0,17,43,.07);
}}

/* ---------- page shell ---------- */
.stApp {{ background: {surface}; }}
.block-container {{ padding-top: 1.1rem; padding-bottom: 3.2rem; max-width: 1500px; }}
html, body, [class*="css"] {{
  font-family: "Segoe UI", "Roobert", -apple-system, Helvetica, Arial, sans-serif;
  color: {dark};
}}
#MainMenu, footer {{ visibility: hidden; }}
h1, h2, h3, h4 {{ color: {dark}; letter-spacing: -.015em; font-weight: 700; }}

/* ---------- masthead ---------- */
.lada-masthead {{
  background: var(--lada-grad);
  border-radius: 20px;
  padding: 22px 30px;
  display: flex; align-items: center; gap: 26px;
  box-shadow: 0 10px 30px rgba(95,30,190,.26);
  margin-bottom: 16px;
  position: relative; overflow: hidden;
}}
.lada-masthead::after {{
  content: ""; position: absolute; right: -70px; top: -110px;
  width: 320px; height: 320px; border-radius: 50%;
  background: rgba(255,255,255,.10);
}}
.lada-masthead::before {{
  content: ""; position: absolute; right: 90px; bottom: -140px;
  width: 260px; height: 260px; border-radius: 50%;
  background: rgba(255,255,255,.07);
}}
.lada-masthead img {{ height: 62px; z-index: 1; filter: drop-shadow(0 2px 6px rgba(0,0,0,.18)); }}
.lada-mast-text {{ z-index: 1; }}
.lada-mast-title {{
  color: #fff; font-size: 1.72rem; font-weight: 800;
  letter-spacing: .5px; line-height: 1.12; margin: 0;
  text-transform: uppercase; font-style: italic;
}}
.lada-mast-sub {{
  color: rgba(255,255,255,.90); font-size: .90rem; margin-top: 5px;
  letter-spacing: .35px;
}}
.lada-mast-chips {{ margin-top: 9px; display: flex; gap: 7px; flex-wrap: wrap; }}
.lada-mast-chips span {{
  background: rgba(255,255,255,.19); color: #fff; font-size: .70rem;
  padding: 3px 11px; border-radius: 999px; border: 1px solid rgba(255,255,255,.34);
  letter-spacing: .4px; font-weight: 600;
}}

/* ---------- cards ---------- */
.lada-card {{
  background: #fff; border: 1px solid {config.lighten(blue, 0.80)};
  border-radius: var(--lada-radius); padding: 18px 20px;
  box-shadow: var(--lada-shadow); margin-bottom: 14px;
}}
.lada-card-title {{
  font-size: .74rem; font-weight: 800; letter-spacing: 1.3px;
  text-transform: uppercase; color: {purple}; margin-bottom: 10px;
  display: flex; align-items: center; gap: 8px;
}}
.lada-card-title::before {{
  content: ""; width: 4px; height: 15px; border-radius: 3px;
  background: var(--lada-grad);
}}

/* ---------- section heading ---------- */
.lada-section {{
  display:flex; align-items:center; gap:12px; margin: 6px 0 12px 0;
}}
.lada-section-num {{
  background: var(--lada-grad); color:#fff; font-weight:800; font-size:.80rem;
  width:32px; height:32px; border-radius:9px; display:flex;
  align-items:center; justify-content:center; flex: 0 0 32px;
  box-shadow: 0 3px 10px rgba(95,30,190,.30);
}}
.lada-section-txt h3 {{ margin:0; font-size:1.18rem; }}
.lada-section-txt p {{ margin:1px 0 0 0; font-size:.82rem; color:{muted}; }}

/* ---------- metrics ---------- */
.lada-metric-row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
.lada-metric {{
  flex: 1 1 150px; background: #fff; border-radius: 13px; padding: 13px 16px;
  border: 1px solid {config.lighten(blue, 0.80)}; box-shadow: var(--lada-shadow);
  border-left: 4px solid {blue}; min-width: 132px;
}}
.lada-metric.purple {{ border-left-color: {purple}; }}
.lada-metric.teal {{ border-left-color: {teal}; }}
.lada-metric.dark {{ border-left-color: {dark}; }}
.lada-metric-label {{
  font-size: .66rem; text-transform: uppercase; letter-spacing: 1.1px;
  color: {muted}; font-weight: 700;
}}
.lada-metric-value {{
  font-size: 1.55rem; font-weight: 800; color: {dark}; line-height: 1.16;
  margin-top: 2px;
}}
.lada-metric-hint {{ font-size: .70rem; color: {muted}; margin-top: 1px; }}

/* ---------- key status pill ---------- */
.lada-pill {{
  display:inline-flex; align-items:center; gap:7px; padding:5px 14px;
  border-radius:999px; font-size:.78rem; font-weight:700; letter-spacing:.3px;
}}
.lada-pill.active {{ background:{config.lighten(teal, .84)}; color:{config.darken(teal, .32)};
  border:1px solid {teal}; }}
.lada-pill.inactive {{ background:#FDECEC; color:#9B2226; border:1px solid {p.get('danger', '#D93F3F')}; }}
.lada-pill.unknown {{ background:{config.lighten('#E8A400', .84)}; color:#7A5600;
  border:1px solid #E8A400; }}
.lada-dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
.lada-dot.on {{ background:{teal}; box-shadow:0 0 0 3px {config.lighten(teal, .74)}; }}
.lada-dot.off {{ background:{p.get('danger', '#D93F3F')}; box-shadow:0 0 0 3px #FBDCDC; }}
.lada-dot.warn {{ background:#E8A400; box-shadow:0 0 0 3px #FBEFCF; }}

/* ---------- sidebar agent rail ---------- */
section[data-testid="stSidebar"] {{
  background: linear-gradient(178deg, {dark} 0%, {config.mix(dark, purple, .34)} 100%);
  border-right: 1px solid rgba(255,255,255,.07);
}}
section[data-testid="stSidebar"] * {{ color: #E9F0FF; }}
section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {{ color:#fff; }}
.lada-rail-head {{
  font-size:.64rem; letter-spacing:1.5px; text-transform:uppercase;
  color: rgba(255,255,255,.62); font-weight:800; margin: 6px 0 9px 2px;
}}
.lada-overall {{
  background: rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.13);
  border-radius:12px; padding:11px 13px; margin-bottom:13px;
}}
.lada-overall-top {{ display:flex; justify-content:space-between; align-items:baseline; }}
.lada-overall-top b {{ font-size:1.28rem; color:#fff; }}
.lada-overall-top span {{ font-size:.68rem; color:rgba(255,255,255,.66);
  text-transform:uppercase; letter-spacing:.9px; font-weight:700; }}
.lada-bar-outer {{
  height:7px; border-radius:99px; background: rgba(255,255,255,.15);
  margin-top:8px; overflow:hidden;
}}
.lada-bar-inner {{
  height:100%; border-radius:99px;
  background: linear-gradient(90deg, {teal}, {blue}, {accent});
  transition: width .35s ease;
}}
.lada-agent {{
  display:flex; gap:11px; align-items:flex-start; padding:9px 11px;
  border-radius:11px; margin-bottom:6px; border:1px solid transparent;
  background: rgba(255,255,255,.035);
}}
.lada-agent.current {{
  background: rgba(60,145,255,.20); border-color: {blue};
  box-shadow: 0 0 0 1px rgba(60,145,255,.30) inset;
}}
.lada-agent.done {{ background: rgba(0,164,166,.15); border-color: rgba(0,164,166,.45); }}
.lada-agent.failed {{ background: rgba(217,63,63,.16); border-color: rgba(217,63,63,.5); }}
.lada-agent.locked {{ opacity:.50; }}
.lada-agent-badge {{
  flex:0 0 27px; width:27px; height:27px; border-radius:8px;
  display:flex; align-items:center; justify-content:center;
  font-size:.70rem; font-weight:800; color:#fff;
  background: rgba(255,255,255,.16); border:1px solid rgba(255,255,255,.24);
}}
.lada-agent.done .lada-agent-badge {{ background:{teal}; border-color:{teal}; }}
.lada-agent.current .lada-agent-badge {{ background:{blue}; border-color:{blue}; }}
.lada-agent.failed .lada-agent-badge {{ background:{p.get('danger', '#D93F3F')}; }}
.lada-agent-body {{ flex:1 1 auto; min-width:0; }}
.lada-agent-name {{ font-size:.83rem; font-weight:700; color:#fff; line-height:1.22; }}
.lada-agent-state {{
  font-size:.67rem; color: rgba(255,255,255,.70); margin-top:2px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}}
.lada-agent-bar {{
  height:4px; border-radius:99px; background: rgba(255,255,255,.16);
  margin-top:6px; overflow:hidden;
}}
.lada-agent-bar > div {{
  height:100%; border-radius:99px;
  background: linear-gradient(90deg, {teal}, {blue});
}}
.lada-tokchip {{
  font-size:.62rem; font-weight:700; color:{light_blue};
  background: rgba(255,255,255,.09); padding:1px 7px; border-radius:99px;
  border:1px solid rgba(255,255,255,.14); white-space:nowrap;
}}

/* ---------- sample input grid ---------- */
.lada-sample {{
  border-left:3px solid {teal}; background:{surface_alt};
  border-radius:0 10px 10px 0; padding:9px 13px; margin-bottom:8px;
}}
.lada-sample b {{ font-size:.79rem; color:{purple}; }}
.lada-sample p {{ margin:2px 0 0 0; font-size:.77rem; color:{muted}; line-height:1.42; }}

/* ---------- misc ---------- */
.lada-note {{
  background:{surface_alt}; border:1px dashed {blue}; border-radius:11px;
  padding:11px 15px; font-size:.82rem; color:{dark};
}}
.lada-scorebar {{
  height:9px; border-radius:99px; background:{config.lighten(blue,.84)}; overflow:hidden;
}}
.lada-scorebar > div {{ height:100%; border-radius:99px; background: var(--lada-grad); }}

.stButton > button, .stDownloadButton > button {{
  border-radius:10px; font-weight:700; letter-spacing:.2px;
  border:1px solid {config.lighten(blue,.62)}; transition: all .16s ease;
}}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{
  background: var(--lada-grad); border:0; color:#fff;
  box-shadow: 0 4px 14px rgba(95,30,190,.28);
}}
.stButton > button[kind="primary"]:hover {{
  filter: brightness(1.07); transform: translateY(-1px);
}}
.stProgress > div > div > div > div {{ background: var(--lada-grad); }}
.stTabs [data-baseweb="tab-list"] {{ gap:4px; border-bottom:1px solid {config.lighten(blue,.80)}; }}
.stTabs [data-baseweb="tab"] {{
  border-radius:9px 9px 0 0; font-weight:650; font-size:.86rem; padding:7px 15px;
}}
.stTabs [aria-selected="true"] {{ background:{surface_alt}; color:{purple}; }}
div[data-testid="stExpander"] details {{
  border-radius:12px; border:1px solid {config.lighten(blue,.80)}; background:#fff;
}}
div[data-testid="stDataFrame"] {{ border-radius:11px; overflow:hidden; }}
.stTextInput input, .stTextArea textarea, .stNumberInput input {{
  border-radius:9px !important;
}}
.stTextArea textarea {{ font-size:.86rem; }}
</style>
""",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------
def masthead(subtitle: str = "", chips: tuple[str, ...] = (),
             palette: dict[str, str] | None = None) -> None:
    logo_uri = _data_uri(graphics.logo_for(palette))
    chip_html = "".join(f"<span>{c}</span>" for c in chips)
    st.markdown(
        f"""
<div class="lada-masthead">
  {'<img src="' + logo_uri + '" alt="Career Shaper"/>' if logo_uri else ''}
  <div class="lada-mast-text">
    <div class="lada-mast-title">Learning Asset Development Agent</div>
    <div class="lada-mast-sub">{subtitle}</div>
    {'<div class="lada-mast-chips">' + chip_html + '</div>' if chip_html else ''}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def section(number: str, title: str, caption: str = "") -> None:
    st.markdown(
        f"""
<div class="lada-section">
  <div class="lada-section-num">{number}</div>
  <div class="lada-section-txt"><h3>{title}</h3><p>{caption}</p></div>
</div>
""",
        unsafe_allow_html=True,
    )


def card_open(title: str = "") -> None:
    head = f'<div class="lada-card-title">{title}</div>' if title else ""
    st.markdown(f'<div class="lada-card">{head}', unsafe_allow_html=True)


def card_close() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def metric_row(items: list[tuple[str, str, str, str]]) -> None:
    """``items`` = list of ``(label, value, hint, tone)``; tone in ''|purple|teal|dark."""
    cells = "".join(
        f'<div class="lada-metric {tone}">'
        f'<div class="lada-metric-label">{label}</div>'
        f'<div class="lada-metric-value">{value}</div>'
        f'<div class="lada-metric-hint">{hint}</div></div>'
        for label, value, hint, tone in items
    )
    st.markdown(f'<div class="lada-metric-row">{cells}</div>', unsafe_allow_html=True)


def key_pill(state: str, detail: str = "") -> str:
    """Return HTML for the API-key status pill. ``state`` in active|inactive|unknown."""
    mapping = {
        "active": ("active", "on", "Active"),
        "inactive": ("inactive", "off", "Use another key"),
        "unknown": ("unknown", "warn", "Not validated"),
    }
    cls, dot, label = mapping.get(state, mapping["unknown"])
    suffix = f" &middot; {detail}" if detail else ""
    return (f'<span class="lada-pill {cls}"><span class="lada-dot {dot}"></span>'
            f'{label}{suffix}</span>')


def note(text: str) -> None:
    st.markdown(f'<div class="lada-note">{text}</div>', unsafe_allow_html=True)


def score_bar(value: float, out_of: float = 10.0) -> str:
    pct = max(0.0, min(100.0, (value / out_of) * 100 if out_of else 0))
    return f'<div class="lada-scorebar"><div style="width:{pct:.1f}%"></div></div>'
