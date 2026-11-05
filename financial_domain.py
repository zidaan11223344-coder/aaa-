"""Restart-safe financial primitives for the Talkin2 migration.

The state is ordinary JSON-compatible data owned by the caller.  No bot,
network, database, or protocol code is imported or connected here.
"""
from __future__ import annotations

import copy
import json
import math
import threading
import time
import uuid
from typing import Any, Callable, Mapping


WAGER_TIMEOUT_SECONDS = 120.0
INVESTMENT_MIN_AMOUNT = 10
INVESTMENT_MAX_AMOUNT = 30_000_000_000
INVESTMENT_DURATION_SECONDS = 60.0


class FinancialDomain:
    """Atomic point operations over JSON state or an atomic external ledger.

    ``balance_callback`` (when supplied) must implement the following contract::

        callback(deltas: dict[str, int], transaction_id: str, reason: str)
            -> {"ok": bool, "balances": {account: integer_balance, ...}}

    It must apply *all* deltas atomically, reject negative resulting balances,
    and deduplicate transaction IDs durably. The account strings may be Giant
    UUIDs. The callback's durable idempotency is essential if the process stops
    after the external ledger commits but before the caller persists ``state``.
    Without a callback, balances and transaction receipts are kept in ``state``.
    """

    def __init__(
        self,
        state: dict[str, Any] | None = None,
        *,
        balance_callback: Callable[[dict[str, int], str, str], Mapping[str, Any]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.state = state if state is not None else {}
        if not isinstance(self.state, dict):
            raise TypeError("state must be a dictionary")
        self.state.setdefault("version", 1)
        self.state.setdefault("balances", {})
        self.state.setdefault("transactions", {})
        self.state.setdefault("wagers", {})
        self.state.setdefault("investments", {})
        for key in ("balances", "transactions", "wagers", "investments"):
            if not isinstance(self.state[key], dict):
                raise ValueError(f"state[{key!r}] must be a dictionary")
        self.balance_callback = balance_callback
        self.clock = clock
        self._lock = threading.RLock()

    @staticmethod
    def _account(account: Any) -> str:
        value = str(account or "").strip()
        if not value:
            raise ValueError("invalid_account")
        return value

    @staticmethod
    def _positive_int(value: Any, error: str = "invalid_amount") -> int:
        if isinstance(value, bool):
            raise ValueError(error)
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(error) from exc
        if result != value or result <= 0:
            raise ValueError(error)
        return result

    @staticmethod
    def _timestamp(value: Any) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid_time") from exc
        if not math.isfinite(result):
            raise ValueError("invalid_time")
        return result

    @staticmethod
    def _json_fingerprint(deltas: Mapping[str, int], reason: str) -> str:
        return json.dumps({"deltas": dict(sorted(deltas.items())), "reason": reason}, sort_keys=True)

    def _apply(self, deltas: Mapping[str, int], transaction_id: Any, reason: str) -> dict[str, Any]:
        txid = str(transaction_id or "").strip()
        if not txid:
            return {"ok": False, "error": "invalid_transaction"}
        clean: dict[str, int] = {}
        try:
            for account, delta in deltas.items():
                key = self._account(account)
                if isinstance(delta, bool) or not isinstance(delta, int):
                    raise ValueError("invalid_amount")
                if delta:
                    clean[key] = clean.get(key, 0) + delta
        except (ValueError, AttributeError) as exc:
            return {"ok": False, "error": str(exc) or "invalid_transaction"}
        if not clean:
            return {"ok": False, "error": "invalid_amount"}
        clean = {key: delta for key, delta in clean.items() if delta}
        if not clean:
            return {"ok": False, "error": "invalid_amount"}
        reason = str(reason or "financial_operation")
        fingerprint = self._json_fingerprint(clean, reason)
        prior = self.state["transactions"].get(txid)
        if prior is not None:
            if prior.get("fingerprint") != fingerprint:
                return {"ok": False, "error": "transaction_conflict"}
            response = copy.deepcopy(prior["response"])
            response["already_applied"] = True
            return response

        if self.balance_callback is not None:
            try:
                external = self.balance_callback(dict(clean), txid, reason)
            except Exception:
                return {"ok": False, "error": "persistence_failed"}
            if not isinstance(external, Mapping) or not external.get("ok"):
                return dict(external) if isinstance(external, Mapping) else {"ok": False, "error": "persistence_failed"}
            balances = external.get("balances", {})
            if not isinstance(balances, Mapping):
                return {"ok": False, "error": "invalid_callback_result"}
            response = {"ok": True, "already_applied": bool(external.get("already_applied", False)),
                        "balances": {str(k): int(v) for k, v in balances.items()}}
        else:
            balances_state = self.state["balances"]
            proposed: dict[str, int] = {}
            for account, delta in clean.items():
                current = balances_state.get(account, 0)
                if isinstance(current, bool) or not isinstance(current, int) or current < 0:
                    return {"ok": False, "error": "invalid_balance"}
                result = current + delta
                if result < 0:
                    return {"ok": False, "error": "insufficient", "balance": current}
                proposed[account] = result
            balances_state.update(proposed)
            response = {"ok": True, "already_applied": False,
                        "balances": dict(proposed)}
        self.state["transactions"][txid] = {"fingerprint": fingerprint, "response": copy.deepcopy(response)}
        return response

    def change_balance(self, account: Any, delta: Any, transaction_id: Any, reason: str = "balance_change") -> dict[str, Any]:
        """Atomically apply one signed balance change, once per transaction ID."""
        try:
            account_key = self._account(account)
            if isinstance(delta, bool):
                raise ValueError("invalid_amount")
            delta = int(delta)
            if delta == 0:
                raise ValueError("invalid_amount")
        except (ValueError, TypeError, OverflowError) as exc:
            return {"ok": False, "error": str(exc) or "invalid_amount"}
        with self._lock:
            return self._apply({account_key: delta}, transaction_id, reason)

    def transfer(self, sender: Any, recipient: Any, amount: Any, transaction_id: Any) -> dict[str, Any]:
        """Atomically debit sender and credit recipient; never creates debt."""
        try:
            source, target = self._account(sender), self._account(recipient)
            value = self._positive_int(amount)
            if source == target:
                raise ValueError("same_account")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            result = self._apply({source: -value, target: value}, transaction_id, "point_transfer")
            if not result.get("ok"):
                return result
            result["sender"] = source
            result["recipient"] = target
            result["amount"] = value
            return result

    def reserve_wager(
        self, account: Any, amount: Any, wager_id: Any = None, *, now: Any = None,
        timeout: Any = WAGER_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Debit and reserve a wager stake; default expiry is the source's 120s."""
        try:
            user = self._account(account)
            stake = self._positive_int(amount)
            wager_key = str(wager_id or uuid.uuid4().hex).strip()
            if not wager_key:
                raise ValueError("invalid_wager")
            created = self._timestamp(self.clock() if now is None else now)
            duration = self._timestamp(timeout)
            if duration <= 0:
                raise ValueError("invalid_timeout")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            existing = self.state["wagers"].get(wager_key)
            if existing:
                if existing.get("account") != user or existing.get("stake") != stake:
                    return {"ok": False, "error": "wager_conflict"}
                return {"ok": True, "already_applied": True, "wager": copy.deepcopy(existing)}
            txid = f"wager:{wager_key}:reserve"
            result = self._apply({user: -stake}, txid, "wager_reserve")
            if not result.get("ok"):
                return result
            wager = {"id": wager_key, "account": user, "stake": stake, "created_at": created,
                     "expires_at": created + duration, "status": "reserved",
                     "reserve_transaction_id": txid}
            self.state["wagers"][wager_key] = wager
            return {"ok": True, "already_applied": False, "wager": copy.deepcopy(wager),
                    "balances": result.get("balances", {})}

    def settle_wager(self, wager_id: Any, payout: Any, transaction_id: Any = None, *, now: Any = None) -> dict[str, Any]:
        """Settle a reserved wager by crediting its payout, if before expiry."""
        key = str(wager_id or "").strip()
        try:
            value = self._positive_int(payout, "invalid_payout") if payout != 0 else 0
            current = self._timestamp(self.clock() if now is None else now)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not key:
            return {"ok": False, "error": "invalid_wager"}
        with self._lock:
            wager = self.state["wagers"].get(key)
            if not wager:
                return {"ok": False, "error": "wager_not_found"}
            if wager["status"] == "settled":
                same = wager.get("payout") == value
                return ({"ok": True, "already_applied": True, "wager": copy.deepcopy(wager)}
                        if same else {"ok": False, "error": "wager_conflict"})
            if wager["status"] != "reserved":
                return {"ok": False, "error": "wager_closed"}
            if current >= float(wager["expires_at"]):
                return {"ok": False, "error": "wager_expired"}
            txid = str(transaction_id or f"wager:{key}:settle")
            if value:
                result = self._apply({wager["account"]: value}, txid, "wager_settlement")
                if not result.get("ok"):
                    return result
            else:
                result = self._record_noop(txid, {"wager": key, "payout": 0}, "wager_settlement")
                if not result.get("ok"):
                    return result
            wager.update(status="settled", payout=value, settled_at=current, settlement_transaction_id=txid)
            return {"ok": True, "already_applied": False, "wager": copy.deepcopy(wager),
                    "balances": result.get("balances", {})}

    def refund_wager(self, wager_id: Any, transaction_id: Any = None, *, now: Any = None, force: bool = False) -> dict[str, Any]:
        """Refund a reserved wager; normal refunds are only allowed at expiry."""
        key = str(wager_id or "").strip()
        try:
            current = self._timestamp(self.clock() if now is None else now)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not key:
            return {"ok": False, "error": "invalid_wager"}
        with self._lock:
            wager = self.state["wagers"].get(key)
            if not wager:
                return {"ok": False, "error": "wager_not_found"}
            if wager["status"] == "refunded":
                return {"ok": True, "already_applied": True, "wager": copy.deepcopy(wager)}
            if wager["status"] != "reserved":
                return {"ok": False, "error": "wager_closed"}
            if not force and current < float(wager["expires_at"]):
                return {"ok": False, "error": "wager_not_expired"}
            txid = str(transaction_id or f"wager:{key}:refund")
            result = self._apply({wager["account"]: wager["stake"]}, txid, "wager_expired_refund")
            if not result.get("ok"):
                return result
            wager.update(status="refunded", refunded_at=current, refund_transaction_id=txid)
            return {"ok": True, "already_applied": False, "wager": copy.deepcopy(wager),
                    "balances": result.get("balances", {})}

    def expire_wagers(self, *, now: Any = None) -> list[dict[str, Any]]:
        """Refund all wagers whose 120-second (or configured) deadline passed."""
        try:
            current = self._timestamp(self.clock() if now is None else now)
        except ValueError as exc:
            return [{"ok": False, "error": str(exc)}]
        with self._lock:
            due_ids = [key for key, wager in self.state["wagers"].items()
                       if wager.get("status") == "reserved" and current >= float(wager.get("expires_at", 0))]
            return [self.refund_wager(key, now=current) for key in due_ids]

    def invest(self, account: Any, asset: Any, amount: Any, investment_id: Any = None, *, now: Any = None,
               duration: Any = INVESTMENT_DURATION_SECONDS) -> dict[str, Any]:
        """Open a 60-second (configurable) investment between 10 and 30bn points."""
        try:
            user = self._account(account)
            stake = self._positive_int(amount)
            if stake < INVESTMENT_MIN_AMOUNT:
                raise ValueError("below_minimum")
            if stake > INVESTMENT_MAX_AMOUNT:
                raise ValueError("above_maximum")
            asset_value = str(asset or "").strip()
            if not asset_value:
                raise ValueError("invalid_asset")
            key = str(investment_id or uuid.uuid4().hex).strip()
            if not key:
                raise ValueError("invalid_investment")
            started = self._timestamp(self.clock() if now is None else now)
            term = self._timestamp(duration)
            if term <= 0:
                raise ValueError("invalid_duration")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            existing = self.state["investments"].get(key)
            if existing:
                if (existing.get("account"), existing.get("asset"), existing.get("amount")) != (user, asset_value, stake):
                    return {"ok": False, "error": "investment_conflict"}
                return {"ok": True, "already_applied": True, "investment": copy.deepcopy(existing)}
            txid = f"investment:{key}:debit"
            result = self._apply({user: -stake}, txid, "investment_open")
            if not result.get("ok"):
                return result
            investment = {"id": key, "account": user, "asset": asset_value, "amount": stake,
                          "started_at": started, "due_at": started + term, "status": "active",
                          "debit_transaction_id": txid}
            self.state["investments"][key] = investment
            return {"ok": True, "already_applied": False, "investment": copy.deepcopy(investment),
                    "balances": result.get("balances", {})}

    def settle_investment(self, investment_id: Any, outcome: str, change_percent: Any,
                          transaction_id: Any = None, *, now: Any = None) -> dict[str, Any]:
        """Settle at/after due time; profit/loss percentage follows 1–50% rules."""
        key = str(investment_id or "").strip()
        normalized_outcome = str(outcome or "").strip().casefold()
        try:
            current = self._timestamp(self.clock() if now is None else now)
            percent = self._positive_int(change_percent, "invalid_percent")
            if normalized_outcome not in {"profit", "loss"}:
                raise ValueError("invalid_outcome")
            if not 1 <= percent <= 50:
                raise ValueError("invalid_percent")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not key:
            return {"ok": False, "error": "invalid_investment"}
        with self._lock:
            investment = self.state["investments"].get(key)
            if not investment:
                return {"ok": False, "error": "investment_not_found"}
            if investment["status"] == "settled":
                if investment.get("outcome") != normalized_outcome or investment.get("change_percent") != percent:
                    return {"ok": False, "error": "investment_conflict"}
                return {"ok": True, "already_applied": True, "investment": copy.deepcopy(investment)}
            if investment["status"] not in {"active", "paying"}:
                return {"ok": False, "error": "investment_closed"}
            if current < float(investment["due_at"]):
                return {"ok": False, "error": "investment_not_due"}
            change = int(investment["amount"]) * percent // 100
            payout = int(investment["amount"]) + change if normalized_outcome == "profit" else max(0, int(investment["amount"]) - change)
            if investment["status"] == "paying" and (
                investment.get("outcome") != normalized_outcome or investment.get("change_percent") != percent
            ):
                return {"ok": False, "error": "investment_conflict"}
            txid = str(transaction_id or f"investment:{key}:payout")
            investment.update(status="paying", outcome=normalized_outcome, change_percent=percent,
                              change=change, payout=payout, settlement_transaction_id=txid)
            if payout:
                result = self._apply({investment["account"]: payout}, txid, "investment_settlement")
                if not result.get("ok"):
                    return result
            else:
                result = self._record_noop(txid, {"investment": key, "payout": 0}, "investment_settlement")
                if not result.get("ok"):
                    return result
            investment.update(status="settled", settled_at=current,
                              net_change=payout - int(investment["amount"]))
            return {"ok": True, "already_applied": False, "investment": copy.deepcopy(investment),
                    "balances": result.get("balances", {})}

    def _record_noop(self, transaction_id: Any, details: Mapping[str, Any], reason: str) -> dict[str, Any]:
        txid = str(transaction_id or "").strip()
        if not txid:
            return {"ok": False, "error": "invalid_transaction"}
        fingerprint = json.dumps({"noop": dict(details), "reason": reason}, sort_keys=True)
        prior = self.state["transactions"].get(txid)
        if prior is not None:
            if prior.get("fingerprint") != fingerprint:
                return {"ok": False, "error": "transaction_conflict"}
            response = copy.deepcopy(prior["response"])
            response["already_applied"] = True
            return response
        response = {"ok": True, "already_applied": False, "balances": {}}
        self.state["transactions"][txid] = {"fingerprint": fingerprint, "response": response}
        return response
