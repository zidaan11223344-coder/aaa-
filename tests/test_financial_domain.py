import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from financial_domain import FinancialDomain


def wallet(**balances):
    return {"balances": balances}


def test_transfer_is_atomic_idempotent_and_cannot_overdraw():
    state = wallet(alice=100, bob=5)
    domain = FinancialDomain(state)

    first = domain.transfer("alice", "bob", 40, "transfer-1")
    assert first["ok"] is True
    assert first["balances"] == {"alice": 60, "bob": 45}
    assert state["balances"] == {"alice": 60, "bob": 45}

    replay = domain.transfer("alice", "bob", 40, "transfer-1")
    assert replay["ok"] is True and replay["already_applied"] is True
    assert state["balances"] == {"alice": 60, "bob": 45}

    conflict = domain.transfer("alice", "bob", 41, "transfer-1")
    assert conflict == {"ok": False, "error": "transaction_conflict"}
    insufficient = domain.transfer("alice", "bob", 61, "transfer-2")
    assert insufficient["ok"] is False and insufficient["error"] == "insufficient"
    assert state["balances"] == {"alice": 60, "bob": 45}


def test_giant_uuid_callback_receives_atomic_deltas_and_stable_id():
    calls = []

    def giant_apply(deltas, transaction_id, reason):
        calls.append((deltas, transaction_id, reason))
        return {"ok": True, "balances": {"uuid-a": 30, "uuid-b": 70}}

    domain = FinancialDomain(balance_callback=giant_apply)
    result = domain.transfer("uuid-a", "uuid-b", 20, "giant-tx-7")
    assert result["ok"] is True
    assert calls == [({"uuid-a": -20, "uuid-b": 20}, "giant-tx-7", "point_transfer")]
    assert domain.transfer("uuid-a", "uuid-b", 20, "giant-tx-7")["already_applied"] is True
    assert len(calls) == 1


def test_wager_reservation_settlement_and_expired_refund_are_idempotent():
    state = wallet(player=500)
    domain = FinancialDomain(state)
    reserved = domain.reserve_wager("player", 125, "wager-a", now=1000)
    assert reserved["ok"] is True
    assert state["balances"]["player"] == 375
    assert reserved["wager"]["expires_at"] == 1120
    assert domain.settle_wager("wager-a", 200, now=1119)["ok"] is True
    assert state["balances"]["player"] == 575
    assert domain.settle_wager("wager-a", 200, now=1119)["already_applied"] is True
    assert domain.refund_wager("wager-a", now=1200)["error"] == "wager_closed"

    domain.reserve_wager("player", 75, "wager-b", now=1000)
    assert domain.settle_wager("wager-b", 150, now=1120)["error"] == "wager_expired"
    expired = domain.expire_wagers(now=1120)
    assert len(expired) == 1 and expired[0]["ok"] is True
    assert domain.expire_wagers(now=1121) == []
    assert state["balances"]["player"] == 575
    assert state["wagers"]["wager-b"]["status"] == "refunded"


def test_wager_cannot_refund_before_deadline_unless_explicit_force():
    state = wallet(player=300)
    domain = FinancialDomain(state)
    domain.reserve_wager("player", 50, "wager-c", now=10)
    assert domain.refund_wager("wager-c", now=129)["error"] == "wager_not_expired"
    assert domain.refund_wager("wager-c", now=20, force=True)["ok"] is True
    assert state["balances"]["player"] == 300


def test_investment_limits_debit_and_delayed_profit_settlement():
    state = wallet(investor=50_000_000_000)
    domain = FinancialDomain(state)
    assert domain.invest("investor", "gold", 9, "too-small", now=100)["error"] == "below_minimum"
    assert domain.invest("investor", "gold", 30_000_000_001, "too-large", now=100)["error"] == "above_maximum"
    assert state["balances"]["investor"] == 50_000_000_000

    opened = domain.invest("investor", "gold", 30_000_000_000, "investment-a", now=100)
    assert opened["ok"] is True
    assert opened["investment"]["due_at"] == 160
    assert state["balances"]["investor"] == 20_000_000_000
    assert domain.settle_investment("investment-a", "profit", 25, now=159)["error"] == "investment_not_due"
    result = domain.settle_investment("investment-a", "profit", 25, now=160)
    assert result["ok"] is True
    assert result["investment"]["payout"] == 37_500_000_000
    assert result["investment"]["net_change"] == 7_500_000_000
    assert state["balances"]["investor"] == 57_500_000_000
    assert domain.settle_investment("investment-a", "profit", 25, now=170)["already_applied"] is True


def test_investment_loss_and_settlement_outcome_cannot_be_changed_on_retry():
    state = wallet(investor=1_000)
    domain = FinancialDomain(state)
    domain.invest("investor", 3, 101, "investment-loss", now=0)
    result = domain.settle_investment("investment-loss", "loss", 50, now=60)
    assert result["ok"] is True
    assert result["investment"]["payout"] == 51
    assert result["investment"]["net_change"] == -50
    assert state["balances"]["investor"] == 950
    assert domain.settle_investment("investment-loss", "profit", 50, now=60)["error"] == "investment_conflict"


def test_state_is_json_serializable_and_restart_safe():
    state = wallet(user=1000)
    domain = FinancialDomain(state)
    domain.reserve_wager("user", 100, "persistent-wager", now=50)
    domain.invest("user", 10, 200, "persistent-investment", now=50)
    serialized = json.dumps(state)
    restored_state = json.loads(serialized)
    restarted = FinancialDomain(restored_state)
    assert restarted.state["balances"]["user"] == 700
    assert restarted.state["wagers"]["persistent-wager"]["status"] == "reserved"
    assert restarted.state["investments"]["persistent-investment"]["due_at"] == 110
    assert restarted.expire_wagers(now=170)[0]["ok"] is True
    assert restarted.state["balances"]["user"] == 800


def test_invalid_operations_do_not_change_balances():
    state = wallet(alice=20)
    domain = FinancialDomain(state)
    assert domain.transfer("alice", "alice", 1, "self")["error"] == "same_account"
    assert domain.reserve_wager("alice", 0, "zero")["error"] == "invalid_amount"
    assert domain.invest("alice", "", 10, "bad-asset")["error"] == "invalid_asset"
    assert domain.settle_investment("missing", "loss", 2)["error"] == "investment_not_found"
    assert state["balances"] == {"alice": 20}


def test_external_callback_failure_does_not_record_local_success():
    calls = []

    def failing_callback(deltas, transaction_id, reason):
        calls.append(transaction_id)
        return {"ok": False, "error": "insufficient"}

    state = {"balances": {}}
    domain = FinancialDomain(state, balance_callback=failing_callback)
    result = domain.transfer("sender-uuid", "recipient-uuid", 10, "failed-transfer")
    assert result["ok"] is False
    assert "failed-transfer" not in state["transactions"]
    assert calls == ["failed-transfer"]
