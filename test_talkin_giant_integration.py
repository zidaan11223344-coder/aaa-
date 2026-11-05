import asyncio
import uuid
from pathlib import Path

from giant_game_bridge import GiantGameBridge
from point_transactions import apply_deltas_once


def make_bridge(tmp_path: Path):
    messages = []
    point_calls = []
    transfer_ledger = {}

    async def send_room_text(room, text):
        messages.append((room, text))

    async def send_room_media(room, text, url, kind):
        messages.append((room, url, kind))

    async def send_dm(user_id, text):
        messages.append((user_id, text))

    def apply_delta(user_id, username, amount, txid, reason):
        point_calls.append((user_id, username, amount, txid, reason))
        return {"ok": True, "already_applied": False}

    def apply_deltas(deltas, names, txid, reason):
        result, changed = apply_deltas_once(transfer_ledger, deltas, names, txid)
        result["changed"] = changed
        return result

    async def is_vip(_uid, _name):
        return True

    async def lookup_profile(name):
        return {"id": "resolved-" + name, "username": name}, None

    bridge = GiantGameBridge(
        tmp_path / "runtime_state", tmp_path / "assets",
        send_room_text=send_room_text,
        send_room_media=send_room_media,
        send_dm=send_dm,
        broadcast_text=lambda text: send_room_text("room", text),
        get_rooms=lambda: ["room"],
        is_vip=is_vip,
        cricket_is_verified=lambda _name: True,
        lookup_profile=lookup_profile,
        balance=lambda _uid, _name: 2_000_000_000,
        apply_delta=apply_delta,
        apply_deltas=apply_deltas,
        public_base=lambda: "https://bot.example",
        owner_name="owner",
        bot_name="Giant",
        log=lambda *_args: None,
    )
    return bridge, messages, point_calls, transfer_ledger


def test_ludo_commands_are_routed_through_giant_room_handler(tmp_path):
    async def run():
        bridge, _, _, _ = make_bridge(tmp_path)
        handled, reply = await bridge.handle_room("room", "uuid-player-1", "one", "ludo")
        assert handled and "اختر عدد اللاعبين" in reply
        handled, reply = await bridge.handle_room("room", "uuid-player-1", "one", "2")
        assert handled and "اختيار 2" in reply
        handled, reply = await bridge.handle_room("room", "uuid-player-2", "two", "join")
        assert handled and "انضم" in reply
        assert bridge.board_games["games"]["room"]["status"] == "active"
        handled, reply = await bridge.handle_room("room", "uuid-player-1", "one", "rool")
        assert handled and "رمى" in reply
        assert bridge.board_games["games"]["room"]["tokens"]["uuid-player-1"] > 0
        assert (tmp_path / "runtime_state" / "talkin_board_games.json").exists()
    asyncio.run(run())


def test_timed_rounds_pay_giant_uuids_and_announce_when_complete(tmp_path):
    async def run():
        bridge, messages, point_calls, _ = make_bridge(tmp_path)
        opened = bridge.timed_events.start_round(["room"], game_key="gold_treasure")
        bridge._save_timed_events()
        for index in range(5):
            handled, reply = await bridge.handle_room("room", f"uuid-{index}", f"player{index}", "كنز")
            assert handled
            if index < 4:
                assert "تم تسجيلك" in reply
            else:
                assert reply is None
        assert len(point_calls) == 5
        assert {row[0] for row in point_calls} == {f"uuid-{i}" for i in range(5)}
        assert len({row[3] for row in point_calls}) == 5
        assert bridge.timed_events.get_round(opened["id"])["announced"] is True
        assert any("انتهت فعالية" in text for _room, text in messages)
        restored = GiantGameBridge(
            tmp_path / "runtime_state", tmp_path / "assets",
            send_room_text=lambda *_: asyncio.sleep(0),
            send_room_media=lambda *_: asyncio.sleep(0),
            send_dm=lambda *_: asyncio.sleep(0), broadcast_text=lambda *_: asyncio.sleep(0),
            get_rooms=lambda: ["room"], is_vip=lambda *_: True,
            cricket_is_verified=lambda *_: True, lookup_profile=lambda *_: (None, None),
            balance=lambda *_: 0, apply_delta=lambda *_: {"ok": True},
            public_base=lambda: "", owner_name="owner", bot_name="Giant", log=lambda *_: None,
        )
        assert restored.timed_events.get_round(opened["id"])["announced"] is True
    asyncio.run(run())


def test_ludo_digits_arabic_and_timeout_work_through_giant(tmp_path):
    async def run():
        bridge, messages, _, _ = make_bridge(tmp_path)
        handled, _ = await bridge.handle_room("room", "uuid-player-1", "one", "لودو")
        assert handled
        handled, reply = await bridge.handle_room("room", "uuid-player-1", "one", "٢")
        assert handled and "اختيار 2" in reply
        game = bridge.board_games["games"]["room"]
        game["last_activity_at"] -= 121
        await bridge.tick_board_games()
        assert bridge.board_games["games"]["room"]["status"] == "timed_out"
        assert any("انتهت مهلة" in text for _room, text in messages)
    asyncio.run(run())


def test_sb_transfer_is_atomic_and_duplicate_delivery_is_idempotent(tmp_path):
    async def run():
        bridge, _, _, ledger = make_bridge(tmp_path)
        ledger["uuid-sender"] = {"username": "sender", "points": 100}
        handled, reply = await bridge.handle_room("room", "uuid-sender", "sender", "sb@target@40", "msg-001")
        assert handled and "تم تحويل 40" in reply
        handled, duplicate = await bridge.handle_room("room", "uuid-sender", "sender", "sb@target@40", "msg-001")
        assert handled and "لم يُخصم مرتين" in duplicate
        handled, second = await bridge.handle_room("room", "uuid-sender", "sender", "sb@target@40", "msg-002")
        assert handled and "تم تحويل 40" in second
        assert ledger["uuid-sender"]["points"] == 20
        assert ledger["resolved-target"]["points"] == 80
        assert ledger["uuid-sender"]["applied_point_transactions"] == ledger["resolved-target"]["applied_point_transactions"]
    asyncio.run(run())


def test_shop_purchase_uses_bot_points_and_persists_uuid_entitlement(tmp_path):
    async def run():
        bridge, _, point_calls, _ = make_bridge(tmp_path)
        user_id = str(uuid.uuid4())
        handled, reply = await bridge.handle_private(user_id, "buyer", "sby 1")
        assert handled and "تم شراء" in reply
        assert point_calls[-1][0] == user_id
        assert point_calls[-1][2] == -10_000_000
        assert bridge.shop.active("user_bomb", user_id=user_id)
        assert (tmp_path / "runtime_state" / "talkin_shop.json").is_file()
    asyncio.run(run())


def test_transfer_without_supabase_message_id_fails_safely(tmp_path):
    async def run():
        bridge, _, _, ledger = make_bridge(tmp_path)
        ledger["uuid-sender"] = {"username": "sender", "points": 100}
        handled, reply = await bridge.handle_room("room", "uuid-sender", "sender", "sb@target@40")
        assert handled and "لم يتم التحويل" in reply
        assert ledger["uuid-sender"]["points"] == 100
        assert "resolved-target" not in ledger
    asyncio.run(run())


def test_stock_market_uses_bot_ledger_and_settles_after_due_time(tmp_path):
    async def run():
        bridge, messages, point_calls, _ = make_bridge(tmp_path)
        user_id = str(uuid.uuid4())
        handled, reply = await bridge.handle_room("room", user_id, "investor", "بورصة")
        assert handled and "1 ذهب" in reply
        handled, reply = await bridge.handle_room("room", user_id, "investor", "1")
        assert handled and "أرسل مبلغ" in reply
        handled, reply = await bridge.handle_room("room", user_id, "investor", "1000", "investment-001")
        assert handled and "بدأ استثمار" in reply
        handled, duplicate = await bridge.handle_room("room", user_id, "investor", "1000", "investment-001")
        assert handled and "سبقًا" in duplicate
        investment = next(iter(bridge.financial.state["investments"].values()))
        assert point_calls[-1][0] == user_id and point_calls[-1][2] == -1000
        investment["due_at"] = 0
        assert await bridge.tick_financial() == 1
        assert investment["status"] == "settled"
        assert investment["announced"] is True
        assert point_calls[-1][0] == user_id and point_calls[-1][2] > 0
        assert any("انتهى استثمار" in message for _room, message in messages)
    asyncio.run(run())


def test_wager_reserves_both_bot_point_stakes_and_deduplicates_messages(tmp_path):
    async def run():
        bridge, messages, point_calls, _ = make_bridge(tmp_path)
        first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
        handled, reply = await bridge.handle_room("room-1", first_id, "first", "رهان@100", "wager-001")
        assert handled and "ينتظر منافسًا" in reply
        handled, result = await bridge.handle_room("room-2", second_id, "second", "رهان@100", "wager-002")
        assert handled and "انتهى رهان" in result
        assert [call[2] for call in point_calls] == [-100, -100, 200]
        handled, duplicate = await bridge.handle_room("room-2", second_id, "second", "رهان@100", "wager-002")
        assert handled and "سبقًا" in duplicate
        assert len(point_calls) == 3
        assert len(messages) >= 3
    asyncio.run(run())
