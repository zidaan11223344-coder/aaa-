# -*- coding: utf-8 -*-
"""Giant Chat adapter for the embedded Talkin bot.

``GiantTalkinBot`` inherits the *complete* original Talkin bot implementation
from :mod:`talkin_full.vendor.talkin_bot` and replaces only its transport:
every outbound protobuf packet is decoded and translated into the matching
Giant Chat call, while inbound Giant messages are converted back into the
RoomEvent/ChatMessage dictionaries the original handlers expect.

Keeping the original command handlers untouched is what makes the merged bot a
faithful copy: games, explore, publishing, gifts, music, points, wallet, shop,
cricket, auction, ludo, snake and the rest of the Talkin feature set keep their
original logic, permissions and texts.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .vendor import talkin_bot as tb

from .giant_api import GiantApi


def _first_text(packet: dict, field: int) -> str:
    values = packet.get(field) or []
    if not values:
        return ""
    value = values[0]
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace")
        except Exception:
            return ""
    return str(value)


class GiantTalkinBot(tb.TalkinBot):
    """The original Talkin bot with a Giant Chat transport underneath."""

    def __init__(self, api: GiantApi, logger: logging.Logger | None = None):
        super().__init__()
        self.api = api
        self.logger = logger or logging.getLogger("talkin_full.bot")
        self._giant_pending_dm: dict | None = None
        self._giant_dm_sentinel = object()
        self._giant_orig_decode = tb.decode_result_message
        tb.decode_result_message = self._giant_decode_result
        self._giant_lock = threading.RLock()
        self._giant_joined_at: dict[str, float] = {}

    # ------------------------------------------------------------- inbound
    def _giant_decode_result(self, message):
        if message is self._giant_dm_sentinel:
            return self._giant_pending_dm or {}
        return self._giant_orig_decode(message)

    def _display_username(self, user_id: str) -> str:
        name = self.api.display_name(user_id)
        return name or f"user_{str(user_id or '')[:8]}"

    def feed_room_message(self, row: dict) -> None:
        """Translate one Giant ``room_messages`` row into a Talkin RoomEvent."""
        if not isinstance(row, dict):
            return
        if str(row.get("user_id") or "") == self.api.user_id:
            return
        message_type = str(row.get("message_type") or "text").strip().lower()
        if message_type in {"system", "event"}:
            return
        room = self.api.room_name(str(row.get("room_id") or ""))
        if not room:
            return
        sender = self._display_username(str(row.get("user_id") or ""))
        body = str(row.get("content") or "")
        media_url = str(row.get("media_url") or "")
        message_id = str(row.get("id") or "")
        event_type = "text"
        if media_url or message_type not in {"text", ""}:
            if message_type in {"image", "photo", "picture", "sticker", "gif"}:
                event_type = "image"
            elif message_type in {"audio", "voice", "music"}:
                event_type = "audio"
            elif message_type in {"video"}:
                event_type = "video"
            elif message_type in {"file", "document"}:
                event_type = "file"
            else:
                event_type = "image" if media_url else "text"
        event = {
            1: event_type,
            2: sender,
            6: body,
            13: room,
            17: sender,
            22: sender,
            41: message_id,
        }
        if media_url:
            event[7] = media_url
            event["url"] = media_url
        try:
            self.handle_room_event({"room_event": event})
        except Exception:
            self.logger.exception("فشل معالجة رسالة غرفة من Giant (%s)", message_id)

    def feed_dm(self, row: dict) -> None:
        """Translate one Giant ``dm_relay`` row into a Talkin ChatMessage."""
        if not isinstance(row, dict):
            return
        envelope = row.get("envelope") or {}
        if isinstance(envelope, str):
            try:
                import json

                envelope = json.loads(envelope)
            except Exception:
                envelope = {}
        if not isinstance(envelope, dict):
            envelope = {}
        sender = self._display_username(str(row.get("sender_id") or ""))
        body = str(envelope.get("content") or "")
        media_url = str(envelope.get("media_url") or "")
        message_type = str(envelope.get("message_type") or "text")
        message_id = str(envelope.get("id") or row.get("id") or "")
        cm = {
            3: sender,
            4: "",
            5: body,
            22: sender,
            41: message_id,
        }
        if media_url:
            cm[7] = media_url
            cm["url"] = media_url
            cm["media_type"] = message_type
        self._giant_pending_dm = {"chat_message": cm, "uid": message_id}
        try:
            self._process_message(None, self._giant_dm_sentinel)
        except Exception:
            self.logger.exception("فشل معالجة رسالة خاصة من Giant (%s)", message_id)
        finally:
            self._giant_pending_dm = None

    # ------------------------------------------------------------ outbound
    def authenticate(self):
        self.auth = {"username": self.api.username}
        return True

    def ack(self, uid: str):
        return True

    def _master_is_online(self):
        return True

    def _set_profile_status(self, status: str):
        try:
            self._profile_current_status = str(status or "")
            self.api.set_status(str(status or ""))
            return True
        except Exception:
            return False

    def _verify_public_media_url(self, url: str, media_kind: str = "image"):
        # The transport mirrors local files to Supabase Storage, so a failing
        # self-fetch of the bot's own media URL must not abort the feature.
        try:
            return super()._verify_public_media_url(url, media_kind)
        except Exception as exc:
            self.logger.debug("تجاوز تحقق رابط الوسائط: %s", exc)
            return True

    def request_live_room(self, room: str):
        self.send_room_text(room, "⚠️ البث الصوتي المباشر غير مدعوم على جينات شات. استخدم «تشغيل» للأغاني.")
        return False

    def _play_music_in_live_room(self, room: str, media_url: str, duration: int = 0):
        return False

    def _stop_live_song(self, room: str):
        return False

    def _stop_live_broadcast(self, room: str):
        return False

    def send_live_invitation_to_user(self, target: str, room: str):
        return self.send_private_invite(target, room)

    def send_native_system_invite(self, username: str, room: str):
        return self.send_private_invite(username, room)

    def send_private_invite(self, username: str, room: str, inviter: str = "", allow_repeat: bool = False):
        text = (
            f"📩 دعوة إلى غرفة «{room}»\n"
            f"افتح تطبيق Giant Chat وادخل الغرفة من قائمة الغرف."
        )
        if inviter:
            text = f"📩 دعوة من @{inviter}\n" + text
        return self.api.send_dm(str(username or "").strip().lstrip("@"), text)

    # -------------------------------------------------------------- roster
    def request_room_occupants(self, room: str):
        room = str(room or "").strip()
        if not room:
            return False
        members = self.api.members(room)
        if not members:
            self.logger.warning("لا توجد أعضاء في %s", room)
            return False
        self.room_users[room] = {m["username"]: m.get("role", "none") for m in members}
        try:
            self.user_profile_ids = getattr(self, "user_profile_ids", {}) or {}
            for member in members:
                uid = str(member.get("user_id") or "")
                if uid:
                    self.user_profile_ids[member["username"].casefold()] = uid
                    self.user_profile_ids[tb._norm_user(member["username"])] = uid
            tb._remember_roster(room, members)
        except Exception:
            self.logger.debug("تعذر حفظ قائمة الأعضاء محليًا")
        try:
            self._complete_pending_room_list({"users": members, "_occupants_room": room})
        except Exception:
            pass
        return True

    # ---------------------------------------------------------- packet router
    def send_query(self, payload: bytes):
        try:
            packet = tb.decode_message(payload)
        except Exception as exc:
            self.logger.warning("تعذر فك حزمة صادرة: %s", exc)
            return False
        action = _first_text(packet, 1)
        if not action:
            return False
        try:
            if action == "room_message":
                return self._route_room_message(packet)
            if action == "chat_message":
                return self._route_chat_message(packet)
            if action == "room_join":
                return self._route_join(_first_text(packet, 6))
            if action == "room_leave":
                return self._route_leave(_first_text(packet, 6))
            if action == "room_admin":
                return self._route_room_admin(packet)
            if action == "ack_msg":
                return True
            if action == "load_list_new":
                return self._route_room_list()
            if action == "gifts":
                # The gift card image flow is handled by handle_gift_command;
                # Giant has no native Talkin gift packets.
                return False
            if action in {"room_stream", "stream_accept", "publish_stream", "sent_invitation"}:
                self.logger.debug("تجاهل حزمة بث غير مدعومة: %s", action)
                return False
        except Exception:
            self.logger.exception("فشل توجيه الحزمة %s", action)
            return False
        self.logger.debug("حزمة Talkin غير مدعومة على Giant: %s", action)
        return False

    def _route_room_message(self, packet: dict) -> bool:
        room = _first_text(packet, 6)
        media_type = (_first_text(packet, 2) or "text").lower()
        body = _first_text(packet, 5)
        url = _first_text(packet, 7)
        if not room:
            return False
        if media_type == "text" and not url:
            return self.api.send_room_text(room, body)
        if media_type == "audio":
            duration = _first_text(packet, 3)
            try:
                duration_ms = int(float(duration) * 1000) if duration else None
            except ValueError:
                duration_ms = None
            return self.api.send_room_media(room, body, url, "audio", duration_ms)
        kind = "image" if media_type in {"image", "photo", "picture", "sticker"} else media_type
        return self.api.send_room_media(room, body, url, kind)

    def _route_chat_message(self, packet: dict) -> bool:
        target = _first_text(packet, 4)
        media_type = (_first_text(packet, 2) or "text").lower()
        body = _first_text(packet, 5)
        url = _first_text(packet, 7)
        if not target:
            return False
        if media_type == "text" and not url:
            return self.api.send_dm(target, body)
        kind = "image" if media_type in {"image", "photo", "picture", "sticker"} else media_type
        return self.api.send_dm_media(target, body, url, kind)

    def _route_join(self, room: str) -> bool:
        ok, detail = self.api.join_room(room)
        if not ok:
            self.logger.warning("فشل الانضمام إلى %s: %s", room, detail)
            return False
        resolved = detail or room

        def announce():
            time.sleep(0.4)
            try:
                self._giant_joined_at[tb._norm_room(resolved)] = time.time()
                self.handle_room_event({"room_event": {1: "you_joined", 13: resolved}})
            except Exception:
                self.logger.exception("تعذر إعلان الانضمام إلى %s", resolved)

        threading.Thread(target=announce, name="giant-join-ack", daemon=True).start()
        return True

    def _route_leave(self, room: str) -> bool:
        ok, detail = self.api.leave_room(room)
        if not ok:
            self.logger.warning("فشل الخروج من %s: %s", room, detail)
        return ok

    def _route_room_admin(self, packet: dict) -> bool:
        operation = (_first_text(packet, 2) or "").lower()
        room = _first_text(packet, 6)
        target = _first_text(packet, 4)
        value = (_first_text(packet, 11) or "").lower()
        if operation == "occupants_list":
            return self.request_room_occupants(room)
        if operation in {"kick", "ban", "ban_ip", "ip_ban"}:
            ok, role = self.api.moderate("kick" if operation == "kick" else "ban", room, target)
            if ok:
                self._announce_role_change(room, target, role)
            return ok
        if operation == "change_role":
            if value == "outcast":
                ok, role = self.api.moderate("ban", room, target)
            elif value in {"admin", "owner"}:
                ok, role = self.api.moderate("rank", room, target, new_rank="admin")
            else:
                ok, role = self.api.moderate("rank", room, target, new_rank="member")
            if ok:
                self._announce_role_change(room, target, role)
            return ok
        self.logger.debug("إجراء إدارة غير مدعوم: %s", operation)
        return False

    def _announce_role_change(self, room: str, target: str, role: str) -> None:
        def announce():
            time.sleep(0.2)
            try:
                self.handle_room_event(
                    {"room_event": {1: "role_changed", 13: room, 17: target, 22: target, 31: role}}
                )
            except Exception:
                self.logger.exception("تعذر إعلان تغيير رتبة %s", target)

        threading.Thread(target=announce, name="giant-role-ack", daemon=True).start()

    def _route_room_list(self) -> bool:
        rooms = self.api.all_rooms()
        if not rooms:
            return True
        try:
            self._process_room_list([{"name": r["name"], "room": r["name"], "id": r["id"]} for r in rooms])
        except Exception:
            self.logger.debug("تعذر تغذية قائمة الغرف", exc_info=True)
        return True
