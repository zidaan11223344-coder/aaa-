"""Standalone domain engine for Talkin2 welcome and room-message replies.

This module has no transport, persistence, or bot.py dependency.  Callers own
persistence by saving :meth:`SocialReplyEngine.to_state` as JSON.
"""
from __future__ import annotations

import copy
import random as _random
import re
import time as _time
import unicodedata
from typing import Any, Callable, Mapping, Sequence


_STATE_VERSION = 1
_DEFAULT_DEPARTURE_REPLIES = {
    "ب": "🌀 في الخلاط",
    "ت": "⚰️ في التابوت",
    "ح": "⚰️ في التابوت",
    "ق": "❤️ بقلبي",
}

# A deliberately conservative standalone copy of Talkin2's command boundary.
# Prefixes cover both ordinary bot commands and management/configuration
# commands; exact matching is only attempted after this check.
_COMMAND_PREFIXES = tuple(sorted({
    "+sr@", "sr@", "+ds@", "ds@", "farewell@", "swc", "welcome@",
    "ترحيب@", "ترحيب_صورة", "welcome_image", "l@", "ts@", "la@",
    "ax@", "x@", "sa@", "sav@", "bom@", "work", "salary", "عمل",
    "راتبي", ".sa ", "vi@", "vip@", "unvip@", "uns@", "b@", "bl@",
    "k@", "u@", "ub@", "m@", "member ", "a@", "o@", "ban ",
    "kick ", "unban ", "admin ", "owner ", "mas@", "umas@", "mvip@",
    "umvip@", "sb@", "i@", "inv", "دعوات", "دعوه ", "دعوة ",
    "invite", "رساله ", "say ", "قل ", "دخول@", "join@", "play ",
    "music ", "song ", "post@", "publish@", "protection", "broadcast@",
    "مشاركه ", "مشاركة ", ".تشغيل ", "بث ", "help", "a1", "a2", "a3",
    "a4", "a5", "a6", "ns", "التالي", "القائمة التالية", "next",
    "اوامر", "المسترات", "نقاطي", "points", "top", "هدايا", "gifts",
    "gv", "sher@", "فتح المليون", "غلق المليون", "فتح مليار", "غلق مليار",
    "فحص صورة المليار", "رهان@", "مضاربة@", "استثمار@", "حظي@", "sv ",
    "رد ", "رسالة@", "خاص@", "l@sr", "l@a", "l@m", "l@o", "l@b",
    "clear@mf", "دخول الكل", "دخولكل", "تشغيل الحماية", "إيقاف الحماية",
    "ايقاف الحماية", "تشغيل الألعاب", "إيقاف الألعاب", "توثيق", "verify",
    "rool", "roll", "بنك", "نشر", "بوست", "انشر", "مساعدة", "العاب",
    "ألعاب", "لعب", "تسلية", "تسليه", "زواج", "تحدي", "لغز", "مزاج",
    "حظ", "نرد", "بورصة", "بورصه", "my stock", "حجر", "ورق", "مقص",
    "مليار", "بنك مليون", "ثعبان", "snake", "لودو", "ludo", "انضمام",
    "عملة", "عجلة", "صندوق", "كوب", "اونو", "سنارة", "سناره", "ياقوت",
    "كاشف", "اسرق", "متجر", "shop", "store", "ترحيب_صورة",
}))
_COMMAND_EXACT = {
    "id", "ايدي", "أيدي", "رتبتك", "رتبتي", "bot rank", "your rank",
    "help", "games", "game", "bom", "vi", "vip", "ns", "next",
    "اوامر", "مساعدة", "نقاطي", "points", "top", "gifts", "هدايا",
    "roll", "rool", "بنك", "نشر", "انشر", "مساعدة", "sv", "inventory",
    "متجر", "shop", "store", "welcome_image", "ترحيب_صورة",
}


def normalize_trigger(value: Any) -> str:
    """Normalize a trigger like Talkin2: NFKC, casefold, Arabic variants/punct."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"}))
    text = re.sub(r"[؟?!.,،؛:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _room_key(room: Any) -> str:
    return " ".join(str(room or "").strip().casefold().split())


def _user_key(username: Any) -> str:
    text = unicodedata.normalize("NFKC", str(username or "")).strip().lstrip("@").casefold()
    return re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", text)


def render_template(template: Any, username: Any = "", room: Any = "") -> str:
    """Expand the Talkin2 ``{username}`` and ``{room}`` template tokens."""
    clean_name = str(username or "").strip().lstrip("@")
    return (str(template or "").replace("@{username}", "{username}")
            .replace("{username}", clean_name).replace("{room}", str(room or ""))
            .replace("@@", "@").strip())


def looks_like_bot_command(text: Any) -> bool:
    """Return whether message text falls within Talkin2's command boundary.

    This guard aims to avoid stealing commands, not to validate or execute them.
    Callers may additionally provide a stricter project-specific predicate.
    """
    low = unicodedata.normalize("NFKC", str(text or "")).strip().casefold()
    if not low:
        return False
    normalized = low.replace("ة", "ه")
    if low in _COMMAND_EXACT or normalized in {x.replace("ة", "ه") for x in _COMMAND_EXACT}:
        return True
    prefixes = _COMMAND_PREFIXES + tuple(x.replace("ة", "ه") for x in _COMMAND_PREFIXES)
    if low.startswith(prefixes) or normalized.startswith(prefixes):
        return True
    if re.fullmatch(r"(?:ايدي|أيدي|id)@.+", low) or re.match(r"^(?:play|music|song)\s+.+$", low):
        return True
    if re.match(r"^(?:حدث|تحديث)\s+(?:ايدي|أيدي|id)$", low):
        return True
    return False


class SocialReplyEngine:
    """Room-scoped, JSON-serializable domain state for social replies.

    ``random_source`` should provide ``shuffle(list)`` and ``choice(sequence)``
    (as :mod:`random` and ``random.Random`` do). ``clock`` is a zero-argument
    seconds clock. Both dependencies are injectable for deterministic tests.
    """

    def __init__(self, state: Mapping[str, Any] | None = None, *,
                 random_source: Any = None, clock: Callable[[], float] | None = None,
                 command_predicate: Callable[[str], bool] | None = None):
        self.random = random_source if random_source is not None else _random
        self.clock = clock if clock is not None else _time.time
        self.command_predicate = command_predicate or looks_like_bot_command
        self._state: dict[str, Any] = {
            "version": _STATE_VERSION,
            "welcomes_enabled": True,
            "welcomes": {},
            "auto_replies_enabled": True,
            "auto_replies": {},
            "departure_farewell_enabled": True,
            "departure_replies": {},
            "cycles": {},
        }
        if isinstance(state, Mapping):
            self._load_state(state)

    def _load_state(self, data: Mapping[str, Any]) -> None:
        for name, default in (
            ("welcomes_enabled", True), ("auto_replies_enabled", True),
            ("departure_farewell_enabled", True),
        ):
            self._state[name] = bool(data.get(name, default))
        for name in ("welcomes", "auto_replies", "departure_replies", "cycles"):
            value = data.get(name, {})
            self._state[name] = copy.deepcopy(value) if isinstance(value, dict) else {}
        # Sanitize trigger maps and cycles so state remains predictable and JSON-friendly.
        welcomes = {}
        for user, template in self._state["welcomes"].items():
            if str(user).strip() and isinstance(template, str) and template.strip():
                welcomes[_user_key(user)] = template
        self._state["welcomes"] = welcomes
        autos = {}
        for trigger, value in self._state["auto_replies"].items():
            key = normalize_trigger(trigger)
            if not key:
                continue
            if isinstance(value, dict):
                raw = value.get("replies", value.get("reply", []))
                display = str(value.get("trigger") or trigger)
            else:
                raw, display = value, str(trigger)
            replies = [str(x).strip() for x in ([raw] if isinstance(raw, str) else raw or []) if str(x).strip()] if isinstance(raw, (str, list, tuple)) else []
            if replies:
                autos[key] = {"trigger": display, "replies": replies}
        self._state["auto_replies"] = autos
        departures = {}
        for trigger, raw in self._state["departure_replies"].items():
            key = normalize_trigger(trigger)
            values = [str(x).strip() for x in ([raw] if isinstance(raw, str) else raw or []) if str(x).strip()] if isinstance(raw, (str, list, tuple)) else []
            if len(key) == 1 and values:
                departures[key] = values
        self._state["departure_replies"] = departures
        cycles = {}
        raw_cycles = self._state["cycles"]
        for room, room_data in raw_cycles.items():
            if not isinstance(room_data, dict):
                continue
            fixed = {}
            for key, entry in room_data.items():
                if isinstance(entry, dict):
                    signature, order = entry.get("signature", []), entry.get("order", [])
                    if isinstance(signature, (list, tuple)) and isinstance(order, list):
                        fixed[normalize_trigger(key)] = {
                            "signature": [str(x) for x in signature],
                            "order": [int(x) for x in order],
                            "index": max(0, int(entry.get("index", 0) or 0)),
                            "last_used_at": float(entry.get("last_used_at", 0) or 0),
                        }
            cycles[_room_key(room)] = fixed
        self._state["cycles"] = cycles

    def to_state(self) -> dict[str, Any]:
        """Return a detached JSON-serializable state snapshot."""
        return copy.deepcopy(self._state)

    @classmethod
    def from_state(cls, state: Mapping[str, Any], **kwargs: Any) -> "SocialReplyEngine":
        return cls(state, **kwargs)

    def set_welcome(self, username: str, template: str | None) -> None:
        key = _user_key(username)
        if not key:
            raise ValueError("username must not be empty")
        if template is None or not str(template).strip():
            self._state["welcomes"].pop(key, None)
        else:
            self._state["welcomes"][key] = str(template)

    def render_welcome(self, username: str, room: str, *, template: str | None = None) -> str | None:
        if not self._state["welcomes_enabled"]:
            return None
        selected = template
        if selected is None:
            selected = self._state["welcomes"].get(_user_key(username))
        if not selected:
            return None
        return render_template(selected, username, room)

    def set_welcomes_enabled(self, enabled: bool) -> None:
        self._state["welcomes_enabled"] = bool(enabled)

    def set_auto_replies_enabled(self, enabled: bool) -> None:
        self._state["auto_replies_enabled"] = bool(enabled)

    def set_auto_replies(self, trigger: str, replies: str | Sequence[str] | None) -> None:
        key = normalize_trigger(trigger)
        if not key:
            raise ValueError("trigger must not be empty")
        variants = [replies] if isinstance(replies, str) else list(replies or [])
        variants = [str(x).strip() for x in variants if str(x).strip()]
        if variants:
            self._state["auto_replies"][key] = {"trigger": str(trigger), "replies": variants}
        else:
            self._state["auto_replies"].pop(key, None)
        for room_state in self._state["cycles"].values():
            room_state.pop(key, None)

    def set_departure_replies(self, trigger: str, replies: str | Sequence[str] | None) -> None:
        key = normalize_trigger(trigger)
        if len(key) != 1:
            raise ValueError("departure trigger must normalize to one character")
        variants = [replies] if isinstance(replies, str) else list(replies or [])
        variants = [str(x).strip() for x in variants if str(x).strip()]
        if variants:
            self._state["departure_replies"][key] = variants
        else:
            self._state["departure_replies"].pop(key, None)

    def set_farewell_enabled(self, enabled: bool) -> None:
        self._state["departure_farewell_enabled"] = bool(enabled)

    def _format_auto_reply(self, template: str, username: str, room: str) -> str:
        text = render_template(template, username, room)
        # Talkin2 prefixes the sender name unless the saved reply had a token.
        if "{username}" not in template and str(username or "").strip():
            return f"{str(username).strip().lstrip('@')} {text}".strip()
        return text

    def _next_auto_reply(self, room: str, trigger: str, username: str) -> str:
        key = normalize_trigger(trigger)
        item = self._state["auto_replies"].get(key, {})
        variants = list(item.get("replies", []))
        if not variants:
            return ""
        room_key = _room_key(room)
        room_cycles = self._state["cycles"].setdefault(room_key, {})
        signature = list(variants)
        entry = room_cycles.get(key)
        if not isinstance(entry, dict) or entry.get("signature") != signature:
            order = list(range(len(variants)))
            self.random.shuffle(order)
            entry = {"signature": signature, "order": order, "index": 0, "last_used_at": 0.0}
        order = entry.get("order", [])
        index = int(entry.get("index", 0) or 0)
        if len(order) != len(variants) or index >= len(order):
            order = list(range(len(variants)))
            self.random.shuffle(order)
            index = 0
        selected = variants[order[index]]
        entry.update(signature=signature, order=list(order), index=index + 1,
                     last_used_at=float(self.clock()))
        room_cycles[key] = entry
        return self._format_auto_reply(selected, username, room)

    def departure_reply(self, trigger: str, username: str, room: str) -> str:
        key = normalize_trigger(trigger)
        if len(key) != 1:
            return ""
        choices = self._state["departure_replies"].get(key, [])
        if choices:
            template = self.random.choice(choices)
        elif key in _DEFAULT_DEPARTURE_REPLIES:
            template = _DEFAULT_DEPARTURE_REPLIES[key]
        elif self._state["departure_farewell_enabled"]:
            template = "👋 غادرنا بالسلامة"
        else:
            return ""
        name = str(username or "").strip().lstrip("@")
        text = render_template(template, name, room)
        if "{username}" not in template and name:
            text = f"👤 {name} {text}".strip()
        return text

    def match_message(self, room: str, message: str, username: str, *,
                      departed_username: str | None = None,
                      is_command: bool | None = None) -> dict[str, Any]:
        """Classify a room message and optionally return one response.

        One-character departure keys are evaluated before automatic replies,
        matching Talkin2's separate departure-reply path. Otherwise only an
        exact normalized trigger match can reply, and bot/admin commands are
        passed through untouched.
        """
        normalized = normalize_trigger(message)
        command = self.command_predicate(str(message or "")) if is_command is None else bool(is_command)
        if len(normalized) == 1:
            return {
                "kind": "departure", "matched": True, "consumed": True,
                "trigger": normalized,
                "reply": self.departure_reply(normalized, departed_username or "", room) if departed_username else None,
            }
        if command or not self._state["auto_replies_enabled"] or not normalized:
            return {"kind": "none", "matched": False, "consumed": False, "trigger": normalized, "reply": None}
        item = self._state["auto_replies"].get(normalized)
        if not item:
            return {"kind": "none", "matched": False, "consumed": False, "trigger": normalized, "reply": None}
        reply = self._next_auto_reply(room, normalized, username)
        return {"kind": "auto_reply", "matched": True, "consumed": bool(reply), "trigger": normalized, "reply": reply}


__all__ = ["SocialReplyEngine", "normalize_trigger", "render_template", "looks_like_bot_command"]
