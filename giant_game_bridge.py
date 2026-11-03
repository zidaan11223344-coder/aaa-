"""Adapters that connect Talkin2 game engines to Giant Chat's async APIs."""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote

from auction_game import AuctionGame, parse_bid_amount, parse_room_command, parse_toggle_command
from cricket_integration import CricketIntegration
from cricket_reward_outbox import CricketRewardOutbox
from rare_diamond_event import RareDiamondEvent


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
        self.public_base = public_base
        self.owner_name = str(owner_name or "").strip().lstrip("@").casefold()
        self.bot_name = str(bot_name or "Giant Chat")
        self.log = log
        self.auction = AuctionGame(self.state_dir / "auction_state.json")
        self.cricket_rewards = CricketRewardOutbox(self.state_dir / "cricket_rewards.json")
        self.rare_diamond = RareDiamondEvent(self.state_dir / "rare_diamond_event.json")
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

    async def handle_room(self, room: str, user_id: str, username: str, text: str) -> tuple[bool, str | None]:
        room_id = str(room or "").strip()
        uid = str(user_id or "").strip()
        name = str(username or "").strip().lstrip("@")
        value = str(text or "").strip()
        if self._cricket_identity_candidate(room_id, value):
            self.cricket_rewards.record_identity(uid, name)
        if self.cricket.handle(room_id, name, value):
            return True, None
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
