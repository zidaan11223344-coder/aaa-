import unittest

import game_engine


class GameEngineTests(unittest.TestCase):
    def setUp(self):
        game_engine.SESSIONS.clear()

    def tearDown(self):
        game_engine.SESSIONS.clear()

    def test_quiz_rewards_first_correct_answer(self):
        game = {
            "command": "quiz-test",
            "title": "اختبار",
            "game_type": "quiz",
            "questions": [{"q": "عاصمة اليمن؟", "a": "صنعاء"}],
            "win_points": 25,
            "timeout_seconds": 60,
        }
        intro, session = game_engine.start_session("room-1", game, "starter", "المضيف")
        self.assertIn("عاصمة اليمن", intro)
        result = game_engine.handle_message("room-1", "winner", "الفائز", "صنعاء")
        self.assertTrue(result["finished"])
        self.assertEqual(result["winner"], ("winner", "الفائز", 25))
        self.assertIsNone(game_engine.get_session("room-1"))

    def test_guess_game_returns_hint_before_winner(self):
        game = {"command": "guess-test", "game_type": "guess", "max_number": 5}
        intro, session = game_engine.start_session("room-2", game, "starter", "المضيف")
        secret = session["secret"]
        wrong = secret % 5 + 1
        result = game_engine.handle_message("room-2", "player", "لاعب", str(wrong))
        self.assertFalse(result["finished"])
        self.assertIn("أكبر" if wrong < secret else "أصغر", result["text"])

    def test_duel_awards_winner_and_timeout_cleans_session(self):
        game = {"command": "duel-test", "game_type": "duel", "timeout_seconds": 15}
        intro, session = game_engine.start_session("room-3", game, "starter", "المضيف")
        result = game_engine.handle_message("room-3", "challenger", "خصم", "انضم")
        self.assertTrue(result["finished"])
        self.assertIn("winner", result)
        self.assertIsNone(game_engine.get_session("room-3"))


if __name__ == "__main__":
    unittest.main()
