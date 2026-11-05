import unittest

from point_transactions import apply_delta_once, apply_deltas_once


class PointTransactionTests(unittest.TestCase):
    def test_retry_does_not_credit_twice(self):
        ledger = {"uuid-1": {"username": "Mina", "points": 100, "cooldowns": {}}}
        first, changed = apply_delta_once(ledger, "uuid-1", "Mina", 50, "mining-redemption:abc")
        retry, retry_changed = apply_delta_once(ledger, "uuid-1", "Mina", 50, "mining-redemption:abc")
        self.assertTrue(first["ok"])
        self.assertFalse(first["already_applied"])
        self.assertTrue(changed)
        self.assertTrue(retry["already_applied"])
        self.assertFalse(retry_changed)
        self.assertEqual(ledger["uuid-1"]["points"], 150)

    def test_debit_cannot_make_balance_negative(self):
        ledger = {"uuid-1": {"username": "Mina", "points": 40}}
        result, changed = apply_delta_once(ledger, "uuid-1", "Mina", -50, "auction:win")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "insufficient")
        self.assertFalse(changed)
        self.assertEqual(ledger["uuid-1"]["points"], 40)

    def test_distinct_user_transactions_do_not_share_balances(self):
        ledger = {}
        apply_delta_once(ledger, "uuid-1", "Same", 25, "tx-1")
        apply_delta_once(ledger, "uuid-2", "Same", 10, "tx-1")
        self.assertEqual(ledger["uuid-1"]["points"], 25)
        self.assertEqual(ledger["uuid-2"]["points"], 10)

    def test_multi_account_transfer_is_atomic_and_idempotent(self):
        ledger = {
            "sender": {"username": "A", "points": 100},
            "recipient": {"username": "B", "points": 5},
        }
        deltas = {"sender": -40, "recipient": 40}
        names = {"sender": "A", "recipient": "B"}
        first, changed = apply_deltas_once(ledger, deltas, names, "transfer:one")
        retry, retry_changed = apply_deltas_once(ledger, deltas, names, "transfer:one")
        self.assertTrue(first["ok"])
        self.assertTrue(changed)
        self.assertTrue(retry["already_applied"])
        self.assertFalse(retry_changed)
        self.assertEqual(ledger["sender"]["points"], 60)
        self.assertEqual(ledger["recipient"]["points"], 45)
        self.assertIn("transfer:one", ledger["sender"]["applied_point_transactions"])
        self.assertIn("transfer:one", ledger["recipient"]["applied_point_transactions"])

    def test_multi_account_transfer_rejects_insufficient_sender_without_mutation(self):
        ledger = {"sender": {"points": 10}, "recipient": {"points": 4}}
        result, changed = apply_deltas_once(
            ledger, {"sender": -11, "recipient": 11}, {"sender": "A", "recipient": "B"}, "transfer:bad"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "insufficient")
        self.assertFalse(changed)
        self.assertEqual(ledger["sender"]["points"], 10)
        self.assertEqual(ledger["recipient"]["points"], 4)

    def test_partial_receipt_is_not_reapplied(self):
        ledger = {
            "sender": {"points": 10, "applied_point_transactions": ["transfer:partial"]},
            "recipient": {"points": 4, "applied_point_transactions": []},
        }
        result, changed = apply_deltas_once(
            ledger, {"sender": -2, "recipient": 2}, {"sender": "A", "recipient": "B"}, "transfer:partial"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "partial_transaction_receipt")
        self.assertFalse(changed)
        self.assertEqual(ledger["sender"]["points"], 10)
        self.assertEqual(ledger["recipient"]["points"], 4)


if __name__ == "__main__":
    unittest.main()
