# -*- coding: utf-8 -*-
"""Shared configuration for running the Talkin bot on Giant Chat.

The merged repository keeps the original Giant bot (``bot.py``) untouched and
adds this package.  Configuration is read from the repository's ``config.json``
(the same file the Giant bot uses) and can be overridden with environment
variables so Railway deployments do not need to store secrets in Git.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # the repository root is on sys.path when the runner starts
    from auth_helpers import giant_auth_email, select_supabase_key
except Exception:  # pragma: no cover - fallback for isolated tests
    def select_supabase_key(environ) -> str:  # type: ignore
        for name in ("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY", "SUPABASE_SECRET_KEY", "SUPABASE_KEY"):
            value = str(environ.get(name) or "").strip()
            if value:
                return value
        return ""

    def giant_auth_email(username: str) -> str:  # type: ignore
        import re

        normalized = re.sub(r"[^a-z0-9_]", "", str(username or "").strip().lower())
        return f"{normalized}@giant.app" if normalized else ""


REPO_ROOT = Path(__file__).resolve().parent.parent


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on", "enable", "enabled"}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).replace("،", ",").split(",") if part.strip()]


def load_repo_config(path: Path | None = None) -> dict:
    path = path or REPO_ROOT / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@dataclass
class Settings:
    supabase_url: str = ""
    supabase_key: str = ""
    username: str = ""
    password: str = ""
    owner_username: str = ""
    site_origin: str = "https://giant-chat-app.lovable.app"
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "runtime_state" / "talkin")
    assets_dir: Path = field(default_factory=lambda: REPO_ROOT / "assets")
    public_media_url: str = ""
    poll_seconds: float = 2.0
    verification_enabled: bool = False
    sync_vip_users: bool = True
    rooms: list[str] = field(default_factory=list)
    music_bucket: str = "bot-music"
    image_bucket: str = "bot-publish"
    game_bucket: str = "bot-games"
    request_timeout: float = 30.0
    log_level: str = "INFO"
    room_password: str = ""

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Settings":
        raw = load_repo_config(config_path)
        env = os.environ

        def pick(config_key: str, env_names: tuple[str, ...], default: str = "") -> str:
            for name in env_names:
                value = str(env.get(name) or "").strip()
                if value:
                    return value
            return str(raw.get(config_key) or default).strip()

        supabase_url = pick("supabase_url", ("SUPABASE_URL",))
        supabase_key = select_supabase_key(env) or str(raw.get("supabase_key") or "").strip()
        username = pick("username", ("GIANT_USERNAME",))
        password = str(env.get("GIANT_PASSWORD") or raw.get("password") or "")
        owner = pick("owner_username", ("OWNER_USERNAME", "BOT_MASTER"), username)

        data_dir = Path(
            os.getenv("TALKIN_DATA_DIR")
            or raw.get("talkin_data_dir")
            or (REPO_ROOT / "runtime_state" / "talkin")
        )
        assets_dir = Path(
            os.getenv("BOT_ASSETS_DIR")
            or raw.get("talkin_assets_dir")
            or (REPO_ROOT / "assets")
        )

        public_media_url = str(
            os.getenv("PUBLIC_MEDIA_URL")
            or raw.get("public_media_url")
            or raw.get("public_base_url")
            or ""
        ).strip().rstrip("/")
        if not public_media_url:
            domain = str(os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
            if domain:
                public_media_url = "https://" + domain.strip("/")

        try:
            poll_seconds = max(1.0, float(os.getenv("TALKIN_POLL_SECONDS") or raw.get("poll_seconds") or 2))
        except (TypeError, ValueError):
            poll_seconds = 2.0

        verification = _as_bool(
            os.getenv("TALKIN_VERIFICATION_ENABLED", raw.get("talkin_verification_enabled")),
            default=False,
        )
        sync_vip = _as_bool(
            os.getenv("TALKIN_SYNC_VIP", raw.get("talkin_sync_vip")),
            default=True,
        )
        rooms = _as_list(os.getenv("TALKIN_ROOMS") or raw.get("talkin_rooms"))

        settings = cls(
            supabase_url=supabase_url.rstrip("/"),
            supabase_key=supabase_key,
            username=username,
            password=password,
            owner_username=owner or username,
            site_origin=str(
                os.getenv("GIANT_SITE_ORIGIN") or raw.get("giant_site_origin") or "https://giant-chat-app.lovable.app"
            ).strip().rstrip("/"),
            data_dir=data_dir,
            assets_dir=assets_dir,
            public_media_url=public_media_url,
            poll_seconds=poll_seconds,
            verification_enabled=verification,
            sync_vip_users=sync_vip,
            rooms=rooms,
            music_bucket=str(raw.get("music_bucket") or "bot-music").strip(),
            image_bucket=str(raw.get("publish_bucket") or "bot-publish").strip(),
            game_bucket=str(raw.get("game_bucket") or "bot-games").strip(),
            request_timeout=max(5.0, float(os.getenv("TALKIN_HTTP_TIMEOUT") or 30)),
            log_level=str(os.getenv("LOG_LEVEL") or "INFO").upper(),
            room_password=str(env.get("ROOM_PASSWORD") or raw.get("room_password") or ""),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        missing = []
        placeholders = ("ضع_", "اسم_", "كلمة_", "example", "xxxx")

        def real(value: str) -> bool:
            text = str(value or "").strip().casefold()
            return bool(text) and not any(marker in text for marker in placeholders)

        if not real(self.supabase_url):
            missing.append("SUPABASE_URL")
        if not real(self.supabase_key):
            missing.append("SUPABASE_KEY")
        if not real(self.username):
            missing.append("GIANT_USERNAME")
        if not real(self.password):
            missing.append("GIANT_PASSWORD")
        if missing:
            raise RuntimeError("إعدادات Giant Chat ناقصة: " + ", ".join(missing))

    def resolve_email(self) -> str:
        """Return the deterministic fallback auth email for the bot account."""
        return giant_auth_email(self.username)
