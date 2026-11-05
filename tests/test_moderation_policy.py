import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moderation_policy import ModerationEngine, RoomPolicy, normalize_arabic


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_arabic_normalization_removes_marks_tatweel_and_spaces_and_normalizes_alef():
    assert normalize_arabic("  أَلسّــــلامُ عَلَيْكُم  ") == "السلامعليكم"
    assert normalize_arabic("فَتًى") == "فتي"
    assert normalize_arabic("ٱلإِبْرَاهِيم") == "الابراهيم"


def test_room_policies_are_independent_and_clamp_repeat_thresholds():
    engine = ModerationEngine()
    first = engine.set_room_policy("North", {"swear": True, "flood": True, "repeat_limit": 1})
    second = engine.set_room_policy("South", RoomPolicy(flood=True, repeat_limit=99))

    assert first.repeat_limit == 2
    assert second.repeat_limit == 50
    assert engine.get_room_policy(" north ").swear is True
    assert engine.get_room_policy("South").swear is False
    assert engine.get_room_policy("Unknown").repeat_limit == 11


def test_repeat_threshold_warns_then_returns_structured_decision_without_action():
    clock = FakeClock()
    engine = ModerationEngine(
        {"Hall": {"flood": True, "repeat_limit": 3}}, clock=clock
    )

    first = engine.evaluate("Hall", "sam", "مرحبا", role="member")
    clock.advance(1)
    second = engine.evaluate("Hall", "sam", "مَرْحَبا", role="member")
    clock.advance(1)
    third = engine.evaluate("Hall", "sam", "مرحبا", role="member")

    assert first.reason == "no_violation"
    assert second.reason == "repeat_warning"
    assert second.recommendation == "warn"
    assert second.sender_count == 2
    assert third.reason == "repeated_sender"
    assert third.recommendation == "ban"
    assert third.allowed is False
    assert third.action_taken is False
    assert third.sender_count == 3
    assert third.window_seconds == 30.0


def test_same_text_rolling_threshold_counts_distinct_senders_and_window_expiry():
    clock = FakeClock()
    engine = ModerationEngine(
        {"Hall": {"flood": True, "repeat_limit": 3}}, clock=clock
    )

    assert engine.evaluate("Hall", "one", "نفس النص", role="member").reason == "no_violation"
    clock.advance(1)
    assert engine.evaluate("Hall", "two", "نفس النص", role="member").reason == "repeat_warning"
    clock.advance(1)
    hit = engine.evaluate("Hall", "three", "نفس النص", role="member")
    assert hit.reason == "repeated_text"
    assert hit.text_count == 3

    clock.advance(31)
    expired = engine.evaluate("Hall", "one", "نفس النص", role="member")
    assert expired.reason == "no_violation"
    assert expired.text_count == 1


def test_word_filter_uses_room_toggle_and_global_plus_room_words():
    engine = ModerationEngine(
        {"Enabled": {"swear": True, "words": ["كلمة غرفة"]},
         "Disabled": {"swear": False, "words": ["ممنوع"]}},
        global_words=["بَذيء"],
    )

    global_hit = engine.evaluate("Enabled", "guest", "هذا بذيء", role="member")
    local_hit = engine.evaluate("Enabled", "guest", "كلمةغرفة هنا", role="member")
    disabled = engine.evaluate("Disabled", "guest", "ممنوع", role="member")

    assert global_hit.reason == "blocked_word"
    assert global_hit.matched_word == "بَذيء"
    assert local_hit.reason == "blocked_word"
    assert local_hit.matched_word == "كلمة غرفة"
    assert disabled.allowed is True


def test_owner_admin_master_and_creator_exemptions_follow_policy_scope():
    engine = ModerationEngine(
        {"Hall": {"flood": True, "swear": True, "repeat_limit": 2}},
        global_words=["سيء"],
    )

    # Flood policy excludes owners/admins, the configured master, and creator.
    for name, kwargs in (
        ("owner", {"role": "owner"}),
        ("admin", {"role": "admin"}),
        ("master", {"role": "member", "is_master": True}),
        ("creator", {"role": "member", "is_room_creator": True}),
    ):
        first = engine.evaluate("Hall", name, "spam", **kwargs)
        second = engine.evaluate("Hall", name, "spam", **kwargs)
        assert first.exempt and second.exempt
        assert second.action_taken is False

    # Talkin2's word-filter path exempts master/bot and explicit filter
    # exceptions, while room rank alone does not suppress word matching.
    assert engine.evaluate("Hall", "owner", "سيء", role="owner").reason == "blocked_word"
    assert engine.evaluate("Hall", "master", "سيء", role="member", is_master=True).reason == "exempt"
    word_only = ModerationEngine(
        {"Hall": {"swear": True}}, global_words=["سيء"]
    )
    assert word_only.evaluate(
        "Hall", "reporter", "سيء", role="member", filter_exception=True
    ).reason == "exempt"


def test_unknown_role_does_not_enter_flood_counters():
    engine = ModerationEngine({"Hall": {"flood": True, "repeat_limit": 2}})
    result = engine.evaluate("Hall", "guest", "same", role=None)
    assert result.allowed is True
    assert result.reason == "no_violation"
    assert result.action_taken is False
