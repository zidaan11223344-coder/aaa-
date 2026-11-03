import asyncio
from pathlib import Path

from giant_game_bridge import GiantGameBridge


def make_bridge(tmp_path: Path, *, vip=True):
    messages = []
    point_calls = []

    async def send_room_text(room, text):
        messages.append(("room", room, text))

    async def send_room_media(room, text, url, kind):
        messages.append(("media", room, url, kind))

    async def send_dm(user_id, text):
        messages.append(("dm", user_id, text))

    async def broadcast(text):
        messages.append(("broadcast", text))

    async def is_vip(user_id, username):
        return vip

    async def lookup_profile(username):
        return {"id": "uuid-" + username, "username": username}, None

    def apply_delta(user_id, username, amount, txid, reason):
        point_calls.append((user_id, username, amount, txid, reason))
        return {"ok": True}

    bridge = GiantGameBridge(
        tmp_path / "runtime_state", tmp_path / "assets",
        send_room_text=send_room_text, send_room_media=send_room_media,
        send_dm=send_dm, broadcast_text=broadcast, get_rooms=lambda: ["room"],
        is_vip=is_vip, cricket_is_verified=lambda _name: True,
        lookup_profile=lookup_profile, balance=lambda _uid, _name: 2_000_000_000,
        apply_delta=apply_delta, public_base=lambda: "https://bot.example",
        owner_name="owner", bot_name="Giant", log=lambda *_args: None,
    )
    return bridge, messages, point_calls


def test_mining_command_remains_available_when_rare_event_is_closed(tmp_path):
    async def run():
        bridge, _, _ = make_bridge(tmp_path)
        handled, reply = await bridge.handle_room("room", "uuid-1", "player", "تنقيب")
        assert not handled and reply is None
    asyncio.run(run())


def test_master_can_enable_auction_and_vip_can_open_it(tmp_path):
    async def run():
        bridge, _, _ = make_bridge(tmp_path)
        handled, reply = await bridge.handle_private("uuid-owner", "owner", "auction on")
        assert handled and "تشغيل" in reply
        handled, reply = await bridge.handle_room("room", "uuid-player", "player", "افتح المزاد")
        assert handled and "بدأ التسجيل" in reply
        assert bridge.auction.active()["phase"] == "registration"
    asyncio.run(run())


def test_five_vip_players_resolve_rare_event_and_receive_result(tmp_path):
    async def run():
        bridge, messages, point_calls = make_bridge(tmp_path)
        bridge.rare_diamond.open_if_due(["room"])
        for index in range(5):
            handled, reply = await bridge.handle_room("room", f"uuid-{index}", f"player{index}", "تنقيب")
            assert handled
            if index < 4:
                assert "انضممت" in reply
            else:
                assert reply is None
        assert len(point_calls) == 5
        assert any("انتهت فعالية الألماسة" in item[-1] for item in messages if item[0] == "room")
    asyncio.run(run())


def test_unverified_player_cannot_join_rare_event(tmp_path):
    async def run():
        bridge, _, _ = make_bridge(tmp_path, vip=False)
        bridge.rare_diamond.open_if_due(["room"])
        handled, reply = await bridge.handle_room("room", "uuid-1", "player", "تنقيب")
        assert handled and "VIP" in reply
        assert bridge.rare_diamond.active()["players"] == []
    asyncio.run(run())
