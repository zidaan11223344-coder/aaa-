from pathlib import Path

from rare_diamond_event import RareDiamondEvent


class Clock:
    def __init__(self, now=1000):
        self.now = now

    def __call__(self):
        return self.now


def test_five_unique_players_resolve_with_stable_point_transactions(tmp_path: Path):
    clock = Clock()
    game = RareDiamondEvent(tmp_path / "rare.json", clock=clock, chooser=lambda players: players[0])
    opened = game.open_if_due(["room-a", "room-b"])
    assert opened and opened["required_players"] == 5
    payouts = []

    for i in range(5):
        room = "room-a" if i < 3 else "room-b"
        result = game.join(f"uid-{i}", f"player{i}", room)
        assert result["ok"]
        if i == 4:
            assert result["ready"]

    def apply_delta(uid, username, amount, txid, reason):
        payouts.append((uid, username, amount, txid, reason))
        return {"ok": True}

    result = game.resolve(apply_delta)
    assert result["ok"] and result["result"]["winner"] == "player0"
    assert len(payouts) == 5
    assert payouts[0][2] == 100_000_000
    assert all(row[2] == -10_000 for row in payouts[1:])
    assert len({row[3] for row in payouts}) == 5
    assert game.resolve(apply_delta) is None
    assert len(payouts) == 5


def test_duplicate_identity_and_wrong_room_are_rejected(tmp_path: Path):
    game = RareDiamondEvent(tmp_path / "rare.json", clock=Clock())
    game.open_if_due(["room-a"])
    assert game.join("uid-1", "one", "room-a")["ok"]
    assert game.join("uid-1", "renamed", "room-a")["already_joined"]
    assert game.join("uid-2", "two", "room-b")["error"] == "wrong_room"


def test_incomplete_round_expires_without_payout_and_starts_cooldown(tmp_path: Path):
    clock = Clock()
    game = RareDiamondEvent(tmp_path / "rare.json", clock=clock)
    game.open_if_due(["room-a"])
    game.join("uid-1", "one", "room-a")
    clock.now += 180
    result = game.expire_if_due()
    assert result["status"] == "incomplete"
    assert game.active() is None
    assert game.open_if_due(["room-a"]) is None
    clock.now += 180
    assert game.open_if_due(["room-a"]) is not None


def test_interrupted_payout_retries_with_same_transaction_id(tmp_path: Path):
    clock = Clock()
    game = RareDiamondEvent(tmp_path / "rare.json", clock=clock, chooser=lambda players: players[0])
    game.open_if_due(["room-a"])
    for i in range(5):
        game.join(f"uid-{i}", f"player{i}", "room-a")
    attempts = []

    def flaky(uid, username, amount, txid, reason):
        attempts.append(txid)
        if len(attempts) == 2:
            return {"ok": False, "error": "temporary"}
        return {"ok": True}

    first = game.resolve(flaky)
    assert first["pending"]
    pending = game.active()
    assert pending["phase"] == "settling"
    expected = {p["transaction_id"] for p in pending["payouts"]}
    second = game.resolve(lambda uid, username, amount, txid, reason: {"ok": True})
    assert second["ok"]
    assert set(attempts).issubset(expected)
