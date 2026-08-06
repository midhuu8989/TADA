"""Security layer: at-rest API-key encryption, admin auth, log redaction.

Design notes
------------
* The Generative AI key is **never** written to disk in plaintext and never
  rendered in the UI beyond a masked tail. It lives encrypted in the SQLite
  settings table under a Fernet key held in ``data/.keyring`` (0600 where the
  platform supports it).
* The admin password is compared as a PBKDF2-HMAC-SHA256 hash with a constant
  salt and a fixed comparison (``hmac.compare_digest``), so no plaintext
  password sits in source or memory longer than the request.
* :func:`redact` is applied to every exception string surfaced to the UI or the
  run log, so a key echoed back by an SDK error can never leak into artifacts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import stat

from cryptography.fernet import Fernet, InvalidToken

from . import config

_PBKDF2_ROUNDS = 240_000
_ADMIN_SALT = b"lada.admin.v1"

#: Live keys observed this process, used to scrub them out of error strings.
_SEEN_SECRETS: set[str] = set()

# Google issues two key shapes: legacy "AIza..." (39 chars) and the newer
# "AQ.<base64ish>" form. Catch both, plus generic api_key=... assignments.
_KEY_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bAQ\.[A-Za-z0-9_\-.]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)([A-Za-z0-9_\-.]{16,})"),
)


# --------------------------------------------------------------------------
# Admin authentication
# --------------------------------------------------------------------------
def _hash_password(password: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               _ADMIN_SALT, _PBKDF2_ROUNDS)


def admin_password_digest() -> bytes:
    """Digest of the configured admin password (env override supported)."""
    configured = os.getenv(config.ADMIN_PASSWORD_ENV) or config.DEFAULT_ADMIN_PASSWORD
    return _hash_password(configured)


def verify_admin_password(candidate: str) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(_hash_password(candidate), admin_password_digest())


# --------------------------------------------------------------------------
# Fernet keyring for the API key
# --------------------------------------------------------------------------
def _load_or_create_fernet() -> Fernet:
    path = config.KEYRING_PATH
    if path.exists():
        raw = path.read_bytes().strip()
        if raw:
            try:
                return Fernet(raw)
            except (ValueError, TypeError):
                pass  # corrupt keyring - regenerate below
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    _harden(path)
    return Fernet(key)


def _harden(path) -> None:
    """Best-effort restriction of file permissions to the owner."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows ACLs / restricted FS - not fatal


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage; returns URL-safe base64 text."""
    remember_secret(plaintext)
    token = _load_or_create_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(token: str) -> str | None:
    """Decrypt a stored secret, or ``None`` if it cannot be read."""
    if not token:
        return None
    try:
        value = _load_or_create_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None
    remember_secret(value)
    return value


# --------------------------------------------------------------------------
# Masking & redaction
# --------------------------------------------------------------------------
def remember_secret(value: str | None) -> None:
    if value and len(value) >= 12:
        _SEEN_SECRETS.add(value)


#: Bullets, not asterisks: the mask is rendered inside markdown, where a run of
#: asterisks is parsed as emphasis and mangles the display.
_MASK_CHAR = "•"


def mask_key(value: str | None) -> str:
    """``AIza••••••••WxYz`` style mask - enough to identify, not enough to use."""
    if not value:
        return "not set"
    value = value.strip()
    if len(value) <= 10:
        return _MASK_CHAR * len(value)
    return f"{value[:4]}{_MASK_CHAR * 8}{value[-4:]}"


def key_fingerprint(value: str | None) -> str:
    """Stable short fingerprint so the same key is recognisable across runs."""
    if not value:
        return "-"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def redact(text: object) -> str:
    """Strip any credential-looking substring out of ``text``."""
    out = str(text)
    for secret in _SEEN_SECRETS:
        if secret and secret in out:
            out = out.replace(secret, "[REDACTED-KEY]")
    for pattern in _KEY_PATTERNS:
        if pattern.groups >= 2:
            out = pattern.sub(lambda m: m.group(1) + "[REDACTED-KEY]", out)
        else:
            out = pattern.sub("[REDACTED-KEY]", out)
    return out


def new_token() -> str:
    """Opaque random identifier (job ids, csrf-ish nonces)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(9)).decode("ascii").rstrip("=")


def looks_like_google_key(value: str | None) -> bool:
    """Cheap client-side shape check before spending a network round-trip.

    Deliberately permissive: Google ships both legacy ``AIza...`` keys and the
    newer dotted ``AQ.<payload>`` form, so this only screens out obvious
    non-keys (too short, whitespace, quotes) and lets the live call be the
    real arbiter.
    """
    if not value:
        return False
    value = value.strip()
    return len(value) >= 20 and bool(re.fullmatch(r"[A-Za-z0-9_\-.]+", value))
