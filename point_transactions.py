"""Idempotent point-ledger operations shared by Giant game integrations."""
from __future__ import annotations


def apply_delta_once(points: dict, user_id: str, username: str, amount: int, transaction_id: str):
    """Mutate a points mapping and return (result, changed).

    The caller is responsible for locking and persisting the mapping. The
    transaction id is stored in the same user record as the balance, making a
    retry after process interruption safe.
    """
    if not isinstance(points, dict):
        return {"ok": False, "error": "invalid_ledger"}, False
    uid = str(user_id or "").strip()
    txid = str(transaction_id or "").strip()
    if not uid or not txid:
        return {"ok": False, "error": "invalid_transaction"}, False

    user = points.get(uid)
    if not isinstance(user, dict):
        user = {"username": str(username or ""), "points": 0, "cooldowns": {}, "married_to": None}
        points[uid] = user
    else:
        user["username"] = str(username or user.get("username") or "")

    applied = user.get("applied_point_transactions", [])
    if not isinstance(applied, list):
        applied = []
    balance = int(user.get("points", 0) or 0)
    if txid in applied:
        return {"ok": True, "already_applied": True, "balance": balance}, False

    delta = int(amount)
    if delta < 0 and balance < -delta:
        return {"ok": False, "error": "insufficient", "balance": balance}, False

    user["points"] = balance + delta
    applied.append(txid)
    user["applied_point_transactions"] = applied[-5000:]
    points[uid] = user
    return {"ok": True, "already_applied": False, "balance": balance + delta}, True


def apply_deltas_once(points: dict, deltas: dict, usernames: dict, transaction_id: str):
    """Atomically apply one transaction across several Giant UUID accounts.

    The caller must hold the ledger lock and persist the mutated ``points``
    mapping once. Every participating account stores the same transaction ID,
    so a retry is either recognized for all accounts or rejected as an
    inconsistent partial receipt; no transfer can mint points on retry.
    """
    if not isinstance(points, dict) or not isinstance(deltas, dict) or not isinstance(usernames, dict):
        return {"ok": False, "error": "invalid_ledger"}, False
    txid = str(transaction_id or "").strip()
    if not txid:
        return {"ok": False, "error": "invalid_transaction"}, False
    clean = {}
    for raw_uid, raw_delta in deltas.items():
        uid = str(raw_uid or "").strip()
        if not uid or isinstance(raw_delta, bool):
            return {"ok": False, "error": "invalid_transaction"}, False
        try:
            delta = int(raw_delta)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_amount"}, False
        if delta:
            clean[uid] = clean.get(uid, 0) + delta
    clean = {uid: delta for uid, delta in clean.items() if delta}
    if len(clean) < 2:
        return {"ok": False, "error": "invalid_transfer"}, False

    existing_flags = []
    balances = {}
    users = {}
    for uid, delta in clean.items():
        user = points.get(uid)
        if not isinstance(user, dict):
            user = {"username": str(usernames.get(uid) or ""), "points": 0,
                    "cooldowns": {}, "married_to": None}
        try:
            balance = int(user.get("points", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_balance"}, False
        applied = user.get("applied_point_transactions", [])
        if not isinstance(applied, list):
            applied = []
        existing_flags.append(txid in applied)
        balances[uid] = balance + delta
        users[uid] = (user, applied)
    if any(existing_flags):
        if all(existing_flags):
            return {"ok": True, "already_applied": True, "balances": {
                uid: int(users[uid][0].get("points", 0) or 0) for uid in clean
            }}, False
        return {"ok": False, "error": "partial_transaction_receipt"}, False

    for uid, delta in clean.items():
        balance = balances[uid] - delta
        if balance + delta < 0:
            return {"ok": False, "error": "insufficient", "balance": balance}, False

    for uid, delta in clean.items():
        user, applied = users[uid]
        user["username"] = str(usernames.get(uid) or user.get("username") or "")
        user["points"] = balances[uid]
        applied.append(txid)
        user["applied_point_transactions"] = applied[-5000:]
        points[uid] = user
    return {"ok": True, "already_applied": False, "balances": balances}, True
