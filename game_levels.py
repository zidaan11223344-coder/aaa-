"""Independent, serializable domain logic for Talkin2 game levels and statistics.

The caller owns persistence: pass a JSON-compatible ``state`` mapping to these
functions, then serialize it as JSON (or another compatible format). Player IDs
are stable strings; display names are metadata, never identity keys.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

STATE_VERSION = 1

# Current Talkin2 progression. Every title is a 500-play step; categories span
# 5,000 plays. These names and symbols intentionally mirror Talkin2's ladder.
GAME_LEVEL_CATEGORIES = (
    ("النجوم الفضية", "✩", (
        "مبتدئ", "لاعب ناشئ", "مقاتل", "برونزي", "فضي",
        "ذهبي", "محترف", "خبير", "أسطورة", "أسطورة فضية",
    )),
    ("النجوم الذهبية", "⭐", (
        "ذهبي مبتدئ", "محارب ذهبي", "ذهبي محترف", "ذهبي متألق", "ذهبي نادر",
        "أمير الذهب", "فارس الذهب", "قائد الذهب", "أسطورة الذهب", "ملك الذهب",
    )),
    ("الماس الأزرق", "💎", (
        "ماسي مبتدئ", "ماسي متألق", "ماسي محترف", "فارس الماس", "الماسي النادر",
        "أمير الماس", "قائد الماس", "أسطورة الماس", "إمبراطور الماس الأزرق", "ملك الماس الأزرق",
    )),
    ("الماس البرونزي", "🔶", (
        "برونزي نادر", "فارس البرونز", "برونزي محترف", "البرونزي المتألق", "سيد البرونز",
        "أمير البرونز", "قائد البرونز", "أسطورة البرونز", "إمبراطور البرونز", "ملك الماس البرونزي",
    )),
    ("الماس الأحمر", "♦️", (
        "أحمر نادر", "محارب الماس الأحمر", "أحمر محترف", "فارس الماس الأحمر", "أمير الماس الأحمر",
        "قائد الماس الأحمر", "أسطورة الماس الأحمر", "سيد الماس الأحمر", "إمبراطور الماس الأحمر", "ملك الماس الأحمر",
    )),
    ("الماس الأسود", "◆", (
        "أسود نادر", "محارب أسود", "متألق أسود", "محترف أسود", "فارس أسود",
        "أمير الأسود", "سيد الأسود", "أسطورة الأسود", "إمبراطور الأسود", "ملك الماس الأسود",
    )),
    ("الماس البنفسجي", "🟣💎", (
        "بنفسجي نادر", "محارب بنفسجي", "متألق بنفسجي", "محترف بنفسجي", "فارس بنفسجي",
        "أمير البنفسج", "سيد البنفسج", "أسطورة البنفسج", "إمبراطور البنفسج", "ملك الماس البنفسجي",
    )),
    ("الماس الملكي", "👑", (
        "ملكي نادر", "محارب ملكي", "متألق ملكي", "محترف ملكي", "فارس ملكي",
        "أمير ملكي", "سيد الملك", "أسطورة ملكية", "إمبراطور الماس", "تاج الملوك الأبدي",
    )),
)
GAME_LEVEL_FUTURE_CATEGORIES = (
    ("الماس الخالد", "💠", (
        "بداية الخلود", "حارس الخلود", "نبض الخلود", "فارس الخلود", "سيد الخلود",
        "أمير الخلود", "أسطورة الخلود", "إمبراطور الخلود", "عرش الخلود", "تاج الخلود",
    )),
    ("الماس الكوني", "🔷", (
        "بداية الكون", "حارس النجوم", "سائر المجرات", "فارس المجرة", "سيد الكون",
        "أمير المجرات", "أسطورة المجرات", "إمبراطور الكون", "عرش المجرات", "سيّد المجرات",
    )),
)
GAME_LEVEL_FUTURE_TITLES = (
    "مستكشف", "مبتدئ", "متألق", "محترف", "فارس", "نادر", "أمير", "قائد", "أسطورة", "إمبراطور",
)


def new_state() -> Dict[str, Any]:
    """Return an empty JSON-serializable state container."""
    return {"version": STATE_VERSION, "players": {}}


def normalize_player_name(name: Any) -> str:
    """Return a decoration-insensitive lookup key without changing display text."""
    raw = unicodedata.normalize("NFKC", str(name or "")).strip().lstrip("@")
    compact = "".join(
        char for char in raw
        if not unicodedata.category(char).startswith(("M", "P", "S", "C", "Z"))
        and not (0x13000 <= ord(char) <= 0x1345F)
    ).replace("ـ", "")
    return (compact or raw).casefold()


def _player_id(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("player_id must be a stable string")
    result = value.strip()
    if not result:
        raise ValueError("player_id must not be empty")
    return result


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _clean_games(games: Any) -> Dict[str, Dict[str, int]]:
    result: Dict[str, Dict[str, int]] = {}
    if not isinstance(games, Mapping):
        return result
    for game_key, raw in games.items():
        if not isinstance(raw, Mapping):
            continue
        key = str(game_key).strip()
        if not key:
            continue
        result[key] = {
            "plays": max(0, _int(raw.get("plays"))),
            "points": _int(raw.get("points")),
            "staked": max(0, _int(raw.get("staked"))),
        }
    return result


def _merge_game_snapshots(target: Dict[str, Dict[str, int]], incoming: Mapping[str, Any]) -> None:
    """Merge duplicate serialized snapshots without double-counting them."""
    for game, values in _clean_games(incoming).items():
        current = target.get(game)
        if current is None:
            # Preserve signed net points, including a negative first snapshot.
            target[game] = dict(values)
            continue
        # Talkin2's migration treats duplicate records as snapshots, not
        # independent histories. Prefer the snapshot with more plays; for
        # ties choose the strongest counters without defaulting signed points
        # against zero (which would erase a legitimate net loss).
        if values["plays"] > current["plays"]:
            target[game] = dict(values)
        elif values["plays"] == current["plays"]:
            current["points"] = max(current["points"], values["points"])
            current["staked"] = max(current["staked"], values["staked"])


def normalize_state(state: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Validate/canonicalize JSON-compatible state and coalesce duplicate IDs.

    Different IDs may intentionally share a display name and remain separate.
    Legacy records whose mapping key differs from their explicit player_id are
    folded under the explicit stable ID; duplicate counters use snapshot maxima.
    """
    raw_players = state.get("players", {}) if isinstance(state, Mapping) else {}
    players: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_players, Mapping):
        for map_id, raw in raw_players.items():
            if not isinstance(raw, Mapping):
                continue
            candidate_id = raw.get("player_id", map_id)
            try:
                pid = _player_id(candidate_id)
            except (TypeError, ValueError):
                continue
            name = str(raw.get("username") or raw.get("name") or "").strip().lstrip("@")
            target = players.setdefault(pid, {"player_id": pid, "username": name, "games": {}})
            if not target["username"] and name:
                target["username"] = name
            _merge_game_snapshots(target["games"], raw.get("games", {}))
            # Support an old aggregate-only play count as a synthetic bucket.
            if not isinstance(raw.get("games"), Mapping) and _int(raw.get("plays")) > 0:
                target["games"].setdefault("restored", {"plays": 0, "points": 0, "staked": 0})
                restored = target["games"]["restored"]
                restored["plays"] = max(restored["plays"], max(0, _int(raw.get("plays"))))
    return {"version": STATE_VERSION, "players": players}


def game_level_details(plays: Any) -> tuple[int, int, str, str, str, int]:
    """Return (category index, level 1-10, category, title, symbol, plays)."""
    count = max(0, _int(plays))
    category_index = count // 5000
    level_number = (count % 5000) // 500 + 1
    if category_index < len(GAME_LEVEL_CATEGORIES):
        category, symbol, titles = GAME_LEVEL_CATEGORIES[category_index]
        title = titles[level_number - 1]
    else:
        future_index = category_index - len(GAME_LEVEL_CATEGORIES)
        if future_index < len(GAME_LEVEL_FUTURE_CATEGORIES):
            category, symbol, titles = GAME_LEVEL_FUTURE_CATEGORIES[future_index]
            title = titles[level_number - 1]
        else:
            dynamic_index = future_index - len(GAME_LEVEL_FUTURE_CATEGORIES) + 1
            category = f"الماس الكوني {dynamic_index}"
            symbol = GAME_LEVEL_FUTURE_CATEGORIES[-1][1]
            title = f"{GAME_LEVEL_FUTURE_TITLES[level_number - 1]} {category}"
    return category_index, level_number, category, title, symbol, count


def player_stats(state: Mapping[str, Any], player_id: str) -> Dict[str, Any]:
    """Return a detached aggregate view for one stable string player ID."""
    pid = _player_id(player_id)
    players = normalize_state(state)["players"]
    player = players.get(pid, {"player_id": pid, "username": "", "games": {}})
    games = player["games"]
    plays = sum(item["plays"] for item in games.values())
    points = sum(item["points"] for item in games.values())
    staked = sum(item["staked"] for item in games.values())
    detail = game_level_details(plays)
    return {
        "player_id": pid, "username": player["username"], "games": games,
        "plays": plays, "points": points, "staked": staked,
        "net": points, "level": detail[1], "category_index": detail[0],
        "category": detail[2], "title": detail[3], "symbol": detail[4],
    }


def record_game(
    state: MutableMapping[str, Any], player_id: str, username: str, game_key: str,
    *, points_delta: Any = 0, stake: Any = 0,
) -> Dict[str, Any]:
    """Record exactly one completed game and return the updated player stats.

    ``points_delta`` is the source's signed points change (therefore its sum is
    net points); ``stake`` is a non-negative amount wagered when available.
    """
    pid = _player_id(player_id)
    key = str(game_key or "").strip()
    if not key:
        raise ValueError("game_key must not be empty")
    # Canonicalize in place while preserving a plain JSON dict for callers.
    clean = normalize_state(state)
    state.clear()
    state.update(clean)
    player = state["players"].setdefault(pid, {"player_id": pid, "username": "", "games": {}})
    display = str(username or "").strip().lstrip("@")
    if display:
        player["username"] = display
    game = player["games"].setdefault(key, {"plays": 0, "points": 0, "staked": 0})
    game["plays"] += 1
    game["points"] += _int(points_delta)
    game["staked"] += max(0, _int(stake))
    return player_stats(state, pid)


def leaderboard(state: Mapping[str, Any], limit: Optional[int] = 10, *, game_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Rank all players, or one game's stats, deterministically.

    Overall: progression tier, total plays, normalized name, then ID.
    Per-game: signed points (descending), stake, plays, normalized name, ID,
    matching Talkin2's points/stake/play ranking while making ties stable.
    """
    normalized = normalize_state(state)
    rows: List[Dict[str, Any]] = []
    selected = str(game_key).strip() if game_key is not None else None
    for pid, player in normalized["players"].items():
        stats = player_stats(normalized, pid)
        if selected is not None:
            game = stats["games"].get(selected)
            if not game or game["plays"] <= 0:
                continue
            row = dict(stats)
            row.update(game_plays=game["plays"], points=game["points"],
                       staked=game["staked"], net=game["points"], game_key=selected)
        else:
            if stats["plays"] <= 0:
                continue
            row = dict(stats)
        row["normalized_name"] = normalize_player_name(row["username"])
        rows.append(row)
    if selected is None:
        rows.sort(key=lambda row: (-((row["category_index"] * 10) + row["level"]),
                                   -row["plays"], row["normalized_name"], row["player_id"]))
    else:
        rows.sort(key=lambda row: (-row["points"], -row["staked"], -row["game_plays"],
                                   row["normalized_name"], row["player_id"]))
    if limit is not None:
        rows = rows[:max(0, _int(limit))]
    return rows


__all__ = [
    "GAME_LEVEL_CATEGORIES", "GAME_LEVEL_FUTURE_CATEGORIES", "STATE_VERSION",
    "game_level_details", "leaderboard", "new_state", "normalize_player_name",
    "normalize_state", "player_stats", "record_game",
]
