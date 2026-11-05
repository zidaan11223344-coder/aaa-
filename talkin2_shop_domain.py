"""Standalone domain model for Talkin2 shop purchases and entitlements.

This module deliberately has no platform/Giant integration. The host supplies
atomic debit/refund callbacks and an optional persistence callback.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any


ITEMS: dict[int, dict[str, Any]] = {
    1: {"name": "personal_bomb_protection", "kind": "user_bomb", "cost": 10_000_000, "seconds": 3600},
    2: {"name": "room_bomb_protection", "kind": "room_bomb", "cost": 25_000_000, "seconds": 3600},
    3: {"name": "search_hiding", "kind": "user_search", "cost": 8_000_000, "seconds": 3600},
    4: {"name": "room_roster_hiding", "kind": "room_roster", "cost": 15_000_000, "seconds": 3600},
    5: {"name": "custom_text_welcome", "kind": "custom_welcome", "cost": 5_000_000, "seconds": 0},
    6: {"name": "custom_image_welcome", "kind": "image_welcome", "cost": 12_000_000, "seconds": 0},
}


class ShopError(Exception):
    """Base exception for shop domain errors."""


class InvalidIdentity(ShopError):
    """Raised when an identity is not a canonicalizable UUID."""


class DebitRejected(ShopError):
    """Raised when the host rejects a points debit."""


class PurchaseRejected(ShopError):
    """Raised when a purchase is unavailable or cannot be persisted."""


def _uuid_text(value: str | uuid.UUID, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidIdentity(f"{label} must be a UUID") from exc


def empty_state() -> dict[str, Any]:
    """Return a fresh JSON-serializable state document."""
    return {"version": 1, "users": {}, "rooms": {}}


class ShopDomain:
    """Manage temporary protections, welcome unlocks, and point purchases.

    ``debit(user_uuid, amount)`` must atomically debit points and return a
    truthy acceptance value. ``refund(user_uuid, amount)`` reverses an accepted
    debit if saving the entitlement fails. The callbacks run under this
    instance's lock; cross-process/account atomicity remains the host's duty.
    ``save(state)`` receives a detached JSON-safe candidate and may return
    ``False`` or raise to reject persistence. The in-memory state is only
    replaced after persistence succeeds.
    """

    def __init__(
        self,
        *,
        state: Mapping[str, Any] | None = None,
        clock: Callable[[], float] = time.time,
        debit: Callable[[str, int], Any] | None = None,
        refund: Callable[[str, int], Any] | None = None,
        save: Callable[[dict[str, Any]], Any] | None = None,
        items: Mapping[int, Mapping[str, Any]] | None = None,
    ) -> None:
        self.clock = clock
        self.debit = debit
        self.refund = refund
        self.save = save
        self.items = {int(key): dict(value) for key, value in (items or ITEMS).items()}
        self._lock = threading.RLock()
        self._state = copy.deepcopy(dict(state)) if state is not None else empty_state()
        self._validate_state(self._state)

    @property
    def state(self) -> dict[str, Any]:
        """Return a defensive copy of the current JSON-compatible state."""
        with self._lock:
            return copy.deepcopy(self._state)

    @staticmethod
    def _validate_state(state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or not isinstance(state.get("users", {}), dict) or not isinstance(state.get("rooms", {}), dict):
            raise ValueError("state must contain object-valued users and rooms")
        # Fail early on non-JSON values rather than persisting an unserializable state.
        json.dumps(state, ensure_ascii=False)

    def _commit(self, candidate: dict[str, Any]) -> None:
        self._validate_state(candidate)
        detached = copy.deepcopy(candidate)
        if self.save is not None:
            accepted = self.save(detached)
            if accepted is False:
                raise PurchaseRejected("state persistence rejected the purchase")
        self._state = candidate

    def purchase(self, user_id: str | uuid.UUID, item_id: int, *, room_id: str | uuid.UUID | None = None) -> dict[str, Any]:
        """Debit points and grant one entitlement; raises on rejection.

        Temporary products do not stack while active. After expiry they can be
        purchased again. Welcome unlocks are permanent and idempotently blocked.
        Each granted entitlement has a UUID record id preserved in state.
        """
        uid = _uuid_text(user_id, "user_id")
        try:
            product = self.items[int(item_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise PurchaseRejected("unknown shop item") from exc
        kind = str(product["kind"])
        cost = int(product["cost"])
        duration = int(product.get("seconds", 0))
        room_kind = kind.startswith("room_")
        if room_kind and room_id is None:
            raise InvalidIdentity("room_id is required for a room entitlement")
        rid = _uuid_text(room_id, "room_id") if room_kind else None
        if self.debit is None or self.refund is None:
            raise ShopError("debit and refund callbacks are required for purchases")

        with self._lock:
            candidate = copy.deepcopy(self._state)
            bucket = candidate["rooms"] if room_kind else candidate["users"]
            subject = rid if room_kind else uid
            row = bucket.setdefault(subject, {})
            old = row.get(kind)
            now = float(self.clock())
            if duration and isinstance(old, dict) and float(old.get("expires_at", 0)) > now:
                raise PurchaseRejected("temporary entitlement is already active")
            if not duration and kind in row.get("unlocks", []):
                raise PurchaseRejected("welcome feature is already unlocked")

            accepted = self.debit(uid, cost)
            if not accepted:
                raise DebitRejected("points debit rejected")

            record = {
                "id": str(uuid.uuid4()),
                "kind": kind,
                "user_id": uid,
                "room_id": rid,
                "purchased_at": now,
                "cost": cost,
            }
            if duration:
                record["expires_at"] = now + duration
                row[kind] = record
            else:
                unlocks = set(row.get("unlocks", []))
                unlocks.add(kind)
                row["unlocks"] = sorted(unlocks)
                # Keep the grant record for auditability, without changing the
                # compact unlock lookup shape.
                grants = row.setdefault("grants", {})
                grants[kind] = record
            try:
                self._commit(candidate)
            except Exception as exc:
                try:
                    self.refund(uid, cost)
                except Exception as refund_exc:
                    raise PurchaseRejected("purchase save failed and debit refund also failed") from refund_exc
                raise PurchaseRejected("purchase save failed; debit refunded") from exc
            return copy.deepcopy(record)

    def active(self, kind: str, *, user_id: str | uuid.UUID | None = None, room_id: str | uuid.UUID | None = None) -> bool:
        """Return whether a permanent unlock or temporary entitlement is active."""
        now = float(self.clock())
        with self._lock:
            if kind in {"room_bomb", "room_roster"}:
                if room_id is None:
                    return False
                rid = _uuid_text(room_id, "room_id")
                record = self._state.get("rooms", {}).get(rid, {}).get(kind, {})
            else:
                if user_id is None:
                    return False
                uid = _uuid_text(user_id, "user_id")
                row = self._state.get("users", {}).get(uid, {})
                if kind in {"custom_welcome", "image_welcome"}:
                    return kind in row.get("unlocks", [])
                record = row.get(kind, {})
            return isinstance(record, dict) and float(record.get("expires_at", 0) or 0) > now

    def remaining(self, kind: str, *, user_id: str | uuid.UUID | None = None, room_id: str | uuid.UUID | None = None) -> int:
        """Return whole remaining seconds for a temporary entitlement."""
        with self._lock:
            if kind.startswith("room_"):
                if room_id is None:
                    return 0
                rid = _uuid_text(room_id, "room_id")
                record = self._state.get("rooms", {}).get(rid, {}).get(kind, {})
            else:
                if user_id is None:
                    return 0
                uid = _uuid_text(user_id, "user_id")
                record = self._state.get("users", {}).get(uid, {}).get(kind, {})
            return max(0, int(float(record.get("expires_at", 0) or 0) - float(self.clock()))) if isinstance(record, dict) else 0
