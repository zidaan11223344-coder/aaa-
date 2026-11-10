# -*- coding: utf-8 -*-
"""Entry point: run the complete Talkin bot on Giant Chat.

Usage::

    python -m talkin_full.runner

Environment (same names the original Giant bot already uses):
    SUPABASE_URL, SUPABASE_KEY / SUPABASE_PUBLISHABLE_KEY ...
    GIANT_USERNAME, GIANT_PASSWORD, OWNER_USERNAME
Optional:
    TALKIN_DATA_DIR, TALKIN_ROOMS, TALKIN_VERIFICATION_ENABLED,
    PUBLIC_MEDIA_URL, PORT
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .config import REPO_ROOT, Settings
from .giant_api import GiantApi, now_iso


def _configure_logging(level: str) -> logging.Logger:
    log_dir = REPO_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_dir / "talkin_giant.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
    return logging.getLogger("talkin_full")


def _prepare_environment(settings: Settings, api: GiantApi, rooms: list[str]) -> None:
    """Set every variable the vendored Talkin module reads at import time."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "generated_gifts").mkdir(parents=True, exist_ok=True)
    first_room = rooms[0] if rooms else "main"
    media_base = settings.public_media_url
    if not media_base:
        port = str(os.getenv("PORT") or os.getenv("ASSET_HTTP_PORT") or "8080").strip()
        media_base = f"http://127.0.0.1:{port}"
    env = {
        "BOT_ID": api.username,
        "BOT_USERNAME": api.username,
        "BOT_PWD": "giant-supabase-session",
        "BOT_MASTER": settings.owner_username or api.username,
        "MASTER_USERNAME": settings.owner_username or api.username,
        "GROUP_TO_JOIN": first_room,
        "AUTO_JOIN_ALL_ROOMS": "0",
        "DATA_DIR": str(settings.data_dir),
        "BOT_DATA_DIR": str(settings.data_dir),
        "BOT_ASSETS_DIR": str(settings.assets_dir),
        "PUBLIC_MEDIA_URL": media_base,
        "ASSET_HTTP_ENABLED": os.getenv("ASSET_HTTP_ENABLED", "1"),
        "ASSET_HTTP_PORT": str(os.getenv("PORT") or os.getenv("ASSET_HTTP_PORT") or "8080"),
        "VERIFICATION_ENABLED": "on" if settings.verification_enabled else "off",
        "RUNNING_AS_MASTER": "0",
        "SUPABASE_URL": settings.supabase_url,
        "SUPABASE_KEY": settings.supabase_key,
        "SUPABASE_EMAIL": getattr(api, "_auth_email", "") or settings.resolve_email(),
        "SUPABASE_PASSWORD": settings.password,
        "DB_CONFIG_PATH": str(REPO_ROOT / "config.json"),
    }
    for key, value in env.items():
        if value is not None:
            os.environ.setdefault(key, str(value))
    # These must win over any stale values inherited from the host.
    os.environ["BOT_ID"] = api.username
    os.environ["BOT_USERNAME"] = api.username
    os.environ["BOT_MASTER"] = settings.owner_username or api.username
    os.environ["MASTER_USERNAME"] = settings.owner_username or api.username
    os.environ["GROUP_TO_JOIN"] = first_room
    os.environ["DATA_DIR"] = str(settings.data_dir)
    os.environ["BOT_ASSETS_DIR"] = str(settings.assets_dir)
    os.environ["PUBLIC_MEDIA_URL"] = media_base
    os.environ["SUPABASE_URL"] = settings.supabase_url
    os.environ["SUPABASE_KEY"] = settings.supabase_key
    os.environ["SUPABASE_EMAIL"] = env["SUPABASE_EMAIL"]
    os.environ["SUPABASE_PASSWORD"] = settings.password
    os.environ["VERIFICATION_ENABLED"] = env["VERIFICATION_ENABLED"]
    if str(REPO_ROOT) not in sys.path:
        sys.path.append(str(REPO_ROOT))


def _seed_master_file(settings: Settings) -> None:
    path = settings.data_dir / "masters.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(data, list):
            data = []
        names = {str(x).strip().casefold() for x in data if str(x).strip()}
        owner = str(settings.owner_username or "").strip()
        if owner and owner.casefold() not in names:
            data.append(owner)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _seed_verified_from_vip(settings: Settings, log: logging.Logger) -> None:
    """Mirror the Giant bot's VIP list into Talkin's verification list."""
    if not settings.sync_vip_users:
        return
    source = REPO_ROOT / "vip_users.json"
    if not source.exists():
        return
    try:
        vip = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return
    names: list[str] = []
    if isinstance(vip, dict):
        for key, value in vip.items():
            if isinstance(value, dict):
                name = str(value.get("username") or key).strip()
            else:
                name = str(key).strip()
            if name:
                names.append(name)
    elif isinstance(vip, list):
        names = [str(item).strip() for item in vip if str(item).strip()]
    if not names:
        return
    def _names_from_saved(data) -> set[str]:
        found: set[str] = set()
        containers = {
            "users", "data", "list", "items", "records", "accounts", "verified_users",
            "vip_users", "members", "entries", "version", "format", "schema_version", "type", "source",
        }
        identity_keys = {"username", "user_name", "name", "account", "user"}

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key).strip()
                    key_norm = key_text.casefold()
                    if key_norm in identity_keys:
                        if isinstance(child, str) and child.strip():
                            found.add(child.strip().lstrip("@"))
                        elif isinstance(child, (dict, list, tuple)):
                            walk(child)
                    elif key_norm in containers:
                        walk(child)
                    else:
                        if key_text:
                            found.add(key_text.lstrip("@"))
                        if isinstance(child, (dict, list, tuple)):
                            walk(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    if isinstance(child, str) and child.strip():
                        found.add(child.strip().lstrip("@"))
                    else:
                        walk(child)

        walk(data)
        return found

    for filename in ("verified_users.json", "vip_users.json"):
        target = settings.data_dir / filename
        try:
            existing = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
            merged = {name.casefold(): name for name in _names_from_saved(existing)}
            added = 0
            for name in names:
                if name.casefold() not in merged:
                    merged[name.casefold()] = name
                    added += 1
            if added:
                payload = {
                    "version": 1,
                    "users": [{"username": name} for name in sorted(merged.values())],
                }
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                log.info("تمت مزامنة %s حساب VIP إلى %s", added, filename)
        except Exception:
            log.warning("تعذر مزامنة قائمة VIP إلى %s", filename, exc_info=True)


def main() -> int:
    try:
        settings = Settings.load()
    except Exception as exc:
        print(f"[TALKIN] {exc}", file=sys.stderr, flush=True)
        return 2
    log = _configure_logging(settings.log_level)
    log.info("تشغيل بوت Talkin الكامل على جينات شات كـ @%s", settings.username)

    api = GiantApi(settings, log)
    try:
        api.login()
    except Exception as exc:
        log.error("فشل تسجيل الدخول إلى Giant Chat: %s", exc)
        print(f"[TALKIN] {exc}", file=sys.stderr, flush=True)
        return 3
    rooms = [name for name in settings.rooms if name]
    if not rooms:
        rooms = [room["name"] for room in api.rooms_for_bot()]
    if not rooms:
        log.warning("لم يُعثر على غرف للبوت؛ أضف البوت إلى غرفة أو حدّد TALKIN_ROOMS")
    _prepare_environment(settings, api, rooms)
    _seed_master_file(settings)
    _seed_verified_from_vip(settings, log)

    # The vendored module and adapter read the environment above at import time.
    from .vendor import talkin_bot as tb  # noqa: E402
    from .adapter import GiantTalkinBot  # noqa: E402

    bot = GiantTalkinBot(api, log)

    # Map local media URLs (/gifts/..., /publish/...) back to their files so
    # they can be mirrored to Supabase Storage before being sent to Giant.
    def resolve_media(url: str) -> Path | None:
        path = ""
        try:
            from urllib.parse import unquote, urlparse

            parsed = urlparse(str(url or ""))
            path = unquote(parsed.path)
        except Exception:
            return None
        routes = {
            "/assets/": tb.ASSETS_DIR,
            "/gifts/": tb.GENERATED_GIFTS_DIR,
            "/cricket-media/": tb.DATA_DIR / "cricket_media",
            "/billion/": tb.BASE_DIR / "generated_billion",
            "/games/": tb.BASE_DIR / "generated_games",
            "/welcomes/": tb.BASE_DIR / "generated_welcomes",
            "/publish/": tb.BASE_DIR / "generated_publish",
            "/media/": tb.BASE_DIR / "generated_music",
            "/lookalikes/": tb.LOOKALIKE_DIR,
        }
        for prefix, root in routes.items():
            if path and path.startswith(prefix):
                candidate = (Path(root) / path[len(prefix):].lstrip("/")).resolve()
                try:
                    if Path(root).resolve() in candidate.parents and candidate.is_file():
                        return candidate
                except Exception:
                    return None
        return None

    api.set_media_resolver(resolve_media)

    try:
        tb.start_asset_server()
    except Exception:
        log.warning("تعذر تشغيل خادم الأصول", exc_info=True)
    try:
        bot._start_cpu_guard()
    except Exception:
        pass

    stop = threading.Event()

    def handle_signal(signum, _frame):
        log.info("استلمت إشارة %s؛ إيقاف البوت", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_signal)
        except Exception:
            pass

    # Join every configured / member room.
    for name in rooms:
        try:
            joined = bot.join_room(name, force=True)
            log.info("انضمام إلى %s: %s", name, "أُرسل" if joined else "مؤجل/موجود")
        except Exception:
            log.exception("فشل الانضمام إلى %s", name)
    # The join RPC may answer "already a member" as an error; polling must
    # still start for every room the account already belongs to.
    try:
        membership = {room["name"].casefold() for room in api.rooms_for_bot()}
        for name in rooms:
            if name.casefold() in membership:
                bot.known_rooms.add(tb._norm_room(name))
                bot.connected_rooms.add(name)
    except Exception:
        log.debug("تعذر تأكيد العضوية", exc_info=True)
    try:
        bot.request_room_occupants(rooms[0]) if rooms else None
    except Exception:
        log.debug("تعذر تحميل القائمة الأولية", exc_info=True)

    log.info("البوت جاهز؛ يبدأ متابعة رسائل Giant")
    cursors: dict[str, str] = {}
    seen: dict[str, set[str]] = {}
    presence_baseline: dict[str, set[str]] = {}
    presence_roles: dict[str, dict[str, str]] = {}
    last_heartbeat = 0.0
    last_presence = 0.0
    last_roster = 0.0
    last_room_sync = 0.0

    def current_rooms() -> list[dict]:
        return [{"id": api.room_id(name), "name": name} for name in sorted(getattr(bot, "connected_rooms", set()) or set()) if name]

    while not stop.is_set():
        loop_start = time.time()
        try:
            api.refresh_session()
            for room in current_rooms():
                rid, name = room["id"], room["name"]
                if not rid:
                    continue
                cursor = cursors.setdefault(rid, now_iso())
                rows = api.fetch_room_messages(rid, cursor, limit=100)
                room_seen = seen.setdefault(rid, set())
                for row in rows:
                    mid = str(row.get("id") or "")
                    if mid and mid in room_seen:
                        continue
                    if mid:
                        room_seen.add(mid)
                        if len(room_seen) > 800:
                            room_seen.clear()
                    bot.feed_room_message(row)
                    created = str(row.get("created_at") or "")
                    if created and created > cursor:
                        cursor = created
                cursors[rid] = cursor

            for row in api.fetch_dms(limit=50):
                bot.feed_dm(row)
                if row.get("id"):
                    api.delete_dm(str(row["id"]))

            now = time.time()
            if now - last_heartbeat >= 30:
                for room in current_rooms():
                    api.heartbeat(room["name"])
                last_heartbeat = now

            if now - last_presence >= 25:
                for room in current_rooms():
                    members = api.members(room["name"])
                    present = {m["user_id"] for m in members if m.get("online")}
                    roles = {m["username"]: m.get("role", "member") for m in members}
                    baseline = presence_baseline.get(rid := room["id"])
                    if baseline is not None:
                        for member in members:
                            uid = member["user_id"]
                            if uid in present and uid not in baseline:
                                bot.handle_room_event(
                                    {
                                        "room_event": {
                                            1: "user_joined",
                                            2: member["username"],
                                            8: member.get("role", "member"),
                                            13: room["name"],
                                            17: member["username"],
                                            22: member["username"],
                                        }
                                    }
                                )
                            elif uid in baseline and uid not in present:
                                bot.handle_room_event(
                                    {
                                        "room_event": {
                                            1: "user_left",
                                            2: member["username"],
                                            13: room["name"],
                                            17: member["username"],
                                            22: member["username"],
                                        }
                                    }
                                )
                    presence_baseline[room["id"]] = present
                    presence_roles[room["id"]] = roles
                last_presence = now

            if now - last_roster >= 600:
                for room in current_rooms():
                    try:
                        bot.request_room_occupants(room["name"])
                    except Exception:
                        log.debug("تعذر تحديث الأعضاء", exc_info=True)
                last_roster = now

            if now - last_room_sync >= 300:
                try:
                    known = {name.casefold() for name in getattr(bot, "connected_rooms", set())}
                    for room in api.rooms_for_bot():
                        if room["name"].casefold() not in known:
                            log.info("غرفة جديدة للبوت: %s", room["name"])
                            bot.join_room(room["name"], force=True)
                except Exception:
                    log.debug("تعذر مزامنة الغرف", exc_info=True)
                last_room_sync = now
        except Exception:
            log.exception("خطأ في حلقة المزامنة")
        elapsed = time.time() - loop_start
        stop.wait(max(0.5, settings.poll_seconds - elapsed))

    log.info("تم إيقاف بوت Talkin على Giant")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
