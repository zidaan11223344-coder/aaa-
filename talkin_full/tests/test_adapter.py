# -*- coding: utf-8 -*-
"""End-to-end tests for the Giant transport adapter (no network)."""
from __future__ import annotations

import time

import pytest

from talkin_full.adapter import GiantTalkinBot
from talkin_full.vendor import talkin_bot as tb


class FakeApi:
    user_id = "bot-1"
    username = "testbot"

    def __init__(self):
        self.room_names = {"r1": "room1", "r2": "room2"}
        self.users = {"bot-1": "testbot", "u1": "ahmad", "u2": "sara"}
        self.sent_room = []
        self.sent_media = []
        self.sent_dm = []
        self.sent_dm_media = []
        self.moderations = []
        self.joins = []
        self.leaves = []
        self.statuses = []
        self.members_map = {
            "room1": [
                {"username": "ahmad", "user_id": "u1", "role": "member", "online": "1"},
                {"username": "sara", "user_id": "u2", "role": "admin", "online": ""},
            ]
        }

    # identity
    def room_name(self, rid):
        return self.room_names.get(str(rid), "")

    def room_id(self, name):
        for rid, rname in self.room_names.items():
            if rname.casefold() == str(name).casefold():
                return rid
        return ""

    def username(self, uid):
        return self.users.get(str(uid), "")

    def resolve_user_id(self, name):
        for uid, uname in self.users.items():
            if uname.casefold() == str(name).casefold():
                return uid
        return ""

    def all_rooms(self):
        return [{"id": rid, "name": name} for rid, name in self.room_names.items()]

    def members(self, room):
        return list(self.members_map.get(room, []))

    # transport
    def send_room_text(self, room, text):
        self.sent_room.append((room, text))
        return True

    def send_room_media(self, room, caption, url, kind="image", duration_ms=None):
        self.sent_media.append((room, caption, url, kind, duration_ms))
        return True

    def send_dm(self, username, text):
        self.sent_dm.append((username, text))
        return True

    def send_dm_media(self, username, caption, url, kind="image"):
        self.sent_dm_media.append((username, caption, url, kind))
        return True

    def join_room(self, room):
        self.joins.append(room)
        return True, room

    def leave_room(self, room):
        self.leaves.append(room)
        return True, room

    def moderate(self, action, room, target, new_rank=""):
        self.moderations.append((action, room, target, new_rank))
        role = "kicked" if action == "kick" else ("outcast" if action == "ban" else new_rank)
        return True, role

    def set_status(self, status):
        self.statuses.append(status)
        return True

    def heartbeat(self, room):
        return True


@pytest.fixture()
def bot():
    api = FakeApi()
    instance = GiantTalkinBot(api)
    yield instance
    instance.stop_event.set()


def test_room_text_reaches_original_handler(bot):
    bot.feed_room_message(
        {
            "id": "m1",
            "room_id": "r1",
            "user_id": "u1",
            "content": "نقاطي",
            "message_type": "text",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    assert any("نقاطي" in text and "الرصيد" in text for _room, text in bot.api.sent_room)


def test_dm_reaches_private_handler(bot):
    bot.feed_dm(
        {
            "id": "d1",
            "sender_id": "u1",
            "recipient_id": "bot-1",
            "envelope": {"id": "e1", "content": "نقاطي", "message_type": "text"},
        }
    )
    assert any(name == "ahmad" and "الرصيد" in text for name, text in bot.api.sent_dm)


def test_room_media_packet_is_translated(bot):
    ok = bot.send_room_media("room1", "http://127.0.0.1:8080/gifts/x.png", "image")
    assert ok is True
    assert bot.api.sent_media and bot.api.sent_media[0][0] == "room1"
    assert bot.api.sent_media[0][2].endswith("/gifts/x.png")


def test_private_media_packet_is_translated(bot):
    ok = bot.send_private_media("ahmad", "http://127.0.0.1:8080/gifts/y.png", "image")
    assert ok is True
    assert bot.api.sent_dm_media and bot.api.sent_dm_media[0][0] == "ahmad"


def test_admin_kick_is_translated(bot):
    assert bot.send_admin("room1", "ahmad", "kick") is True
    assert bot.api.moderations == [("kick", "room1", "ahmad", "")]


def test_admin_ban_and_rank_are_translated(bot):
    assert bot.send_admin("room1", "ahmad", "ban") is True
    assert bot.send_admin("room1", "ahmad", "admin") is True
    assert bot.api.moderations[0][0] == "ban"
    assert bot.api.moderations[1] == ("rank", "room1", "ahmad", "admin")


def test_join_and_leave_are_translated(bot):
    assert bot.join_room("room2", force=True) is True
    assert bot.api.joins == ["room2"]
    deadline = time.time() + 2
    while time.time() < deadline and "room2" not in bot.connected_rooms:
        time.sleep(0.05)
    assert "room2" in bot.connected_rooms
    assert bot.leave_room("room2") is True
    assert bot.api.leaves == ["room2"]


def test_roster_populates_roles(bot):
    assert bot.request_room_occupants("room1") is True
    assert bot.room_users["room1"]["ahmad"] == "member"
    assert bot.room_users["room1"]["sara"] == "admin"


def test_unsupported_packet_returns_false(bot):
    packet = tb.encode_query("load_list_new")
    assert bot.send_query(packet) is True
    unknown = tb.encode_query("sent_invitation", to="x")
    assert bot.send_query(unknown) is False
