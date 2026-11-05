import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from talkin_social_domain import SocialReplyEngine, normalize_trigger


class NoShuffle:
    def shuffle(self, values):
        # Keep insertion order, making each cycle deterministic.
        return None

    def choice(self, values):
        return values[0]


def test_normalized_exact_match_and_commands_are_never_stolen():
    engine = SocialReplyEngine(random_source=NoShuffle(), clock=lambda: 123.0)
    engine.set_auto_replies("أهلا، كيف حالك؟", "{username} في {room}")

    hit = engine.match_message("North", "اهلا كيف حالك", "@Nora")
    assert hit == {
        "kind": "auto_reply", "matched": True, "consumed": True,
        "trigger": "اهلا كيف حالك", "reply": "Nora في North",
    }
    assert normalize_trigger("  أَهْلًا؟  ") == "اَهْلًا"
    assert engine.match_message("North", "اهلا كيف حالك اليوم", "Nora")["kind"] == "none"

    # These are representative command prefixes/exacts from Talkin2; even a
    # configured exact auto-reply trigger must not consume a command.
    for command in ("roll", "publish@news", "+sr@hello@reply", "بنك", "welcome@text"):
        engine.set_auto_replies(command, "must not run")
        outcome = engine.match_message("North", command, "Nora")
        assert outcome["kind"] == "none"
        assert outcome["consumed"] is False


def test_multi_reply_cycle_uses_every_variant_and_is_room_scoped():
    now = iter([10.0, 11.0, 12.0, 13.0, 14.0])
    engine = SocialReplyEngine(random_source=NoShuffle(), clock=lambda: next(now))
    engine.set_auto_replies("hello", ["one", "two", "three"])

    north = [engine.match_message("North", "hello", "Nora")["reply"] for _ in range(3)]
    assert north == ["Nora one", "Nora two", "Nora three"]
    # The independent South room starts at the start of the same cycle.
    assert engine.match_message("South", "hello", "Sam")["reply"] == "Sam one"
    # Exhausted room starts a new cycle; only the full-cycle guarantee is made.
    assert engine.match_message("North", "hello", "Nora")["reply"] == "Nora one"

    serialized = json.dumps(engine.to_state(), ensure_ascii=False)
    restored = SocialReplyEngine.from_state(json.loads(serialized), random_source=NoShuffle(), clock=lambda: 15.0)
    assert restored.match_message("South", "hello", "Sam")["reply"] == "Sam two"


def test_welcome_templates_and_enabled_flag():
    engine = SocialReplyEngine()
    engine.set_welcome("@Nora", "Welcome @{username} to {room}!")
    assert engine.render_welcome("Nora", "North") == "Welcome Nora to North!"
    assert engine.render_welcome("unknown", "North") is None
    engine.set_welcomes_enabled(False)
    assert engine.render_welcome("Nora", "North") is None
    state = engine.to_state()
    assert state["welcomes"]["nora"] == "Welcome @{username} to {room}!"


def test_departure_keys_use_dedicated_replies_not_auto_replies():
    engine = SocialReplyEngine(random_source=NoShuffle())
    engine.set_auto_replies("ق", "automatic must not run")
    engine.set_departure_replies("ق", ["وداع يا {username} في {room}", "بديل"])

    no_departed_user = engine.match_message("North", "ق", "Nora")
    assert no_departed_user == {
        "kind": "departure", "matched": True, "consumed": True,
        "trigger": "ق", "reply": None,
    }
    departure = engine.match_message("North", "ق", "Nora", departed_username="LeftUser")
    assert departure["kind"] == "departure"
    assert departure["reply"] == "وداع يا LeftUser في North"

    # A multi-character trigger remains an automatic reply, and it is still
    # independent of the departure-reply configuration.
    engine.set_auto_replies("goodbye", ["automatic"])
    assert engine.match_message("North", "goodbye", "Nora")["kind"] == "auto_reply"


def test_default_and_toggleable_generic_farewell_are_separate_from_letter_overrides():
    engine = SocialReplyEngine(random_source=NoShuffle())
    engine.set_farewell_enabled(False)
    assert engine.departure_reply("ق", "Left", "North") == "👤 Left ❤️ بقلبي"
    assert engine.departure_reply("x", "Left", "North") == ""
    engine.set_departure_replies("x", "Goodbye {username}")
    assert engine.departure_reply("x", "Left", "North") == "Goodbye Left"
