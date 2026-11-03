from unittest.mock import patch

from auction_game import AuctionGame


class Clock:
    def __init__(self, now=1000):
        self.now = now

    def __call__(self):
        return self.now


def test_auction_settlement_and_redemption_use_stable_giant_user_id(tmp_path):
    clock = Clock()
    game = AuctionGame(tmp_path / "auction.json", chooser=lambda items: items[0], clock=clock)
    game.set_enabled(True)
    opened = game.open_auction("uuid-opener", "opener", "room-a")
    assert opened["ok"]
    for uid, name in (("uuid-opener", "opener"), ("uuid-two", "two"), ("uuid-three", "three")):
        assert game.join(uid, name, "room-a")["ok"]
    started = game.start("uuid-opener", "opener")
    assert started["ok"]

    active = game.active()
    turn_id = active["turn_user_id"]
    turn_name = active["turn_username"]
    charges = []

    def apply_delta(uid, username, amount, transaction_id, reason):
        charges.append((uid, username, amount, transaction_id, reason))
        return {"ok": True}

    bid = game.bid(turn_id, turn_name, 100_000_000, 1_000_000_000)
    assert bid["ok"]
    close = game.close("uuid-opener", "opener", apply_delta=apply_delta)
    assert close["ok"] and close["status"] == "sold"
    sold = close["result"]
    assert charges[0][0] == sold["winner_id"]
    assert charges[0][2] == -100_000_000
    assert charges[0][3] == f"auction:{sold['auction_id']}:winning-bid"

    inventory = game.inventory(sold["winner_id"], sold["winner"])
    assert len(inventory) == 1
    with patch("auction_game.random.random", return_value=0.0):
        redeemed = game.redeem(sold["winner_id"], sold["winner"], inventory[0]["id"], apply_delta)
    assert redeemed["ok"] and redeemed["authentic"]
    assert charges[-1][0] == sold["winner_id"]
    assert charges[-1][2] == redeemed["points"]
    assert charges[-1][3] == f"auction-redemption:{inventory[0]['id']}"
    assert game.inventory(sold["winner_id"], sold["winner"]) == []


def test_auction_debit_insufficient_balance_prevents_sale(tmp_path):
    clock = Clock()
    game = AuctionGame(tmp_path / "auction.json", chooser=lambda items: items[0], clock=clock)
    game.set_enabled(True)
    game.open_auction("uid1", "one", "room")
    for uid, name in (("uid1", "one"), ("uid2", "two"), ("uid3", "three")):
        game.join(uid, name, "room")
    game.start("uid1", "one")
    active = game.active()
    game.bid(active["turn_user_id"], active["turn_username"], 100_000_000, 1_000_000_000)
    result = game.close("uid1", "one", apply_delta=lambda *_: {"ok": False, "error": "insufficient"})
    assert result["ok"] and result["status"] == "no_sale"
