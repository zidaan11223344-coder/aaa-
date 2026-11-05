import uuid

import pytest

from treasure_rounds import (
    GAME_ORDER,
    INTER_ROUND_BREAK_SECONDS,
    LOSER_DEDUCTION,
    REGISTRATION_SECONDS,
    TIMED_TREASURE_GAMES,
    RoundError,
    TreasureRoundEngine,
)


class Clock:
    def __init__(self, value=1_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FirstChoice:
    def choice(self, choices):
        return choices[0]


def room_ids(count=2):
    return [str(uuid.uuid4()) for _ in range(count)]


def fill_round(engine, round_data, room_list, prefix="p"):
    for index in range(round_data["required_players"]):
        result = engine.join(round_data["id"], room_list[index % len(room_list)], f"{prefix}{index}")
        assert result["accepted"] is True
    return engine.get_round(round_data["id"])


def test_cycle_order_counts_cross_room_uuid_joins_and_rng_selected_winner():
    clock = Clock()
    engine = TreasureRoundEngine(clock=clock, rng=FirstChoice())
    rooms = room_ids()
    observed_order = []

    for game_no, expected_key in enumerate(GAME_ORDER):
        round_data = engine.start_round(rooms)
        assert round_data["game_key"] == expected_key
        assert round_data["ends_at"] == clock() + REGISTRATION_SECONDS
        observed_order.append(round_data["game_key"])
        completed = fill_round(engine, round_data, rooms, prefix=f"round{game_no}-")
        config = TIMED_TREASURE_GAMES[expected_key]
        assert completed["status"] == "resolved"
        assert len(completed["players"]) == config["players"]
        assert completed["winner"] == f"round{game_no}-0"
        assert engine.next_due == clock() + INTER_ROUND_BREAK_SECONDS
        clock.value = engine.next_due

    assert observed_order == list(GAME_ORDER)
    assert len(GAME_ORDER) == 9


def test_duplicate_player_is_counted_once_across_rooms_and_completed_once():
    clock = Clock()
    engine = TreasureRoundEngine(clock=clock, rng=FirstChoice())
    rooms = room_ids()
    round_data = engine.start_round(rooms, "rare_diamond")

    first = engine.join(round_data["id"], rooms[0], "  @Alice  ")
    duplicate = engine.join(round_data["id"], rooms[1], "alice")
    assert first["accepted"] is True
    assert duplicate["accepted"] is False
    assert duplicate["reason"] == "duplicate"
    assert duplicate["count"] == 1

    for name in ("Bob", "Cora", "Dan"):
        engine.join(round_data["id"], rooms[1], name)
    filled = engine.join(round_data["id"], rooms[0], "Eve")
    assert filled["reason"] == "roster_filled"
    assert filled["round"]["status"] == "resolved"
    assert engine.active_round_id is None
    intents = engine.pending_settlements(round_data["id"])
    assert len(intents) == 5
    assert sum(intent["delta"] > 0 for intent in intents) == 1
    assert sum(intent["delta"] == -LOSER_DEDUCTION for intent in intents) == 4
    assert len({intent["idempotency_key"] for intent in intents}) == 5


def test_only_registered_rooms_can_join_and_unknown_game_or_non_uuid_round_is_rejected():
    clock = Clock()
    engine = TreasureRoundEngine(clock=clock)
    allowed_room = room_ids(1)[0]
    round_data = engine.start_round([allowed_room], "gold_treasure")
    rejected = engine.join(round_data["id"], str(uuid.uuid4()), "Visitor")
    assert rejected["accepted"] is False
    assert rejected["reason"] == "room_not_in_round"
    with pytest.raises(RoundError):
        engine.join("not-a-uuid", allowed_room, "Visitor")
    with pytest.raises(RoundError):
        engine.start_round([allowed_room], "not-a-game")


def test_expiry_closes_round_and_enforces_break_before_next_registration():
    clock = Clock()
    engine = TreasureRoundEngine(clock=clock, rng=FirstChoice())
    rooms = room_ids(1)
    round_data = engine.start_round(rooms, "rare_diamond")
    clock.value = round_data["ends_at"]
    closed = engine.expire(round_data["id"])
    assert closed["status"] == "expired"
    assert closed["closed_at"] == clock()
    assert engine.next_due == clock() + INTER_ROUND_BREAK_SECONDS
    assert engine.start_round(rooms) is None
    clock.advance(INTER_ROUND_BREAK_SECONDS)
    assert engine.start_round(rooms)["game_key"] == "gold_treasure"


def test_settlement_intents_are_retryable_serializable_and_idempotently_acknowledged():
    clock = Clock()
    rooms = room_ids()
    engine = TreasureRoundEngine(clock=clock, rng=FirstChoice())
    round_data = engine.start_round(rooms, "rare_diamond")
    completed = fill_round(engine, round_data, rooms)
    pending = engine.pending_settlements(round_data["id"])
    assert len(pending) == 5
    failed = engine.acknowledge_settlement(pending[0]["idempotency_key"], success=False, error="temporary")
    assert failed["status"] == "pending"
    assert failed["last_error"] == "temporary"

    recovered = TreasureRoundEngine.recover(engine.export_state(), clock=clock, rng=FirstChoice())
    recovered_round = recovered.get_round(round_data["id"])
    assert recovered_round["winner"] == completed["winner"]
    assert recovered_round["players"] == completed["players"]
    assert len(recovered.pending_settlements(round_data["id"])) == 5
    applied = recovered.acknowledge_settlement(pending[0]["idempotency_key"], success=True, receipt={"tx": "external-1"})
    assert applied["status"] == "applied"
    assert recovered.acknowledge_settlement(pending[0]["idempotency_key"], success=False)["status"] == "applied"
    assert len(recovered.pending_settlements(round_data["id"])) == 4


def test_doctor_losers_are_banned_from_exactly_the_next_doctor_round_only():
    clock = Clock()
    engine = TreasureRoundEngine(clock=clock, rng=FirstChoice())
    rooms = room_ids()
    doctor_round = engine.start_round(rooms, "imperial_sickness")
    done = fill_round(engine, doctor_round, rooms, prefix="doc-")
    loser = next(player["name"] for key, player in done["players"].items() if player["name"] != done["winner"])
    winner = done["winner"]
    clock.value = engine.next_due

    next_doctor = engine.start_round(rooms, "imperial_sickness")
    assert engine.join(next_doctor["id"], rooms[0], winner)["accepted"] is True
    barred = engine.join(next_doctor["id"], rooms[0], loser)
    assert barred["accepted"] is False
    assert barred["reason"] == "doctor_round_ban"
    clock.value = next_doctor["ends_at"]
    engine.expire(next_doctor["id"])
    clock.value = engine.next_due
    another_doctor = engine.start_round(rooms, "imperial_sickness")
    allowed = engine.join(another_doctor["id"], rooms[0], loser)
    assert allowed["accepted"] is True


def test_round_state_can_be_restored_with_active_uuid_round():
    clock = Clock(55.0)
    rooms = room_ids()
    first = TreasureRoundEngine(clock=clock)
    opened = first.start_round(rooms, "fortune_hunt")
    first.join(opened["id"], rooms[1], "Player")
    restored = TreasureRoundEngine.recover(first.export_state(), clock=clock)
    assert restored.active_round_id == opened["id"]
    again = restored.get_round(opened["id"])
    assert again["players"] == {"player": {"name": "Player", "room_id": rooms[1]}}
