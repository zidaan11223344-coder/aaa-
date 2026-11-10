# -*- coding: utf-8 -*-
"""Synchronous Giant Chat transport used by the embedded Talkin bot.

Giant Chat stores chat messages in Supabase tables (``room_messages`` and
``dm_relay``) and performs room operations through RPC functions
(``room_join``, ``room_leave``, ``room_heartbeat`` ...).  The embedded Talkin
bot is synchronous and thread based, so this module intentionally uses the
synchronous Supabase client: every packet the bot emits can be translated into
one blocking Giant call without an event-loop bridge.
"""
from __future__ import annotations

import logging
import mimetypes
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlparse

import requests
from supabase import create_client

from .config import Settings

try:  # shared with the original Giant bot
    from auth_helpers import create_supabase_client, giant_auth_email, safe_auth_error
except Exception:  # pragma: no cover
    def create_supabase_client(url: str, key: str, factory):  # type: ignore
        return factory(url, key)

    def giant_auth_email(username: str) -> str:  # type: ignore
        normalized = re.sub(r"[^a-z0-9_]", "", str(username or "").strip().lower())
        return f"{normalized}@giant.app" if normalized else ""

    def safe_auth_error(error: Any, secrets: Iterable[str] = ()) -> str:  # type: ignore
        return str(error)[:300]


class GiantError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GiantApi:
    """Thin, thread-safe wrapper around the Giant Supabase backend."""

    def __init__(self, settings: Settings, logger: logging.Logger | None = None):
        self.settings = settings
        self.log = logger or logging.getLogger("talkin_full.giant")
        self.client = None
        self.user_id = ""
        self.username = settings.username
        self._lock = threading.RLock()
        self._session_lock = threading.RLock()
        self._http = requests.Session()
        self._http.headers.update({"User-Agent": "alsfer-talkin-giant/1.0"})
        self._user_ids: dict[str, str] = {}
        self._usernames: dict[str, str] = {}
        self._photos: dict[str, str] = {}
        self._rooms_by_id: dict[str, str] = {}
        self._rooms_by_name: dict[str, str] = {}
        self._last_auth_refresh = 0.0
        self._media_resolver: Callable[[str], Path | None] | None = None
        self._auth_email = ""

    # ------------------------------------------------------------------ auth
    def set_media_resolver(self, resolver: Callable[[str], Path | None]) -> None:
        self._media_resolver = resolver

    def resolve_email(self) -> str:
        """Resolve the internal Supabase auth email exactly like the Giant app."""
        username = self.settings.username.strip()
        if not username:
            raise GiantError("اسم مستخدم Giant Chat غير مضبوط.")
        origins = list(
            dict.fromkeys(
                (
                    self.settings.site_origin,
                    "https://giant-chat-app.lovable.app",
                )
            )
        )
        for origin in origins:
            try:
                response = self._http.post(
                    f"{origin}/api/public/login-email",
                    json={"username": username},
                    timeout=min(20, self.settings.request_timeout),
                )
                if response.status_code != 200:
                    self.log.warning("Giant login-email returned HTTP %s", response.status_code)
                    continue
                payload = response.json()
                email = str(payload.get("email") or "").strip() if isinstance(payload, dict) else ""
                if email and "@" in email and email.lower() != "null":
                    self.log.info("تم حل بريد المصادقة عبر واجهة Giant الرسمية")
                    return email
            except Exception as exc:
                self.log.warning("تعذر الاتصال بواجهة login-email: %s", safe_auth_error(exc, (self.settings.password,)))
        email = giant_auth_email(username)
        if not email:
            raise GiantError("تعذر تحديد بريد المصادقة الداخلي لهذا الحساب.")
        self.log.info("استخدام تحويل اسم المستخدم الاحتياطي لتسجيل الدخول")
        return email

    def login(self) -> None:
        with self._session_lock:
            if self.client is not None and self.user_id:
                return
            email = self.resolve_email()
            self._auth_email = email
            client = create_supabase_client(
                self.settings.supabase_url,
                self.settings.supabase_key,
                create_client,
            )
            try:
                result = client.auth.sign_in_with_password({"email": email, "password": self.settings.password})
            except Exception as exc:
                raise GiantError(
                    "تعذر تسجيل الدخول إلى Giant Chat: "
                    + safe_auth_error(exc, (self.settings.password, self.settings.supabase_key))
                ) from exc
            user = getattr(result, "user", None)
            if not user:
                raise GiantError("رفض Giant Chat تسجيل الدخول ولم يُرجع مستخدمًا.")
            self.client = client
            self.user_id = str(user.id)
            self.username = str(getattr(user, "user_metadata", {}).get("username") or self.settings.username)
            self._last_auth_refresh = time.time()
            self.log.info("تم تسجيل الدخول إلى Giant Chat كـ @%s", self.username)

    def refresh_session(self, force: bool = False) -> None:
        if self.client is None:
            return
        if not force and time.time() - self._last_auth_refresh < 45 * 60:
            return
        with self._session_lock:
            try:
                self.client.auth.refresh_session()
                self._last_auth_refresh = time.time()
                self.log.info("تم تجديد جلسة Supabase")
            except Exception as exc:
                self.log.warning("تعذر تجديد الجلسة، إعادة تسجيل الدخول: %s", safe_auth_error(exc, (self.settings.password,)))
                self.client = None
                self.user_id = ""
                self.login()

    # --------------------------------------------------------------- helpers
    def _run(self, operation: Callable[[], Any], *, retry_auth: bool = True):
        """Execute a Supabase call with retries and one re-login on auth errors."""
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with self._lock:
                    if self.client is None:
                        self.login()
                    return operation()
            except Exception as exc:  # supabase raises instead of returning errors
                last_error = exc
                text = str(exc).lower()
                if retry_auth and ("jwt" in text or "401" in text or "unauthorized" in text or "token" in text):
                    try:
                        self.refresh_session(force=True)
                    except Exception as auth_exc:
                        self.log.warning("إعادة تسجيل الدخول فشلت: %s", safe_auth_error(auth_exc))
                time.sleep(min(3.0, 0.4 * (attempt + 1)))
        raise GiantError(str(last_error) if last_error else "فشل غير معروف في Giant")

    @staticmethod
    def _rows(response) -> list[dict]:
        return list(getattr(response, "data", None) or [])

    # ------------------------------------------------------------ identities
    def _cache_profile(self, row: dict) -> None:
        uid = str(row.get("id") or "").strip()
        name = str(row.get("username") or "").strip().lstrip("@")
        if not uid or not name:
            return
        self._user_ids[name.casefold()] = uid
        self._usernames[uid] = name
        photo = str(row.get("avatar_url") or row.get("photo_url") or "").strip()
        if photo:
            self._photos[uid] = photo
            self._photos[name.casefold()] = photo

    def display_name(self, uid: str) -> str:
        uid = str(uid or "").strip()
        if not uid:
            return ""
        if uid == self.user_id:
            return self.username
        if uid in self._usernames:
            return self._usernames[uid]
        try:
            rows = self._rows(
                self._run(lambda: self.client.table("profiles").select("id,username").eq("id", uid).limit(1).execute())
            )
            if rows:
                self._cache_profile(rows[0])
                return self._usernames.get(uid, "")
        except Exception as exc:
            self.log.warning("تعذر جلب اسم المستخدم %s: %s", uid[:8], safe_auth_error(exc))
        return ""

    def resolve_user_id(self, username: str) -> str:
        name = str(username or "").strip().lstrip("@")
        if not name:
            return ""
        if name.casefold() == self.username.casefold():
            return self.user_id
        if name.casefold() in self._user_ids:
            return self._user_ids[name.casefold()]
        try:
            rows = self._rows(
                self._run(
                    lambda: self.client.table("profiles").select("id,username").ilike("username", name).limit(1).execute()
                )
            )
            if rows:
                self._cache_profile(rows[0])
                return self._user_ids.get(name.casefold(), "")
        except Exception as exc:
            self.log.warning("تعذر جلب معرّف %s: %s", name, safe_auth_error(exc))
        return ""

    def profile_photo(self, username: str) -> str:
        uid = self.resolve_user_id(username)
        return self._photos.get(uid) or self._photos.get(str(username).casefold()) or ""

    # ----------------------------------------------------------------- rooms
    def refresh_rooms(self) -> dict[str, str]:
        try:
            rows = self._rows(self._run(lambda: self.client.table("rooms").select("id,name").execute()))
            for row in rows:
                rid, name = str(row.get("id") or ""), str(row.get("name") or "").strip()
                if rid and name:
                    self._rooms_by_id[rid] = name
                    self._rooms_by_name[name.casefold()] = rid
        except Exception as exc:
            self.log.warning("تعذر تحميل قائمة الغرف: %s", safe_auth_error(exc))
        return dict(self._rooms_by_id)

    def room_name(self, room_id: str) -> str:
        rid = str(room_id or "").strip()
        if rid not in self._rooms_by_id:
            self.refresh_rooms()
        return self._rooms_by_id.get(rid, "")

    def room_id(self, room_name: str) -> str:
        name = str(room_name or "").strip()
        if not name:
            return ""
        key = name.casefold()
        if key not in self._rooms_by_name:
            self.refresh_rooms()
        if key in self._rooms_by_name:
            return self._rooms_by_name[key]
        try:
            rows = self._rows(
                self._run(lambda: self.client.table("rooms").select("id,name").ilike("name", name).limit(1).execute())
            )
            if rows:
                rid, resolved = str(rows[0].get("id") or ""), str(rows[0].get("name") or "")
                self._rooms_by_id[rid] = resolved
                self._rooms_by_name[resolved.casefold()] = rid
                return rid
        except Exception as exc:
            self.log.warning("تعذر البحث عن غرفة %s: %s", name, safe_auth_error(exc))
        return ""

    def all_rooms(self) -> list[dict]:
        try:
            rows = self._rows(self._run(lambda: self.client.table("rooms").select("id,name").execute()))
            return [{"id": str(r.get("id")), "name": str(r.get("name") or "")} for r in rows]
        except Exception as exc:
            self.log.warning("تعذر جلب الغرف: %s", safe_auth_error(exc))
            return []

    def rooms_for_bot(self) -> list[dict]:
        """Rooms the bot account is a member of."""
        self.login()
        try:
            rows = self._rows(
                self._run(
                    lambda: self.client.table("room_members")
                    .select("room_id,rank,is_present")
                    .eq("user_id", self.user_id)
                    .execute()
                )
            )
        except Exception as exc:
            self.log.warning("تعذر جلب غرف البوت: %s", safe_auth_error(exc))
            return []
        rooms = []
        for row in rows:
            rid = str(row.get("room_id") or "")
            name = self.room_name(rid)
            if rid and name:
                rooms.append({"id": rid, "name": name, "rank": str(row.get("rank") or "member")})
        return rooms

    def join_room(self, room_name: str) -> tuple[bool, str]:
        rid = self.room_id(room_name)
        if not rid:
            return False, f"الغرفة «{room_name}» غير موجودة في Giant."
        try:
            password = str(self.settings.room_password or "")
            self._run(
                lambda: self.client.rpc("room_join", {"_room": rid, "_password": password}).execute()
            )
            return True, self.room_name(rid)
        except Exception as exc:
            return False, f"رفض Giant الانضمام: {safe_auth_error(exc)}"

    def leave_room(self, room_name: str) -> tuple[bool, str]:
        rid = self.room_id(room_name)
        if not rid:
            return False, "الغرفة غير موجودة."
        try:
            self._run(lambda: self.client.rpc("room_leave", {"_room": rid}).execute())
            return True, self.room_name(rid)
        except Exception as exc:
            return False, f"رفض Giant الخروج: {safe_auth_error(exc)}"

    def heartbeat(self, room_name: str) -> bool:
        rid = self.room_id(room_name)
        if not rid:
            return False
        try:
            self._run(lambda: self.client.rpc("room_heartbeat", {"_room": rid}).execute())
            return True
        except Exception as exc:
            self.log.debug("heartbeat failed for %s: %s", room_name, safe_auth_error(exc))
            return False

    def members(self, room_name: str) -> list[dict]:
        rid = self.room_id(room_name)
        if not rid:
            return []
        try:
            rows = self._rows(
                self._run(
                    lambda: self.client.table("room_members")
                    .select("user_id,rank,is_present")
                    .eq("room_id", rid)
                    .execute()
                )
            )
        except Exception as exc:
            self.log.warning("تعذر جلب أعضاء %s: %s", room_name, safe_auth_error(exc))
            return []
        uids = [str(r.get("user_id")) for r in rows if r.get("user_id")]
        profiles: dict[str, str] = {}
        photos: dict[str, str] = {}
        for start in range(0, len(uids), 100):
            batch = uids[start : start + 100]
            try:
                prows = self._rows(
                    self._run(
                        lambda b=batch: self.client.table("profiles").select("id,username").in_("id", b).execute()
                    )
                )
            except Exception:
                prows = []
            for prow in prows:
                uid = str(prow.get("id") or "")
                profiles[uid] = str(prow.get("username") or "").strip()
                if uid:
                    self._usernames[uid] = profiles[uid]
                    self._user_ids[profiles[uid].casefold()] = uid
        out = []
        for row in rows:
            uid = str(row.get("user_id") or "")
            name = profiles.get(uid, "")
            if not name or uid == self.user_id:
                continue
            out.append(
                {
                    "username": name,
                    "user_id": uid,
                    "role": str(row.get("rank") or "member").lower(),
                    "online": "1" if row.get("is_present") else "",
                    "photo": photos.get(uid, ""),
                }
            )
        return out

    # ------------------------------------------------------------- moderation
    def _member_rank(self, rid: str, uid: str) -> str | None:
        rows = self._rows(
            self._run(
                lambda: self.client.table("room_members")
                .select("rank")
                .eq("room_id", rid)
                .eq("user_id", uid)
                .limit(1)
                .execute()
            )
        )
        if not rows:
            return None
        return str(rows[0].get("rank") or "member").lower()

    def moderate(self, action: str, room_name: str, target_username: str, new_rank: str = "") -> tuple[bool, str]:
        rid = self.room_id(room_name)
        target = self.resolve_user_id(target_username)
        if not rid:
            return False, "الغرفة غير موجودة في Giant."
        if not target:
            return False, f"المستخدم @{target_username} غير موجود في Giant."
        bot_rank = self._member_rank(rid, self.user_id)
        if bot_rank not in {"moderator", "admin", "owner"}:
            return False, f"رتبة البوت «{bot_rank or 'غير عضو'}» لا تسمح بالإدارة في هذه الغرفة."
        priority = {"member": 0, "moderator": 1, "admin": 2, "owner": 3}
        target_rank = self._member_rank(rid, target)
        if target_rank is not None and priority.get(target_rank, 0) >= priority.get(bot_rank, 0):
            return False, f"رتبة @{target_username} («{target_rank}») مساوية أو أعلى من رتبة البوت."
        try:
            if action == "kick":
                self._run(lambda: self.client.rpc("kick_room_member", {"_room": rid, "_user": target}).execute())
                return True, "kicked"
            if action == "ban":
                self._run(
                    lambda: self.client.rpc(
                        "ban_room_member",
                        {"_room": rid, "_user": target, "_reason": "إجراء إداري عبر البوت"},
                    ).execute()
                )
                return True, "outcast"
            if action == "rank":
                rank = {"moderator": "moderator", "admin": "admin", "member": "member"}.get(new_rank)
                if not rank:
                    return False, "رتبة غير مدعومة."
                self._run(
                    lambda: self.client.rpc(
                        "set_member_rank", {"_room": rid, "_user": target, "_new_rank": rank}
                    ).execute()
                )
                return True, rank
        except Exception as exc:
            return False, f"رفض Giant الإجراء: {safe_auth_error(exc)}"
        return False, "إجراء غير مدعوم."

    # ------------------------------------------------------------------- send
    def send_room_text(self, room_name: str, text: str) -> bool:
        text = str(text or "")
        if not text.strip():
            return True
        rid = self.room_id(room_name)
        if not rid:
            self.log.warning("وجهة غير معروفة للرسالة: %s", room_name)
            return False
        try:
            self._run(
                lambda: self.client.table("room_messages")
                .insert({"room_id": rid, "user_id": self.user_id, "content": text, "message_type": "text"})
                .execute()
            )
            return True
        except Exception as exc:
            self.log.warning("فشل إرسال رسالة إلى %s: %s", room_name, safe_auth_error(exc))
            return False

    def send_room_media(
        self,
        room_name: str,
        caption: str,
        media_url: str,
        media_type: str = "image",
        duration_ms: int | None = None,
    ) -> bool:
        rid = self.room_id(room_name)
        if not rid:
            return False
        url = self.publish_media(media_url, kind=media_type)
        if not url:
            return False
        try:
            self._run(
                lambda: self.client.table("room_messages")
                .insert(
                    {
                        "room_id": rid,
                        "user_id": self.user_id,
                        "content": str(caption or ""),
                        "message_type": media_type or "image",
                        "media_url": url,
                        "media_duration_ms": int(duration_ms or 0) or None,
                    }
                )
                .execute()
            )
            return True
        except Exception as exc:
            self.log.warning("فشل إرسال وسائط إلى %s: %s", room_name, safe_auth_error(exc))
            return False

    def send_dm(self, username: str, text: str) -> bool:
        uid = self.resolve_user_id(username)
        if not uid:
            self.log.warning("لا يمكن إرسال خاص: المستخدم %s غير موجود", username)
            return False
        envelope = {
            "v": 1,
            "id": str(uuid.uuid4()),
            "content": str(text or ""),
            "message_type": "text",
            "media_url": None,
            "media_duration_ms": None,
            "reply_to_id": None,
            "created_at": now_iso(),
        }
        try:
            self._run(
                lambda: self.client.table("dm_relay")
                .insert({"sender_id": self.user_id, "recipient_id": uid, "envelope": envelope})
                .execute()
            )
            return True
        except Exception as exc:
            self.log.warning("فشل إرسال خاص إلى %s: %s", username, safe_auth_error(exc))
            return False

    def send_dm_media(self, username: str, caption: str, media_url: str, media_type: str = "image") -> bool:
        uid = self.resolve_user_id(username)
        if not uid:
            return False
        url = self.publish_media(media_url, kind=media_type)
        if not url:
            return False
        envelope = {
            "v": 1,
            "id": str(uuid.uuid4()),
            "content": str(caption or ""),
            "message_type": media_type or "image",
            "media_url": url,
            "media_duration_ms": None,
            "reply_to_id": None,
            "created_at": now_iso(),
        }
        try:
            self._run(
                lambda: self.client.table("dm_relay")
                .insert({"sender_id": self.user_id, "recipient_id": uid, "envelope": envelope})
                .execute()
            )
            return True
        except Exception as exc:
            self.log.warning("فشل إرسال وسائط خاصة إلى %s: %s", username, safe_auth_error(exc))
            return False

    # ------------------------------------------------------------------ media
    def _local_path_for(self, url: str) -> Path | None:
        if self._media_resolver:
            try:
                path = self._media_resolver(url)
                if path and path.is_file():
                    return path
            except Exception:
                pass
        parsed = urlparse(url)
        if parsed.scheme in ("", "file"):
            candidate = Path(unquote(parsed.path if parsed.scheme else url))
            if candidate.is_file():
                return candidate
        return None

    def _download(self, url: str) -> Path | None:
        try:
            response = self._http.get(url, timeout=min(25, self.settings.request_timeout), stream=True)
            if response.status_code != 200:
                return None
            suffix = Path(unquote(urlparse(url).path)).suffix or ".bin"
            target = Path("/tmp") / f"talkin_media_{uuid.uuid4().hex}{suffix}"
            with target.open("wb") as fh:
                for chunk in response.iter_content(256 * 1024):
                    fh.write(chunk)
                    if target.stat().st_size > 60 * 1024 * 1024:
                        target.unlink(missing_ok=True)
                        return None
            return target
        except Exception:
            return None

    def publish_media(self, media_url: str, kind: str = "image") -> str:
        """Return a URL the Giant app can display.

        Local files generated by the Talkin bot are uploaded to Supabase
        Storage.  Remote URLs are passed through unless they point at this
        bot's own asset server.
        """
        url = str(media_url or "").strip()
        if not url:
            return ""
        local = self._local_path_for(url)
        if local is None and url.startswith(("http://", "https://")):
            base = self.settings.public_media_url
            is_own = bool(base and url.startswith(base))
            if not is_own:
                # External image/audio: try to mirror it for reliability.
                local = self._download(url)
                if local is None:
                    return url
        if local is None:
            return url
        bucket = self.settings.music_bucket if kind == "audio" else (
            self.settings.game_bucket if kind == "game" else self.settings.image_bucket
        )
        content_type = mimetypes.guess_type(str(local))[0] or "application/octet-stream"
        filename = f"talkin/{uuid.uuid4().hex}{local.suffix.lower() or '.bin'}"
        try:
            data = local.read_bytes()

            def upload():
                storage = self.client.storage.from_(bucket)
                storage.upload(filename, data, {"content-type": content_type, "upsert": "true"})
                return storage.get_public_url(filename)

            public = self._run(upload)
            if isinstance(public, dict):
                public = public.get("publicUrl") or public.get("publicURL") or ""
            if public:
                return str(public)
        except Exception as exc:
            self.log.warning("تعذر رفع الوسائط إلى Supabase (%s): %s", bucket, safe_auth_error(exc))
        return url

    # ------------------------------------------------------------------- poll
    def fetch_room_messages(self, room_id: str, since_iso: str, limit: int = 100) -> list[dict]:
        try:
            return self._rows(
                self._run(
                    lambda: self.client.table("room_messages")
                    .select("*")
                    .eq("room_id", room_id)
                    .gt("created_at", since_iso)
                    .order("created_at")
                    .limit(limit)
                    .execute()
                )
            )
        except Exception as exc:
            self.log.warning("تعذر قراءة رسائل الغرفة: %s", safe_auth_error(exc))
            return []

    def fetch_dms(self, limit: int = 50) -> list[dict]:
        try:
            return self._rows(
                self._run(
                    lambda: self.client.table("dm_relay")
                    .select("*")
                    .eq("recipient_id", self.user_id)
                    .order("created_at")
                    .limit(limit)
                    .execute()
                )
            )
        except Exception as exc:
            self.log.warning("تعذر قراءة الرسائل الخاصة: %s", safe_auth_error(exc))
            return []

    def delete_dm(self, row_id: str) -> None:
        try:
            self._run(lambda: self.client.table("dm_relay").delete().eq("id", row_id).execute())
        except Exception as exc:
            self.log.debug("تعذر حذف رسالة خاصة معالجة: %s", safe_auth_error(exc))

    # ----------------------------------------------------------------- status
    def set_status(self, status: str) -> bool:
        """Best-effort profile status update (the column is optional)."""
        try:
            self._run(
                lambda: self.client.table("profiles").update({"status": str(status)[:160]}).eq("id", self.user_id).execute()
            )
            return True
        except Exception:
            return False

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass
