"""Durable outbox for cricket prizes that must reach Giant's UUID ledger."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from cricket_state import JsonState


def _blank() -> dict[str, Any]:
    return {"pending": {}, "identities": {}}


class CricketRewardOutbox:
    def __init__(self, path: str | Path):
        self.state = JsonState(path, _blank)

    @staticmethod
    def _identity_key(username: str) -> str:
        return str(username or "").strip().lstrip("@").casefold()

    def record_identity(self, user_id: str, username: str) -> bool:
        uid = str(user_id or "").strip()
        name = str(username or "").strip().lstrip("@")
        key = self._identity_key(name)
        if not uid or not key:
            return False

        def action(state):
            identities = state.setdefault("identities", {})
            if not isinstance(identities, dict):
                identities = state["identities"] = {}
            identities[key] = {"user_id": uid, "username": name}

        self.state.mutate(action)
        return True

    def identity_for(self, username: str) -> dict | None:
        key = self._identity_key(username)
        identities = self.state.load().get("identities", {})
        value = identities.get(key) if isinstance(identities, dict) else None
        return copy.deepcopy(value) if isinstance(value, dict) else None

    def enqueue(self, username: str, amount: int, transaction_id: str, user_id: str = "") -> dict:
        name = str(username or "").strip().lstrip("@")
        txid = str(transaction_id or "").strip()
        if not name or not txid:
            return {"ok": False, "error": "invalid_reward"}
        identity = self.identity_for(name) or {}
        record = {
            "username": name, "user_id": str(user_id or identity.get("user_id") or ""),
            "amount": int(amount), "transaction_id": txid, "reason": "cricket match prize",
        }

        def action(state):
            pending = state.setdefault("pending", {})
            if not isinstance(pending, dict):
                pending = state["pending"] = {}
            old = pending.get(txid)
            if isinstance(old, dict):
                return {"ok": True, "already_queued": True}
            pending[txid] = record
            return {"ok": True, "already_queued": False}

        return self.state.mutate(action)

    def pending(self) -> list[dict]:
        data = self.state.load()
        records = data.get("pending", {})
        if not isinstance(records, dict):
            return []
        return [copy.deepcopy(value) for _, value in sorted(records.items()) if isinstance(value, dict)]

    def complete(self, transaction_id: str) -> bool:
        txid = str(transaction_id or "").strip()
        removed = False

        def action(state):
            nonlocal removed
            pending = state.get("pending", {})
            if isinstance(pending, dict) and txid in pending:
                pending.pop(txid, None)
                removed = True

        self.state.mutate(action)
        return removed
