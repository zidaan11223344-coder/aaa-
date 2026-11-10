"""Persistent points shop for Talkin2 temporary protections and welcome perks."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

SHOP_PATH = Path(os.getenv("TALKIN_SHOP_FILE", Path(__file__).resolve().parent / "shop_data.json"))
SHOP_LOCK = threading.RLock()

ITEMS = {
    1: {"icon": "🛡️", "name": "حماية شخصية من القنابل لمدة ساعة", "cost": 10_000_000, "kind": "user_bomb", "seconds": 3600},
    2: {"icon": "🏰", "name": "درع الغرفة ضد القنابل لمدة ساعة", "cost": 25_000_000, "kind": "room_bomb", "seconds": 3600},
    3: {"icon": "🕵️", "name": "إخفاء النك عن البحث عن الشبيه لمدة ساعة", "cost": 8_000_000, "kind": "user_search", "seconds": 3600},
    4: {"icon": "🌫️", "name": "إخفاء قائمة متواجدي الغرفة لمدة ساعة", "cost": 15_000_000, "kind": "room_roster", "seconds": 3600},
    5: {"icon": "💌", "name": "فتح ميزة الترحيب المخصص النصي", "cost": 5_000_000, "kind": "custom_welcome", "seconds": 0},
    6: {"icon": "🖼️", "name": "فتح ميزة الترحيب المخصص بصورة", "cost": 12_000_000, "kind": "image_welcome", "seconds": 0},
}


def _load() -> dict:
    try:
        data = json.loads(SHOP_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save(data: dict) -> None:
    SHOP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHOP_PATH.with_suffix(SHOP_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SHOP_PATH)


def _user_keys(username: str, user_id: str = "", aliases=()) -> list[str]:
    keys = []
    uid = str(user_id or "").strip()
    if uid.isdigit():
        keys.append(f"id:{uid}")
    if isinstance(aliases, str):
        aliases = (aliases,)
    for value in (username, *(aliases or ())):
        key = str(value or "").strip().lstrip("@").casefold()
        if key and key not in keys:
            keys.append(key)
    return keys


def purchase(username: str, item_id: int, room: str = "", *, user_id: str = "", aliases=()) -> tuple[bool, str]:
    """Return a durable entitlement record; caller debits points transactionally."""
    item = ITEMS.get(int(item_id))
    if not item:
        return False, "❌ رقم العنصر غير موجود. أرسل متجر لعرض العناصر."
    now = time.time()
    user_keys = _user_keys(username, user_id, aliases)
    user_key = user_keys[0] if user_id and str(user_id).strip().isdigit() else (user_keys[0] if user_keys else "")
    room_key = str(room or "").strip().casefold()
    if not user_key:
        return False, "❌ تعذر تحديد حساب المشتري."
    bucket = "rooms" if item["kind"].startswith("room_") else "users"
    subject = room_key if bucket == "rooms" else user_key
    with SHOP_LOCK:
        data = _load()
        bucket_data = data.setdefault(bucket, {})
        current = bucket_data.get(subject)
        if current is None and bucket == "users":
            old_key = next((key for key in user_keys[1:] if key in bucket_data), "")
            if old_key:
                current = bucket_data.pop(old_key)
                bucket_data[subject] = current
        if not isinstance(current, dict):
            current = {}
            bucket_data[subject] = current
        kind = item["kind"]
        old = current.get(kind)
        if item["seconds"] and isinstance(old, dict) and float(old.get("expires_at", 0) or 0) > now:
            return False, "⏳ لديك هذا العنصر فعال بالفعل؛ لا يمكن تكديس المدة."
        if item["seconds"]:
            current[kind] = {
                "expires_at": now + int(item["seconds"]), "buyer": username,
                "user_id": str(user_id or ""), "room": room,
            }
        else:
            owned = set(current.get("unlocks", []))
            if kind in owned:
                return False, "✅ هذه الميزة مفتوحة لحسابك بالفعل."
            owned.add(kind)
            current["unlocks"] = sorted(owned)
        _save(data)
    return True, ""


def active(kind: str, *, username: str = "", room: str = "", user_id: str = "", aliases=()) -> bool:
    now = time.time()
    keys = _user_keys(username, user_id, aliases)
    key = keys[0] if user_id and str(user_id).strip().isdigit() else (keys[0] if keys else "")
    rkey = str(room or "").strip().casefold()
    with SHOP_LOCK:
        data = _load()
        if kind in {"room_bomb", "room_roster"}:
            entry = (data.get("rooms") or {}).get(rkey, {}).get(kind, {})
        else:
            users = data.get("users") or {}
            user = next((users[candidate] for candidate in keys if isinstance(users.get(candidate), dict)), {})
            entry = user.get(kind, {})
        if kind in {"custom_welcome", "image_welcome"}:
            return kind in (user.get("unlocks") or [])
        return isinstance(entry, dict) and float(entry.get("expires_at", 0) or 0) > now


def remove_welcome_unlocks(username: str, *, user_id: str = "", aliases=()) -> int:
    """Remove purchased text/image welcome entitlements without a refund."""
    keys = _user_keys(username, user_id, aliases)
    if not keys:
        return 0
    with SHOP_LOCK:
        data = _load()
        users = data.get("users") or {}
        subject = next((candidate for candidate in keys if isinstance(users.get(candidate), dict)), "")
        if not subject:
            return 0
        row = users[subject]
        owned = set(row.get("unlocks") or [])
        removed = len(owned.intersection({"custom_welcome", "image_welcome"}))
        if not removed:
            return 0
        row["unlocks"] = sorted(owned - {"custom_welcome", "image_welcome"})
        _save(data)
        return removed


def remaining(kind: str, *, username: str = "", room: str = "", user_id: str = "", aliases=()) -> int:
    keys = _user_keys(username, user_id, aliases)
    key = keys[0] if user_id and str(user_id).strip().isdigit() else (keys[0] if keys else "")
    rkey = str(room or "").strip().casefold()
    with SHOP_LOCK:
        data = _load()
        bucket = "rooms" if kind.startswith("room_") else "users"
        subject = rkey if bucket == "rooms" else key
        bucket_data = data.get(bucket) or {}
        if bucket == "users":
            user = next((bucket_data[candidate] for candidate in keys if isinstance(bucket_data.get(candidate), dict)), {})
            row = user.get(kind) or {}
        else:
            row = (bucket_data.get(subject) or {}).get(kind) or {}
        return max(0, int(float(row.get("expires_at", 0) or 0) - time.time()))


def render_catalog() -> str:
    # Keep the entire catalog below Talkin's 1008-byte protobuf ceiling.
    # The previous verbose Arabic names were ~1030 bytes after encoding and
    # were dropped by the transport before members could see the shop.
    lines = [
        "🛍️ متجر الأعضاء — الأسعار بالنقاط",
        "1 🛡️ قنابل شخصية/ساعة: 10,000,000",
        "2 🏰 قنابل الغرفة/ساعة: 25,000,000",
        "3 🕵️ إخفاء النك عن البحث/ساعة: 8,000,000",
        "4 🌫️ إخفاء قائمة الغرفة/ساعة: 15,000,000",
        "5 💌 ترحيب نصي مخصص: 5,000,000",
        "6 🖼️ ترحيب بصورة: 12,000,000",
        "للشراء: sby 1 إلى sby 6 أو شراء 1 إلى 6 أو buy 1 إلى 6",
        "ترحيب نصي: ترحيب@النص أو welcome@text",
        "ترحيب بصورة: ترحيب_صورة أو welcome_image؛ يظهر اسمك على الصورة",
        "الحمايات ساعة ولا تتراكم.",
    ]
    return "\n".join(lines)
