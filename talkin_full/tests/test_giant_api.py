# -*- coding: utf-8 -*-
"""Unit tests for the Supabase transport using an in-memory fake client."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from talkin_full.config import Settings
from talkin_full.giant_api import GiantApi


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = {}
        self.operation = "select"
        self.payload = None
        self.limit_value = None

    def select(self, *args, **kwargs):
        self.operation = "select"
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def ilike(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, key, values):
        self.filters[key] = list(values)
        return self

    def gt(self, key, value):
        self.filters[key] = value
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        self.client.calls.append((self.table, self.operation, self.payload, dict(self.filters)))
        if self.operation == "insert":
            rows = self.client.inserted.setdefault(self.table, [])
            rows.append(self.payload)
            return FakeResponse([self.payload])
        data = list(self.client.tables.get(self.table, []))
        for key, value in self.filters.items():
            if isinstance(value, list):
                data = [row for row in data if row.get(key) in value]
            else:
                data = [row for row in data if row.get(key) == value]
        if self.limit_value:
            data = data[: self.limit_value]
        return FakeResponse(data)


class FakeBucket:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def upload(self, path, data, options=None):
        self.client.uploads.append((self.name, path, data, options))
        return {"path": path}

    def get_public_url(self, path):
        return f"https://storage.example/{self.name}/{path}"


class FakeStorage:
    def __init__(self, client):
        self.client = client

    def from_(self, name):
        return FakeBucket(self.client, name)


class FakeRpc:
    def __init__(self, client, name, params):
        self.client = client
        self.name = name
        self.params = params

    def execute(self):
        self.client.rpcs.append((self.name, dict(self.params)))
        return FakeResponse([])


class FakeClient:
    def __init__(self):
        self.tables = {
            "rooms": [
                {"id": "r1", "name": "room1"},
                {"id": "r2", "name": "room2"},
            ],
            "profiles": [
                {"id": "u1", "username": "ahmad"},
                {"id": "u2", "username": "sara"},
            ],
            "room_members": [
                {"room_id": "r1", "user_id": "bot-1", "rank": "moderator", "is_present": True},
                {"room_id": "r1", "user_id": "u1", "rank": "member", "is_present": True},
                {"room_id": "r1", "user_id": "u2", "rank": "admin", "is_present": False},
            ],
        }
        self.inserted = {}
        self.calls = []
        self.rpcs = []
        self.uploads = []
        self.storage = FakeStorage(self)

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        return FakeRpc(self, name, params)


@pytest.fixture()
def api(tmp_path):
    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_key="sb_publishable_x",
        username="testbot",
        password="secret",
        owner_username="owner",
        data_dir=tmp_path,
        assets_dir=tmp_path,
    )
    instance = GiantApi(settings, logging.getLogger("test.giant"))
    instance.client = FakeClient()
    instance.user_id = "bot-1"
    instance.username = "testbot"
    instance._run = lambda operation, retry_auth=True: operation()
    instance.refresh_rooms()
    return instance


def test_send_room_text_inserts_giant_row(api):
    assert api.send_room_text("room1", "مرحبا") is True
    row = api.client.inserted["room_messages"][0]
    assert row == {"room_id": "r1", "user_id": "bot-1", "content": "مرحبا", "message_type": "text"}


def test_send_room_media_uploads_local_file(api, tmp_path):
    image = tmp_path / "card.png"
    image.write_bytes(b"fake-image-bytes")
    ok = api.send_room_media("room1", "وصف", str(image), "image")
    assert ok is True
    bucket, path, data, _options = api.client.uploads[0]
    assert bucket == "bot-publish"
    assert data == b"fake-image-bytes"
    row = api.client.inserted["room_messages"][0]
    assert row["media_url"].startswith("https://storage.example/bot-publish/")


def test_send_dm_builds_relay_envelope(api):
    assert api.send_dm("ahmad", "نقاطي") is True
    row = api.client.inserted["dm_relay"][0]
    assert row["sender_id"] == "bot-1"
    assert row["recipient_id"] == "u1"
    assert row["envelope"]["content"] == "نقاطي"
    assert row["envelope"]["message_type"] == "text"


def test_moderation_allows_lower_rank(api):
    ok, detail = api.moderate("kick", "room1", "ahmad")
    assert ok is True
    assert api.client.rpcs == [("kick_room_member", {"_room": "r1", "_user": "u1"})]


def test_moderation_denies_equal_or_higher_rank(api):
    ok, detail = api.moderate("kick", "room1", "sara")
    assert ok is False
    assert "أعلى" in detail
    assert api.client.rpcs == []


def test_moderation_denies_when_bot_is_member(api):
    api.client.tables["room_members"][0]["rank"] = "member"
    ok, detail = api.moderate("kick", "room1", "ahmad")
    assert ok is False
    assert api.client.rpcs == []


def test_join_room_uses_configured_password(api):
    api.settings.room_password = "pw"
    ok, name = api.join_room("room2")
    assert ok is True and name == "room2"
    assert api.client.rpcs[0] == ("room_join", {"_room": "r2", "_password": "pw"})


def test_members_skip_bot_and_include_roles(api):
    members = api.members("room1")
    names = {m["username"]: m["role"] for m in members}
    assert names == {"ahmad": "member", "sara": "admin"}
