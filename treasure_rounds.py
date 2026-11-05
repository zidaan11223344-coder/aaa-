"""Transport-independent timed treasure-round engine.

This module intentionally performs no network, database, or user-account I/O.
It returns durable settlement intents for an adapter to apply idempotently.
"""
from __future__ import annotations

import copy
import secrets
import threading
import time
import uuid
from typing import Any, Callable, Iterable, Mapping

REGISTRATION_SECONDS = 180.0
INTER_ROUND_BREAK_SECONDS = 180.0
LOSER_DEDUCTION = 10_000

# Ordered to match Talkin2's timed-game cycle. Counts and prizes are copied from
# its TIMED_TREASURE_GAMES configuration; presentation and transport are omitted.
TIMED_TREASURE_GAMES: dict[str, dict[str, Any]] = {
    "rare_diamond": {"players": 5, "prize": 100_000_000, "cycle": "classic", "join_ar": "تنقيب", "join_en": "mining", "aliases": ("تنقيب", "نقب", "rare diamond", "rare-diamond", "mining", "diamond mining")},
    "gold_treasure": {"players": 5, "prize": 100_000_000, "cycle": "classic", "join_ar": "كنز", "join_en": "treasure", "aliases": ("كنز", "treasure", "gold treasure")},
    "treasure_island": {"players": 5, "prize": 100_000_000, "cycle": "classic", "join_ar": "جزيرة", "join_en": "island", "aliases": ("جزيرة", "island", "treasure island")},
    "fortune_hunt": {"players": 5, "prize": 100_000_000, "cycle": "classic", "join_ar": "ثروة", "join_en": "fortune", "aliases": ("ثروة", "fortune", "wealth hunt")},
    "gem_mine": {"players": 5, "prize": 100_000_000, "cycle": "classic", "join_ar": "منجم", "join_en": "gem mine", "aliases": ("منجم", "gem mine", "gemstone mine")},
    "imperial_jade": {"players": 5, "prize": 100_000_000, "cycle": "classic", "join_ar": "يشم", "join_en": "jade", "aliases": ("يشم", "jade", "imperial jade")},
    "imperial_sickness": {"players": 7, "prize": 500_000_000, "cycle": "doctor", "join_ar": "طبيب", "join_en": "doctor", "aliases": ("طبيب", "doctor", "emperor doctor")},
    "flight_radar": {"players": 7, "prize": 500_000_000, "cycle": "royal", "join_ar": "كابتن", "join_en": "captain", "aliases": ("كابتن", "captain", "radar", "flight attack")},
    "mercenary_escape": {"players": 7, "prize": 500_000_000, "cycle": "royal", "join_ar": "أمن", "join_en": "police", "aliases": ("أمن", "امن", "police", "security", "chase")},
}
GAME_ORDER = tuple(TIMED_TREASURE_GAMES)


class RoundError(ValueError):
    """Invalid round operation or identifier."""


class TreasureRoundEngine:
    """State machine for one globally active round and its recoverable payouts.

    Args:
        clock: Callable returning seconds since epoch. Inject a fake clock in tests.
        rng: Object exposing ``choice(sequence)``; defaults to ``secrets``.
        state: Optional JSON-compatible state returned earlier by ``export_state``.
        registration_seconds: Round signup window (Talkin2: 180 seconds).
        break_seconds: Cooldown measured from round resolution/timeout (180 seconds).
        id_factory: Callable returning a UUID-like value; normally ``uuid.uuid4``.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        rng: Any = secrets,
        state: Mapping[str, Any] | None = None,
        registration_seconds: float = REGISTRATION_SECONDS,
        break_seconds: float = INTER_ROUND_BREAK_SECONDS,
        id_factory: Callable[[], Any] = uuid.uuid4,
    ) -> None:
        if registration_seconds <= 0 or break_seconds < 0:
            raise ValueError("registration_seconds must be positive and break_seconds non-negative")
        self._clock = clock
        self._rng = rng
        self._id_factory = id_factory
        self.registration_seconds = float(registration_seconds)
        self.break_seconds = float(break_seconds)
        self._lock = threading.RLock()
        if state is None:
            self._state: dict[str, Any] = {
                "version": 1,
                "cycle_index": 0,
                "next_due": 0.0,
                "active_round_id": None,
                "rounds": {},
                "doctor_bans": {},
            }
        else:
            self._state = copy.deepcopy(dict(state))
            self._validate_state(self._state)

    @staticmethod
    def _validate_state(state: dict[str, Any]) -> None:
        if state.get("version") != 1 or not isinstance(state.get("rounds"), dict):
            raise RoundError("unsupported or malformed engine state")
        state.setdefault("cycle_index", 0)
        state.setdefault("next_due", 0.0)
        state.setdefault("active_round_id", None)
        state.setdefault("doctor_bans", {})
        for round_id in state["rounds"]:
            try:
                uuid.UUID(str(round_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise RoundError("persisted round IDs must be UUIDs") from exc

    @staticmethod
    def _round_id(value: Any) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RoundError("round_id must be a UUID") from exc

    @staticmethod
    def _player_key(username: Any) -> str:
        return " ".join(str(username or "").strip().lstrip("@").casefold().split())

    def _expire_locked(self, round_data: dict[str, Any], now: float) -> dict[str, Any]:
        round_data["status"] = "expired"
        round_data["closed_at"] = float(now)
        self._state["active_round_id"] = None
        self._state["next_due"] = float(now) + self.break_seconds
        return copy.deepcopy(round_data)

    def _close_expired_active_locked(self, now: float) -> None:
        active_id = self._state.get("active_round_id")
        if not active_id:
            return
        active = self._state["rounds"].get(active_id)
        if active and active.get("status") == "registration" and now >= float(active["ends_at"]):
            self._expire_locked(active, now)

    def start_round(self, rooms: Iterable[Any], game_key: str | None = None) -> dict[str, Any] | None:
        """Open a cycle round, or a selected game, if active/cooldown allow it.

        Room values are opaque transport-owned IDs (UUID strings are supported).
        Returns a detached round snapshot, or ``None`` when blocked by a live
        round/cooldown. ``game_key=None`` advances the nine-game ordered cycle.
        """
        room_ids = sorted({str(room).strip() for room in rooms if str(room).strip()}, key=str.casefold)
        if not room_ids:
            raise RoundError("at least one room ID is required")
        if game_key is not None and str(game_key) not in TIMED_TREASURE_GAMES:
            raise RoundError(f"unknown game_key: {game_key}")
        with self._lock:
            now = float(self._clock())
            self._close_expired_active_locked(now)
            active_id = self._state.get("active_round_id")
            if active_id or now < float(self._state.get("next_due", 0.0)):
                return None
            if game_key is None:
                index = int(self._state["cycle_index"]) % len(GAME_ORDER)
                selected = GAME_ORDER[index]
            else:
                selected = str(game_key)
                if selected not in TIMED_TREASURE_GAMES:
                    raise RoundError(f"unknown game_key: {selected}")
                index = GAME_ORDER.index(selected)
            raw_id = self._id_factory()
            round_id = self._round_id(raw_id)
            if round_id in self._state["rounds"]:
                raise RoundError("id_factory returned a previously used round UUID")
            config = TIMED_TREASURE_GAMES[selected]
            doctor_bans = {}
            if selected == "imperial_sickness":
                doctor_bans = copy.deepcopy(self._state["doctor_bans"])
                self._state["doctor_bans"] = {}
            round_data = {
                "id": round_id,
                "game_key": selected,
                "status": "registration",
                "rooms": room_ids,
                "required_players": int(config["players"]),
                "prize": int(config["prize"]),
                "opened_at": now,
                "ends_at": now + self.registration_seconds,
                "players": {},
                "doctor_banned_once": doctor_bans,
                "winner": None,
                "settlement_intents": {},
            }
            self._state["rounds"][round_id] = round_data
            self._state["active_round_id"] = round_id
            self._state["cycle_index"] = (index + 1) % len(GAME_ORDER)
            return copy.deepcopy(round_data)

    def join(self, round_id: Any, room_id: Any, username: Any, *, user_id: Any = None) -> dict[str, Any]:
        """Join by round UUID from any configured room; a player counts once.

        Returns ``{"accepted": bool, "reason": str, "count": int, "round": ...}``.
        A full roster resolves immediately and creates settlement intents.
        """
        rid = self._round_id(round_id)
        room = str(room_id).strip()
        name = str(username or "").strip().lstrip("@")
        player_key = str(user_id or "").strip() or self._player_key(name)
        if not room or not player_key:
            raise RoundError("room_id and username are required")
        with self._lock:
            now = float(self._clock())
            round_data = self._state["rounds"].get(rid)
            if round_data is None:
                return {"accepted": False, "reason": "unknown_round", "count": 0, "round": None}
            self._close_expired_active_locked(now)
            # Expiry may have changed this record above.
            status = round_data["status"]
            count = len(round_data["players"])
            if status != "registration":
                return {"accepted": False, "reason": status, "count": count, "round": copy.deepcopy(round_data)}
            if now >= float(round_data["ends_at"]):
                self._expire_locked(round_data, now)
                return {"accepted": False, "reason": "expired", "count": count, "round": copy.deepcopy(round_data)}
            if room not in round_data["rooms"]:
                return {"accepted": False, "reason": "room_not_in_round", "count": count, "round": copy.deepcopy(round_data)}
            if round_data["game_key"] == "imperial_sickness" and player_key in round_data["doctor_banned_once"]:
                return {"accepted": False, "reason": "doctor_round_ban", "count": count, "round": copy.deepcopy(round_data)}
            if player_key in round_data["players"]:
                return {"accepted": False, "reason": "duplicate", "count": count, "round": copy.deepcopy(round_data)}
            if count >= int(round_data["required_players"]):
                return {"accepted": False, "reason": "full", "count": count, "round": copy.deepcopy(round_data)}
            player_record = {"name": name, "room_id": room}
            if str(user_id or "").strip():
                player_record["user_id"] = str(user_id).strip()
            round_data["players"][player_key] = player_record
            count += 1
            if count == int(round_data["required_players"]):
                self._resolve_locked(round_data, now)
                reason = "roster_filled"
            else:
                reason = "joined"
            return {"accepted": True, "reason": reason, "count": count, "round": copy.deepcopy(round_data)}

    def _resolve_locked(self, round_data: dict[str, Any], now: float) -> None:
        players = list(round_data["players"].items())
        winner_key = self._rng.choice([key for key, _record in players])
        winner_player = round_data["players"][winner_key]
        winner_name = winner_player["name"]
        round_data["winner"] = winner_name
        round_data["status"] = "resolved"
        round_data["resolved_at"] = float(now)
        self._state["active_round_id"] = None
        self._state["next_due"] = float(now) + self.break_seconds
        game_key = round_data["game_key"]
        for player_key, player in players:
            if player_key == winner_key:
                continue
            self._add_intent(round_data, player_key, player["name"], -LOSER_DEDUCTION,
                             f"{game_key} participation", "loss", player.get("user_id"))
            if game_key == "imperial_sickness":
                self._state["doctor_bans"][player_key] = {"name": player["name"], "round_id": round_data["id"]}
        self._add_intent(round_data, winner_key, winner_name, int(round_data["prize"]),
                         f"{game_key} prize", "winner", winner_player.get("user_id"))

    @staticmethod
    def _add_intent(round_data: dict[str, Any], player_key: str, username: str, delta: int,
                    reason: str, kind: str, user_id: str | None = None) -> None:
        # Deterministic per-round/player/kind key makes retries safe at adapters.
        key = f"{round_data['id']}:{kind}:{player_key}"
        round_data["settlement_intents"][key] = {
            "idempotency_key": key,
            "round_id": round_data["id"],
            "game_key": round_data["game_key"],
            "username": username,
            "player_key": player_key,
            "user_id": user_id,
            "delta": int(delta),
            "reason": reason,
            "status": "pending",
            "receipt": None,
            "last_error": None,
        }

    def expire(self, round_id: Any) -> dict[str, Any]:
        """Expire a due, incomplete round; raise if it is not due or not active."""
        rid = self._round_id(round_id)
        with self._lock:
            round_data = self._state["rounds"].get(rid)
            if round_data is None:
                raise RoundError("unknown round")
            if round_data["status"] != "registration":
                return copy.deepcopy(round_data)
            now = float(self._clock())
            if now < float(round_data["ends_at"]):
                raise RoundError("round registration has not ended")
            return self._expire_locked(round_data, now)

    def pending_settlements(self, round_id: Any | None = None) -> list[dict[str, Any]]:
        """Return copies of all pending intents, suitable for crash recovery."""
        with self._lock:
            selected = [self._round_id(round_id)] if round_id is not None else list(self._state["rounds"])
            result = []
            for rid in selected:
                round_data = self._state["rounds"].get(rid)
                if round_data is None:
                    continue
                result.extend(copy.deepcopy(intent) for intent in round_data["settlement_intents"].values() if intent["status"] == "pending")
            return sorted(result, key=lambda item: item["idempotency_key"])

    def acknowledge_settlement(self, idempotency_key: str, *, success: bool, receipt: Any = None, error: str | None = None) -> dict[str, Any]:
        """Record adapter outcome; failed intents remain pending for retry.

        Re-acknowledging an applied key cannot revert it to pending. The adapter
        must use the supplied idempotency_key when applying the external change.
        """
        key = str(idempotency_key)
        with self._lock:
            for round_data in self._state["rounds"].values():
                intent = round_data["settlement_intents"].get(key)
                if intent is None:
                    continue
                if intent["status"] == "applied":
                    return copy.deepcopy(intent)
                if success:
                    intent.update(status="applied", receipt=copy.deepcopy(receipt), last_error=None)
                else:
                    intent.update(status="pending", last_error=str(error or "settlement failed"))
                return copy.deepcopy(intent)
            raise RoundError("unknown settlement intent")

    def get_round(self, round_id: Any) -> dict[str, Any] | None:
        rid = self._round_id(round_id)
        with self._lock:
            value = self._state["rounds"].get(rid)
            return copy.deepcopy(value) if value is not None else None

    def mark_announced(self, round_id: Any) -> dict[str, Any]:
        """Persist the adapter's successful delivery of a resolved-round notice."""
        rid = self._round_id(round_id)
        with self._lock:
            value = self._state["rounds"].get(rid)
            if value is None:
                raise RoundError("unknown round")
            if value.get("status") not in {"resolved", "expired"}:
                raise RoundError("round is not resolved")
            value["announced"] = True
            return copy.deepcopy(value)

    @property
    def next_due(self) -> float:
        with self._lock:
            return float(self._state["next_due"])

    @property
    def active_round_id(self) -> str | None:
        with self._lock:
            return self._state.get("active_round_id")

    def export_state(self) -> dict[str, Any]:
        """Return detached JSON-compatible state for persistence/restart."""
        with self._lock:
            return copy.deepcopy(self._state)

    @classmethod
    def recover(cls, state: Mapping[str, Any], **kwargs: Any) -> "TreasureRoundEngine":
        """Restore a fresh engine from an exported state and injected dependencies."""
        return cls(state=state, **kwargs)
