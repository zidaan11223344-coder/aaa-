"""Persistent world-auction game engine for Talkin2.

The bot owns network delivery and authorization; this module owns the auction
state machine, winning-bid settlement, collectible inventory, and redemption
journal. Point changes are delegated through an idempotent callback.
"""
from __future__ import annotations

import copy
import random
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Callable

BID_INCREMENT = 10_000_000
MAX_PARTICIPANTS = 5
MIN_PARTICIPANTS_TO_START_EARLY = 3
BIDDING_DURATION_SECONDS = 60
TURN_TIMEOUT_SECONDS = 7
REDEMPTION_PROFIT_BASIS_POINTS = 75
REDEMPTION_LOSS_BASIS_POINTS = 125
REDEMPTION_BASIS_POINTS = 10_000
PLAYER_START_COOLDOWN_SECONDS = 3600
MAX_IMAGE_BYTES = 50_000

# These existing Talkin2 treasure images are already below MAX_IMAGE_BYTES.
# Each image represents a rare auction collectible; auction goods are separate
# from the ordinary mining-store catalog and carry their own cost basis.
AUCTION_ITEMS = (
    {"key": "jade_seal", "name_ar": "ختم اليشم الإمبراطوري", "name_en": "Imperial Jade Seal", "icon": "🐉", "image": "auction_imperial_jade.jpg", "starting_price": 100_000_000},
    {"key": "king_crown", "name_ar": "تاج الملك المرصع", "name_en": "Jeweled King's Crown", "icon": "👑", "image": "auction_king_crown.jpg", "starting_price": 250_000_000},
    {"key": "royal_scepter", "name_ar": "الصولجان الملكي الزمردي", "name_en": "Emerald Royal Scepter", "icon": "🪄", "image": "auction_royal_scepter.jpg", "starting_price": 400_000_000},
    {"key": "phoenix_amulet", "name_ar": "تميمة العنقاء الذهبية", "name_en": "Golden Phoenix Amulet", "icon": "🔥", "image": "auction_phoenix_amulet.jpg", "starting_price": 600_000_000},
    {"key": "royal_chalice", "name_ar": "الكأس الملكي العتيق", "name_en": "Ancient Royal Chalice", "icon": "🏆", "image": "auction_royal_chalice.jpg", "starting_price": 800_000_000},
    {"key": "amber_orb", "name_ar": "كرة العنبر الإمبراطورية", "name_en": "Imperial Sunfire Amber Orb", "icon": "🟠", "image": "auction_sunfire_amber_orb.jpg", "starting_price": 1_000_000_000},
)
_ITEM_BY_KEY = {item["key"]: item for item in AUCTION_ITEMS}


def normalize_command(value: str) -> str:
    """Normalize Arabic command spelling and whitespace for exact matching."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه").replace("ى", "ي")
    return " ".join(text.split())


_ROOM_COMMANDS = {
    "open": {"افتح مزاد", "افتح المزاد", "فتح المزاد", "افتح لعبه مزاد", "ابي افتح لعبه مزاد", "open auction"},
    "join": {"مزايده", "مزايده للمزاد", "انضم للمزاد", "join auction"},
    "start": {"ابدا المزاد", "ابدأ المزاد", "start auction"},
    "bid": {"زايد", "زايد للمزاد", "bid", "raise bid"},
    "pass": {"تمرير", "مرر", "تمرير الدور", "pass", "skip"},
    "close": {"اقفل المزاد", "اغلق المزاد", "اغلاق المزاد", "إغلاق المزاد", "انهاء المزاد", "close auction"},
    "cancel": {"الغاء المزاد", "الغ المزاد", "cancel auction"},
}
_TOGGLE_ON = {"شغل لعبه مزاد", "شغل لعبة مزاد", "تشغيل لعبة مزاد", "تشغيل لعبة المزاد", "auction on"}
_TOGGLE_OFF = {"اوقف لعبه مزاد", "اوقف لعبة مزاد", "وقف لعبة مزاد", "ايقاف لعبة مزاد", "ايقاف لعبه مزاد", "auction off"}


def parse_room_command(text: str) -> str | None:
    # Do not translate Arabic letters globally: commands such as «افتح المزاد»
    # and «مزايده» contain م/ب as ordinary letters. Translate suffixes only
    # after command aliases have been checked.
    normalized = normalize_command(str(text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    for action, aliases in _ROOM_COMMANDS.items():
        if normalized in {normalize_command(alias) for alias in aliases}:
            return action
    if any(normalized.startswith(normalize_command(alias) + " ") for alias in _ROOM_COMMANDS["bid"]):
        return "bid"
    numeric = normalized.translate(str.maketrans({"م": "m", "ب": "b"}))
    if re.fullmatch(r"[0-9]+(?:[mb])?", numeric, re.I):
        return "bid"
    return None


def parse_bid_amount(text: str) -> int | None:
    """Parse an explicit bid amount from Arabic or English bid commands."""
    digits = "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹"
    ascii_digits = "01234567890123456789"
    normalized = normalize_command(str(text or "").translate(str.maketrans(digits, ascii_digits))).translate(str.maketrans({"م": "m", "ب": "b"}))
    if re.fullmatch(r"[0-9]+(?:[mb])?", normalized, re.I):
        token = normalized
        suffix = token[-1:].casefold() if token[-1:].casefold() in {"m", "b"} else ""
        raw = token[:-1] if suffix else token
        return int(raw) * ({"": 1, "m": 1_000_000, "b": 1_000_000_000}[suffix])
    for alias in sorted(_ROOM_COMMANDS["bid"], key=len, reverse=True):
        prefix = normalize_command(alias)
        if not normalized.startswith(prefix + " "):
            continue
        raw_amount = normalized[len(prefix):].strip()
        compact = re.sub(r"[\s,_،٬]", "", raw_amount)
        suffix_terms = list(re.finditer(r"(\d+(?:\.\d+)?)([mb])", compact, flags=re.I))
        if suffix_terms and "".join(match.group(0) for match in suffix_terms) == compact:
            amount = 0
            for match in suffix_terms:
                value, suffix = match.groups()
                multiplier = 1_000_000 if suffix.casefold() == "m" else 1_000_000_000
                whole, dot, fraction = value.partition(".")
                amount += int(whole) * multiplier
                if dot and fraction:
                    fractional_value = int(fraction) * multiplier
                    divisor = 10 ** len(fraction)
                    if fractional_value % divisor:
                        return None
                    amount += fractional_value // divisor
            return amount if amount > 0 else None
        if compact.isdigit():
            try:
                amount = int(compact)
            except ValueError:
                return None
            return amount if amount > 0 else None
    return None


def parse_toggle_command(text: str) -> bool | None:
    normalized = normalize_command(text)
    if normalized in {normalize_command(alias) for alias in _TOGGLE_ON}:
        return True
    if normalized in {normalize_command(alias) for alias in _TOGGLE_OFF}:
        return False
    return None


class AuctionGame:
    """Thread-safe persistent auction engine with idempotent point settlement."""

    def __init__(
        self,
        path: str | Path,
        load_json: Callable | None = None,
        save_json: Callable | None = None,
        *,
        chooser: Callable[[tuple[dict, ...]], dict] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.path = Path(path)
        self.load_json = load_json or self._load_file
        self.save_json = save_json or self._save_file
        self.chooser = chooser or random.choice
        self.clock = clock
        self.lock = threading.RLock()

    @staticmethod
    def _load_file(path, default):
        import json
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else copy.deepcopy(default)
        except (OSError, ValueError, TypeError):
            return copy.deepcopy(default)

    @staticmethod
    def _save_file(path, data):
        import json
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _empty_state() -> dict:
        return {
            "version": 1,
            "enabled": False,
            "active": None,
            "inventory": {},
            "last_result": None,
            "player_start_at": {},
        }

    def _read(self) -> dict:
        raw = self.load_json(self.path, self._empty_state())
        if not isinstance(raw, dict):
            raw = self._empty_state()
        state = self._empty_state()
        state.update(raw)
        state["version"] = 1
        state["enabled"] = bool(state.get("enabled", False))
        state["active"] = state.get("active") if isinstance(state.get("active"), dict) else None
        state["inventory"] = state.get("inventory") if isinstance(state.get("inventory"), dict) else {}
        state["player_start_at"] = state.get("player_start_at") if isinstance(state.get("player_start_at"), dict) else {}
        return state

    def _write(self, state: dict) -> None:
        self.save_json(self.path, state)

    @staticmethod
    def _user_key(user_id: str, username: str) -> str:
        key = str(user_id or "").strip()
        if key:
            return key
        normalized = normalize_command(username).replace(" ", "")
        return "name:" + normalized if normalized else ""

    @staticmethod
    def _begin_bidding(active: dict, now: float) -> None:
        active["phase"] = "bidding"
        started_at = float(now)
        active["started_at"] = started_at
        active["deadline_at"] = started_at + BIDDING_DURATION_SECONDS
        active["passed_since_bid"] = []
        AuctionGame._set_turn(active, 0)
        active["turn_deadline_at"] = started_at + TURN_TIMEOUT_SECONDS

    @staticmethod
    def _set_turn(active: dict, index: int) -> dict | None:
        participants = active.get("participants") or []
        if not participants:
            active["turn_index"] = 0
            active["turn_user_id"] = ""
            active["turn_username"] = ""
            return None
        index = int(index) % len(participants)
        player = participants[index]
        active["turn_index"] = index
        active["turn_user_id"] = str(player.get("user_id") or "")
        active["turn_username"] = str(player.get("username") or "")
        return player

    @classmethod
    def _ensure_turn_state(cls, active: dict) -> bool:
        participants = active.get("participants") or []
        ids = [str(player.get("user_id") or "") for player in participants if isinstance(player, dict)]
        passed = active.get("passed_since_bid")
        changed = False
        if not isinstance(passed, list):
            active["passed_since_bid"] = []
            changed = True
        else:
            normalized_passes = [str(user_id) for user_id in passed if str(user_id) in ids]
            if normalized_passes != passed:
                active["passed_since_bid"] = normalized_passes
                changed = True
        try:
            index = int(active.get("turn_index", -1))
        except (TypeError, ValueError):
            index = -1
        turn_id = str(active.get("turn_user_id") or "")
        if not ids or index < 0 or index >= len(ids) or ids[index] != turn_id:
            cls._set_turn(active, 0)
            changed = True
        return changed

    @classmethod
    def _advance_turn(cls, active: dict, current_index: int, *, skip_passed: bool) -> dict | None:
        participants = active.get("participants") or []
        if not participants:
            return cls._set_turn(active, 0)
        passed = set(str(user_id) for user_id in active.get("passed_since_bid", [])) if skip_passed else set()
        for offset in range(1, len(participants) + 1):
            index = (int(current_index) + offset) % len(participants)
            user_id = str(participants[index].get("user_id") or "")
            if user_id not in passed:
                return cls._set_turn(active, index)
        return None

    def enabled(self) -> bool:
        with self.lock:
            return bool(self._read().get("enabled"))

    def set_enabled(self, enabled: bool) -> dict:
        with self.lock:
            state = self._read()
            state["enabled"] = bool(enabled)
            cancelled = False
            active = state.get("active")
            if not enabled and isinstance(active, dict) and active.get("phase") != "settling":
                state["last_result"] = {
                    "status": "cancelled", "auction_id": active.get("id"),
                    "reason": "feature_disabled", "at": int(self.clock()),
                }
                state["active"] = None
                cancelled = True
            self._write(state)
            return {"ok": True, "enabled": bool(enabled), "cancelled_active": cancelled}

    def active(self) -> dict | None:
        with self.lock:
            state = self._read()
            value = state.get("active")
            if not isinstance(value, dict):
                return None
            if value.get("phase") == "bidding" and self._ensure_turn_state(value):
                state["active"] = value
                self._write(state)
            return copy.deepcopy(value)

    def open_auction(self, opener_id: str, opener_name: str, room: str) -> dict:
        uid = self._user_key(opener_id, opener_name)
        name = str(opener_name or "").strip().lstrip("@")
        if not uid or not name:
            return {"ok": False, "error": "invalid_player"}
        with self.lock:
            state = self._read()
            if not state.get("enabled"):
                return {"ok": False, "error": "disabled"}
            if isinstance(state.get("active"), dict):
                return {"ok": False, "error": "already_open", "active": copy.deepcopy(state["active"])}
            item = copy.deepcopy(self.chooser(AUCTION_ITEMS))
            if item.get("key") not in _ITEM_BY_KEY:
                item = copy.deepcopy(AUCTION_ITEMS[0])
            active = {
                "id": uuid.uuid4().hex,
                "phase": "registration",
                "opened_at": int(self.clock()),
                "room": str(room or "").strip(),
                "opener_id": uid,
                "opener_name": name,
                "item": item,
                "participants": [],
                "current_bid": 0,
                "highest_bidder_id": "",
                "highest_bidder_name": "",
                "bid_count": 0,
                "bid_history": [],
            }
            state["active"] = active
            state["last_result"] = None
            self._write(state)
            return {"ok": True, "auction": copy.deepcopy(active)}

    def join(self, user_id: str, username: str, room: str = "") -> dict:
        uid = self._user_key(user_id, username)
        name = str(username or "").strip().lstrip("@")
        if not uid or not name:
            return {"ok": False, "error": "invalid_player"}
        with self.lock:
            state = self._read()
            active = state.get("active")
            if not isinstance(active, dict):
                return {"ok": False, "error": "no_auction"}
            if active.get("phase") != "registration":
                return {"ok": False, "error": "registration_closed", "auction": copy.deepcopy(active)}
            participants = active.setdefault("participants", [])
            if any(str(player.get("user_id") or "") == uid for player in participants if isinstance(player, dict)):
                return {"ok": True, "already_joined": True, "auction": copy.deepcopy(active)}
            if len(participants) >= MAX_PARTICIPANTS:
                return {"ok": False, "error": "full", "auction": copy.deepcopy(active)}
            participants.append({
                "user_id": uid, "username": name, "joined_at": int(self.clock()),
                "room": str(room or active.get("room") or "").strip(),
            })
            auto_started = len(participants) >= MAX_PARTICIPANTS
            if auto_started:
                self._begin_bidding(active, float(self.clock()))
            state["active"] = active
            self._write(state)
            return {
                "ok": True, "already_joined": False,
                "auto_started": auto_started,
                "participant_count": len(participants),
                "auction": copy.deepcopy(active),
            }

    def start(self, user_id: str, username: str, *, is_master: bool = False) -> dict:
        uid = self._user_key(user_id, username)
        with self.lock:
            state = self._read()
            active = state.get("active")
            if not isinstance(active, dict):
                return {"ok": False, "error": "no_auction"}
            if active.get("phase") != "registration":
                return {"ok": False, "error": "not_registering", "auction": copy.deepcopy(active)}
            if not is_master and uid != str(active.get("opener_id") or ""):
                return {"ok": False, "error": "not_opener"}
            count = len(active.get("participants") or [])
            if count < MIN_PARTICIPANTS_TO_START_EARLY:
                return {"ok": False, "error": "not_enough_players", "participant_count": count}
            now = float(self.clock())
            if not is_master:
                last_start = float(state["player_start_at"].get(uid) or 0)
                remaining = PLAYER_START_COOLDOWN_SECONDS - (now - last_start) if last_start > 0 else 0
                if remaining > 0:
                    return {
                        "ok": False,
                        "error": "start_cooldown",
                        "remaining_seconds": int(remaining + 0.999),
                        "auction": copy.deepcopy(active),
                    }
            self._begin_bidding(active, now)
            if not is_master:
                state["player_start_at"][uid] = now
            state["active"] = active
            self._write(state)
            return {"ok": True, "auction": copy.deepcopy(active)}

    def bid(self, user_id: str, username: str, amount: int, balance: int) -> dict:
        uid = self._user_key(user_id, username)
        name = str(username or "").strip().lstrip("@")
        if not uid or not name:
            return {"ok": False, "error": "invalid_player"}
        with self.lock:
            state = self._read()
            active = state.get("active")
            if not isinstance(active, dict):
                return {"ok": False, "error": "no_auction"}
            if active.get("phase") != "bidding":
                return {"ok": False, "error": "not_bidding"}
            deadline_at = float(active.get("deadline_at") or (float(active.get("started_at", 0) or 0) + BIDDING_DURATION_SECONDS))
            if float(self.clock()) >= deadline_at:
                return {"ok": False, "error": "expired", "deadline_at": deadline_at}
            participants = active.get("participants") or []
            if not any(str(player.get("user_id") or "") == uid for player in participants if isinstance(player, dict)):
                return {"ok": False, "error": "not_participant"}
            self._ensure_turn_state(active)
            if uid != str(active.get("turn_user_id") or ""):
                return {"ok": False, "error": "not_your_turn", "turn_username": active.get("turn_username", ""), "auction": copy.deepcopy(active)}
            try:
                turn_deadline_at = float(active.get("turn_deadline_at") or (float(active.get("started_at", 0) or 0) + TURN_TIMEOUT_SECONDS))
            except (TypeError, ValueError):
                turn_deadline_at = deadline_at
            if float(self.clock()) >= turn_deadline_at:
                return {"ok": False, "error": "turn_expired", "turn_deadline_at": turn_deadline_at}
            item = active.get("item") if isinstance(active.get("item"), dict) else {}
            try:
                starting_price = max(0, int(item.get("starting_price", 100_000_000) or 0))
            except (TypeError, ValueError):
                starting_price = 100_000_000
            current_bid = int(active.get("current_bid", 0) or 0)
            required_bid = starting_price if current_bid <= 0 else current_bid + BID_INCREMENT
            try:
                bid_amount = int(amount)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_amount", "required": required_bid}
            if bid_amount < required_bid:
                return {"ok": False, "error": "bid_too_low", "required": required_bid, "amount": bid_amount}
            try:
                balance = int(balance)
            except (TypeError, ValueError):
                balance = 0
            if balance < bid_amount:
                return {"ok": False, "error": "insufficient", "required": bid_amount, "balance": balance}
            active["current_bid"] = bid_amount
            active["highest_bidder_id"] = uid
            active["highest_bidder_name"] = name
            active["bid_count"] = int(active.get("bid_count", 0) or 0) + 1
            active["passed_since_bid"] = []
            history = active.setdefault("bid_history", [])
            history.append({"user_id": uid, "username": name, "amount": bid_amount, "at": int(self.clock())})
            del history[:-200]
            current_index = int(active.get("turn_index", 0) or 0)
            self._advance_turn(active, current_index, skip_passed=False)
            active["turn_deadline_at"] = float(self.clock()) + TURN_TIMEOUT_SECONDS
            state["active"] = active
            self._write(state)
            return {
                "ok": True, "amount": bid_amount, "bid_count": active["bid_count"],
                "next_turn_username": active.get("turn_username", ""), "auction": copy.deepcopy(active),
            }

    def pass_turn(self, user_id: str, username: str, *, apply_delta: Callable | None = None) -> dict:
        uid = self._user_key(user_id, username)
        with self.lock:
            state = self._read()
            active = state.get("active")
            if not isinstance(active, dict):
                return {"ok": False, "error": "no_auction"}
            if active.get("phase") != "bidding":
                return {"ok": False, "error": "not_bidding"}
            deadline_at = float(active.get("deadline_at") or (float(active.get("started_at", 0) or 0) + BIDDING_DURATION_SECONDS))
            if float(self.clock()) >= deadline_at:
                return {"ok": False, "error": "expired", "deadline_at": deadline_at}
            participants = active.get("participants") or []
            if not any(str(player.get("user_id") or "") == uid for player in participants if isinstance(player, dict)):
                return {"ok": False, "error": "not_participant"}
            self._ensure_turn_state(active)
            if uid != str(active.get("turn_user_id") or ""):
                return {"ok": False, "error": "not_your_turn", "turn_username": active.get("turn_username", ""), "auction": copy.deepcopy(active)}
            current_index = int(active.get("turn_index", 0) or 0)
            passed = set(str(user_id) for user_id in active.get("passed_since_bid", []))
            passed.add(uid)
            active["passed_since_bid"] = sorted(passed)
            opener_id = str(active.get("opener_id") or "")
            opener_name = str(active.get("opener_name") or "")
            if len(passed) >= len(participants):
                state["active"] = active
                self._write(state)
            else:
                self._advance_turn(active, current_index, skip_passed=True)
                active["turn_deadline_at"] = float(self.clock()) + TURN_TIMEOUT_SECONDS
                state["active"] = active
                self._write(state)
                return {
                    "ok": True, "status": "passed", "passed_username": str(username or "").strip().lstrip("@"),
                    "next_turn_username": active.get("turn_username", ""),
                    "passed_count": len(passed), "auction": copy.deepcopy(active),
                }
        result = self.close(opener_id, opener_name, apply_delta=apply_delta)
        result["auto_closed"] = True
        return result

    def close(self, user_id: str, username: str, *, is_master: bool = False, apply_delta: Callable | None = None) -> dict:
        uid = self._user_key(user_id, username)
        with self.lock:
            state = self._read()
            active = state.get("active")
            if not isinstance(active, dict):
                return {"ok": False, "error": "no_auction"}
            if not is_master and uid != str(active.get("opener_id") or ""):
                return {"ok": False, "error": "not_opener"}
            if active.get("phase") == "settling":
                pass
            elif active.get("phase") != "bidding":
                return {"ok": False, "error": "not_bidding"}
            elif not str(active.get("highest_bidder_id") or ""):
                result = {"status": "no_bids", "auction_id": active.get("id"), "item": active.get("item"), "at": int(self.clock())}
                state["last_result"] = result
                state["active"] = None
                self._write(state)
                return {"ok": True, "status": "no_bids", "result": result}
            else:
                active["phase"] = "settling"
                active["settlement"] = {
                    "auction_id": str(active.get("id") or ""),
                    "winner_id": str(active.get("highest_bidder_id") or ""),
                    "winner_name": str(active.get("highest_bidder_name") or ""),
                    "bid": int(active.get("current_bid", 0) or 0),
                    "item": copy.deepcopy(active.get("item") or {}),
                    "transaction_id": f"auction:{active.get('id')}:winning-bid",
                }
                state["active"] = active
                try:
                    self._write(state)
                except Exception:
                    return {"ok": False, "error": "persistence_failed", "pending": False}
        if apply_delta is None:
            return {"ok": False, "error": "settlement_callback_required", "pending": True}
        return self._finish_pending_settlement(apply_delta)

    def _finish_pending_settlement(self, apply_delta: Callable) -> dict:
        with self.lock:
            state = self._read()
            active = state.get("active")
            if not isinstance(active, dict) or active.get("phase") != "settling":
                return {"ok": False, "error": "no_pending_settlement"}
            settlement = copy.deepcopy(active.get("settlement") or {})
        result = apply_delta(
            settlement.get("winner_name"),
            -int(settlement.get("bid", 0) or 0),
            settlement.get("transaction_id"),
            "world auction winning bid",
        )
        if not isinstance(result, dict) or not result.get("ok"):
            error = result.get("error") if isinstance(result, dict) else "persistence_failed"
            if error == "insufficient":
                final = None
                with self.lock:
                    state = self._read()
                    active = state.get("active")
                    if isinstance(active, dict) and active.get("phase") == "settling" and str(active.get("id")) == str(settlement.get("auction_id")):
                        final = {
                            "status": "no_sale", "auction_id": settlement.get("auction_id"),
                            "winner": settlement.get("winner_name"), "bid": settlement.get("bid"),
                            "reason": "insufficient_at_close", "item": settlement.get("item"),
                            "at": int(self.clock()),
                        }
                        state["last_result"] = final
                        state["active"] = None
                        try:
                            self._write(state)
                        except Exception:
                            return {"ok": False, "error": "persistence_failed", "pending": True}
                if final is not None:
                    return {"ok": True, "status": "no_sale", "result": final}
                return {"ok": False, "error": "settlement_state_changed", "pending": True}
            return {"ok": False, "error": error or "persistence_failed", "pending": True}

        with self.lock:
            state = self._read()
            active = state.get("active")
            if not isinstance(active, dict) or active.get("phase") != "settling" or str(active.get("id")) != str(settlement.get("auction_id")):
                # A retry can see that the debit was already committed. Never
                # create a second item unless the matching round is settling.
                return {"ok": False, "error": "settlement_state_changed", "pending": True}
            winner_id = str(settlement.get("winner_id") or "")
            item = copy.deepcopy(settlement.get("item") or {})
            bid_amount = int(settlement.get("bid", 0) or 0)
            inventory = state.setdefault("inventory", {})
            player_items = inventory.setdefault(winner_id, [])
            auction_id = str(settlement.get("auction_id") or "")
            if not any(isinstance(row, dict) and str(row.get("id") or "") == auction_id for row in player_items):
                player_items.append({
                    "id": auction_id,
                    "item_key": str(item.get("key") or ""),
                    "item_name_ar": str(item.get("name_ar") or "جائزة المزاد"),
                    "item_name_en": str(item.get("name_en") or "Auction prize"),
                    "icon": str(item.get("icon") or "💎"),
                    "username": str(settlement.get("winner_name") or ""),
                    "winning_bid": bid_amount,
                    # The outcome is intentionally unknown until redemption.
                    "redemption_value": bid_amount,
                    "status": "available",
                    "won_at": int(self.clock()),
                })
            final = {
                "status": "sold", "auction_id": auction_id,
                "winner_id": winner_id, "winner": str(settlement.get("winner_name") or ""),
                "bid": bid_amount,
                "redemption_value": bid_amount,
                "item": item, "at": int(self.clock()),
            }
            state["last_result"] = final
            state["active"] = None
            try:
                self._write(state)
            except Exception:
                return {"ok": False, "error": "persistence_failed", "pending": True}
            return {"ok": True, "status": "sold", "result": copy.deepcopy(final)}

    def recover_pending_settlement(self, apply_delta: Callable) -> dict | None:
        active = self.active()
        if not isinstance(active, dict) or active.get("phase") != "settling":
            return None
        return self._finish_pending_settlement(apply_delta)

    def cancel(self, user_id: str, username: str, *, is_master: bool = False) -> dict:
        uid = self._user_key(user_id, username)
        with self.lock:
            state = self._read()
            active = state.get("active")
            if not isinstance(active, dict):
                return {"ok": False, "error": "no_auction"}
            if not is_master and uid != str(active.get("opener_id") or ""):
                return {"ok": False, "error": "not_opener"}
            if active.get("phase") == "settling":
                return {"ok": False, "error": "settlement_in_progress"}
            result = {"status": "cancelled", "auction_id": active.get("id"), "item": active.get("item"), "at": int(self.clock())}
            state["last_result"] = result
            state["active"] = None
            self._write(state)
            return {"ok": True, "status": "cancelled", "result": result}

    def inventory(self, user_id: str, username: str = "") -> list[dict]:
        uid = self._user_key(user_id, username)
        with self.lock:
            state = self._read()
            rows = state.get("inventory", {}).get(uid, [])
            rows = [copy.deepcopy(row) for row in rows if isinstance(row, dict) and row.get("status") in {"available", "redeeming"}]
            rows.sort(key=lambda row: (int(row.get("won_at", 0) or 0), str(row.get("id") or "")))
            return rows

    def redeem(self, user_id: str, username: str, selector: str, apply_delta: Callable) -> dict:
        uid = self._user_key(user_id, username)
        token = str(selector or "").strip()
        token = re.sub(r"^(?:auction|مزاد)\s*", "", token, flags=re.I).strip().lstrip("#")
        if not uid or not token:
            return {"ok": False, "error": "selector_required"}
        with self.lock:
            state = self._read()
            rows = state.setdefault("inventory", {}).setdefault(uid, [])
            redeemable = [row for row in rows if isinstance(row, dict) and row.get("status") in {"available", "redeeming"}]
            redeemable.sort(key=lambda row: (int(row.get("won_at", 0) or 0), str(row.get("id") or "")))
            match = None
            if token.isdigit():
                index = int(token) - 1
                if 0 <= index < len(redeemable):
                    match = redeemable[index]
            if match is None:
                folded = token.casefold()
                match = next((row for row in redeemable if str(row.get("id") or "").casefold().startswith(folded)), None)
            if match is None:
                folded = normalize_command(token)
                match = next((row for row in redeemable if folded in {normalize_command(row.get("item_name_ar")), normalize_command(row.get("item_name_en")), normalize_command(row.get("item_key"))}), None)
            if match is None:
                return {"ok": False, "error": "item_not_found"}
            if match.get("status") == "available":
                try:
                    cost_basis = max(0, int(match.get("winning_bid", match.get("redemption_value", 0)) or 0))
                except (TypeError, ValueError):
                    cost_basis = max(0, int(match.get("redemption_value", 0) or 0))
                authentic = random.random() < 0.5
                adjustment = REDEMPTION_PROFIT_BASIS_POINTS if authentic else -REDEMPTION_LOSS_BASIS_POINTS
                redemption_value = (cost_basis * (REDEMPTION_BASIS_POINTS + adjustment)) // REDEMPTION_BASIS_POINTS
                match["status"] = "redeeming"
                match["redemption_transaction_id"] = f"auction-redemption:{match.get('id')}"
                match["redemption_outcome"] = "authentic" if authentic else "counterfeit"
                match["redemption_value"] = redemption_value
                try:
                    self._write(state)
                except Exception:
                    return {"ok": False, "error": "persistence_failed", "pending": False}
            record = copy.deepcopy(match)
        result = apply_delta(
            str(username or record.get("username") or "").strip().lstrip("@"),
            int(record.get("redemption_value", 0) or 0),
            str(record.get("redemption_transaction_id") or f"auction-redemption:{record.get('id')}"),
            "world auction collectible redemption with random authenticity and +0.75%/-1.25% adjustment",
        )
        if not isinstance(result, dict) or not result.get("ok"):
            return {"ok": False, "error": (result.get("error") if isinstance(result, dict) else "persistence_failed"), "pending": True, "item": record}
        with self.lock:
            state = self._read()
            rows = state.setdefault("inventory", {}).setdefault(uid, [])
            current = next((row for row in rows if isinstance(row, dict) and str(row.get("id") or "") == str(record.get("id") or "")), None)
            if current and current.get("status") != "redeemed":
                current["status"] = "redeemed"
                current["redeemed_at"] = int(self.clock())
                current["username"] = str(username or current.get("username") or "").strip().lstrip("@")
                try:
                    self._write(state)
                except Exception:
                    return {"ok": False, "error": "persistence_failed", "pending": True, "item": record}
        return {
            "ok": True,
            "item": record,
            "points": int(record.get("redemption_value", 0) or 0),
            "outcome": str(record.get("redemption_outcome") or "unknown"),
            "authentic": record.get("redemption_outcome") == "authentic",
            "already_applied": bool(result.get("already_applied")),
        }
