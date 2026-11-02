"""Safe Supabase client setup and authentication error formatting."""
from __future__ import annotations

import re
from typing import Callable, Iterable, Any


_NEW_KEY_PREFIXES = ("sb_publishable_", "sb_secret_")
_SUPABASE_KEY_ENV_NAMES = (
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_KEY",
)


def select_supabase_key(environ) -> str:
    """Select the first configured key using the working control bot's order."""
    for name in _SUPABASE_KEY_ENV_NAMES:
        value = str(environ.get(name) or "").strip()
        if value:
            return value
    return ""


def giant_auth_email(username: str) -> str:
    """Map a Giant Chat username to the deterministic Supabase auth email."""
    normalized = re.sub(r"[^a-z0-9_]", "", str(username or "").strip().lower())
    return f"{normalized}@giant.app" if normalized else ""


def create_supabase_client(url: str, key: str, client_factory: Callable[[str, str], Any]):
    """Create a Supabase client with legacy JWT or current sb_* API keys.

    supabase-py versions that validate keys as JWTs reject the current short
    API key formats. A placeholder is passed only through that local check;
    the actual key is restored in the client's apiKey header immediately.
    """
    url = str(url or "").strip()
    key = str(key or "").strip()
    if not url:
        raise ValueError("SUPABASE_URL is missing.")
    if not key:
        raise ValueError("SUPABASE_KEY is missing.")

    if key.startswith(_NEW_KEY_PREFIXES):
        client = client_factory(url, "a.b.c")
        client.supabase_key = key
        headers = client.options.headers
        headers["apiKey"] = key
        headers.pop("Authorization", None)
        return client

    # Supabase's legacy anon/service_role keys are JWTs.
    if key.startswith("eyJ") and len(key.split(".")) == 3:
        return client_factory(url, key)

    raise ValueError(
        "SUPABASE_KEY has an unsupported format. Use a Supabase sb_publishable_*/"
        "sb_secret_* key or a legacy anon/service_role JWT."
    )


def safe_auth_error(error: Any, secrets: Iterable[str] = ()) -> str:
    """Return a useful, bounded auth error while redacting known secrets."""
    text = str(getattr(error, "message", None) or error or "Unknown authentication error")
    text = re.sub(r"\s+", " ", text).strip()
    for secret in sorted({str(value) for value in secrets if value}, key=len, reverse=True):
        if len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|password|access[_-]?token|refresh[_-]?token)\s*[:=]\s*[^ ,;]+",
        r"\1=[REDACTED]",
        text,
    )
    return text[:360] or "Unknown authentication error"
