import unittest

from auth_helpers import create_supabase_client, safe_auth_error


class FakeOptions:
    def __init__(self):
        self.headers = {"Authorization": "Bearer placeholder", "apiKey": "placeholder"}


class FakeClient:
    def __init__(self):
        self.options = FakeOptions()
        self.supabase_key = None


class AuthHelperTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def factory(url, key):
            self.calls.append((url, key))
            return FakeClient()

        self.factory = factory

    def test_publishable_key_replaces_placeholder_header(self):
        key = "sb_publishable_test-value"
        client = create_supabase_client(" https://example.supabase.co ", key, self.factory)
        self.assertEqual(self.calls, [("https://example.supabase.co", "a.b.c")])
        self.assertEqual(client.supabase_key, key)
        self.assertEqual(client.options.headers["apiKey"], key)
        self.assertNotIn("Authorization", client.options.headers)

    def test_secret_key_is_supported_for_server_client(self):
        key = "sb_secret_test-value"
        client = create_supabase_client("https://example.supabase.co", key, self.factory)
        self.assertEqual(client.supabase_key, key)
        self.assertEqual(client.options.headers["apiKey"], key)

    def test_legacy_jwt_passes_through(self):
        key = "eyJheader.payload.signature"
        client = create_supabase_client("https://example.supabase.co", key, self.factory)
        self.assertEqual(self.calls, [("https://example.supabase.co", key)])
        self.assertIsInstance(client, FakeClient)

    def test_rejects_unknown_short_key_without_echoing_it(self):
        key = "not-a-valid-supabase-key"
        with self.assertRaisesRegex(ValueError, "unsupported format") as error:
            create_supabase_client("https://example.supabase.co", key, self.factory)
        self.assertNotIn(key, str(error.exception))

    def test_auth_error_redacts_password_and_bearer_token(self):
        error = "Invalid login for password=hunter2; Bearer abc.def.ghi"
        result = safe_auth_error(error, ("hunter2",))
        self.assertNotIn("hunter2", result)
        self.assertNotIn("abc.def.ghi", result)
        self.assertIn("Invalid login", result)


if __name__ == "__main__":
    unittest.main()
