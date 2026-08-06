"""Central configuration: brand palette, model routing, paths, agent registry."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
JOBS_DIR = DATA_DIR / "jobs"
ASSETS_DIR = ROOT / "assets"
DB_PATH = DATA_DIR / "lada.db"
KEYRING_PATH = DATA_DIR / ".keyring"
UPLOADS_DIRNAME = "uploads"

for _d in (DATA_DIR, JOBS_DIR, ASSETS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

LOGO_PATH = ASSETS_DIR / "career_shaper_logo.png"
LOGO_MARK_PATH = ASSETS_DIR / "career_shaper_mark.png"

#: Drop a real brand logo in ``assets/`` under any of these names and it takes
#: precedence over the generated placeholder everywhere - app header, Excel
#: banners and slide top bars. Checked in order.
USER_LOGO_NAMES = (
    "logo.png", "logo.jpg", "logo.jpeg", "logo.webp",
    "career_shaper_logo_custom.png", "career_shaper_logo_custom.jpg",
)

#: Optional curated slide artwork. A file named ``deck<module>-slide<number>.png``
#: here is used verbatim by Agent 3 instead of generating that slide's image.
ASSETS_IMAGE_DIR = ASSETS_DIR / "images"

# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------
# Stored as a PBKDF2 hash rather than a plaintext literal. Default secret is
# "EdTech@123" as specified; override with the LADA_ADMIN_PASSWORD env var.
ADMIN_PASSWORD_ENV = "LADA_ADMIN_PASSWORD"
DEFAULT_ADMIN_PASSWORD = "EdTech@123"

CONFIDENTIALITY_NOTE = (
    "Confidential - for authorised learner and internal use only. "
    "Do not distribute without written permission."
)

# --------------------------------------------------------------------------
# HCLTech brand palette (defaults)
# --------------------------------------------------------------------------
HCLTECH_PALETTE = {
    "primary_purple": "#5F1EBE",   # Purple Heart
    "primary_blue": "#3C91FF",     # Dodger Blue
    "secondary_teal": "#00A4A6",   # Persian Green
    "dark_neutral": "#00112B",     # Midnight
    "accent_magenta": "#C626FF",   # gradient partner for purple
    "light_blue": "#BBD9FF",       # grid lines / light fills
    "surface": "#F5F8FF",          # page surface
    "surface_alt": "#EAF1FF",      # zebra rows
    "white": "#FFFFFF",
    "text_dark": "#00112B",
    "text_muted": "#5A6B85",
    "success": "#00A4A6",
    "warning": "#E8A400",
    "danger": "#D93F3F",
}

PALETTE_KEYS = tuple(HCLTECH_PALETTE.keys())

#: Keys a user is allowed to override via the palette override box.
PALETTE_OVERRIDABLE = (
    "primary_purple",
    "primary_blue",
    "secondary_teal",
    "dark_neutral",
    "accent_magenta",
    "light_blue",
    "surface",
    "surface_alt",
)

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


def normalise_hex(value: str) -> str | None:
    """Return an upper-case ``#RRGGBB`` string, or ``None`` if not a hex colour."""
    if not value:
        return None
    value = value.strip()
    if not value.startswith("#"):
        value = "#" + value
    m = _HEX_RE.fullmatch(value)
    if not m:
        return None
    h = value[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.upper()


def parse_palette_override(text: str) -> tuple[dict[str, str], list[str]]:
    """Parse a free-form palette guideline into palette overrides.

    Accepts ``key: #hex`` / ``key = #hex`` lines (keys fuzzy-matched against
    :data:`PALETTE_OVERRIDABLE`) and also a bare list of hex codes, which are
    assigned positionally to purple / blue / teal / dark / accent.

    Returns ``(overrides, notes)``.
    """
    overrides: dict[str, str] = {}
    notes: list[str] = []
    if not text or not text.strip():
        return overrides, notes

    alias = {
        "purple": "primary_purple", "primary": "primary_purple",
        "primarypurple": "primary_purple", "brandpurple": "primary_purple",
        "blue": "primary_blue", "primaryblue": "primary_blue",
        "secondary": "primary_blue", "brandblue": "primary_blue",
        "teal": "secondary_teal", "green": "secondary_teal",
        "secondaryteal": "secondary_teal", "accentteal": "secondary_teal",
        "dark": "dark_neutral", "darkneutral": "dark_neutral",
        "midnight": "dark_neutral", "darkblue": "dark_neutral",
        "accent": "accent_magenta", "magenta": "accent_magenta",
        "accentmagenta": "accent_magenta",
        "lightblue": "light_blue", "grid": "light_blue", "gridline": "light_blue",
        "surface": "surface", "background": "surface", "page": "surface",
        "surfacealt": "surface_alt", "zebra": "surface_alt", "alt": "surface_alt",
    }

    keyed_lines = 0
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("*-•").strip()
        if not line:
            continue
        parts = re.split(r"[:=]", line, maxsplit=1)
        if len(parts) != 2:
            continue
        label = re.sub(r"[^a-z]", "", parts[0].lower())
        hexes = _HEX_RE.findall(parts[1])
        if not hexes:
            continue
        target = alias.get(label)
        if target is None:
            for a_key, a_val in alias.items():
                if a_key and a_key in label:
                    target = a_val
                    break
        colour = normalise_hex(hexes[0])
        if target and colour:
            overrides[target] = colour
            keyed_lines += 1

    if not overrides:
        # Positional fallback: bare hex list.
        found = [normalise_hex(h) for h in _HEX_RE.findall(text)]
        found = [f for f in found if f]
        positional = ["primary_purple", "primary_blue", "secondary_teal",
                      "dark_neutral", "accent_magenta"]
        for key, colour in zip(positional, found):
            overrides[key] = colour
        if overrides:
            notes.append(
                f"Interpreted {len(overrides)} bare hex code(s) positionally as "
                + ", ".join(overrides)
            )
    elif keyed_lines:
        notes.append(f"Applied {keyed_lines} named colour override(s).")

    return overrides, notes


def resolve_palette(override_text: str | None = None) -> dict[str, str]:
    """Merge any override text over the HCLTech defaults."""
    palette = dict(HCLTECH_PALETTE)
    if override_text:
        overrides, _ = parse_palette_override(override_text)
        palette.update({k: v for k, v in overrides.items() if k in palette})
        # Keep derived colours coherent with any new dark neutral.
        if "dark_neutral" in overrides:
            palette["text_dark"] = overrides["dark_neutral"]
    return palette


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = (normalise_hex(value) or "#000000")[1:]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(c))) for c in rgb))


def mix(colour_a: str, colour_b: str, weight: float = 0.5) -> str:
    """Linear blend of two hex colours (``weight`` = share of ``colour_b``)."""
    ra, ga, ba = hex_to_rgb(colour_a)
    rb, gb, bb = hex_to_rgb(colour_b)
    w = max(0.0, min(1.0, weight))
    return rgb_to_hex((ra + (rb - ra) * w, ga + (gb - ga) * w, ba + (bb - ba) * w))


def lighten(colour: str, amount: float = 0.5) -> str:
    return mix(colour, "#FFFFFF", amount)


def darken(colour: str, amount: float = 0.3) -> str:
    return mix(colour, "#000000", amount)


def readable_text_on(colour: str) -> str:
    """Pick white or midnight text for adequate contrast on ``colour``."""
    r, g, b = hex_to_rgb(colour)

    def _lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)
    return "#FFFFFF" if luminance < 0.42 else HCLTECH_PALETTE["dark_neutral"]


# --------------------------------------------------------------------------
# Model routing
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelRouting:
    """Candidate model lists, tried in order until one succeeds.

    Gemini deprecates models per-key (older 2.5 text models 404 for new keys)
    and the free tier has no image quota, so every modality carries fallbacks.
    """

    text_fast: tuple[str, ...] = (
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
    )
    #: Ends with the lite model on purpose: when the pro tiers are quota-capped
    #: and the flash tiers are momentarily overloaded, finishing the job on a
    #: smaller model beats abandoning the agent run.
    text_deep: tuple[str, ...] = (
        "gemini-3.6-flash",
        "gemini-3.1-pro-preview",
        "gemini-3-pro-preview",
        "gemini-3.5-flash",
        "gemini-2.5-pro",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    )
    image: tuple[str, ...] = (
        "gemini-3.1-flash-image",
        "gemini-3.1-flash-lite-image",
        "gemini-2.5-flash-image",
        "gemini-3-pro-image",
    )
    tts: tuple[str, ...] = (
        "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-preview-tts",
        "gemini-2.5-pro-preview-tts",
    )

    def as_dict(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in asdict(self).items()}


ROUTING = ModelRouting()

#: Model used purely to prove a key works (cheapest available text model).
KEY_TEST_MODELS = ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.5-flash")

# --------------------------------------------------------------------------
# Voice options for Agent 4 (Indian professional female narration)
# --------------------------------------------------------------------------
VOICE_OPTIONS: dict[str, str] = {
    "Kore - firm, professional (default)": "Kore",
    "Achernar - soft, warm": "Achernar",
    "Leda - youthful, energetic": "Leda",
    "Aoede - breezy, conversational": "Aoede",
    "Autonoe - bright, upbeat": "Autonoe",
    "Vindemiatrix - gentle, measured": "Vindemiatrix",
    "Sulafat - warm, mature": "Sulafat",
    "Erinome - clear, neutral": "Erinome",
}
DEFAULT_VOICE = "Kore"

NARRATION_PACE: dict[str, str] = {
    "Slow (0.85x)": "slowly and very deliberately, with clear pauses between clauses",
    "Normal (1.0x)": "at a natural, measured professional pace",
    "Brisk (1.15x)": "at a brisk but clearly articulated pace",
    "Fast (1.3x)": "quickly and energetically while staying fully intelligible",
}
DEFAULT_PACE = "Normal (1.0x)"

# --------------------------------------------------------------------------
# Deck constraints
# --------------------------------------------------------------------------
MAX_SLIDES_PER_DECK = 35
DECK_MINUTES = 60
MAX_MODULE_MINUTES = 60
ALLOWED_TOPIC_MINUTES = (15, 30, 45, 60)
MAX_VOICEOVER_WORDS = 35

# --------------------------------------------------------------------------
# Agent registry
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AgentSpec:
    number: int
    key: str
    name: str
    short: str
    icon: str
    blurb: str
    produces: str


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(1, "guidesheet", "Guide-Sheet Generator Agent", "Guide Sheet", "01",
              "Builds the 8-section programme guide sheet as a brand-formatted Excel workbook.",
              "Excel (.xlsx)"),
    AgentSpec(2, "deck", "PowerPoint Presentation Agent", "Decks", "02",
              "Turns the guide sheet into 60-minute decks of up to 35 branded slides with voice-over scripts.",
              "PowerPoint (.pptx)"),
    AgentSpec(3, "images", "Image Generator Agent", "Imagery", "03",
              "Parses each deck and fills every image placeholder with on-brand generated artwork.",
              "Updated .pptx"),
    AgentSpec(4, "audio", "Audio Enabling Agent", "Narration", "04",
              "Narrates every slide from its presenter-notes script in an Indian professional female voice.",
              "Narrated .pptx + audio"),
    AgentSpec(5, "validator", "Deck Validator Agent", "Validation", "05",
              "Scores authenticity, originality, correctness, imagery, activities and feasibility.",
              "Validation report"),
)

AGENT_BY_NUMBER = {a.number: a for a in AGENTS}
AGENT_BY_KEY = {a.key: a for a in AGENTS}
TOTAL_AGENTS = len(AGENTS)

# --------------------------------------------------------------------------
# Sample input guidance shown on the landing page
# --------------------------------------------------------------------------
SAMPLE_INPUTS: tuple[dict[str, str], ...] = (
    {
        "title": "Subject / discipline",
        "example": "Cloud-Native Application Engineering; Data Science with Python; "
                   "Cyber Security Operations; Embedded Systems Design.",
    },
    {
        "title": "Curriculum & coverage",
        "example": "Paste the syllabus verbatim - units, chapters, credit structure, "
                   "prescribed textbook chapters, lab lists and evaluation scheme.",
    },
    {
        "title": "Topics & sub-topics",
        "example": "Module 2 Containerisation -> 2.1 Docker images, 2.2 Layer caching, "
                   "2.3 Multi-stage builds, 2.4 Registries & tagging strategy.",
    },
    {
        "title": "Duration",
        "example": "Total contact hours, e.g. 40 hours; the orchestrator splits this into "
                   "60-minute modules and one deck per module.",
    },
    {
        "title": "Audience & entity",
        "example": "3rd-year B.Tech CSE cohort at an HEI, or a graduate-engineer-trainee "
                   "batch at an enterprise; add prior knowledge and tooling constraints.",
    },
)

SAMPLE_CONTENT_BRIEF = """Subject: Cloud-Native Application Engineering
Audience: 3rd-year B.Tech CSE students with Java and basic Linux exposure
Intent: Make learners deployment-ready on containerised microservices

Coverage expected:
1. Cloud-native principles, 12-factor apps, monolith vs microservices
2. Containerisation with Docker - images, layers, multi-stage builds, registries
3. Orchestration with Kubernetes - pods, deployments, services, config & secrets
4. CI/CD pipelines - build, test, scan, deploy gates
5. Observability - logging, metrics, tracing, SLOs
6. Cloud security basics - least privilege, secret management, image scanning

Tooling: Docker Desktop, kubectl, minikube, GitHub Actions, Prometheus, Grafana
Assessment: pre-qualifier MCQ, hands-on lab, capstone deployment
"""


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


OFFLINE_MODE = env_flag("LADA_OFFLINE", False)
