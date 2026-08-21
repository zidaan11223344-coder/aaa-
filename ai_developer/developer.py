"""Isolated game developer for Giant Chat.
New AI-generated games are staged under games/testing and are never loaded by
production until an explicit approval moves them into custom_games.json.
"""
from __future__ import annotations
import json, re, shutil
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent.parent
TESTING = BASE / "games" / "testing"
APPROVED = BASE / "games" / "approved"
TESTING_JSON = TESTING / "games.json"
APPROVED_JSON = BASE / "custom_games.json"


def _load(path, default):
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else default
    except Exception:
        pass
    return default


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_int(value, default, lo=None, hi=None):
    if isinstance(value, bool):
        n = int(value)
    elif isinstance(value, (int, float)):
        n = int(value)
    else:
        m = re.search(r"-?\d+(?:[.,]\d+)?", str(value or ""))
        try:
            n = int(float(m.group(0).replace(",", "."))) if m else default
        except Exception:
            n = default
    if lo is not None: n = max(lo, n)
    if hi is not None: n = min(hi, n)
    return n


def validate_game(data):
    if not isinstance(data, dict):
        raise ValueError("تعريف اللعبة ليس JSON object")
    command = re.sub(r"[^\w\u0600-\u06ff-]", "", str(data.get("command", ""))).strip().lower()
    if not command:
        raise ValueError("command فارغ")
    out = {
        "command": command[:24],
        "title": str(data.get("title") or command)[:80],
        "win_chance": normalize_int(data.get("win_chance", 50), 50, 1, 100),
        "win_points": normalize_int(data.get("win_points", 20), 20, -1000, 1000),
        "lose_points": normalize_int(data.get("lose_points", -5), -5, -1000, 1000),
        "win_message": str(data.get("win_message") or "🎉 فوز!")[:200],
        "lose_message": str(data.get("lose_message") or "😅 حظاً أوفر!")[:200],
        "image_prompt": str(data.get("image_prompt") or "Game card")[:1000],
        "status": "testing",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return out


class GameDeveloper:
    def stage(self, data):
        game = validate_game(data)
        testing = _load(TESTING_JSON, {})
        testing[game["command"]] = game
        _save(TESTING_JSON, testing)
        return game

    def list_testing(self):
        return _load(TESTING_JSON, {})

    def approve(self, command):
        command = str(command).strip().lower()
        testing = _load(TESTING_JSON, {})
        game = testing.get(command)
        if not game:
            raise KeyError(command)
        approved = _load(APPROVED_JSON, {})
        approved[command] = {k:v for k,v in game.items() if k not in ("status",)}
        _save(APPROVED_JSON, approved)
        _save(APPROVED / f"{command}.json", approved[command])
        testing.pop(command, None)
        _save(TESTING_JSON, testing)
        return approved[command]

    def reject(self, command):
        testing = _load(TESTING_JSON, {})
        existed = testing.pop(str(command).strip().lower(), None)
        _save(TESTING_JSON, testing)
        return existed is not None
