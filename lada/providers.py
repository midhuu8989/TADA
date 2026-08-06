"""Secondary generation providers used when the primary key runs out of quota.

Gemini's free tier carries no image or TTS allowance, and that is not a
hypothetical: an earlier run of this pipeline placed 44 locally-composed
placeholders instead of generated artwork, and narrated 13 of 56 slides before
the TTS quota closed. Both agents already degrade gracefully, but degrading is
not the same as finishing the job.

This module adds OpenAI as a fallback for exactly those two modalities. Text is
deliberately not routed here - it never hit a limit, and rerouting a working
path buys nothing.

The fallback is only ever consulted after the primary raises
:class:`~lada.llm.QuotaError`, so a healthy Gemini key costs nothing extra and
the provider stays entirely out of the way.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config, security, store

#: Image model. gpt-image-1 is the only OpenAI model that reliably honours a
#: landscape aspect ratio, which slide artwork needs.
IMAGE_MODEL = "gpt-image-1"

#: Speech model. gpt-4o-mini-tts accepts free-text ``instructions``, which is
#: what lets the Indian-English narrator brief survive the provider switch.
TTS_MODEL = "gpt-4o-mini-tts"

#: gpt-image-1 accepts a fixed set of sizes rather than an arbitrary ratio.
#: 1536x1024 is the widest landscape it offers - the closest match to the 16:9
#: placeholders the decks are built around.
_SIZE_FOR_RATIO = {
    "16:9": "1536x1024",
    "4:3": "1536x1024",
    "3:2": "1536x1024",
    "1:1": "1024x1024",
    "9:16": "1024x1536",
    "2:3": "1024x1536",
}
_DEFAULT_SIZE = "1536x1024"

#: Gemini voice -> nearest OpenAI voice. Every target is female, matching the
#: specification's "Indian professional female" narrator.
_VOICE_MAP = {
    "Kore": "nova",           # firm, professional
    "Achernar": "shimmer",    # soft, warm
    "Leda": "coral",          # youthful, energetic
    "Aoede": "sage",          # breezy, conversational
    "Autonoe": "coral",       # bright, upbeat
    "Vindemiatrix": "sage",   # gentle, measured
    "Sulafat": "shimmer",     # warm, mature
    "Erinome": "nova",        # clear, neutral
}
_DEFAULT_VOICE = "nova"

OPENAI_KEY_SETTING = "openai_api_key_enc"
OPENAI_KEY_FINGERPRINT = "openai_api_key_fp"


class ProviderUnavailable(RuntimeError):
    """No usable fallback provider is configured."""


# --------------------------------------------------------------------------
# Key storage - same encrypted-at-rest treatment as the primary key
# --------------------------------------------------------------------------
def save_openai_key(plaintext: str) -> str:
    """Persist the OpenAI key encrypted; returns its fingerprint."""
    plaintext = (plaintext or "").strip()
    if not plaintext:
        raise ValueError("Empty OpenAI key.")
    store.set_setting(OPENAI_KEY_SETTING, security.encrypt_secret(plaintext))
    fp = security.key_fingerprint(plaintext)
    store.set_setting(OPENAI_KEY_FINGERPRINT, fp)
    return fp


def load_openai_key() -> str | None:
    token = store.get_setting(OPENAI_KEY_SETTING)
    return security.decrypt_secret(token) if token else None


def clear_openai_key() -> None:
    store.delete_setting(OPENAI_KEY_SETTING)
    store.delete_setting(OPENAI_KEY_FINGERPRINT)


def bootstrap_openai_key() -> str | None:
    """Adopt ``OPENAI_API_KEY`` from Streamlit secrets or the environment."""
    import os

    if load_openai_key():
        return None

    candidates: list[tuple[str, str]] = []
    try:
        import streamlit as st

        value = str(st.secrets.get("OPENAI_API_KEY", "") or "").strip()
        if value:
            candidates.append(("OPENAI_API_KEY (secrets)", value))
    except Exception:
        pass  # no secrets file, malformed TOML, or no Streamlit runtime
    candidates.append(("OPENAI_API_KEY", (os.getenv("OPENAI_API_KEY") or "").strip()))

    for name, value in candidates:
        if len(value) >= 20:
            save_openai_key(value)
            store.log_event(None, None, "info",
                            f"Adopted OpenAI fallback key from {name}.")
            return name
    return None


def is_configured() -> bool:
    return bool(load_openai_key())


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------
@dataclass
class FallbackResult:
    path: Path
    model: str
    provider: str = "openai"


def _client() -> Any:
    key = load_openai_key()
    if not key:
        raise ProviderUnavailable(
            "No OpenAI fallback key is configured. Add one in the Admin module "
            "to keep generating when the Gemini quota is exhausted."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise ProviderUnavailable(
            "The 'openai' package is not installed; run "
            "'pip install -r requirements.txt'."
        ) from exc
    security.remember_secret(key)
    return OpenAI(api_key=key)


def _log(kind: str, model: str, usage: Any, context: Any) -> None:
    """Mirror OpenAI usage into the same token ledger the sidebar reads."""
    prompt = output = total = 0
    if usage is not None:
        prompt = int(getattr(usage, "input_tokens", 0) or 0)
        output = int(getattr(usage, "output_tokens", 0) or 0)
        total = int(getattr(usage, "total_tokens", 0) or (prompt + output))
    if total <= 0 and prompt <= 0:
        return
    try:
        store.log_tokens(
            job_id=getattr(context, "job_id", None),
            job_name=getattr(context, "job_name", None),
            agent_name=getattr(context, "agent_name", None),
            agent_no=getattr(context, "agent_no", None),
            model=f"openai/{model}",
            call_kind=kind,
            prompt_tokens=prompt,
            output_tokens=output,
            thought_tokens=0,
            total_tokens=total,
            key_fingerprint=store.get_setting(OPENAI_KEY_FINGERPRINT) or "-",
        )
    except Exception:
        pass  # accounting must never break generation


# --------------------------------------------------------------------------
# Image
# --------------------------------------------------------------------------
def generate_image(prompt: str, out_path: Path, *, aspect_ratio: str = "16:9",
                   context: Any = None) -> FallbackResult:
    """Generate one image via OpenAI, written to ``out_path`` as PNG."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    response = _client().images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size=_SIZE_FOR_RATIO.get(aspect_ratio, _DEFAULT_SIZE),
        n=1,
    )
    _log("image", IMAGE_MODEL, getattr(response, "usage", None), context)

    payload = response.data[0]
    encoded = getattr(payload, "b64_json", None)
    if not encoded:
        raise RuntimeError(f"{IMAGE_MODEL} returned no image payload.")
    out_path.write_bytes(base64.b64decode(encoded))
    return FallbackResult(out_path, IMAGE_MODEL)


# --------------------------------------------------------------------------
# Speech
# --------------------------------------------------------------------------
def synthesize_speech(script: str, out_path: Path, *,
                      voice: str = config.DEFAULT_VOICE,
                      style: str = "at a natural, measured professional pace",
                      accent: str = "warm, professional Indian English",
                      context: Any = None) -> FallbackResult:
    """Narrate ``script`` via OpenAI into a 24 kHz mono WAV at ``out_path``.

    ``response_format="pcm"`` yields headerless 24 kHz 16-bit mono samples -
    byte-identical in layout to what Gemini returns - so the existing
    :func:`lada.llm.write_wav` framing and the WAV concatenation in Agent 4 work
    unchanged.
    """
    from .llm import write_wav  # local import: llm imports this module

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clean = " ".join((script or "").split())
    if not clean:
        raise RuntimeError("Empty narration script.")

    instructions = (
        f"Speak as a professional female e-learning narrator with a {accent} "
        f"accent. Deliver the narration {style}. Read only the words given; add "
        "no commentary, greetings or sound effects."
    )

    response = _client().audio.speech.create(
        model=TTS_MODEL,
        voice=_VOICE_MAP.get(voice, _DEFAULT_VOICE),
        input=clean,
        instructions=instructions,
        response_format="pcm",
    )
    pcm = response.read() if hasattr(response, "read") else response.content
    if not pcm:
        raise RuntimeError(f"{TTS_MODEL} returned no audio data.")
    write_wav(out_path, pcm)
    _log("tts", TTS_MODEL, None, context)
    return FallbackResult(out_path, TTS_MODEL)


def validate() -> tuple[bool, str]:
    """Cheapest possible proof that the configured OpenAI key works."""
    try:
        models = _client().models.list()
        available = {m.id for m in list(models)[:400]}
    except ProviderUnavailable as exc:
        return False, str(exc)
    except Exception as exc:
        return False, security.redact(exc)[:300]

    missing = [m for m in (IMAGE_MODEL, TTS_MODEL) if m not in available]
    if missing:
        return True, ("Key valid, but this account cannot see: "
                      + ", ".join(missing))
    return True, f"Validated - {IMAGE_MODEL} and {TTS_MODEL} both available."
