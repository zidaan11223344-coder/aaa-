"""Adapters that connect Talkin2 game engines to Giant Chat's async APIs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote

from auction_game import AuctionGame, parse_bid_amount, parse_room_command, parse_toggle_command
from cricket_integration import CricketIntegration
from cricket_reward_outbox import CricketRewardOutbox
from rare_diamond_event import RareDiamondEvent
from ludo_snake_engine import apply_action as apply_board_action, current_player, new_ludo_game, new_snake_game
from treasure_rounds import GAME_ORDER, TIMED_TREASURE_GAMES, TreasureRoundEngine
from game_levels import leaderboard as game_leaderboard, new_state as new_game_stats_state, player_stats, record_game
from talkin2_shop_domain import ITEMS as SHOP_ITEMS, ShopDomain, ShopError, PurchaseRejected, DebitRejected, InvalidIdentity
from financial_domain import FinancialDomain


class GiantGameBridge:
    def __init__(
        self,
        state_dir: str | Path,
        assets_dir: str | Path,
        *,
        send_room_text: Callable,
        send_room_media: Callable,
        send_dm: Callable,
        broadcast_text: Callable,
        get_rooms: Callable[[], Iterable[str]],
        is_vip: Callable[[str, str], object],
        cricket_is_verified: Callable[[str], bool],
        lookup_profile: Callable[[str], object],
        balance: Callable[[str, str], int],
        apply_delta: Callable[[str, str, int, str, str], dict],
        public_base: Callable[[], str],
        owner_name: str,
        bot_name: str,
        log: Callable = print,
        apply_deltas: Callable | None = None,
        shop_state_path: str | Path | None = None,
    ):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir = Path(assets_dir)
        self.send_room_text = send_room_text
        self.send_room_media = send_room_media
        self.send_dm = send_dm
        self.broadcast_text = broadcast_text
        self.get_rooms = get_rooms
        self.is_vip = is_vip
        self.cricket_is_verified = cricket_is_verified
        self.lookup_profile = lookup_profile
        self.balance = balance
        self.apply_delta = apply_delta
        self.apply_deltas = apply_deltas
        self._shop_usernames: dict[str, str] = {}
        self.shop_state_path = Path(shop_state_path) if shop_state_path else self.state_dir / "talkin_shop.json"
        self.shop = ShopDomain(
            state=self._read_state(self.shop_state_path, None),
            debit=self._shop_debit,
            refund=self._shop_refund,
            save=self._save_shop_candidate,
        )
        self.financial_state_path = self.state_dir / "talkin_financial.json"
        self._financial_usernames: dict[str, str] = {}
        financial_state = self._read_state(self.financial_state_path, None) or {}
        financial_state.setdefault("opening_intents", {})
        financial_state.setdefault("wager_opening_intents", {})
        financial_state.setdefault("wager_challenges", {})
        financial_state.setdefault("wager_messages", {})
        financial_state.setdefault("wager_cooldowns", {})
        financial_state.setdefault("financial_messages", {})
        self.financial = FinancialDomain(state=financial_state, balance_callback=self._financial_apply)
        self._financial_pending: dict[str, dict] = {}
        self.public_base = public_base
        self.owner_name = str(owner_name or "").strip().lstrip("@").casefold()
        self.bot_name = str(bot_name or "Giant Chat")
        self.log = log
        self.auction = AuctionGame(self.state_dir / "auction_state.json")
        self.cricket_rewards = CricketRewardOutbox(self.state_dir / "cricket_rewards.json")
        self.rare_diamond = RareDiamondEvent(self.state_dir / "rare_diamond_event.json")
        self.board_state_path = self.state_dir / "talkin_board_games.json"
        self.board_games = self._read_state(self.board_state_path, {"version": 1, "games": {}})
        self.game_stats_path = self.state_dir / "talkin_game_stats.json"
        self.game_stats_store = self._read_state(self.game_stats_path, {"version": 1, "stats": new_game_stats_state(), "recorded_rounds": []})
        self.game_stats_store.setdefault("stats", new_game_stats_state())
        self.game_stats_store.setdefault("recorded_rounds", [])
        timed_path = self.state_dir / "talkin_timed_events.json"
        self.timed_events = TreasureRoundEngine(state=self._read_state(timed_path, None))
        self.timed_state_path = timed_path
        self._private_uids: dict[str, str] = {}
        self._reward_lock = asyncio.Lock()
        self.cricket = CricketIntegration(
            self.state_dir,
            is_master=lambda username: str(username or "").strip().lstrip("@").casefold() == self.owner_name,
            is_configured_master=lambda username: str(username or "").strip().lstrip("@").casefold() == self.owner_name,
            is_verified=self.cricket_is_verified,
            send_room_text=self._cricket_send_room_text,
            send_room_media=self._cricket_send_room_media,
            send_all_rooms_text=self._cricket_broadcast,
            send_private_text=self._cricket_send_private,
            public_base=self.public_base,
            reward=self._queue_cricket_reward,
            bot_name=self.bot_name,
            log=self.log,
        )

    @staticmethod
    def _read_state(path: Path, default):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else default
        except (OSError, ValueError, TypeError):
            return default

    @staticmethod
    def _write_state(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _save_board_games(self) -> None:
        self._write_state(self.board_state_path, self.board_games)

    def _save_timed_events(self) -> None:
        self._write_state(self.timed_state_path, self.timed_events.export_state())

    def _save_game_stats(self) -> None:
        self._write_state(self.game_stats_path, self.game_stats_store)

    def _record_board_results(self) -> int:
        recorded = 0
        for room, game in self.board_games.get("games", {}).items():
            if not isinstance(game, dict) or game.get("status") != "finished" or game.get("stats_recorded"):
                continue
            game_key = f"board_{game.get('game_type', 'game')}"
            names = game.get("player_names") or {}
            for player_id in game.get("players", []):
                if player_id == "🤖 البوت":
                    continue
                username = str(names.get(player_id) or "لاعب")
                winner_delta = (100_000 if game.get("game_type") == "snake" else 50_000) if player_id == game.get("winner") else 0
                record_game(self.game_stats_store["stats"], str(player_id), username, game_key, points_delta=winner_delta)
                recorded += 1
            game["stats_recorded"] = True
            self._save_board_games()
        if recorded:
            self._save_game_stats()
        return recorded

    def _record_timed_results(self, result: dict) -> None:
        round_id = str(result.get("id") or "")
        recorded_rounds = self.game_stats_store.setdefault("recorded_rounds", [])
        if not round_id or round_id in recorded_rounds or result.get("status") != "resolved":
            return
        intents = result.get("settlement_intents", {})
        for player_key, player in result.get("players", {}).items():
            user_id = str(player.get("user_id") or "")
            if not user_id:
                continue
            delta = 0
            if player.get("name") == result.get("winner"):
                delta = int(result.get("prize", 0) or 0)
            else:
                candidates = [item for item in intents.values() if item.get("player_key") == player_key and item.get("reason", "").endswith("participation")]
                if candidates and candidates[0].get("status") == "applied" and not (candidates[0].get("receipt") or {}).get("skipped"):
                    delta = int(candidates[0].get("delta", 0) or 0)
            record_game(self.game_stats_store["stats"], user_id, str(player.get("name") or "لاعب"), str(result.get("game_key") or "timed_event"), points_delta=delta)
        recorded_rounds.append(round_id)
        self.game_stats_store["recorded_rounds"] = recorded_rounds[-1000:]
        self._save_game_stats()

    def _game_stats_reply(self, user_id: str, username: str, *, leaderboard: bool = False) -> str:
        state = self.game_stats_store.get("stats", new_game_stats_state())
        if leaderboard:
            rows = game_leaderboard(state, 10)
            if not rows:
                return "📊 لا توجد نتائج مسجلة لألعاب Talkin2 بعد."
            lines = ["🏆 مستويات ألعاب Talkin2:"]
            for index, row in enumerate(rows, 1):
                lines.append(f"{index}. @{row.get('username') or 'لاعب'} — {row.get('symbol', '')} {row.get('title', '')} ({row.get('plays', 0)} لعبة)")
            return "\n".join(lines)
        stats = player_stats(state, str(user_id))
        return (f"🎮 إحصاءات ألعابك: @{username}\n"
                f"المستوى: {stats['symbol']} {stats['title']} — {stats['category']}\n"
                f"الألعاب المسجلة: {stats['plays']} | صافي نقاط الألعاب: {stats['net']:,}")

    async def _handle_transfer(self, room: str, uid: str, username: str, text: str, message_id: str = "") -> tuple[bool, str | None]:
        match = re.fullmatch(r"sb@([^@\s]+)@(\d+)", str(text or "").strip(), re.I)
        if not match:
            return False, None
        if not str(message_id or "").strip():
            return True, "❌ تعذر التحقق من معرّف الرسالة؛ لم يتم التحويل حفاظًا على نقاط البوت."
        if not self.apply_deltas:
            return True, "❌ التحويل غير متاح: لم يُهيأ دفتر المعاملات الذرية."
        if not await self.is_vip(uid, username):
            return True, "🔒 تحويل النقاط يتطلب توثيق VIP."
        target_name = match.group(1).strip().lstrip("@")
        amount = int(match.group(2))
        if amount <= 0:
            return True, "❌ مبلغ التحويل يجب أن يكون أكبر من صفر."
        try:
            profile, error = await self.lookup_profile(target_name)
        except Exception as exc:
            self.log(f"[points-transfer] profile lookup failed: {exc!r}")
            return True, "❌ تعذر العثور على حساب المستلم في Giant Chat."
        if error or not isinstance(profile, dict) or not profile.get("id"):
            return True, "❌ لم أجد المستلم؛ استخدم اسم المستخدم المسجل في Giant Chat."
        target_id = str(profile["id"])
        if target_id == uid:
            return True, "❌ لا يمكن تحويل النقاط إلى حسابك نفسه."
        recipient_name = str(profile.get("username") or target_name).strip().lstrip("@")
        identity = f"supabase-message:{str(message_id).strip()}"
        txid = "talkin-transfer:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        try:
            result = self.apply_deltas(
                {uid: -amount, target_id: amount},
                {uid: username, target_id: recipient_name}, txid, "Talkin2 point transfer",
            )
        except Exception as exc:
            self.log(f"[points-transfer] atomic ledger failed: {exc!r}")
            return True, "❌ فشل حفظ التحويل بأمان؛ لم أتمكن من تغيير الأرصدة."
        if not isinstance(result, dict) or not result.get("ok"):
            error = (result or {}).get("error") if isinstance(result, dict) else "unknown"
            if error == "insufficient":
                return True, "❌ رصيدك غير كافٍ للتحويل؛ لم يتغير أي رصيد."
            return True, "❌ لم يكتمل التحويل؛ لم يتغير أي رصيد."
        balances = result.get("balances") or {}
        suffix = " (تم التعرف على إعادة إرسال التحويل، ولم يُخصم مرتين)" if result.get("already_applied") else ""
        return True, (f"✅ تم تحويل {amount:,} نقطة بوت إلى @{recipient_name}. "
                      f"رصيد نقاط البوت الآن {int(balances.get(uid, 0)):,}.{suffix}")

    def _save_shop_candidate(self, state: dict) -> bool:
        self._write_state(self.shop_state_path, state)
        return True

    def _shop_debit(self, uid: str, amount: int) -> bool:
        username = self._shop_usernames.get(str(uid), "مستخدم")
        current = int(self.balance(uid, username) or 0)
        if current < int(amount):
            return False
        result = self.apply_delta(
            uid, username, -int(amount), f"talkin-shop-debit:{uuid.uuid4().hex}", "Talkin2 shop purchase",
        )
        return isinstance(result, dict) and bool(result.get("ok"))

    def _shop_refund(self, uid: str, amount: int) -> bool:
        result = self.apply_delta(
            uid, self._shop_usernames.get(str(uid), "مستخدم"), int(amount),
            f"talkin-shop-refund:{uuid.uuid4().hex}", "Talkin2 shop purchase rollback",
        )
        return isinstance(result, dict) and bool(result.get("ok"))

    async def _handle_shop(self, room: str, uid: str, username: str, text: str) -> tuple[bool, str | None]:
        value = " ".join(str(text or "").strip().casefold().split())
        if value in {"shop", "store", "متجر", "store shop"}:
            lines = ["🛍️ متجر Talkin2 — الأسعار من نقاط البوت المستقلة عن نقاط Giant Chat:"]
            for item_id, item in SHOP_ITEMS.items():
                lines.append(f"{item_id}. {item['name']} — {int(item['cost']):,} نقطة بوت")
            lines.append("للشراء: sby 1–6 أو شراء 1–6؛ الحمايات مؤقتة لمدة ساعة.")
            return True, "\n".join(lines)
        match = re.fullmatch(r"(?:sby|buy|purchase|شراء|اشتر|اشتري)\s*@?\s*([1-6])", value, re.I)
        if not match:
            return False, None
        if not await self.is_vip(uid, username):
            return True, "🔒 متجر الأعضاء متاح لحسابات VIP الموثقة في Giant Chat."
        item_id = int(match.group(1))
        item = SHOP_ITEMS[item_id]
        is_room_item = str(item["kind"]).startswith("room_")
        if is_room_item and not room:
            return True, "⚠️ اشترِ حماية الغرفة من داخل الغرفة المطلوبة."
        try:
            self._shop_usernames[str(uid)] = username
            self.shop.purchase(uid, item_id, room_id=room if is_room_item else None)
        except DebitRejected:
            return True, f"❌ رصيدك غير كافٍ أو تعذر الخصم؛ سعر العنصر {int(item['cost']):,} نقطة."
        except (PurchaseRejected, ShopError, InvalidIdentity) as exc:
            self.log(f"[shop] purchase rejected: {exc!r}")
            return True, "❌ لم يكتمل الشراء؛ لم يتم تثبيت الميزة."
        suffix = "\n⏱️ تنتهي الحماية بعد ساعة." if int(item.get("seconds", 0)) else ""
        return True, f"✅ تم شراء {item['name']} مقابل {int(item['cost']):,} نقطة بوت.{suffix}"

    def _financial_apply(self, deltas: dict[str, int], transaction_id: str, reason: str) -> dict:
        names = {uid: self._financial_usernames.get(str(uid), "مستخدم") for uid in deltas}
        if len(deltas) == 1:
            uid, delta = next(iter(deltas.items()))
            result = self.apply_delta(uid, names[uid], delta, transaction_id, reason)
            if isinstance(result, dict) and result.get("ok"):
                return {**result, "balances": {uid: int(result.get("balance", 0))}}
            return result if isinstance(result, dict) else {"ok": False, "error": "persistence_failed"}
        return self.apply_deltas(deltas, names, transaction_id, reason)

    def _save_financial(self) -> None:
        self._write_state(self.financial_state_path, self.financial.state)

    @staticmethod
    def _stock_assets() -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (
            ("ذهب", ("ذهب", "gold")), ("فضة", ("فضة", "silver")),
            ("نفط", ("نفط", "oil")), ("أرض", ("ارض", "أرض", "land")),
        )

    async def _open_stock_investment(self, room: str, uid: str, username: str, asset: str, amount: int,
                                     message_id: str = "") -> str:
        if amount < 10:
            return "❌ الحد الأدنى للاستثمار 10 نقاط بوت."
        if amount > 30_000_000_000:
            return "❌ الحد الأعلى للاستثمار 30,000,000,000 نقطة بوت."
        investment_id = str(uuid.uuid4())
        self._financial_usernames[uid] = username
        intent = {"id": investment_id, "account": uid, "username": username,
                  "asset": asset, "amount": amount, "room": room, "message_id": message_id,
                  "created_at": time.time()}
        self.financial.state.setdefault("opening_intents", {})[investment_id] = intent
        self._save_financial()
        result = self.financial.invest(uid, asset, amount, investment_id=investment_id)
        if not result.get("ok"):
            if result.get("error") in {"insufficient", "below_minimum", "above_maximum"}:
                self.financial.state["opening_intents"].pop(investment_id, None)
                self._save_financial()
                return "❌ رصيد نقاط البوت غير كافٍ أو مبلغ الاستثمار غير صالح؛ لم يبدأ الاستثمار."
            return "❌ تعذر حفظ الاستثمار بأمان؛ ستتم محاولة استعادته تلقائيًا."
        row = self.financial.state["investments"][investment_id]
        row.update(room=room, username=username, announced=False)
        self.financial.state["opening_intents"].pop(investment_id, None)
        reply = f"📈 بدأ استثمار {asset} بمبلغ {amount:,} نقطة بوت. ستظهر النتيجة بعد دقيقة."
        if message_id:
            self.financial.state.setdefault("financial_messages", {})[message_id] = reply
        self._save_financial()
        return reply

    async def _finish_wager(self, game_key: str) -> str:
        challenges = self.financial.state.setdefault("wager_challenges", {})
        challenge = challenges.get(game_key)
        if not isinstance(challenge, dict) or not challenge.get("second"):
            return "⌛ الرهان ما زال ينتظر منافسًا."
        winner_id = str(challenge.get("winner_wager_id") or "")
        loser_id = str(challenge.get("loser_wager_id") or "")
        stake = int(challenge.get("stake", 0) or 0)
        if not winner_id or not loser_id or stake <= 0:
            return "❌ تعذر التحقق من سجل الرهان؛ لا يمكن تسويته الآن."
        winner = self.financial.state["wagers"].get(winner_id, {})
        loser = self.financial.state["wagers"].get(loser_id, {})
        winner_uid = str(winner.get("account") or "")
        self._financial_usernames[winner_uid] = str(winner.get("username") or "مستخدم")
        credited = self.financial.settle_wager(winner_id, stake * 2)
        if not credited.get("ok"):
            self._save_financial()
            return "⌛ تعذرت تسوية الرهان مؤقتًا؛ ستتم إعادة المحاولة بأمان."
        lost = self.financial.settle_wager(loser_id, 0)
        if not lost.get("ok"):
            self._save_financial()
            return "⌛ اكتمل جزء من التسوية؛ ستتم إعادة المحاولة دون تكرار الإيداع."
        self._save_financial()
        winner_name = str(winner.get("username") or "لاعب")
        loser_name = str(loser.get("username") or "لاعب")
        message = (f"🏆 انتهى رهان {challenge.get('label', 'رهان')}\n"
                   f"👑 الفائز: @{winner_name} — ربح صافي {stake:,} نقطة بوت.\n"
                   f"❌ الخاسر: @{loser_name} — خسر {stake:,} نقطة بوت.")
        for target_room in dict.fromkeys(str(part.get("room") or "") for part in (challenge.get("first"), challenge.get("second"))):
            if target_room:
                await self.send_room_text(target_room, message)
        challenges.pop(game_key, None)
        self._save_financial()
        return message

    async def _queue_wager(self, room: str, uid: str, username: str, game_key: str, label: str,
                           amount: int, message_id: str) -> str:
        if not message_id:
            return "❌ تعذر التحقق من معرّف الرسالة؛ لم يُحجز أي رهان."
        seen = self.financial.state.setdefault("wager_messages", {})
        if message_id in seen:
            return "✅ تمت معالجة هذه الرسالة مسبقًا؛ لم يُخصم الرهان مرة أخرى."
        cooldowns = self.financial.state.setdefault("wager_cooldowns", {})
        cooldown_key = f"{game_key}:{uid}"
        last_played = float(cooldowns.get(cooldown_key, 0) or 0)
        if time.time() - last_played < 120:
            remaining = max(1, int(120 - (time.time() - last_played)))
            return f"⏳ انتظر {remaining} ثانية قبل لعب هذا الرهان مجددًا."
        challenges = self.financial.state.setdefault("wager_challenges", {})
        active = challenges.get(game_key)
        if isinstance(active, dict) and str((active.get("first") or {}).get("account")) == uid:
            return "⏳ رهانك ينتظر منافسًا بالفعل؛ مهلة الانتظار دقيقتان."
        if isinstance(active, dict):
            first_wager_id = str((active.get("first") or {}).get("wager_id") or "")
            first_wager = self.financial.state.get("wagers", {}).get(first_wager_id, {})
            if first_wager.get("status") == "reserved" and time.time() >= float(first_wager.get("expires_at", 0)):
                refunded = self.financial.refund_wager(first_wager_id, force=True)
                if refunded.get("ok"):
                    challenges.pop(game_key, None)
                    self._save_financial()
                return "⌛ انتهت مهلة هذا الرهان وأُعيدت نقاط البوت؛ ابدأ تحديًا جديدًا."
        stake = int(active.get("stake", 0)) if isinstance(active, dict) else int(amount)
        if stake <= 0:
            return "❌ مبلغ الرهان غير صالح."
        wager_id = "talkin-wager:" + hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()
        intents = self.financial.state.setdefault("wager_opening_intents", {})
        role = "second" if isinstance(active, dict) else "first"
        intent = {"id": wager_id, "account": uid, "username": username, "room": room,
                  "stake": stake, "game_key": game_key, "label": label, "role": role,
                  "message_id": message_id, "created_at": time.time()}
        self._financial_usernames[uid] = username
        intents[wager_id] = intent
        self._save_financial()
        reserved = self.financial.reserve_wager(uid, stake, wager_id=wager_id)
        if not reserved.get("ok"):
            intents.pop(wager_id, None)
            self._save_financial()
            if reserved.get("error") == "insufficient":
                return "❌ رصيد نقاط البوت غير كافٍ لحجز مبلغ الرهان."
            return "❌ تعذر حجز الرهان؛ لم يكتمل الخصم."
        intents.pop(wager_id, None)
        cooldowns[cooldown_key] = time.time()
        participant = {"wager_id": wager_id, "account": uid, "username": username, "room": room}
        if role == "first":
            challenges[game_key] = {"game_key": game_key, "label": label, "stake": stake,
                                    "first": participant, "created_at": time.time()}
            seen[message_id] = "opened"
            self._save_financial()
            rooms = list(dict.fromkeys([room, *[str(x) for x in self.get_rooms() if str(x).strip()]]))
            for target_room in rooms:
                await self.send_room_text(target_room, f"🎲 بدأ {label} من نقاط البوت بمبلغ {stake:,}. للمنافسة اكتب {label}@المبلغ خلال دقيقتين.")
            return f"🎲 تم حجز {stake:,} نقطة بوت؛ رهانك ينتظر منافسًا لمدة دقيقتين."
        active["second"] = participant
        winner = secrets.choice((active["first"], participant))
        loser = participant if winner is active["first"] else active["first"]
        active["winner_wager_id"] = winner["wager_id"]
        active["loser_wager_id"] = loser["wager_id"]
        seen[message_id] = "matched"
        self._save_financial()
        while len(seen) > 5000:
            seen.pop(next(iter(seen)))
        return await self._finish_wager(game_key)

    async def _process_wager_command(self, room: str, uid: str, username: str, text: str,
                                    message_id: str) -> tuple[bool, str | None]:
        match = re.fullmatch(r"(رهان|مراهنه|مضاربه|استثمار|حظي|حظ\s+يا\s+نصيب)\s*[@\s]+([0-9][0-9,]*(?:\.[0-9]+)?[kmb]?)", self._normalize_event_command(text), re.I)
        if not match:
            return False, None
        if not await self.is_vip(uid, username):
            return True, "🔒 رهانات نقاط البوت متاحة لحسابات VIP الموثقة في Giant Chat."
        raw_amount = match.group(2).replace(",", "")
        suffix = raw_amount[-1:].casefold()
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
        try:
            amount = int(float(raw_amount[:-1] if suffix in {"k", "m", "b"} else raw_amount) * multiplier)
        except (ValueError, OverflowError):
            return True, "❌ مبلغ الرهان غير صالح."
        if amount <= 0:
            return True, "❌ مبلغ الرهان يجب أن يكون أكبر من صفر."
        label = match.group(1)
        key = self._normalize_event_command(label)
        return True, await self._queue_wager(room, uid, username, key, label, amount, str(message_id or ""))

    async def _handle_financial_command(self, room: str, uid: str, username: str, text: str, message_id: str = "") -> tuple[bool, str | None]:
        raw = " ".join(str(text or "").strip().split())
        normalized = self._normalize_event_command(raw)
        prior_reply = self.financial.state.setdefault("financial_messages", {}).get(str(message_id or ""))
        if message_id and prior_reply:
            return True, f"✅ تمت معالجة هذه الرسالة مسبقًا: {prior_reply}"
        waiting = self._financial_pending.get(uid)
        if waiting and time.time() - float(waiting.get("created_at", 0)) >= 120:
            self._financial_pending.pop(uid, None)
            waiting = None
        if normalized in {"بورصة", "بورصه", "البورصة", "البورصه", "stock", "market"}:
            if not await self.is_vip(uid, username):
                return True, "🔒 الاستثمار في البورصة متاح لحسابات VIP الموثقة في Giant Chat."
            self._financial_pending[uid] = {"room": room, "stage": "asset", "created_at": time.time()}
            return True, "📈 البورصة (نقاط البوت): اختر الأصل بإرسال 1 ذهب، 2 فضة، 3 نفط، أو 4 أرض."
        wager_handled, wager_reply = await self._process_wager_command(room, uid, username, raw, message_id)
        if wager_handled:
            return wager_handled, wager_reply
        if waiting and waiting.get("stage") == "asset":
            digit = raw.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
            assets = self._stock_assets()
            asset = assets[int(digit) - 1][0] if digit in {"1", "2", "3", "4"} else None
            if asset is None:
                return True, "❌ اختر 1 ذهب أو 2 فضة أو 3 نفط أو 4 أرض."
            waiting.update(stage="amount", asset=asset, room=room, created_at=time.time())
            return True, f"أرسل مبلغ استثمار {asset} بنقاط البوت (من 10 إلى 30 مليار)."
        if waiting and waiting.get("stage") == "amount":
            amount_text = raw.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).replace(",", "")
            if not re.fullmatch(r"\d+", amount_text):
                return True, "❌ أرسل المبلغ كعدد صحيح من نقاط البوت."
            self._financial_pending.pop(uid, None)
            if not await self.is_vip(uid, username):
                return True, "🔒 الاستثمار يتطلب حساب VIP موثقًا."
            if not message_id:
                return True, "❌ تعذر التحقق من معرّف الرسالة؛ لم يبدأ الاستثمار حفاظًا على نقاط البوت."
            return True, await self._open_stock_investment(room, uid, username, waiting["asset"], int(amount_text), message_id)
        return False, None

    async def tick_financial(self) -> int:
        completed = 0
        for game_key, challenge in list(self.financial.state.get("wager_challenges", {}).items()):
            if challenge.get("second") and challenge.get("winner_wager_id"):
                try:
                    await self._finish_wager(game_key)
                except Exception as exc:
                    self.log(f"[wager] settlement/announcement retry pending: {exc!r}")
        expired = self.financial.expire_wagers()
        if expired:
            for game_key, challenge in list(self.financial.state.get("wager_challenges", {}).items()):
                first_id = str((challenge.get("first") or {}).get("wager_id") or "")
                first = self.financial.state.get("wagers", {}).get(first_id, {})
                if first.get("status") == "refunded":
                    room = str((challenge.get("first") or {}).get("room") or "")
                    if room:
                        await self.send_room_text(room, f"⌛ انتهت مهلة {challenge.get('label', 'الرهان')}؛ أُعيدت نقاط البوت المحجوزة.")
                    self.financial.state["wager_challenges"].pop(game_key, None)
            self._save_financial()
            completed += len(expired)
        for investment_id, row in list(self.financial.state.get("investments", {}).items()):
            if not isinstance(row, dict) or row.get("status") not in {"active", "paying"}:
                continue
            if time.time() < float(row.get("due_at", 0)):
                continue
            digest = hashlib.sha256(str(investment_id).encode("utf-8")).digest()
            outcome = "profit" if digest[0] % 2 == 0 else "loss"
            percent = digest[1] % 50 + 1
            self._financial_usernames[str(row.get("account") or "")] = str(row.get("username") or "مستخدم")
            result = self.financial.settle_investment(investment_id, outcome, percent)
            if not result.get("ok"):
                self.log(f"[stock-market] settlement pending for {investment_id}: {result.get('error')}")
                continue
            self._save_financial()
            settled = result.get("investment") or {}
            if not settled.get("announced"):
                change = int(settled.get("net_change", 0) or 0)
                label = "ربح" if change >= 0 else "خسارة"
                message = (f"📊 انتهى استثمار @{settled.get('username', 'مستخدم')} في {settled.get('asset')}: "
                           f"{label} {abs(change):,} نقطة بوت. صافي المبلغ المعاد {int(settled.get('payout', 0)):,}.")
                try:
                    await self.send_room_text(str(settled.get("room") or ""), message)
                except Exception as exc:
                    self.log(f"[stock-market] announcement failed: {exc!r}")
                    continue
                self.financial.state["investments"][investment_id]["announced"] = True
                self._save_financial()
            completed += 1
        return completed

    async def recover_financial_intents(self) -> int:
        recovered = 0
        intents = self.financial.state.setdefault("opening_intents", {})
        for investment_id, intent in list(intents.items()):
            try:
                uid = str(intent["account"])
                self._financial_usernames[uid] = str(intent.get("username") or "مستخدم")
                result = self.financial.invest(uid, str(intent["asset"]), int(intent["amount"]), investment_id=investment_id)
                if result.get("ok"):
                    self.financial.state["investments"][investment_id].update(
                        room=str(intent.get("room") or ""), username=str(intent.get("username") or "مستخدم"), announced=False,
                    )
                    message_id = str(intent.get("message_id") or "")
                    if message_id:
                        self.financial.state.setdefault("financial_messages", {})[message_id] = (
                            f"📈 بدأ استثمار {intent.get('asset')} بمبلغ {int(intent.get('amount', 0)):,} نقطة بوت. ستظهر النتيجة بعد دقيقة."
                        )
                    recovered += 1
                if result.get("ok") or result.get("error") == "insufficient":
                    intents.pop(investment_id, None)
            except Exception as exc:
                self.log(f"[stock-market] recovery pending for {investment_id}: {exc!r}")
        wager_intents = self.financial.state.setdefault("wager_opening_intents", {})
        for wager_id, intent in list(wager_intents.items()):
            try:
                uid = str(intent["account"])
                self._financial_usernames[uid] = str(intent.get("username") or "مستخدم")
                result = self.financial.reserve_wager(uid, int(intent["stake"]), wager_id=wager_id)
                if result.get("ok"):
                    participant = {"wager_id": wager_id, "account": uid,
                                   "username": str(intent.get("username") or "مستخدم"),
                                   "room": str(intent.get("room") or "")}
                    key = str(intent.get("game_key") or "رهان")
                    if intent.get("role") == "first" and key not in self.financial.state["wager_challenges"]:
                        self.financial.state["wager_challenges"][key] = {
                            "game_key": key, "label": str(intent.get("label") or "رهان"),
                            "stake": int(intent["stake"]), "first": participant,
                            "created_at": float(intent.get("created_at") or time.time()),
                        }
                    elif intent.get("role") == "second" and key in self.financial.state["wager_challenges"]:
                        challenge = self.financial.state["wager_challenges"][key]
                        if not challenge.get("second"):
                            challenge["second"] = participant
                            winner = secrets.choice((challenge["first"], participant))
                            loser = participant if winner is challenge["first"] else challenge["first"]
                            challenge["winner_wager_id"] = winner["wager_id"]
                            challenge["loser_wager_id"] = loser["wager_id"]
                    message_id = str(intent.get("message_id") or "")
                    if message_id:
                        self.financial.state.setdefault("wager_messages", {})[message_id] = "recovered"
                    wager_intents.pop(wager_id, None)
                    recovered += 1
                elif result.get("error") == "insufficient":
                    wager_intents.pop(wager_id, None)
            except Exception as exc:
                self.log(f"[wager] recovery pending for {wager_id}: {exc!r}")
        self._save_financial()
        for game_key, challenge in list(self.financial.state.get("wager_challenges", {}).items()):
            if challenge.get("second") and challenge.get("winner_wager_id"):
                await self._finish_wager(game_key)
        return recovered

    @staticmethod
    def _normalize_event_command(text: str) -> str:
        value = " ".join(str(text or "").strip().casefold().split())
        return value.translate(str.maketrans({"أ":"ا", "إ":"ا", "آ":"ا", "ى":"ي", "ة":"ه"}))

    def _event_game_for_command(self, text: str) -> str | None:
        command = self._normalize_event_command(text)
        for key, config in TIMED_TREASURE_GAMES.items():
            aliases = {self._normalize_event_command(alias) for alias in config.get("aliases", ())}
            if command in aliases:
                return key
        return None

    async def _board_command(self, room: str, uid: str, username: str, text: str) -> tuple[bool, str | None]:
        value = " ".join(str(text or "").strip().casefold().split())
        room_games = self.board_games.setdefault("games", {})
        current = room_games.get(room)
        aliases_ludo = {"لودو", "ludo"}
        aliases_snake = {"ثعبان", "snake", "سناكي", "snakes and ladders"}
        start_kind = "ludo" if value in aliases_ludo else ("snake" if value in aliases_snake else None)
        active = isinstance(current, dict) and current.get("status") not in {"finished", "timed_out"}
        commands = {"join", "انضمام", "roll", "rool", "رول", "ارمي", "ارم", "رمي"}
        count_text = value.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        candidate = bool(start_kind or (active and (value in commands or (current.get("game_type") == "ludo" and count_text in {"1", "2", "3", "4"}))))
        if not candidate:
            return False, None
        if not uid:
            return True, "تعذر تحديد حساب Giant للعبة."
        if not await self.is_vip(uid, username):
            return True, "🔒 ألعاب اللوح تتطلب توثيق VIP في Giant Chat."
        now = time.time()
        if start_kind:
            if active:
                label = "لودو" if current.get("game_type") == "ludo" else "السلم والثعبان"
                return True, f"🎲 توجد لعبة {label} قائمة هنا بالفعل؛ اكتب join للمشاركة."
            state = new_ludo_game(str(uuid.uuid4()), room, uid, now=now) if start_kind == "ludo" else new_snake_game(str(uuid.uuid4()), room, uid, now=now)
            state["player_names"] = {uid: username}
            room_games[room] = state
            self._save_board_games()
            if start_kind == "ludo":
                return True, "🎲 بدأت لعبة لودو. اختر عدد اللاعبين 1–4؛ الرقم 1 للعب ضد البوت، ثم ينضم الآخرون بكتابة join. عند بدء اللعب اكتب rool في دورك."
            return True, "🐍 بدأت لعبة السلم والثعبان؛ ينتظر لاعباً ثانياً. اكتب join للمشاركة، وبعدها اكتب rool عند دورك."

        game = current
        if not isinstance(game, dict):
            return False, None
        actor_name = username or uid
        game.setdefault("player_names", {})[uid] = actor_name
        if value in commands and value in {"join", "انضمام"}:
            command, kwargs = "join", {}
        elif game.get("game_type") == "ludo" and count_text in {"1", "2", "3", "4"}:
            command, kwargs = "choose", {"count": int(count_text)}
        elif value in {"roll", "rool", "رول", "ارمي", "ارم", "رمي"}:
            bot_roll = secrets.randbelow(6) + 1 if game.get("game_type") == "ludo" and game.get("bot") else None
            command, kwargs = "roll", {"roll": secrets.randbelow(6) + 1, "bot_roll": bot_roll}
        else:
            return False, None
        result = apply_board_action(game, uid, command, now=now, **kwargs)
        if not result.get("ok"):
            errors = {
                "not_lobby_creator": "اختيار عدد اللاعبين متاح لمنشئ اللعبة فقط.",
                "invalid_player_count": "اختر 1 أو 2 أو 3 أو 4 لاعبين.",
                "already_joined": "أنت منضم بالفعل.", "game_full": "اكتمل عدد اللاعبين.",
                "waiting_for_players": "اللعبة تنتظر اكتمال عدد اللاعبين.",
                "choose_player_count_first": "اختر عدد اللاعبين أولاً: 1–4.",
                "not_your_turn": "انتظر دورك.", "not_a_player": "لست مسجلاً في هذه اللعبة.",
                "game_not_joinable": "اللعبة لم تعد تستقبل لاعبين.",
            }
            return True, errors.get(result.get("error"), "تعذر تنفيذ الحركة؛ تحقق من حالة اللعبة.")
        updated = result["state"]
        updated["player_names"] = game.get("player_names", {})
        room_games[room] = updated
        self._save_board_games()
        messages = []
        for event in result.get("events", []):
            kind = event.get("type")
            if kind == "player_count_selected":
                messages.append(f"✅ تم اختيار {event['count']} لاعبين؛ نحتاج {event['needed']} لاعباً إضافياً. اكتبوا join.")
            elif kind == "player_joined":
                messages.append(f"✅ انضم @{updated['player_names'].get(event['player'], event['player'])}.")
            elif kind == "started":
                messages.append("🎲 بدأت اللعبة! اللاعبون: " + "، ".join(updated["player_names"].get(p, p) for p in event["players"]) + ". اكتب rool في دورك.")
            elif kind == "moved":
                if updated["game_type"] == "snake":
                    messages.append(f"🎲 @{updated['player_names'].get(event['player'], event['player'])} رمى {event['roll']} وانتقل من {event['from']} إلى {event['to']}.")
                else:
                    messages.append(f"🎲 @{updated['player_names'].get(event['player'], event['player'])} رمى {event['roll']} وانتقل من المربع {event['from']} إلى {event['to']}.")
            elif kind == "bot_moved":
                messages.append(f"🤖 رمى البوت {event['roll']} وانتقل من {event['from']} إلى {event['to']}.")
            elif kind == "turn":
                messages.append(f"🎯 الآن دور @{updated['player_names'].get(event['player'], event['player'])}؛ اكتب rool.")
            elif kind == "won":
                winner = event["player"]
                messages.append(f"🏆 فاز @{updated['player_names'].get(winner, winner)}! ستُضاف جائزة اللعبة إلى رصيد حساب Giant.")
        self._save_board_games()
        await self._award_board_winners()
        self._record_board_results()
        return True, "\n".join(messages) or None

    async def _award_board_winners(self) -> int:
        awarded = 0
        for room, game in self.board_games.get("games", {}).items():
            if not isinstance(game, dict) or game.get("status") != "finished" or game.get("reward_applied"):
                continue
            winner = str(game.get("winner") or "")
            if not winner or winner == "🤖 البوت":
                game["reward_applied"] = True
                self._save_board_games()
                continue
            names = game.get("player_names") or {}
            username = str(names.get(winner) or "لاعب")
            amount = 100_000 if game.get("game_type") == "snake" else 50_000
            txid = f"board-game:{game.get('game_id')}:{winner}:win"
            try:
                result = self.apply_delta(winner, username, amount, txid, f"{game.get('game_type')} board-game prize")
            except Exception as exc:
                self.log(f"[board-game] reward remains pending for room {room!r}: {exc!r}")
                continue
            if isinstance(result, dict) and result.get("ok"):
                game["reward_applied"] = True
                self._save_board_games()
                awarded += 1
        return awarded

    async def tick_board_games(self) -> None:
        now = time.time()
        for room, game in list(self.board_games.get("games", {}).items()):
            if not isinstance(game, dict) or game.get("status") in {"finished", "timed_out"}:
                continue
            expired = apply_board_action(game, "", "timeout", now=now)
            if expired.get("state", {}).get("status") == "timed_out":
                self.board_games["games"][room] = expired["state"]
                self._save_board_games()
                try:
                    await self.send_room_text(room, "⌛ انتهت مهلة لعبة اللوح لعدم وجود حركة لمدة دقيقتين.")
                except Exception as exc:
                    self.log(f"[board-game] timeout announcement failed for {room!r}: {exc!r}")
        await self._award_board_winners()
        self._record_board_results()

    async def _handle_timed_event(self, room: str, uid: str, username: str, text: str) -> tuple[bool, str | None]:
        round_id = self.timed_events.active_round_id
        active = self.timed_events.get_round(round_id) if round_id else None
        if not isinstance(active, dict) or active.get("status") != "registration":
            return False, None
        requested = self._event_game_for_command(text)
        generic_join = self._normalize_event_command(text) in {"join", "انضمام"}
        if not generic_join and requested != active.get("game_key"):
            return False, None
        if not uid:
            return True, "تعذر تحديد حساب Giant للمشاركة."
        if not await self.is_vip(uid, username):
            return True, "🔒 المشاركة في الفعاليات تتطلب توثيق VIP في Giant Chat."
        try:
            result = self.timed_events.join(active["id"], room, username, user_id=uid)
        except Exception as exc:
            self.log(f"[timed-events] join failed: {exc!r}")
            return True, "❌ تعذر تسجيل مشاركتك في الفعالية."
        self._save_timed_events()
        if not result.get("accepted"):
            messages = {
                "duplicate": "✅ أنت مسجل بالفعل في هذه الجولة.",
                "full": "اكتمل عدد المشاركين في الجولة.",
                "wrong_room": "هذه الغرفة غير مشمولة في الجولة الحالية.",
                "doctor_round_ban": "شاركت في جولة الطبيب السابقة؛ انتظر الجولة التالية.",
                "expired": "انتهت مهلة الانضمام إلى الجولة الحالية.",
            }
            return True, messages.get(result.get("reason"), "لا يمكن الانضمام إلى هذه الجولة الآن.")
        updated = result.get("round") or {}
        count = int(result.get("count", 0))
        required = int(updated.get("required_players", 0))
        if updated.get("status") == "resolved":
            await self._flush_timed_settlements()
            await self._announce_timed_round(updated["id"])
            return True, None
        join_name = TIMED_TREASURE_GAMES[updated["game_key"]]["join_ar"]
        return True, f"✅ تم تسجيلك في فعالية {updated['game_key']} ({count}/{required})."

    async def _flush_timed_settlements(self) -> int:
        completed = 0
        for intent in self.timed_events.pending_settlements():
            user_id = str(intent.get("user_id") or "")
            if not user_id:
                continue
            delta = int(intent.get("delta", 0) or 0)
            username = str(intent.get("username") or "لاعب")
            key = str(intent.get("idempotency_key") or "")
            if delta < 0:
                try:
                    if int(self.balance(user_id, username)) < -delta:
                        self.timed_events.acknowledge_settlement(key, success=True, receipt={"skipped": "insufficient_balance"})
                        self._save_timed_events()
                        completed += 1
                        continue
                except Exception:
                    continue
            try:
                result = self.apply_delta(user_id, username, delta, key, str(intent.get("reason") or "timed event"))
            except Exception as exc:
                self.log(f"[timed-events] settlement remains pending: {exc!r}")
                continue
            ok = isinstance(result, dict) and bool(result.get("ok"))
            self.timed_events.acknowledge_settlement(key, success=ok, receipt=result if ok else None,
                                                      error=(result or {}).get("error", "apply_delta_failed"))
            self._save_timed_events()
            if ok:
                completed += 1
        return completed

    async def _announce_timed_round(self, round_id: str) -> bool:
        result = self.timed_events.get_round(round_id)
        if not isinstance(result, dict) or result.get("status") not in {"resolved", "expired"} or result.get("announced"):
            return False
        intents = result.get("settlement_intents", {}).values()
        if result.get("status") == "resolved" and any(item.get("status") != "applied" for item in intents):
            return False
        self._record_timed_results(result)
        config = TIMED_TREASURE_GAMES[result["game_key"]]
        rooms = [str(value) for value in result.get("rooms", [])]
        if result.get("status") == "expired":
            text = f"⌛ انتهت مهلة فعالية {config['join_ar']} دون اكتمال العدد؛ لم تُمنح جائزة."
        else:
            text = f"🏆 انتهت فعالية {config['join_ar']}! الفائز @{result.get('winner', 'لاعب')} حصل على {int(result.get('prize', 0)):,} نقطة."
        sent_ok = True
        for room in rooms:
            try:
                await self.send_room_text(room, text)
            except Exception as exc:
                sent_ok = False
                self.log(f"[timed-events] result announcement failed for room {room!r}: {exc!r}")
        if sent_ok:
            self.timed_events.mark_announced(round_id)
            self._save_timed_events()
        return sent_ok

    async def tick_timed_events(self) -> None:
        await self._flush_timed_settlements()
        round_id = self.timed_events.active_round_id
        active = self.timed_events.get_round(round_id) if round_id else None
        if isinstance(active, dict) and active.get("status") == "registration" and time.time() >= float(active.get("ends_at", 0)):
            try:
                self.timed_events.expire(active["id"])
                self._save_timed_events()
            except Exception as exc:
                self.log(f"[timed-events] expiry failed: {exc!r}")
            await self._announce_timed_round(active["id"])
            return
        if isinstance(active, dict) and active.get("status") == "resolved":
            await self._announce_timed_round(active["id"])
            return
        if self.timed_events.active_round_id is None and time.time() >= self.timed_events.next_due:
            available = [str(room) for room in self.get_rooms() if str(room).strip()]
            if not available:
                return
            opened = self.timed_events.start_round(available)
            if opened:
                self._save_timed_events()
                config = TIMED_TREASURE_GAMES[opened["game_key"]]
                text = (f"{config['join_ar']}! بدأت فعالية Talkin2: {config['game_key']}\n"
                        f"يلزم {opened['required_players']} مشاركاً خلال 3 دقائق؛ اكتبوا «{config['join_ar']}» للانضمام. "
                        f"الجائزة {opened['prize']:,} نقطة، مع رسم مشاركة 10,000 عند كفاية الرصيد.")
                for room in opened["rooms"]:
                    try:
                        await self.send_room_text(room, text)
                    except Exception as exc:
                        self.log(f"[timed-events] opening announcement failed for {room!r}: {exc!r}")

    def _schedule(self, awaitable, name: str):
        try:
            task = asyncio.get_running_loop().create_task(awaitable, name=name)
        except RuntimeError:
            close = getattr(awaitable, "close", None)
            if close:
                close()
            self.log(f"[games] no running event loop for {name}")
            return None

        def report(done):
            try:
                error = done.exception()
            except asyncio.CancelledError:
                return
            if error:
                self.log(f"[games] {name} failed: {error!r}")

        task.add_done_callback(report)
        return task

    def _cricket_send_room_text(self, room: str, text: str):
        self._schedule(self.send_room_text(str(room), str(text)), "cricket-room-text")

    def _cricket_send_room_media(self, room: str, media_url: str, media_type: str):
        self._schedule(self.send_room_media(str(room), "", str(media_url), str(media_type)), "cricket-room-media")

    def _cricket_broadcast(self, text: str):
        self._schedule(self.broadcast_text(str(text)), "cricket-broadcast")

    def _cricket_send_private(self, username: str, text: str):
        uid = self._private_uids.get(str(username or "").strip().lstrip("@").casefold())
        if uid:
            self._schedule(self.send_dm(uid, str(text)), "cricket-private-text")
        else:
            self.log("[cricket] private reply skipped: no Giant user id in this DM poll")

    def _queue_cricket_reward(self, username: str, amount: int, transaction_id: str):
        queued = self.cricket_rewards.enqueue(username, int(amount), transaction_id)
        if queued.get("ok"):
            self._schedule(self.flush_cricket_rewards(), "cricket-reward-flush")
        else:
            self.log(f"[cricket] reward queue rejected: {queued.get('error')}")
        return queued

    async def flush_cricket_rewards(self) -> int:
        completed = 0
        async with self._reward_lock:
            for payout in self.cricket_rewards.pending():
                username = str(payout.get("username") or "")
                user_id = str(payout.get("user_id") or "")
                if not user_id:
                    try:
                        profile, error = await self.lookup_profile(username)
                    except Exception as exc:
                        self.log(f"[cricket] cannot resolve reward recipient: {exc!r}")
                        continue
                    if error or not isinstance(profile, dict):
                        self.log(f"[cricket] reward recipient lookup pending for {username!r}: {error}")
                        continue
                    user_id = str(profile.get("id") or "")
                if not user_id:
                    self.log("[cricket] reward recipient has no profile UUID; keeping payout pending")
                    continue
                try:
                    result = self.apply_delta(
                        user_id, username, int(payout.get("amount", 0) or 0),
                        str(payout.get("transaction_id") or ""),
                        str(payout.get("reason") or "cricket match prize"),
                    )
                except Exception as exc:
                    self.log(f"[cricket] point award pending after error: {exc!r}")
                    continue
                if isinstance(result, dict) and result.get("ok"):
                    self.cricket_rewards.complete(str(payout.get("transaction_id") or ""))
                    completed += 1
                else:
                    self.log(f"[cricket] point award remains pending: {(result or {}).get('error', 'unknown')}")
        return completed

    def _cricket_identity_candidate(self, room: str, text: str) -> bool:
        low = str(text or "").strip().casefold()
        if (
            low in {"join", "انضمام", ".cr 1", ".cr1", ".cr b", "cricket 1", "كريكيت 1"}
            or re.fullmatch(r"[0-6٠-٦۰-۶]", low)
        ):
            return True
        return bool(self.cricket.game.current(room))

    def _is_auction_message(self, room: str, uid: str, username: str, text: str) -> bool:
        if parse_toggle_command(text) is not None:
            return True
        low = text.strip().casefold()
        if low in {"sv", "auction inventory", "مخزون مزاد"}:
            return True
        if re.fullmatch(r"(?:red\s+auction|استرداد\s+مزاد|استبدال\s+مزاد)\s+(.+)", text.strip(), re.I):
            return True
        action = parse_room_command(text)
        if action is None:
            return False
        if action == "bid":
            active = self.auction.active()
            return bool(
                isinstance(active, dict) and active.get("phase") == "bidding"
                and any(
                    str(player.get("user_id") or "") == uid
                    and str(player.get("room") or "") == room
                    for player in active.get("participants", []) if isinstance(player, dict)
                )
            )
        return True

    async def handle_room(self, room: str, user_id: str, username: str, text: str, message_id: str = "") -> tuple[bool, str | None]:
        room_id = str(room or "").strip()
        uid = str(user_id or "").strip()
        name = str(username or "").strip().lstrip("@")
        value = str(text or "").strip()
        stats_command = self._normalize_event_command(value)
        if stats_command in {"مستواي", "احصائياتي", "احصائيات العابي", "game stats", "my game stats"}:
            return True, self._game_stats_reply(uid, name)
        if stats_command in {"توب الالعاب", "متصدرين الالعاب", "game leaderboard"}:
            return True, self._game_stats_reply(uid, name, leaderboard=True)
        transfer_handled, transfer_reply = await self._handle_transfer(room_id, uid, name, value, str(message_id or ""))
        if transfer_handled:
            return True, transfer_reply
        shop_handled, shop_reply = await self._handle_shop(room_id, uid, name, value)
        if shop_handled:
            return True, shop_reply
        board_handled, board_reply = await self._board_command(room_id, uid, name, value)
        if board_handled:
            return True, board_reply
        financial_handled, financial_reply = await self._handle_financial_command(room_id, uid, name, value, str(message_id or ""))
        if financial_handled:
            return True, financial_reply
        if self._cricket_identity_candidate(room_id, value):
            self.cricket_rewards.record_identity(uid, name)
        if self.cricket.handle(room_id, name, value):
            return True, None
        timed_handled, timed_reply = await self._handle_timed_event(room_id, uid, name, value)
        if timed_handled:
            return True, timed_reply
        if self._is_auction_message(room_id, uid, name, value):
            return True, await self._handle_auction(room_id, uid, name, value)
        low = " ".join(value.casefold().split())
        explicit_event = low in {"join rare diamond", "rare diamond", "فعالية الالماسة", "انضم للالماسة", "انضم للألماسة"}
        mining_alias = low in {"تنقيب", "نقب", "mining"}
        if explicit_event or (mining_alias and isinstance(self.rare_diamond.active(), dict)):
            return True, await self._handle_rare_diamond(room_id, uid, name, value)
        return False, None

    async def handle_private(self, user_id: str, username: str, text: str) -> tuple[bool, str | None]:
        uid = str(user_id or "").strip()
        name = str(username or "").strip().lstrip("@")
        value = str(text or "").strip()
        self._private_uids[name.casefold()] = uid
        shop_handled, shop_reply = await self._handle_shop("", uid, name, value)
        if shop_handled:
            return True, shop_reply
        if self.cricket.handle(uid, name, value, is_private=True):
            return True, None
        toggle = parse_toggle_command(value)
        if toggle is None:
            return False, None
        if name.casefold() != self.owner_name:
            return True, "🔒 تشغيل المزاد وإيقافه متاحان للماستر المحدد فقط."
        previous = self.auction.active()
        result = self.auction.set_enabled(toggle)
        if not toggle and previous and result.get("cancelled_active"):
            await self._auction_broadcast("⛔ أوقف الماستر المزاد المفتوح؛ لم تُخصم نقاط ولم تُمنح قطعة.", previous)
        return True, "✅ تم تشغيل المزاد." if toggle else "⛔ تم إيقاف المزاد وإلغاء الجولة المفتوحة إن وجدت."

    def _auction_rooms(self, active: dict | None) -> list[str]:
        if not isinstance(active, dict):
            return [str(room) for room in self.get_rooms() if str(room).strip()]
        candidates = [str(active.get("room") or "").strip()]
        candidates.extend(
            str(player.get("room") or "").strip()
            for player in active.get("participants", []) if isinstance(player, dict)
        )
        online = {str(room) for room in self.get_rooms()}
        return list(dict.fromkeys(room for room in candidates if room and room in online))

    async def _auction_broadcast(self, text: str, active: dict | None):
        for room in self._auction_rooms(active):
            try:
                await self.send_room_text(room, text)
            except Exception as exc:
                self.log(f"[auction] send failed for room {room!r}: {exc!r}")

    async def _auction_open_media(self, active: dict):
        item = active.get("item") or {}
        filename = str(item.get("image") or "")
        image = self.assets_dir / "treasures" / filename
        if not filename or not image.is_file() or image.stat().st_size > 50_000:
            self.log(f"[auction] missing/oversized item image: {filename!r}")
            return
        base = str(self.public_base() or "").rstrip("/")
        if not base:
            self.log("[auction] public base URL is not configured; image skipped")
            return
        url = f"{base}/assets/treasures/{quote(filename)}"
        for room in self._auction_rooms(active):
            try:
                await self.send_room_media(room, "", url, "image")
            except Exception as exc:
                self.log(f"[auction] image send failed for {room!r}: {exc!r}")

    async def _announce_auction_result(self, result: dict, active: dict | None = None, *, expired: bool = False):
        if not isinstance(result, dict):
            return
        prefix = "⏱️ انتهت مهلة المزايدة.\n" if expired else ""
        status = result.get("status")
        if status == "no_bids":
            text = prefix + "🔔 أُغلق المزاد دون مزايدات؛ لم تُخصم نقاط ولم تُمنح قطعة."
        elif status == "no_sale":
            text = prefix + "⚠️ تعذر إكمال البيع لعدم كفاية الرصيد؛ لم تُخصم نقاط ولم تُمنح قطعة."
        elif status == "sold":
            sale = result.get("result") or {}
            item = sale.get("item") or {}
            text = (
                f"{prefix}🏆 فاز @{sale.get('winner', 'لاعب')} بـ{item.get('icon', '💎')} "
                f"{item.get('name_ar', 'قطعة المزاد')} مقابل {int(sale.get('bid', 0)):,} نقطة.\n"
                f"المخزون: sv — الاسترداد: red auction {str(sale.get('auction_id') or '')[:8]}"
            )
        elif result.get("pending"):
            text = "⌛ تسوية المزاد معلقة مؤقتاً؛ ستُعاد المحاولة دون خصم مكرر."
        else:
            return
        await self._auction_broadcast(text, active)
        if status == "sold" and isinstance(active, dict):
            await self._auction_open_media(active)

    async def _handle_auction(self, room: str, uid: str, username: str, text: str) -> str | None:
        toggle = parse_toggle_command(text)
        if toggle is not None:
            if username.casefold() != self.owner_name:
                return "🔒 تشغيل المزاد وإيقافه متاحان للماستر المحدد فقط."
            previous = self.auction.active()
            changed = self.auction.set_enabled(toggle)
            if not toggle and previous and changed.get("cancelled_active"):
                await self._auction_broadcast("⛔ أوقف الماستر المزاد المفتوح؛ لم تُخصم نقاط ولم تُمنح قطعة.", previous)
            return "✅ تم تشغيل المزاد." if toggle else "⛔ تم إيقاف المزاد وإلغاء الجولة المفتوحة إن وجدت."

        low = text.strip().casefold()
        if low in {"sv", "auction inventory", "مخزون مزاد"}:
            if not await self.is_vip(uid, username):
                return "🔒 أمر المخزون يتطلب توثيق VIP."
            inventory = self.auction.inventory(uid, username)
            if not inventory:
                return "🎒 لا توجد قطع مزاد في مخزونك الآن."
            lines = ["🎒 قطع المزاد المتاحة للاسترداد:"]
            for index, item in enumerate(inventory, 1):
                lines.append(f"{index}. {item.get('icon', '💎')} {item.get('item_name_ar', 'قطعة')} — الرمز {str(item.get('id') or '')[:8]}")
            return "\n".join(lines)

        redeem = re.fullmatch(r"(?:red\s+auction|استرداد\s+مزاد|استبدال\s+مزاد)\s+(.+)", text.strip(), re.I)
        if redeem:
            if not await self.is_vip(uid, username):
                return "🔒 استرداد قطع المزاد يتطلب توثيق VIP."
            result = self.auction.redeem(uid, username, redeem.group(1), self.apply_delta)
            if not result.get("ok"):
                if result.get("pending"):
                    return "⌛ الاسترداد معلق مؤقتاً؛ أعد المحاولة لاحقاً، ولن تتكرر المعاملة."
                return "❌ لم أجد هذه القطعة في مخزونك أو تعذر الاسترداد."
            outcome = "أصلية" if result.get("authentic") else "مقلدة"
            return f"💎 نتيجة الاسترداد: القطعة {outcome}. أضيفت {int(result.get('points', 0)):,} نقطة إلى حسابك."

        action = parse_room_command(text)
        active = self.auction.active()
        if action is None:
            return None
        if action == "bid":
            if not isinstance(active, dict) or active.get("phase") != "bidding":
                return None
            participants = active.get("participants", [])
            if not any(str(p.get("user_id") or "") == uid and str(p.get("room") or "") == room for p in participants if isinstance(p, dict)):
                return None
        elif action not in {"open", "join"} and isinstance(active, dict):
            participant_rooms = {str(p.get("room") or "") for p in active.get("participants", []) if isinstance(p, dict)}
            if room not in participant_rooms and room != str(active.get("room") or "") and username.casefold() != self.owner_name:
                return None

        vip_error = await self.is_vip(uid, username)
        if not vip_error:
            return "🔒 أوامر المزاد تتطلب توثيق VIP."

        if action == "open":
            result = self.auction.open_auction(uid, username, room)
            if not result.get("ok"):
                if result.get("error") == "disabled":
                    return "⛔ المزاد متوقف حالياً؛ على الماستر تشغيله من الخاص بكتابة auction on أو «تشغيل لعبة مزاد»."
                return "⚠️ يوجد مزاد مفتوح بالفعل."
            opened = result["auction"]
            item = opened.get("item") or {}
            await self._auction_open_media(opened)
            return (
                f"🔨 بدأ التسجيل للمزاد: {item.get('icon', '💎')} {item.get('name_ar', 'قطعة نادرة')}\n"
                f"سعر البداية {int(item.get('starting_price', 0)):,} نقطة. اكتب «مزايدة» للانضمام؛ يبدأ المزاد عند 3 لاعبين، وبحد أقصى 5."
            )

        if action == "join":
            result = self.auction.join(uid, username, room)
            if not result.get("ok"):
                return "⚠️ لا يوجد تسجيل مفتوح أو اكتمل العدد."
            count = int(result.get("participant_count", len((result.get("auction") or {}).get("participants", []))) or 0)
            if result.get("auto_started"):
                await self._auction_broadcast("🔨 اكتمل عدد المشاركين؛ بدأ المزاد. اتبعوا دور المزايدة والمهلة.", result.get("auction"))
                return f"✅ انضممت للمزاد ({count}/5) وبدأت المزايدة."
            return f"✅ تم تسجيلك للمزاد ({count}/5). يبدأ عند 3 لاعبين بأمر «ابدأ المزاد» أو تلقائياً عند اكتمال 5."

        if action == "start":
            result = self.auction.start(uid, username, is_master=username.casefold() == self.owner_name)
            if not result.get("ok"):
                return "⚠️ لا يمكن بدء المزاد بعد؛ يلزم 3 مشاركين على الأقل، والبدء للفاتح أو الماستر."
            active = result.get("auction") or self.auction.active() or {}
            item = active.get("item") or {}
            await self._auction_broadcast(
                f"🔨 بدأت المزايدة على {item.get('name_ar', 'القطعة')} بسعر {int(item.get('starting_price', 0)):,} نقطة. يبدأ الدور بـ@{active.get('turn_username', '')}، والمهلة 7 ثوانٍ للدور و60 ثانية للجولة.",
                active,
            )
            await self._auction_open_media(active)
            return None

        if action == "bid":
            amount = parse_bid_amount(text)
            if amount is None:
                return "❌ اكتب «زايد 100000000» أو «زايد 100m»."
            result = self.auction.bid(uid, username, amount, self.balance(uid, username))
            if not result.get("ok"):
                return "⚠️ لم تُقبل المزايدة: تحقق من دورك ورصيدك وأنها أعلى من العرض السابق."
            active = self.auction.active() or {}
            await self._auction_broadcast(
                f"💰 عرض جديد: @{username} — {amount:,} نقطة. الدور التالي لـ@{active.get('turn_username', '')}.", active,
            )
            return None

        if action == "pass":
            result = self.auction.pass_turn(uid, username, apply_delta=self.apply_delta)
            if result.get("status") in {"sold", "no_bids", "no_sale"} or result.get("pending"):
                await self._announce_auction_result(result, active)
                return None
            if not result.get("ok"):
                return "⚠️ لا يمكنك تمرير الدور الآن؛ تحقق من أنه دورك."
            latest = self.auction.active() or {}
            await self._auction_broadcast(f"⏭️ مرّر @{username} دوره؛ الدور التالي لـ@{latest.get('turn_username', '')}.", latest)
            return None

        if action == "close":
            result = self.auction.close(uid, username, is_master=username.casefold() == self.owner_name, apply_delta=self.apply_delta)
            if not result.get("ok"):
                return "⚠️ لا يمكن إغلاق المزاد؛ المسموح للفاتح أو الماستر بعد بدء المزايدة."
            await self._announce_auction_result(result, active)
            return None

        if action == "cancel":
            result = self.auction.cancel(uid, username, is_master=username.casefold() == self.owner_name)
            if not result.get("ok"):
                return "⚠️ لا يمكن إلغاء المزاد؛ الإلغاء للفاتح أو الماستر قبل التسوية."
            await self._auction_broadcast("⛔ أُلغي المزاد؛ لم تُخصم نقاط ولم تُمنح قطعة.", active)
            return None
        return None

    async def _handle_rare_diamond(self, room: str, uid: str, username: str, text: str) -> str | None:
        low = " ".join(str(text or "").strip().casefold().split())
        if low not in {"تنقيب", "نقب", "mining", "join rare diamond", "rare diamond", "فعالية الالماسة", "انضم للالماسة", "انضم للألماسة"}:
            return None
        if not await self.is_vip(uid, username):
            return "🔒 الانضمام لفعالية الألماسة يتطلب توثيق VIP."
        result = self.rare_diamond.join(uid, username, room)
        if not result.get("ok"):
            if result.get("error") == "expired":
                await self.tick_rare_diamond()
                return "⌛ انتهى وقت الانضمام إلى الجولة الحالية."
            if result.get("error") == "no_event":
                return "💎 لا توجد جولة ألماسة مفتوحة الآن؛ انتظر إعلان الجولة التالية."
            if result.get("error") == "wrong_room":
                return "🔒 هذه الجولة غير مفتوحة في هذه الغرفة."
            if result.get("error") == "full":
                return "💎 اكتمل عدد المشاركين في الجولة."
            return "❌ تعذر تسجيلك في فعالية الألماسة."
        if result.get("already_joined"):
            return f"✅ أنت مسجل بالفعل ({result.get('count')}/{result.get('required')})."
        count, required = int(result.get("count", 0)), int(result.get("required", 5))
        if not result.get("ready"):
            return f"💎 انضممت لفعالية الألماسة ({count}/{required}). اكتب «تنقيب» في أي غرفة مشمولة لإكمال العدد."
        resolved = self.rare_diamond.resolve(self.apply_delta)
        await self._announce_rare_result(resolved)
        return None

    async def _announce_rare_result(self, resolved: dict | None):
        if not isinstance(resolved, dict) or not resolved.get("ok"):
            if isinstance(resolved, dict) and resolved.get("pending"):
                self.log("[rare-diamond] settlement pending; stable transaction IDs will be retried")
            return
        result = resolved.get("result") or {}
        if result.get("status") != "complete":
            return
        winner = str(result.get("winner") or "لاعب")
        payout_rows = result.get("payouts", [])
        skipped = sum(1 for row in payout_rows if isinstance(row, dict) and row.get("status") == "insufficient")
        text = f"💎 انتهت فعالية الألماسة النادرة! الفائز @{winner} حصل على 100,000,000 نقطة."
        if skipped:
            text += f" لم يُخصم رسم المشاركة من {skipped} لاعب(ين) لعدم كفاية الرصيد."
        text += " اكتبوا «تنقيب» عند إعلان الجولة التالية."
        rooms = [str(room) for room in result.get("rooms", []) if str(room).strip()]
        for room in rooms:
            try:
                await self.send_room_text(room, text)
            except Exception as exc:
                self.log(f"[rare-diamond] announce failed for {room!r}: {exc!r}")
        image = self.assets_dir / "treasures" / "rare_diamond_3d.png"
        if not image.is_file():
            image = self.assets_dir / "treasures" / "rare_diamond_3d.jpg"
        base = str(self.public_base() or "").rstrip("/")
        if image.is_file() and base:
            url = f"{base}/assets/treasures/{quote(image.name)}"
            for room in rooms:
                try:
                    await self.send_room_media(room, "", url, "image")
                except Exception as exc:
                    self.log(f"[rare-diamond] image send failed for {room!r}: {exc!r}")

    async def tick_rare_diamond(self):
        active = self.rare_diamond.active()
        if isinstance(active, dict) and active.get("phase") in {"ready", "settling"}:
            await self._announce_rare_result(self.rare_diamond.resolve(self.apply_delta))
            return
        expired = self.rare_diamond.expire_if_due()
        if isinstance(expired, dict):
            if expired.get("status") == "ready":
                await self._announce_rare_result(self.rare_diamond.resolve(self.apply_delta))
                return
            event = expired.get("event") or {}
            for room in event.get("rooms", []):
                try:
                    await self.send_room_text(str(room), "⌛ انتهت مهلة فعالية الألماسة دون اكتمال العدد؛ لم تُمنح جائزة ولم تُخصم نقاط.")
                except Exception as exc:
                    self.log(f"[rare-diamond] timeout announcement failed: {exc!r}")
        opened = self.rare_diamond.open_if_due([str(room) for room in self.get_rooms()])
        if opened:
            text = (
                "💎 بدأت فعالية الألماسة النادرة! يلزم 5 لاعبين خلال 3 دقائق.\n"
                "اكتبوا «تنقيب» للانضمام؛ الفائز يحصل على 100,000,000 نقطة، ويُخصم 10,000 من رصيد كل خاسر عند كفاية الرصيد."
            )
            for room in opened.get("rooms", []):
                try:
                    await self.send_room_text(str(room), text)
                except Exception as exc:
                    self.log(f"[rare-diamond] opening announcement failed: {exc!r}")

    async def tick_auction(self):
        active = self.auction.active()
        if not isinstance(active, dict):
            return
        if active.get("phase") == "settling":
            result = self.auction.recover_pending_settlement(self.apply_delta)
            await self._announce_auction_result(result, active)
            return
        if active.get("phase") != "bidding":
            return
        now = time.time()
        if now >= float(active.get("deadline_at", 0) or 0):
            result = self.auction.close(
                str(active.get("opener_id") or ""), str(active.get("opener_name") or ""),
                is_master=True, apply_delta=self.apply_delta,
            )
            await self._announce_auction_result(result, active, expired=True)
            return
        if now >= float(active.get("turn_deadline_at", 0) or 0):
            result = self.auction.pass_turn(
                str(active.get("turn_user_id") or ""), str(active.get("turn_username") or ""),
                apply_delta=self.apply_delta,
            )
            if result.get("status") in {"sold", "no_bids", "no_sale"} or result.get("pending"):
                await self._announce_auction_result(result, active, expired=True)
            elif result.get("ok"):
                latest = self.auction.active() or {}
                await self._auction_broadcast(
                    f"⏱️ انتهت مهلة الدور؛ تم تمرير دور @{active.get('turn_username', '')}. الدور التالي لـ@{latest.get('turn_username', '')}.",
                    latest,
                )

    async def recover(self):
        active = self.auction.active()
        recovered = self.auction.recover_pending_settlement(self.apply_delta)
        if recovered:
            self.log(f"[auction] pending settlement recovery: {recovered.get('status', recovered.get('error', 'unknown'))}")
            await self._announce_auction_result(recovered, active)
        rare = self.rare_diamond.resolve(self.apply_delta)
        if rare and rare.get("ok"):
            self.log("[rare-diamond] recovered a pending round settlement")
            await self._announce_rare_result(rare)
        await self.flush_cricket_rewards()
