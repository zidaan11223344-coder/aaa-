import json
import threading

from treasure_mining import (
    COOLDOWN_SECONDS,
    DAILY_DIAMOND_SPAWNS,
    DAY_SECONDS,
    DIAMOND_KEYS,
    ITEMS,
    MiningStore,
    resolve_item_keys,
)


def build_store(path, outcomes, diamonds=(), spawn_times=None):
    queue = list(outcomes)
    diamond_queue = list(diamonds)

    def choose():
        return queue.pop(0) if queue else None

    def choose_diamond():
        return diamond_queue.pop(0) if diamond_queue else DIAMOND_KEYS[0]

    if spawn_times is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "version": 1,
                "players": {},
                "diamond_schedule": {
                    "cycle_end": max(spawn_times or [0]) + DAY_SECONDS,
                    "times": sorted(spawn_times),
                },
            }),
            encoding="utf-8",
        )
    return MiningStore(
        path,
        chooser=choose,
        diamond_chooser=choose_diamond,
        schedule_chooser=lambda start: [start + DAY_SECONDS] * DAILY_DIAMOND_SPAWNS,
    )


def test_dig_is_keyed_by_numeric_user_id_and_30_second_cooldown_persists(tmp_path):
    path = tmp_path / "mining_game.json"
    store = build_store(path, [None], diamonds=["diamond_blue"], spawn_times=[10_000])

    first = store.dig("3456782", "Ahmed", now=10_000)
    assert first["ok"] is True
    assert first["item_key"] == "diamond_blue"
    assert first["diamonds_found"] == 1
    assert store.inventory("3456782")["inventory"]["diamond_blue"] == 1

    # A changed display name still resolves to the same numeric-ID record.
    blocked = store.dig("3456782", "أحمد الجديد", now=10_020)
    assert blocked["error"] == "cooldown"
    assert blocked["remaining"] == 10
    assert store.inventory("3456782")["username"] == "Ahmed"

    second = store.dig("3456782", "أحمد الجديد", now=10_000 + COOLDOWN_SECONDS)
    assert second["ok"] is True
    assert second["item_key"] is None
    assert store.inventory("3456782")["username"] == "أحمد الجديد"
    assert store.inventory("3456782")["diamonds_found"] == 1


def test_distinct_numeric_ids_never_share_inventory_even_with_same_name(tmp_path):
    store = build_store(tmp_path / "mining_game.json", ["copper", "iron"])
    store.dig("10001", "SameName", now=20_000)
    store.dig("10002", "SameName", now=20_000)

    first = store.inventory("10001")["inventory"]
    second = store.inventory("10002")["inventory"]
    assert first["copper"] == 1 and first["iron"] == 0
    assert second["iron"] == 1 and second["copper"] == 0


def test_two_concurrent_attempts_commit_only_one_mine(tmp_path):
    store = build_store(tmp_path / "mining_game.json", ["ruby"])
    barrier = threading.Barrier(3)
    results = []

    def run():
        barrier.wait()
        results.append(store.dig("2222", "Player", now=30_000))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sum(bool(result.get("ok")) for result in results) == 1
    assert sum(result.get("error") == "cooldown" for result in results) == 1
    assert store.inventory("2222")["inventory"]["ruby"] == 1


def test_redemption_reservation_is_persisted_before_credit_and_restorable(tmp_path):
    path = tmp_path / "mining_game.json"
    store = build_store(
        path, ["copper"], diamonds=["diamond_royal"], spawn_times=[40_030]
    )
    store.dig("300", "Miner", now=40_000)
    store.dig("300", "Miner", now=40_000 + COOLDOWN_SECONDS)

    reserved = store.reserve_redemption("300", "Miner", DIAMOND_KEYS)
    assert reserved["ok"] is True
    assert reserved["pending"]["items"] == {"diamond_royal": 1}
    assert reserved["pending"]["points"] == ITEMS["diamond_royal"]["price"]
    assert store.inventory("300")["inventory"]["diamond_royal"] == 0

    # A restart still sees the pending transaction and will reuse its ID with
    # the idempotent points ledger rather than reserving/crediting it again.
    restarted = MiningStore(path)
    pending = restarted.pending_redemption("300")
    assert pending["id"] == reserved["pending"]["id"]
    assert restarted.complete_redemption("300", pending["id"]) is True
    assert restarted.pending_redemption("300") is None
    assert restarted.complete_redemption("300", pending["id"]) is False


def test_inventory_persistence_and_diamond_leaderboard(tmp_path):
    path = tmp_path / "mining_game.json"
    store = build_store(
        path,
        ["iron"],
        diamonds=["diamond_clear", "diamond_blue"],
        spawn_times=[50_000, 50_000 + COOLDOWN_SECONDS],
    )
    store.dig("9", "First", now=50_000)
    store.dig("9", "First", now=50_000 + COOLDOWN_SECONDS)
    store.dig("12", "Second", now=50_000)

    restarted = MiningStore(path)
    assert restarted.inventory("9")["diamonds_found"] == 2
    assert restarted.leaderboard(10)[0]["user_id"] == "9"
    assert restarted.leaderboard(10)[0]["diamonds_found"] == 2


def test_each_daily_spawn_is_claimed_by_only_the_first_miner(tmp_path):
    path = tmp_path / "mining_game.json"
    start = 70_000
    store = MiningStore(
        path,
        chooser=lambda: "copper",
        diamond_chooser=lambda: "diamond_red",
        schedule_chooser=lambda moment: [moment + index * 100 for index in range(1, DAILY_DIAMOND_SPAWNS + 1)],
    )
    before_spawn = store.dig("101", "First", now=start)
    assert before_spawn["item_key"] == "copper"
    state = json.loads(path.read_text(encoding="utf-8"))
    assert len(state["diamond_schedule"]["times"]) == 15
    assert state["diamond_schedule"]["times"] == sorted(state["diamond_schedule"]["times"])

    first_after_spawn = store.dig("101", "First", now=start + 100)
    another_miner = store.dig("202", "Second", now=start + 100)
    assert first_after_spawn["item_key"] == "diamond_red"
    assert another_miner["item_key"] == "copper"
    assert store.inventory("101")["diamonds_found"] == 1
    assert store.inventory("202")["diamonds_found"] == 0


def test_master_test_diamond_rotates_and_is_saved_to_inventory(tmp_path):
    store = MiningStore(tmp_path / "mining_game.json")
    first = store.give_test_diamond("88", "Master")
    second = store.give_test_diamond("88", "Master")

    assert first["item_key"] == DIAMOND_KEYS[0]
    assert second["item_key"] == DIAMOND_KEYS[1]
    inventory = store.inventory("88")["inventory"]
    assert inventory[DIAMOND_KEYS[0]] == 1
    assert inventory[DIAMOND_KEYS[1]] == 1
    assert store.inventory("88")["diamonds_found"] == 2


def test_item_aliases_cover_redeem_commands():
    assert resolve_item_keys("الماس") == DIAMOND_KEYS
    assert resolve_item_keys("R نحاس") is None
    assert resolve_item_keys("الماس الأزرق") == ("diamond_blue",)
    assert resolve_item_keys("royal diamond") == ("diamond_royal",)


def test_outcome_distribution_is_complete_and_daily_diamonds_are_configured():
    from treasure_mining import OUTCOME_KEYS, OUTCOME_WEIGHTS

    assert sum(OUTCOME_WEIGHTS) == 10_000
    assert len(OUTCOME_KEYS) == len(OUTCOME_WEIGHTS)
    assert len(DIAMOND_KEYS) == 6
    assert COOLDOWN_SECONDS == 30
    assert DAILY_DIAMOND_SPAWNS == 15


def test_giant_uuid_accounts_keep_separate_inventory_and_survive_name_change(tmp_path):
    first_id = "11111111-1111-4111-8111-111111111111"
    second_id = "22222222-2222-4222-8222-222222222222"
    store = build_store(tmp_path / "mining_game.json", ["copper", "iron"])

    first = store.dig(first_id, "SameName", now=30_000)
    second = store.dig(second_id, "SameName", now=30_000)
    assert first["ok"] and second["ok"]
    assert store.inventory(first_id, "RenamedUser")["inventory"]["copper"] == 1
    assert store.inventory(first_id)["inventory"]["iron"] == 0
    assert store.inventory(second_id)["inventory"]["iron"] == 1
    assert store.inventory(second_id)["inventory"]["copper"] == 0


def test_display_names_are_not_accepted_as_mining_identity(tmp_path):
    store = MiningStore(tmp_path / "mining_game.json")
    import pytest
    with pytest.raises(ValueError):
        store.inventory("SameName", "SameName")
