import json

import pytest

from game_levels import (
    GAME_LEVEL_CATEGORIES,
    GAME_LEVEL_FUTURE_CATEGORIES,
    game_level_details,
    leaderboard,
    new_state,
    normalize_player_name,
    normalize_state,
    player_stats,
    record_game,
)


@pytest.mark.parametrize(
    "plays, category, level, title, symbol",
    [
        (0, "النجوم الفضية", 1, "مبتدئ", "✩"),
        (499, "النجوم الفضية", 1, "مبتدئ", "✩"),
        (500, "النجوم الفضية", 2, "لاعب ناشئ", "✩"),
        (2_500, "النجوم الفضية", 6, "ذهبي", "✩"),
        (4_500, "النجوم الفضية", 10, "أسطورة فضية", "✩"),
        (4_999, "النجوم الفضية", 10, "أسطورة فضية", "✩"),
        (5_000, "النجوم الذهبية", 1, "ذهبي مبتدئ", "⭐"),
        (7_000, "النجوم الذهبية", 5, "ذهبي نادر", "⭐"),
        (9_500, "النجوم الذهبية", 10, "ملك الذهب", "⭐"),
        (10_000, "الماس الأزرق", 1, "ماسي مبتدئ", "💎"),
        (12_500, "الماس الأزرق", 6, "أمير الماس", "💎"),
        (15_000, "الماس البرونزي", 1, "برونزي نادر", "🔶"),
        (20_000, "الماس الأحمر", 1, "أحمر نادر", "♦️"),
        (25_000, "الماس الأسود", 1, "أسود نادر", "◆"),
        (30_000, "الماس البنفسجي", 1, "بنفسجي نادر", "🟣💎"),
        (35_000, "الماس الملكي", 1, "ملكي نادر", "👑"),
        (39_500, "الماس الملكي", 10, "تاج الملوك الأبدي", "👑"),
        (40_000, "الماس الخالد", 1, "بداية الخلود", "💠"),
        (44_500, "الماس الخالد", 10, "تاج الخلود", "💠"),
        (45_000, "الماس الكوني", 1, "بداية الكون", "🔷"),
        (49_500, "الماس الكوني", 10, "سيّد المجرات", "🔷"),
        (50_000, "الماس الكوني 1", 1, "مستكشف الماس الكوني 1", "🔷"),
    ],
)
def test_exact_talkin2_level_boundaries(plays, category, level, title, symbol):
    _category_index, actual_level, actual_category, actual_title, actual_symbol, count = game_level_details(plays)
    assert (actual_category, actual_level, actual_title, actual_symbol, count) == (
        category, level, title, symbol, plays
    )


def test_invalid_negative_and_non_numeric_counts_normalize_to_zero():
    assert game_level_details(-50)[-1] == 0
    assert game_level_details("not a count")[-1] == 0
    assert game_level_details(None)[-1] == 0


def test_category_titles_are_unique_and_each_has_ten_500_play_levels():
    categories = GAME_LEVEL_CATEGORIES + GAME_LEVEL_FUTURE_CATEGORIES
    all_titles = [title for _name, _symbol, titles in categories for title in titles]
    assert len(all_titles) == len(set(all_titles))
    assert all(len(titles) == 10 for _name, _symbol, titles in categories)


def test_name_normalization_ignores_decoration_and_preserves_identity_scope():
    assert normalize_player_name("  @✦ＮＡＤＥＲـٌ!  ") == normalize_player_name("nader")
    assert normalize_player_name("@لاعب") == normalize_player_name("لاعب")
    state = new_state()
    record_game(state, "uuid-a", "✦ Same Name!", "star")
    record_game(state, "uuid-b", "same name", "star")
    assert len(state["players"]) == 2
    assert {row["player_id"] for row in leaderboard(state)} == {"uuid-a", "uuid-b"}


def test_same_identifier_updates_one_player_and_counts_each_recorded_game():
    state = new_state()
    first = record_game(state, "giant-uuid-1", "Alice", "star", points_delta=20, stake=7)
    second = record_game(state, "giant-uuid-1", "Alice", "bet", points_delta=-5, stake=12)
    again = record_game(state, "giant-uuid-1", "Alice", "star", points_delta=3, stake=1)
    assert len(state["players"]) == 1
    assert second["plays"] == 2
    assert again["plays"] == 3
    assert again["points"] == 18
    assert again["net"] == 18
    assert again["staked"] == 20
    assert again["games"]["star"] == {"plays": 2, "points": 23, "staked": 8}
    assert first["level"] == 1


def test_duplicate_serialized_identifiers_merge_snapshot_max_not_sum():
    dirty = {
        "players": {
            "old-key": {
                "player_id": "stable-id", "username": "Decorated",
                "games": {"star": {"plays": 4, "points": 10, "staked": 9}},
            },
            "stable-id": {
                "player_id": "stable-id", "username": "Decorated",
                "games": {
                    "star": {"plays": 4, "points": 10, "staked": 9},
                    "bet": {"plays": 2, "points": -3, "staked": 5},
                },
            },
        }
    }
    clean = normalize_state(dirty)
    stats = player_stats(clean, "stable-id")
    assert list(clean["players"]) == ["stable-id"]
    assert stats["plays"] == 6
    assert stats["points"] == 7
    assert stats["staked"] == 14


def test_per_game_and_overall_leaderboards_have_deterministic_source_order():
    state = new_state()
    record_game(state, "id-c", "Cora", "bet", points_delta=8, stake=10)
    record_game(state, "id-b", "Bee", "bet", points_delta=8, stake=20)
    record_game(state, "id-a", "Ari", "bet", points_delta=8, stake=20)
    record_game(state, "id-a", "Ari", "bet", points_delta=0, stake=0)
    record_game(state, "id-c", "Cora", "star", points_delta=0)

    game_rows = leaderboard(state, game_key="bet")
    assert [row["player_id"] for row in game_rows] == ["id-a", "id-b", "id-c"]
    assert [row["points"] for row in game_rows] == [8, 8, 8]
    assert [row["staked"] for row in game_rows] == [20, 20, 10]
    overall = leaderboard(state)
    assert [row["player_id"] for row in overall] == ["id-a", "id-c", "id-b"]
    assert len(leaderboard(state, limit=2)) == 2
    assert leaderboard(state, limit=0) == []


def test_json_round_trip_and_stable_string_identifier_contract():
    state = new_state()
    record_game(state, "550e8400-e29b-41d4-a716-446655440000", "Player", "luck", points_delta=-11, stake=5)
    restored = json.loads(json.dumps(state, ensure_ascii=False))
    assert player_stats(restored, "550e8400-e29b-41d4-a716-446655440000")["net"] == -11
    with pytest.raises(TypeError):
        record_game(state, 123, "Player", "luck")
    with pytest.raises(ValueError):
        record_game(state, " ", "Player", "luck")
