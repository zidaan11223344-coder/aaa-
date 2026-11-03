from cricket_reward_outbox import CricketRewardOutbox


def test_enqueue_is_idempotent_and_pending_reward_survives_restart(tmp_path):
    path = tmp_path / "cricket-rewards.json"
    box = CricketRewardOutbox(path)
    box.record_identity("giant-uuid-1", "winner")
    assert box.enqueue("winner", 10_000, "cricket:match-1:winner:winner")["ok"]
    assert box.enqueue("winner", 10_000, "cricket:match-1:winner:winner")["already_queued"]

    restarted = CricketRewardOutbox(path)
    pending = restarted.pending()
    assert len(pending) == 1
    assert pending[0]["username"] == "winner"
    assert pending[0]["user_id"] == "giant-uuid-1"
    assert restarted.identity_for("winner")["user_id"] == "giant-uuid-1"
    assert pending[0]["amount"] == 10_000
    assert pending[0]["transaction_id"] == "cricket:match-1:winner:winner"
    assert restarted.complete(pending[0]["transaction_id"])
    assert restarted.pending() == []
