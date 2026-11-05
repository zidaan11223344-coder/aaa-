"""Platform-agnostic state machine for Ludo and Snake & Ladders.

Interface contract
------------------
``new_ludo_game`` and ``new_snake_game`` create JSON-compatible state dictionaries.
``apply_action(state, actor, command, *, now, count=None, roll=None,
bot_roll=None)`` never mutates its input and returns a mapping with ``ok``,
``state``, ``events`` and, on rejection, ``error``. Commands are ``choose``
(Ludo player count), ``join``, ``roll`` and ``timeout``. The caller supplies
all timestamps and dice results; this module performs no I/O, transport,
randomness, scheduling, UUID generation or persistence. Ludo's token progress
is 0..58 (start yard to finish); Snake positions start at square 1 and clamp
to 100 before resolving a ladder or snake. ``expire_if_idle`` provides the
same 120-second inactivity decision as Talkin2's board-game timer. Use
``to_json``/``from_json`` when a persistence adapter needs a JSON round trip.
"""
from __future__ import annotations

import copy
import json
from typing import Any

TIMEOUT_SECONDS = 120.0
LUDO_FINISH = 58
SNAKE_FINISH = 100
BOT_PLAYER = "🤖 البوت"
LADDERS = {3: 22, 8: 30, 28: 55, 36: 44, 51: 72, 71: 92, 80: 99}
SNAKES = {98: 40, 95: 75, 92: 70, 88: 48, 62: 18, 48: 26, 24: 5, 17: 7}


def new_ludo_game(game_id: str, room_id: str, creator: str, *, now: float = 0.0,
                  language: str = "ar") -> dict[str, Any]:
    """Create a Ludo lobby; creator is the first player, progress 0, count unset."""
    _require_identity(game_id, room_id, creator)
    return {
        "game_id": str(game_id), "game_type": "ludo", "room_id": str(room_id),
        "players": [str(creator)], "tokens": {str(creator): 0}, "language": language,
        "turn": 0, "created_at": float(now), "last_activity_at": float(now),
        "max_players": 0, "bot": False, "status": "lobby", "last_roll": None,
        "winner": None,
    }


def new_snake_game(game_id: str, room_id: str, creator: str, *, now: float = 0.0,
                   language: str = "ar") -> dict[str, Any]:
    """Create a two-player Snake & Ladders lobby; each player starts at square 1."""
    _require_identity(game_id, room_id, creator)
    return {
        "game_id": str(game_id), "game_type": "snake", "room_id": str(room_id),
        "players": [str(creator)], "positions": {str(creator): 1},
        "language": language, "turn": 0, "created_at": float(now),
        "last_activity_at": float(now), "status": "lobby", "last_roll": None,
        "winner": None,
    }


def apply_action(state: dict[str, Any], actor: str, command: str, *, now: float,
                 count: int | None = None, roll: int | None = None,
                 bot_roll: int | None = None) -> dict[str, Any]:
    """Apply one explicit action and return ``{ok, state, events[, error]}``.

    Allowed commands: ``choose`` and ``join`` for Ludo, ``join`` for Snake,
    ``roll`` for either game, and ``timeout`` for an inactivity check. A roll
    is supplied by the host (1..6); Ludo-vs-bot also requires the caller to
    supply ``bot_roll`` because Talkin2 rolls for its bot immediately.
    Rejected commands leave the input state unchanged.
    """
    _validate_state(state)
    current = copy.deepcopy(state)
    actor = str(actor)
    if command == "timeout":
        timed = expire_if_idle(current, now=now)
        if timed["status"] == "timed_out":
            return _result(True, timed, [{"type": "timed_out"}])
        return _failure(state, "not_timed_out")
    if current["status"] in ("finished", "timed_out"):
        return _failure(state, "game_over")
    if command == "join":
        return _join(current, actor, now)
    if command == "choose" and current["game_type"] == "ludo":
        return _choose_ludo(current, actor, count)
    if command == "roll":
        return _roll(current, actor, now, roll, bot_roll)
    if command == "choose":
        return _failure(state, "unsupported_command")
    return _failure(state, "invalid_command")


def expire_if_idle(state: dict[str, Any], *, now: float) -> dict[str, Any]:
    """Return a copy marked ``timed_out`` once 120 seconds have elapsed idle."""
    _validate_state(state)
    result = copy.deepcopy(state)
    if result["status"] not in ("finished", "timed_out"):
        if float(now) - float(result["last_activity_at"]) >= TIMEOUT_SECONDS:
            result["status"] = "timed_out"
    return result


def current_player(state: dict[str, Any]) -> str | None:
    """Return the current player's name, or None for a malformed/empty roster."""
    players = state.get("players")
    turn = state.get("turn")
    if not isinstance(players, list) or not players or not isinstance(turn, int):
        return None
    return players[turn % len(players)]


def to_json(state: dict[str, Any]) -> str:
    """Serialize a game state as compact, standards-compliant JSON."""
    _validate_state(state)
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def from_json(payload: str) -> dict[str, Any]:
    """Deserialize and validate JSON previously produced for a game state."""
    state = json.loads(payload)
    _validate_state(state)
    return state


def _choose_ludo(state: dict[str, Any], actor: str, count: int | None) -> dict[str, Any]:
    if actor != state["players"][0] or state["status"] != "lobby":
        return _failure(state, "not_lobby_creator")
    if len(state["players"]) != 1 or state["max_players"] != 0:
        return _failure(state, "player_count_already_set")
    if isinstance(count, bool) or count not in (1, 2, 3, 4):
        return _failure(state, "invalid_player_count")
    state["max_players"] = 1 if count == 1 else count
    if count == 1:
        state["bot"] = True
        state["players"].append(BOT_PLAYER)
        state["tokens"][BOT_PLAYER] = 0
        state["status"] = "active"
        state["turn"] = 0
        return _result(True, state, [{"type": "started", "players": list(state["players"]), "against_bot": True}])
    return _result(True, state, [{"type": "player_count_selected", "count": count,
                                 "needed": count - len(state["players"])}])


def _join(state: dict[str, Any], actor: str, now: float) -> dict[str, Any]:
    if state["status"] != "lobby":
        return _failure(state, "game_not_joinable")
    if actor in state["players"]:
        return _failure(state, "already_joined")
    if state["game_type"] == "snake":
        capacity = 2
    else:
        if state["max_players"] == 1:
            return _failure(state, "bot_game")
        capacity = state["max_players"] or 4
    if len(state["players"]) >= capacity:
        return _failure(state, "game_full")
    state["players"].append(actor)
    if state["game_type"] == "ludo":
        state["tokens"][actor] = 0
    else:
        state["positions"][actor] = 1
    state["last_activity_at"] = float(now)
    events = [{"type": "player_joined", "player": actor}]
    if state["game_type"] == "ludo" and state["max_players"] and len(state["players"]) >= state["max_players"]:
        state["status"] = "active"
        state["turn"] = 0
        events.append({"type": "started", "players": list(state["players"]), "against_bot": False})
    elif state["game_type"] == "snake" and len(state["players"]) == 2:
        state["status"] = "active"
        state["turn"] = 0
    return _result(True, state, events)


def _roll(state: dict[str, Any], actor: str, now: float, roll: int | None,
          bot_roll: int | None) -> dict[str, Any]:
    if actor not in state["players"]:
        return _failure(state, "not_a_player")
    if state["game_type"] == "ludo":
        if state["max_players"] == 0:
            return _failure(state, "choose_player_count_first")
        if state["status"] != "active":
            return _failure(state, "waiting_for_players")
    elif len(state["players"]) < 2:
        return _failure(state, "waiting_for_players")
    if state["status"] != "active":
        return _failure(state, "game_not_active")
    if current_player(state) != actor:
        return _failure(state, "not_your_turn")
    if isinstance(roll, bool) or not isinstance(roll, int) or not 1 <= roll <= 6:
        return _failure(state, "invalid_roll")
    if state["game_type"] == "ludo" and state["bot"]:
        if isinstance(bot_roll, bool) or not isinstance(bot_roll, int) or not 1 <= bot_roll <= 6:
            return _failure(state, "invalid_bot_roll")

    state["last_activity_at"] = float(now)
    state["last_roll"] = roll
    events: list[dict[str, Any]] = []
    if state["game_type"] == "snake":
        old = state["positions"][actor]
        landed = min(SNAKE_FINISH, old + roll)
        final = LADDERS.get(landed, SNAKES.get(landed, landed))
        state["positions"][actor] = final
        events.append({"type": "moved", "player": actor, "roll": roll,
                       "from": old, "landed": landed, "to": final})
        if final >= SNAKE_FINISH:
            state["status"] = "finished"
            state["winner"] = actor
            events.append({"type": "won", "player": actor})
        else:
            state["turn"] = (state["turn"] + 1) % len(state["players"])
            events.append({"type": "turn", "player": current_player(state)})
        return _result(True, state, events)

    old = state["tokens"][actor]
    new = min(LUDO_FINISH, old + roll)
    state["tokens"][actor] = new
    events.append({"type": "moved", "player": actor, "roll": roll, "from": old, "to": new})
    if new >= LUDO_FINISH:
        state["status"] = "finished"
        state["winner"] = actor
        events.append({"type": "won", "player": actor})
        return _result(True, state, events)

    state["turn"] = (state["turn"] + 1) % len(state["players"])
    if state["bot"] and current_player(state) == BOT_PLAYER:
        bot_old = state["tokens"][BOT_PLAYER]
        bot_new = min(LUDO_FINISH, bot_old + bot_roll)
        state["tokens"][BOT_PLAYER] = bot_new
        state["last_roll"] = bot_roll
        events.append({"type": "bot_moved", "player": BOT_PLAYER, "roll": bot_roll,
                       "from": bot_old, "to": bot_new})
        # Talkin2 automatically hands the turn back after the bot's single roll.
        state["turn"] = 0
    events.append({"type": "turn", "player": current_player(state)})
    return _result(True, state, events)


def _result(ok: bool, state: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": ok, "state": state, "events": events}


def _failure(state: dict[str, Any], error: str) -> dict[str, Any]:
    return {"ok": False, "state": copy.deepcopy(state), "events": [], "error": error}


def _require_identity(game_id: str, room_id: str, creator: str) -> None:
    if not all(isinstance(v, str) and v.strip() for v in (game_id, room_id, creator)):
        raise ValueError("game_id, room_id, and creator must be non-empty strings")


def _validate_state(state: dict[str, Any]) -> None:
    if not isinstance(state, dict) or state.get("game_type") not in ("ludo", "snake"):
        raise ValueError("state must be a Ludo or Snake game dictionary")
    required = ("game_id", "room_id", "players", "turn", "created_at",
                "last_activity_at", "status", "winner")
    if any(key not in state for key in required):
        raise ValueError("state is missing required game fields")
    if not isinstance(state["players"], list) or not state["players"]:
        raise ValueError("state players must be a non-empty list")
    if not isinstance(state["turn"], int) or isinstance(state["turn"], bool):
        raise ValueError("state turn must be an integer")
    if state["status"] not in ("lobby", "active", "finished", "timed_out"):
        raise ValueError("state has an invalid status")
    if state["game_type"] == "ludo" and not isinstance(state.get("tokens"), dict):
        raise ValueError("Ludo state requires a tokens mapping")
    if state["game_type"] == "snake" and not isinstance(state.get("positions"), dict):
        raise ValueError("Snake state requires a positions mapping")
