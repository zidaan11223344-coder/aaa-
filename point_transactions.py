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
