import pytest

from ludo_snake_engine import (
    BOT_PLAYER,
    LUDO_FINISH,
    SNAKE_FINISH,
    apply_action,
    current_player,
    expire_if_idle,
    from_json,
    new_ludo_game,
    new_snake_game,
    to_json,
)


def act(state, actor, command, now=0, **kwargs):
    return apply_action(state, actor, command, now=now, **kwargs)


def test_create_ludo_lobby_and_pick_multiplayer_capacity():
    game = new_ludo_game("id-l", "room-1", "Alice", now=10, language="en")
    assert game["status"] == "lobby"
    assert game["players"] == ["Alice"]
    assert game["tokens"] == {"Alice": 0}
    assert game["max_players"] == 0
    chosen = act(game, "Alice", "choose", now=11, count=3)
    assert chosen["ok"]
    assert chosen["state"]["max_players"] == 3
    assert chosen["state"]["status"] == "lobby"
    assert chosen["events"] == [{"type": "player_count_selected", "count": 3, "needed": 2}]
    assert game["max_players"] == 0  # Inputs are never mutated.


def test_ludo_multiplayer_join_turn_roll_and_win():
    game = new_ludo_game("id-l", "room-1", "Alice")
    game = act(game, "Alice", "choose", now=1, count=2)["state"]
    joined = act(game, "Bob", "join", now=2)
    assert joined["ok"]
    assert joined["state"]["status"] == "active"
    assert joined["state"]["tokens"] == {"Alice": 0, "Bob": 0}
    assert current_player(joined["state"]) == "Alice"

    wrong_turn = act(joined["state"], "Bob", "roll", now=3, roll=2)
    assert not wrong_turn["ok"] and wrong_turn["error"] == "not_your_turn"
    invalid_die = act(joined["state"], "Alice", "roll", now=3, roll=7)
    assert not invalid_die["ok"] and invalid_die["error"] == "invalid_roll"
    assert invalid_die["state"] == joined["state"]

    moved = act(joined["state"], "Alice", "roll", now=4, roll=6)
    assert moved["state"]["tokens"]["Alice"] == 6
    assert current_player(moved["state"]) == "Bob"
    win_state = moved["state"]
    win_state["tokens"]["Bob"] = LUDO_FINISH - 2
    winning = act(win_state, "Bob", "roll", now=5, roll=5)
    assert winning["ok"]
    assert winning["state"]["tokens"]["Bob"] == LUDO_FINISH
    assert winning["state"]["winner"] == "Bob"
    assert winning["state"]["status"] == "finished"
    assert {event["type"] for event in winning["events"]} == {"moved", "won"}
    assert act(winning["state"], "Alice", "roll", now=6, roll=1)["error"] == "game_over"


def test_ludo_requires_count_and_rejects_bad_capacity_and_non_creator():
    game = new_ludo_game("id-l", "room", "Alice")
    assert act(game, "Alice", "roll", now=1, roll=3)["error"] == "choose_player_count_first"
    for bad in (0, 5, "2", None, True):
        assert act(game, "Alice", "choose", now=1, count=bad)["error"] == "invalid_player_count"
    assert act(game, "Mallory", "choose", now=1, count=2)["error"] == "not_lobby_creator"


def test_ludo_room_joining_duplicate_and_full_lobby():
    game = new_ludo_game("id-l", "room", "A")
    game = act(game, "A", "choose", now=1, count=2)["state"]
    game = act(game, "B", "join", now=2)["state"]
    assert act(game, "B", "join", now=3)["error"] == "game_not_joinable"
    assert act(game, "C", "join", now=3)["error"] == "game_not_joinable"

    four = new_ludo_game("id-4", "room", "A")
    four = act(four, "A", "choose", now=1, count=4)["state"]
    for index, name in enumerate(("B", "C", "D"), 2):
        four = act(four, name, "join", now=index)["state"]
    assert four["players"] == ["A", "B", "C", "D"]
    assert four["status"] == "active"
    assert act(four, "E", "join", now=5)["error"] == "game_not_joinable"


def test_ludo_vs_bot_automatic_opponent_roll_and_human_turn():
    game = new_ludo_game("id-bot", "room", "A")
    started = act(game, "A", "choose", now=1, count=1)
    assert started["state"]["players"] == ["A", BOT_PLAYER]
    assert started["state"]["status"] == "active"
    assert act(started["state"], "A", "roll", now=2, roll=3)["error"] == "invalid_bot_roll"
    result = act(started["state"], "A", "roll", now=2, roll=3, bot_roll=4)
    assert result["state"]["tokens"] == {"A": 3, BOT_PLAYER: 4}
    assert current_player(result["state"]) == "A"
    assert [e["type"] for e in result["events"]] == ["moved", "bot_moved", "turn"]
    assert act(started["state"], "B", "join", now=3)["error"] == "game_not_joinable"


def test_create_snake_lobby_join_required_and_ladder_transition():
    game = new_snake_game("id-s", "room", "Alice", now=10)
    assert game["status"] == "lobby"
    assert game["positions"] == {"Alice": 1}
    assert act(game, "Alice", "roll", now=11, roll=1)["error"] == "waiting_for_players"
    joined = act(game, "Bob", "join", now=12)
    assert joined["ok"]
    assert joined["state"]["positions"] == {"Alice": 1, "Bob": 1}
    assert act(joined["state"], "Carol", "join", now=13)["error"] == "game_not_joinable"
    result = act(joined["state"], "Alice", "roll", now=14, roll=2)
    assert result["state"]["positions"]["Alice"] == 22  # square 3 ladder to 22
    assert result["events"][0] == {"type": "moved", "player": "Alice", "roll": 2,
                                   "from": 1, "landed": 3, "to": 22}
    assert current_player(result["state"]) == "Bob"
    assert act(result["state"], "Alice", "roll", now=15, roll=1)["error"] == "not_your_turn"


def test_snake_slide_invalid_die_and_winner_at_clamped_square_100():
    game = new_snake_game("id-s", "room", "Alice")
    game = act(game, "Bob", "join", now=1)["state"]
    game["positions"]["Alice"] = 16
    bad = act(game, "Alice", "roll", now=2, roll=0)
    assert bad["error"] == "invalid_roll"
    assert bad["state"] == game
    slide = act(game, "Alice", "roll", now=2, roll=1)
    assert slide["state"]["positions"]["Alice"] == 7  # square 17 snake to 7
    assert slide["events"][0]["landed"] == 17

    near_finish = slide["state"]
    near_finish["turn"] = 1
    near_finish["positions"]["Bob"] = 98
    win = act(near_finish, "Bob", "roll", now=3, roll=6)
    assert win["state"]["positions"]["Bob"] == SNAKE_FINISH
    assert win["state"]["status"] == "finished"
    assert win["state"]["winner"] == "Bob"
    assert win["events"][-1] == {"type": "won", "player": "Bob"}


def test_timeout_is_inclusive_at_120_seconds_and_does_not_mutate_source():
    game = new_snake_game("id-s", "room", "Alice", now=10)
    assert expire_if_idle(game, now=129.999)["status"] == "lobby"
    expired = act(game, "", "timeout", now=130)
    assert expired["ok"]
    assert expired["state"]["status"] == "timed_out"
    assert game["status"] == "lobby"
    assert act(expired["state"], "Bob", "join", now=131)["error"] == "game_over"
    assert act(game, "", "timeout", now=129)["error"] == "not_timed_out"


def test_join_and_roll_refresh_inactivity_deadline():
    game = new_snake_game("id-s", "room", "Alice", now=0)
    game = act(game, "Bob", "join", now=100)["state"]
    assert expire_if_idle(game, now=219.9)["status"] == "active"
    moved = act(game, "Alice", "roll", now=200, roll=1)["state"]
    assert moved["last_activity_at"] == 200
    assert expire_if_idle(moved, now=319.9)["status"] == "active"
    assert expire_if_idle(moved, now=320)["status"] == "timed_out"


def test_json_round_trip_for_both_game_types():
    ludo = new_ludo_game("l", "room", "A", now=1.25, language="en")
    ludo = act(ludo, "A", "choose", now=2, count=2)["state"]
    ludo = act(ludo, "B", "join", now=3)["state"]
    snake = new_snake_game("s", "room", "A", now=1)
    snake = act(snake, "B", "join", now=2)["state"]
    for state in (ludo, snake):
        encoded = to_json(state)
        decoded = from_json(encoded)
        assert decoded == state
        assert to_json(decoded) == encoded


def test_invalid_action_and_invalid_json_state_validation():
    game = new_ludo_game("id", "room", "A")
    assert act(game, "A", "roll dice", now=1, roll=2)["error"] == "invalid_command"
    with pytest.raises(ValueError):
        from_json('{"game_type":"other"}')
    with pytest.raises(ValueError):
        new_ludo_game("", "room", "A")
