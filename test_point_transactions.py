import unittest

from point_transactions import apply_delta_once


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


if __name__ == "__main__":
    unittest.main()
