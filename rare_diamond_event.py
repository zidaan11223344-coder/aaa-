"""Persistent five-player Rare Diamond event for Giant Chat."""
from __future__ import annotations

import copy
import secrets
import time
import uuid
from pathlib import Path
from typing import Callable

from cricket_state import JsonState

ROUND_SECONDS = 180
COOLDOWN_SECONDS = 180
REQUIRED_PLAYERS = 5
WINNER_PRIZE = 100_000_000
LOSER_DEBIT = 10_000


def _blank() -> dict:
    return {"active": None, "next_open_at": 0.0, "last_result": None}


class RareDiamondEvent:
    """Own event state; caller owns chat delivery and point persistence."""

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time, chooser: Callable = secrets.choice):
        self.state = JsonState(path, _blank)
        self.clock = clock
        self.chooser = chooser

    def active(self) -> dict | None:
        active = self.state.load().get("active")
        return copy.deepcopy(active) if isinstance(active, dict) else None

    def open_if_due(self, rooms: list[str] | tuple[str, ...]) -> dict | None:
        targets = sorted({str(room).strip() for room in rooms if str(room).strip()}, key=str.casefold)
        if not targets:
            return None
        now = float(self.clock())
        result = {"opened": False, "event": None}

        def action(state):
            current = state.get("active")
            if isinstance(current, dict) or now < float(state.get("next_open_at", 0) or 0):
                return
            event = {
                "id": "rare-diamond-" + uuid.uuid4().hex,
                "phase": "open",
                "rooms": targets,
                "players": [],
                "required_players": REQUIRED_PLAYERS,
                "prize": WINNER_PRIZE,
                "opened_at": now,
                "ends_at": now + ROUND_SECONDS,
            }
            state["active"] = event
            result["opened"] = True
            result["event"] = copy.deepcopy(event)

        self.state.mutate(action)
        return result["event"] if result["opened"] else None

    def join(self, user_id: str, username: str, room: str) -> dict:
        uid = str(user_id or "").strip()
        name = str(username or "").strip().lstrip("@")
        room_id = str(room or "").strip()
        outcome: dict = {"ok": False, "error": "no_event"}
        if not uid or not name or not room_id:
            return {"ok": False, "error": "invalid_player"}
        now = float(self.clock())

        def action(state):
            nonlocal outcome
            event = state.get("active")
            if not isinstance(event, dict) or event.get("phase") not in {"open", "ready", "settling"}:
                outcome = {"ok": False, "error": "no_event"}
                return
            if now >= float(event.get("ends_at", 0) or 0) and event.get("phase") == "open":
                outcome = {"ok": False, "error": "expired", "event": copy.deepcopy(event)}
                return
            if room_id not in [str(x) for x in event.get("rooms", [])]:
                outcome = {"ok": False, "error": "wrong_room"}
                return
            players = event.setdefault("players", [])
            if any(str(p.get("user_id") or "") == uid for p in players if isinstance(p, dict)):
                outcome = {"ok": True, "already_joined": True, "count": len(players), "required": int(event.get("required_players", REQUIRED_PLAYERS))}
                return
            required = int(event.get("required_players", REQUIRED_PLAYERS))
            if event.get("phase") != "open" or len(players) >= required:
                outcome = {"ok": False, "error": "full", "count": len(players), "required": required}
                return
            players.append({"user_id": uid, "username": name, "room": room_id})
            if len(players) >= required:
                event["phase"] = "ready"
            outcome = {
                "ok": True, "already_joined": False, "count": len(players),
                "required": required, "ready": event.get("phase") == "ready",
                "event_id": event.get("id"),
            }

        self.state.mutate(action)
        return outcome

    def expire_if_due(self) -> dict | None:
        now = float(self.clock())
        result: dict | None = None

        def action(state):
            nonlocal result
            event = state.get("active")
            if not isinstance(event, dict) or event.get("phase") not in {"open", "ready"}:
                return
            if now < float(event.get("ends_at", 0) or 0):
                return
            players = event.get("players", [])
            required = int(event.get("required_players", REQUIRED_PLAYERS))
            if len(players) >= required:
                event["phase"] = "ready"
                result = {"status": "ready", "event": copy.deepcopy(event)}
            else:
                result = {"status": "incomplete", "event": copy.deepcopy(event)}
                state["last_result"] = {"status": "incomplete", "event_id": event.get("id"), "players": len(players), "at": now}
                state["active"] = None
                state["next_open_at"] = now + COOLDOWN_SECONDS

        self.state.mutate(action)
        return result

    def resolve(self, apply_delta: Callable[[str, str, int, str, str], dict]) -> dict | None:
        """Credit/debit once using stable transaction IDs; retain interrupted work."""
        winner: dict | None = None
        event_id = ""
        now = float(self.clock())

        def prepare(state):
            nonlocal winner, event_id
            event = state.get("active")
            if not isinstance(event, dict):
                return
            if event.get("phase") == "settling":
                winner = copy.deepcopy(event)
                event_id = str(event.get("id") or "")
                return
            players = event.get("players", [])
            required = int(event.get("required_players", REQUIRED_PLAYERS))
            if len(players) < required:
                return
            chosen = self.chooser(players)
            winner_id = str(chosen.get("user_id") or "")
            round_id = str(event.get("id") or "")
            payouts = []
            for player in players:
                uid = str(player.get("user_id") or "")
                name = str(player.get("username") or "")
                is_winner = uid == winner_id
                payouts.append({
                    "user_id": uid, "username": name,
                    "amount": WINNER_PRIZE if is_winner else -LOSER_DEBIT,
                    "transaction_id": f"{round_id}:{'winner' if is_winner else 'loss'}:{uid}",
                    "reason": "rare diamond event prize" if is_winner else "rare diamond event participation",
                    "status": "pending",
                })
            event["phase"] = "settling"
            event["winner_id"] = winner_id
            event["winner_name"] = str(chosen.get("username") or "")
            event["payouts"] = payouts
            event["settling_at"] = now
            winner = copy.deepcopy(event)
            event_id = round_id

        self.state.mutate(prepare)
        if not isinstance(winner, dict):
            return None

        for payout in winner.get("payouts", []):
            if payout.get("status") in {"applied", "insufficient"}:
                continue
            try:
                result = apply_delta(
                    str(payout.get("user_id") or ""),
                    str(payout.get("username") or ""),
                    int(payout.get("amount", 0) or 0),
                    str(payout.get("transaction_id") or ""),
                    str(payout.get("reason") or ""),
                )
            except Exception:
                result = {"ok": False, "error": "persistence_failed"}
            if isinstance(result, dict) and result.get("ok"):
                status = "applied"
            elif isinstance(result, dict) and result.get("error") == "insufficient":
                status = "insufficient"
            else:
                return {"ok": False, "pending": True, "event_id": event_id}

            def mark(state, txid=str(payout.get("transaction_id") or ""), state_value=status):
                active = state.get("active")
                if not isinstance(active, dict) or str(active.get("id") or "") != event_id or active.get("phase") != "settling":
                    return
                for row in active.get("payouts", []):
                    if isinstance(row, dict) and str(row.get("transaction_id") or "") == txid:
                        row["status"] = state_value

            self.state.mutate(mark)

        final: dict | None = None

        def finish(state):
            nonlocal final
            active = state.get("active")
            if not isinstance(active, dict) or str(active.get("id") or "") != event_id or active.get("phase") != "settling":
                return
            if any(row.get("status") not in {"applied", "insufficient"} for row in active.get("payouts", []) if isinstance(row, dict)):
                return
            final = {
                "status": "complete", "event_id": event_id,
                "winner_id": active.get("winner_id"), "winner": active.get("winner_name"),
                "rooms": list(active.get("rooms", [])),
                "payouts": copy.deepcopy(active.get("payouts", [])),
                "at": float(self.clock()),
            }
            state["last_result"] = final
            state["active"] = None
            state["next_open_at"] = float(self.clock()) + COOLDOWN_SECONDS

        self.state.mutate(finish)
        return {"ok": True, "result": final} if final else {"ok": False, "pending": True, "event_id": event_id}
