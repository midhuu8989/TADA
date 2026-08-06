"""Gemini client wrapper - the single gateway for every generative call.

Every text / image / audio request in LADA funnels through :class:`GeminiClient`,
which gives one place to enforce:

* **Token accounting** - each call writes a ``token_log`` row tagged with the
  job, agent and model, which is what the admin table and dashboards read.
* **Key hygiene** - the key is passed to the SDK and never logged; all raised
  errors are pushed through :func:`lada.security.redact`.
* **Model fallback** - Gemini retires models per-key (2.5 text models 404 for
  new keys) and free-tier keys have no image quota, so each modality walks a
  candidate list and reports precisely why it gave up.
"""

from __future__ import annotations

import json
import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from . import config, security, store

TTS_SAMPLE_RATE = 24_000
TTS_SAMPLE_WIDTH = 2
TTS_CHANNELS = 1


class LLMError(RuntimeError):
    """Raised when a generative call cannot be completed."""


class QuotaError(LLMError):
    """Raised specifically on HTTP 429 / resource-exhausted responses."""


class ModelUnavailable(LLMError):
    """Raised when every candidate model for a modality is unusable."""


@dataclass
class Usage:
    prompt: int = 0
    output: int = 0
    thoughts: int = 0
    total: int = 0
    model: str = ""

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.prompt + other.prompt, self.output + other.output,
                     self.thoughts + other.thoughts, self.total + other.total,
                     other.model or self.model)


@dataclass
class CallContext:
    """Identifies who is spending tokens, for the admin log."""
    job_id: str | None = None
    job_name: str = "(ad-hoc)"
    agent_no: int | None = None
    agent_name: str = "System"

    def for_agent(self, agent_no: int) -> "CallContext":
        spec = config.AGENT_BY_NUMBER.get(agent_no)
        return CallContext(self.job_id, self.job_name, agent_no,
                           spec.name if spec else f"Agent {agent_no}")


def _usage_from(response: Any, model: str) -> Usage:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return Usage(model=model)

    def _int(name: str) -> int:
        return int(getattr(meta, name, 0) or 0)

    prompt = _int("prompt_token_count")
    output = _int("candidates_token_count")
    thoughts = _int("thoughts_token_count")
    total = _int("total_token_count") or (prompt + output + thoughts)
    return Usage(prompt, output, thoughts, total, model)


def _is_quota(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429,):
        return True
    text = str(exc).lower()
    return "resource_exhausted" in text or "429" in text or "quota" in text


def _is_missing_model(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (404,):
        return True
    text = str(exc).lower()
    return "not_found" in text or "no longer available" in text or "404" in text


def _is_retryable(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (500, 502, 503, 504):
        return True
    text = str(exc).lower()
    return any(s in text for s in ("unavailable", "internal error", "deadline",
                                   "timeout", "overloaded"))


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def parse_json(text: str) -> Any:
    """Parse model JSON, tolerating code fences and trailing prose."""
    if text is None:
        raise LLMError("Model returned no text to parse.")
    raw = text.strip()
    if not raw:
        raise LLMError("Model returned an empty response.")
    fenced = _JSON_FENCE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Salvage the outermost JSON object/array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("Model response was not valid JSON.")


class GeminiClient:
    """Thin, accounted wrapper over ``google-genai``."""

    def __init__(self, api_key: str, *, context: CallContext | None = None,
                 routing: config.ModelRouting | None = None):
        if not api_key or not api_key.strip():
            raise LLMError("No Generative AI API key supplied.")
        self._api_key = api_key.strip()
        security.remember_secret(self._api_key)
        self.fingerprint = security.key_fingerprint(self._api_key)
        self.context = context or CallContext()
        self.routing = routing or config.ROUTING
        try:
            self._client = genai.Client(api_key=self._api_key)
        except Exception as exc:  # pragma: no cover - SDK construction
            raise LLMError(security.redact(exc)) from None
        # Models proven dead for this key, skipped on later calls.
        self._dead: set[str] = set()
        self._preferred: dict[str, str] = {}
        self.last_usage = Usage()
        self.notices: list[str] = []

    # ---------------------------------------------------------------- utils
    def _note(self, message: str) -> None:
        text = security.redact(message)
        if text not in self.notices:
            self.notices.append(text)

    def _candidates(self, modality: str, override: str | None = None) -> list[str]:
        if override:
            base = [override]
        else:
            base = list(getattr(self.routing, modality, ()) or ())
            preferred = self._preferred.get(modality)
            if preferred and preferred in base:
                base.remove(preferred)
                base.insert(0, preferred)
        return [m for m in base if m not in self._dead]

    def _record(self, usage: Usage, call_kind: str,
                context: CallContext | None = None) -> None:
        ctx = context or self.context
        self.last_usage = usage
        if usage.total <= 0 and usage.prompt <= 0:
            return
        try:
            store.log_tokens(
                job_id=ctx.job_id,
                job_name=ctx.job_name,
                agent_name=ctx.agent_name,
                agent_no=ctx.agent_no,
                model=usage.model,
                call_kind=call_kind,
                prompt_tokens=usage.prompt,
                output_tokens=usage.output,
                thought_tokens=usage.thoughts,
                total_tokens=usage.total,
                key_fingerprint=self.fingerprint,
            )
        except Exception as exc:  # accounting must never break generation
            self._note(f"Token log write failed: {exc}")

    def _walk(self, modality: str, models: Sequence[str],
              attempt: Callable[[str], Any], *, retries: int = 3) -> Any:
        """Try each candidate model, with backoff on transient failures.

        Gemini returns 503 UNAVAILABLE under load often enough that a single
        attempt per model loses whole agent runs, so transient failures get
        exponential backoff before the next candidate is tried.
        """
        if not models:
            raise ModelUnavailable(
                f"No usable {modality} model remains for this API key."
            )
        errors: list[str] = []
        for model in models:
            for tries in range(retries + 1):
                try:
                    result = attempt(model)
                    self._preferred[modality] = model
                    return result
                except Exception as exc:
                    msg = security.redact(exc)
                    if _is_missing_model(exc):
                        self._dead.add(model)
                        errors.append(f"{model}: unavailable for this key")
                        break
                    if _is_quota(exc):
                        errors.append(f"{model}: quota exhausted (429)")
                        break
                    if _is_retryable(exc) and tries < retries:
                        time.sleep(min(20.0, 2.0 * (2 ** tries)))
                        continue
                    errors.append(f"{model}: {msg[:220]}")
                    break
        detail = " | ".join(errors) or "unknown error"
        if all("quota" in e or "429" in e for e in errors):
            raise QuotaError(f"All {modality} models are quota-limited. {detail}")
        raise ModelUnavailable(f"{modality} generation failed. {detail}")

    # ---------------------------------------------------------------- text
    def generate_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        deep: bool = False,
        json_schema: dict | None = None,
        temperature: float = 0.65,
        max_output_tokens: int | None = None,
        thinking: str | None = None,
        model: str | None = None,
        call_kind: str = "text",
        context: CallContext | None = None,
    ) -> str:
        """Generate text (optionally schema-constrained JSON) and log usage."""
        modality = "text_deep" if deep else "text_fast"
        cfg_kwargs: dict[str, Any] = {"temperature": temperature}
        if system:
            cfg_kwargs["system_instruction"] = system
        if json_schema is not None:
            cfg_kwargs["response_mime_type"] = "application/json"
            cfg_kwargs["response_schema"] = json_schema
        if max_output_tokens:
            cfg_kwargs["max_output_tokens"] = int(max_output_tokens)

        def attempt(candidate: str) -> str:
            kwargs = dict(cfg_kwargs)
            if thinking:
                try:
                    kwargs["thinking_config"] = types.ThinkingConfig(
                        thinking_level=thinking)
                except (TypeError, ValueError):
                    kwargs.pop("thinking_config", None)
            response = self._client.models.generate_content(
                model=candidate,
                contents=prompt,
                config=types.GenerateContentConfig(**kwargs),
            )
            usage = _usage_from(response, candidate)
            self._record(usage, call_kind, context)
            text = getattr(response, "text", None)
            if not text:
                finish = ""
                try:
                    finish = str(response.candidates[0].finish_reason)
                except Exception:
                    pass
                raise LLMError(
                    f"{candidate} returned no text"
                    + (f" (finish_reason={finish})" if finish else "")
                )
            return text

        return self._walk(modality, self._candidates(modality, model), attempt)

    def generate_json(
        self,
        prompt: str,
        schema: dict,
        *,
        system: str | None = None,
        deep: bool = False,
        temperature: float = 0.55,
        thinking: str | None = None,
        call_kind: str = "json",
        context: CallContext | None = None,
        repair_attempts: int = 1,
    ) -> Any:
        """Schema-constrained JSON with a parse-repair retry."""
        last_error: Exception | None = None
        for attempt_no in range(repair_attempts + 1):
            text = self.generate_text(
                prompt if attempt_no == 0 else
                prompt + "\n\nIMPORTANT: return ONLY valid JSON matching the schema. "
                         "No prose, no code fences.",
                system=system, deep=deep, json_schema=schema,
                temperature=temperature if attempt_no == 0 else 0.2,
                thinking=thinking, call_kind=call_kind, context=context,
            )
            try:
                return parse_json(text)
            except LLMError as exc:
                last_error = exc
        raise LLMError(f"Could not obtain valid JSON: {last_error}")

    # --------------------------------------------------------------- image
    def generate_image(
        self,
        prompt: str,
        out_path: Path,
        *,
        aspect_ratio: str = "16:9",
        model: str | None = None,
        call_kind: str = "image",
        context: CallContext | None = None,
    ) -> Path:
        """Generate one image to ``out_path``.

        Raises :class:`QuotaError` when the key has no image quota, which lets
        Agent 3 fall back to the programmatic renderer rather than fail.
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        def attempt(candidate: str) -> Path:
            cfg: dict[str, Any] = {"response_modalities": ["IMAGE"]}
            try:
                cfg["image_config"] = types.ImageConfig(aspect_ratio=aspect_ratio)
            except (AttributeError, TypeError, ValueError):
                pass
            response = self._client.models.generate_content(
                model=candidate, contents=prompt,
                config=types.GenerateContentConfig(**cfg),
            )
            self._record(_usage_from(response, candidate), call_kind, context)
            data = self._first_inline(response, "image")
            if not data:
                raise LLMError(f"{candidate} returned no image data")
            out_path.write_bytes(data)
            return out_path

        return self._walk("image", self._candidates("image", model), attempt,
                          retries=1)

    @staticmethod
    def _first_inline(response: Any, want: str) -> bytes | None:
        try:
            parts = response.candidates[0].content.parts or []
        except (AttributeError, IndexError, TypeError):
            return None
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                mime = (getattr(inline, "mime_type", "") or "").lower()
                if want in mime or want == "any":
                    return inline.data
        return None

    # ----------------------------------------------------------------- tts
    def synthesize_speech(
        self,
        script: str,
        out_path: Path,
        *,
        voice: str = config.DEFAULT_VOICE,
        style: str = "at a natural, measured professional pace",
        accent: str = "warm, professional Indian English",
        model: str | None = None,
        call_kind: str = "tts",
        context: CallContext | None = None,
    ) -> Path:
        """Synthesize ``script`` to a 24 kHz mono WAV at ``out_path``."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        clean = " ".join((script or "").split())
        if not clean:
            raise LLMError("Empty narration script.")

        directive = (
            f"You are a professional female e-learning narrator with a {accent} "
            f"accent. Read the following narration {style}. Read only the words "
            f"given, do not add commentary, greetings or sound effects.\n\n"
            f"Narration:\n{clean}"
        )

        def attempt(candidate: str) -> Path:
            response = self._client.models.generate_content(
                model=candidate,
                contents=directive,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice))),
                ),
            )
            self._record(_usage_from(response, candidate), call_kind, context)
            pcm = self._first_inline(response, "audio")
            if not pcm:
                raise LLMError(f"{candidate} returned no audio data")
            write_wav(out_path, pcm)
            return out_path

        return self._walk("tts", self._candidates("tts", model), attempt, retries=1)

    # -------------------------------------------------------------- health
    def validate(self) -> dict[str, Any]:
        """Prove the key works with a minimal billed call."""
        for candidate in config.KEY_TEST_MODELS:
            if candidate in self._dead:
                continue
            try:
                response = self._client.models.generate_content(
                    model=candidate,
                    contents="Reply with the single word: READY",
                    config=types.GenerateContentConfig(
                        temperature=0.0, max_output_tokens=8),
                )
                usage = _usage_from(response, candidate)
                self._record(usage, "key-validation",
                             CallContext(None, "API key validation",
                                         None, "Admin / Key check"))
                return {"ok": True, "model": candidate,
                        "tokens": usage.total,
                        "detail": f"Validated against {candidate}."}
            except Exception as exc:
                if _is_missing_model(exc):
                    self._dead.add(candidate)
                    continue
                if _is_quota(exc):
                    # Quota errors prove authentication succeeded.
                    return {"ok": True, "model": candidate, "tokens": 0,
                            "detail": f"Key authenticated, but {candidate} is "
                                      "rate-limited right now (HTTP 429)."}
                return {"ok": False, "model": candidate, "tokens": 0,
                        "detail": security.redact(exc)[:400]}
        return {"ok": False, "model": "", "tokens": 0,
                "detail": "No test model is available to this key."}

    def available_models(self) -> dict[str, list[str]]:
        """Group the key's visible models by modality (admin diagnostics)."""
        out: dict[str, list[str]] = {"text": [], "image": [], "tts": [], "other": []}
        try:
            for model in self._client.models.list():
                name = model.name.replace("models/", "")
                actions = list(getattr(model, "supported_actions", None) or [])
                if "generateContent" not in actions and "predict" not in actions:
                    continue
                if "tts" in name:
                    out["tts"].append(name)
                elif "image" in name or name.startswith("imagen"):
                    out["image"].append(name)
                elif name.startswith("gemini"):
                    out["text"].append(name)
                else:
                    out["other"].append(name)
        except Exception as exc:
            self._note(f"Model listing failed: {exc}")
        return {k: sorted(v) for k, v in out.items()}


# --------------------------------------------------------------------------
# WAV helpers (Gemini TTS returns raw little-endian PCM)
# --------------------------------------------------------------------------
def write_wav(path: Path, pcm: bytes, *, rate: int = TTS_SAMPLE_RATE) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(TTS_CHANNELS)
        handle.setsampwidth(TTS_SAMPLE_WIDTH)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return path


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or TTS_SAMPLE_RATE
        return round(frames / float(rate), 2)
    except Exception:
        return 0.0


def concat_wavs(paths: Iterable[Path], out_path: Path,
                gap_seconds: float = 0.6) -> Path | None:
    """Join narration WAVs into one continuous track (full-course audio)."""
    paths = [Path(p) for p in paths if Path(p).exists()]
    if not paths:
        return None
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()
    silence = b"\x00" * int(params.framerate * params.sampwidth *
                            params.nchannels * gap_seconds)
    with wave.open(str(out_path), "wb") as writer:
        writer.setparams(params)
        for index, item in enumerate(paths):
            try:
                with wave.open(str(item), "rb") as reader:
                    if reader.getparams()[:3] != params[:3]:
                        continue
                    writer.writeframes(reader.readframes(reader.getnframes()))
            except Exception:
                continue
            if index < len(paths) - 1:
                writer.writeframes(silence)
    return out_path


# --------------------------------------------------------------------------
# Key state helpers used by the UI
# --------------------------------------------------------------------------
@dataclass
class KeyState:
    present: bool = False
    active: bool = False
    masked: str = "not set"
    fingerprint: str = "-"
    checked_at: str | None = None
    model: str | None = None
    detail: str = ""

    @property
    def pill_state(self) -> str:
        if not self.present:
            return "inactive"
        if self.active:
            return "active"
        return "unknown"

    @property
    def message(self) -> str:
        if not self.present:
            return ("No Generative AI API key is available. Open the Admin module "
                    "and add a key before starting a job.")
        if self.active:
            return f"API key {self.masked} is active and shared across all agents."
        return (f"API key {self.masked} has not passed validation. "
                "Use another key or re-validate in the Admin module.")


def read_key_state() -> KeyState:
    """Current persisted key status, without spending a network call."""
    key = store.load_api_key()
    if not key:
        return KeyState()
    status = (store.get_setting(store.API_KEY_STATUS) or "unknown").lower()
    return KeyState(
        present=True,
        active=status == "active",
        masked=security.mask_key(key),
        fingerprint=security.key_fingerprint(key),
        checked_at=store.get_setting(store.API_KEY_CHECKED),
        model=store.get_setting(store.API_KEY_MODEL),
        detail="",
    )


def validate_and_store_key(candidate: str) -> tuple[bool, str, KeyState]:
    """Test ``candidate`` against the live API, persisting it only if it works."""
    candidate = (candidate or "").strip()
    if not candidate:
        return False, "Please paste an API key first.", read_key_state()
    if not security.looks_like_google_key(candidate):
        return (False,
                "That does not look like a Google Generative AI key - expected "
                "20+ characters with no spaces or quotes (e.g. 'AIza...' or 'AQ....').",
                read_key_state())
    try:
        client = GeminiClient(candidate)
        result = client.validate()
    except LLMError as exc:
        return False, security.redact(exc), read_key_state()

    if not result["ok"]:
        store.set_setting(store.API_KEY_STATUS, "inactive")
        store.set_setting(store.API_KEY_CHECKED, store.now_iso())
        return False, f"Key rejected by Google: {result['detail']}", read_key_state()

    store.save_api_key(candidate)
    store.set_setting(store.API_KEY_STATUS, "active")
    store.set_setting(store.API_KEY_CHECKED, store.now_iso())
    store.set_setting(store.API_KEY_MODEL, result["model"])
    return True, result["detail"], read_key_state()


def revalidate_stored_key() -> tuple[bool, str, KeyState]:
    key = store.load_api_key()
    if not key:
        return False, "No stored key to validate.", read_key_state()
    return validate_and_store_key(key)


#: Names checked, in order, when adopting a key that was supplied out of band.
KEY_ENV_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")


def _key_from_secrets() -> tuple[str, str] | None:
    """Look for a key in ``st.secrets`` - the Streamlit Cloud deployment path.

    Community Cloud has no ``.env``: secrets are pasted into the app settings and
    surfaced through :data:`streamlit.secrets`. Reading them explicitly is the
    documented contract, so the app does not rely on Streamlit also mirroring
    top-level secrets into the process environment.
    """
    try:
        import streamlit as st

        for name in KEY_ENV_NAMES:
            value = str(st.secrets.get(name, "") or "").strip()
            if value:
                return name, value
    except Exception:
        pass  # no secrets file, or not running under Streamlit at all
    return None


def bootstrap_key_from_env() -> str | None:
    """Adopt ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` from secrets or the environment.

    Runs once at startup so a key supplied by the deployment - Streamlit Cloud
    secrets, or an exported environment variable - is picked up without the
    operator having to paste it into the Admin module. The key is immediately
    re-persisted encrypted; the plaintext value is never written to disk by LADA
    itself.
    """
    import os

    if store.load_api_key():
        return None

    candidates: list[tuple[str, str]] = []
    from_secrets = _key_from_secrets()
    if from_secrets:
        candidates.append(from_secrets)
    candidates.extend((name, (os.getenv(name) or "").strip())
                      for name in KEY_ENV_NAMES)

    for name, value in candidates:
        if value and security.looks_like_google_key(value):
            store.save_api_key(value)
            store.set_setting(store.API_KEY_STATUS, "unknown")
            store.log_event(None, None, "info",
                            f"Adopted API key from {name} (encrypted at rest).")
            return name
    return None


def get_client(context: CallContext | None = None) -> GeminiClient:
    """Build a client from the stored key, or raise a user-actionable error."""
    key = store.load_api_key()
    if not key:
        raise LLMError("No Generative AI API key is configured. "
                       "Add one in the Admin module.")
    return GeminiClient(key, context=context)
