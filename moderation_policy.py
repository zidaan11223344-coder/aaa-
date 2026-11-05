"""Standalone, side-effect-free room moderation policy evaluator.

The policy shapes and normalization rules are modeled on Talkin2's room
protection settings. This module does not connect to a platform, persist state,
or execute warnings/bans; it only returns structured decisions.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping


DEFAULT_WINDOW_SECONDS = 30.0
MIN_REPEAT_LIMIT = 2
MAX_REPEAT_LIMIT = 50


def normalize_arabic(text: object) -> str:
    """Normalize Arabic/Unicode text for filter matching and repetition keys.

    Mirrors the relevant Talkin2 filter behavior: case folding, removal of
    Arabic diacritics and tatweel, alef/ya normalization, and whitespace
    removal. NFKC also makes compatibility forms deterministic.
    """
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    value = "".join(
        char for char in value
        if char != "\u0640" and unicodedata.category(char) not in {"Mn", "Mc", "Me"}
    )
    value = value.translate(str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي",
    }))
    return re.sub(r"\s+", "", value)


def _identity(value: object) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def _room_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _bounded_limit(value: object, default: int = 11) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(MIN_REPEAT_LIMIT, min(MAX_REPEAT_LIMIT, number))


@dataclass(frozen=True)
class RoomPolicy:
    """Room-local protection toggles and filtered words.

    ``repeat_limit`` defaults to 11, matching Talkin2's effective message
    moderation fallback. The source's protection-record fallback is 3; the
    numbered room setting synchronizes that value with moderation settings.
    """

    swear: bool = False
    flood: bool = False
    joinleave: bool = False
    no_photo: bool = False
    repeat_limit: int = 11
    words: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "swear", bool(self.swear))
        object.__setattr__(self, "flood", bool(self.flood))
        object.__setattr__(self, "joinleave", bool(self.joinleave))
        object.__setattr__(self, "no_photo", bool(self.no_photo))
        object.__setattr__(self, "repeat_limit", _bounded_limit(self.repeat_limit))
        words = self.words
        if isinstance(words, str):
            words = (words,)
        object.__setattr__(self, "words", tuple(str(word).strip() for word in (words or ()) if str(word).strip()))

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None) -> "RoomPolicy":
        values = values if isinstance(values, Mapping) else {}
        words = values.get("words", ())
        if isinstance(words, Mapping):
            words = tuple(words.keys())
        elif isinstance(words, str):
            words = (words,)
        elif not isinstance(words, (list, tuple, set, frozenset)):
            words = ()
        return cls(
            swear=values.get("swear", False),
            flood=values.get("flood", False),
            joinleave=values.get("joinleave", False),
            no_photo=values.get("no_photo", False),
            repeat_limit=values.get("repeat_limit", 11),
            words=tuple(words),
        )


@dataclass(frozen=True)
class ModerationDecision:
    """Pure evaluation output; ``action_taken`` is always false."""

    room: str
    sender: str
    allowed: bool = True
    reason: str = "no_violation"
    recommendation: str = "none"  # none, warn, or ban
    matched_word: str | None = None
    sender_count: int = 0
    text_count: int = 0
    repeat_limit: int | None = None
    window_seconds: float | None = None
    exempt: bool = False
    action_taken: bool = False


class ModerationEngine:
    """Evaluate word filters and rolling repeat/flood policies per room.

    ``clock`` is injectable for deterministic tests. Message events remain in
    memory only and are pruned to the configured rolling window on evaluation.
    Flood counting follows the source's two independent signals: repeated
    messages by one sender, or the same normalized text across senders.
    """

    def __init__(
        self,
        room_configs: Mapping[str, RoomPolicy | Mapping[str, object]] | None = None,
        global_words: Iterable[str] = (),
        *,
        clock: Callable[[], float] = time.time,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._clock = clock
        self.window_seconds = max(0.001, float(window_seconds))
        self._room_policies: dict[str, RoomPolicy] = {}
        self._global_words = tuple(str(word).strip() for word in global_words if str(word).strip())
        self._events: dict[str, deque[tuple[float, str, str]]] = {}
        for room, config in (room_configs or {}).items():
            self.set_room_policy(room, config)

    def set_global_words(self, words: Iterable[str]) -> None:
        self._global_words = tuple(str(word).strip() for word in words if str(word).strip())

    def set_room_policy(self, room: str, policy: RoomPolicy | Mapping[str, object]) -> RoomPolicy:
        if not isinstance(policy, RoomPolicy):
            policy = RoomPolicy.from_mapping(policy)
        self._room_policies[_room_key(room)] = policy
        return policy

    def get_room_policy(self, room: str) -> RoomPolicy:
        return self._room_policies.get(_room_key(room), RoomPolicy())

    def evaluate(
        self,
        room: str,
        sender: str,
        text: str,
        *,
        role: str | None = None,
        is_master: bool = False,
        is_room_creator: bool = False,
        is_bot: bool = False,
        filter_exception: bool = False,
        now: float | None = None,
    ) -> ModerationDecision:
        """Return a moderation recommendation without performing any action."""
        room_name = str(room or "")
        sender_name = str(sender or "")
        room_id = _room_key(room_name)
        sender_id = _identity(sender_name)
        policy = self.get_room_policy(room_name)
        timestamp = float(self._clock() if now is None else now)
        normalized_text = normalize_arabic(text)

        # Source flood protection only considers known non-supervisory roles;
        # unknown roles are deliberately not guessed to be eligible.
        role_key = re.sub(r"[\s_-]+", "", str(role or "").casefold().strip())
        protected_roles = {
            "owner", "creator", "roomowner", "roomcreator", "host",
            "admin", "administrator", "moderator", "moder", "mod",
            "مشرف", "مشرفين", "ادمن", "إداري", "صانع", "صانعي",
            "صانعالغرفة", "صانعالغرفه", "مالك",
        }
        flood_exempt = bool(
            is_master or is_room_creator or is_bot or role_key in protected_roles
        )

        sender_count = text_count = 0
        if policy.flood and sender_id and normalized_text and not flood_exempt and role is not None and str(role).strip():
            history = self._events.setdefault(room_id, deque())
            cutoff = timestamp - self.window_seconds
            while history and history[0][0] < cutoff:
                history.popleft()
            history.append((timestamp, sender_id, normalized_text))
            sender_count = sum(event_sender == sender_id for _, event_sender, _ in history)
            text_count = sum(event_text == normalized_text for _, _, event_text in history)
            limit = policy.repeat_limit
            if sender_count >= limit or text_count >= limit:
                reason = "repeated_sender" if sender_count >= limit else "repeated_text"
                return ModerationDecision(
                    room_name, sender_name, allowed=False, reason=reason,
                    recommendation="ban", sender_count=sender_count,
                    text_count=text_count, repeat_limit=limit,
                    window_seconds=self.window_seconds,
                )
            if sender_count == limit - 1 or text_count == limit - 1:
                return ModerationDecision(
                    room_name, sender_name, allowed=True, reason="repeat_warning",
                    recommendation="warn", sender_count=sender_count,
                    text_count=text_count, repeat_limit=limit,
                    window_seconds=self.window_seconds,
                )

        # The source's word filter is enabled per room and checks the union of
        # global and room word lists using normalized substring matching.
        word_hit = None
        if policy.swear and not (is_master or is_bot or filter_exception):
            candidates: dict[str, str] = {}
            for word in (*self._global_words, *policy.words):
                key = normalize_arabic(word)
                if key:
                    candidates.setdefault(key, str(word).strip())
            for key in sorted(candidates):
                if key in normalized_text:
                    word_hit = candidates[key]
                    break
        if word_hit:
            return ModerationDecision(
                room_name, sender_name, allowed=False, reason="blocked_word",
                recommendation="ban", matched_word=word_hit,
                window_seconds=self.window_seconds,
            )

        if policy.swear and (is_master or is_bot or filter_exception):
            return ModerationDecision(
                room_name, sender_name, reason="exempt", exempt=True,
                window_seconds=self.window_seconds,
            )
        if policy.flood and flood_exempt:
            return ModerationDecision(
                room_name, sender_name, reason="exempt", exempt=True,
                window_seconds=self.window_seconds,
            )
        return ModerationDecision(
            room_name, sender_name, sender_count=sender_count,
            text_count=text_count,
            repeat_limit=policy.repeat_limit if policy.flood else None,
            window_seconds=self.window_seconds,
        )
