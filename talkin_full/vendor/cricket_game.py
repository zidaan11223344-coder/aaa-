"""Persistent cross-room cricket with player rosters and an optional Talkin2 opponent."""
from __future__ import annotations

import random
import time
import uuid
from pathlib import Path
from typing import Any

from .cricket_state import JsonState, normalize


ASSET_FILES = (
    "cricket_attack_blue.png",
    "cricket_defense_red.png",
    "cricket_out.png",
)
BOT_TEAM_KEY = "__sboot_cricket_bot__"
_TERMINAL_MATCH_STAGES = {
    "finished", "complete", "completed", "ended", "over", "closed",
    "cancelled", "canceled", "result", "done",
}


def _blank() -> dict[str, Any]:
    return {
        "enabled": False,
        "enabled_rooms": {},
        "next_event_id": 0,
        "matches": {},
        # Legacy single-match slot; migrated lazily by _matches().
        "match": None,
        "events": [],
        "points": {},
        "wins": {},
        "stats": {},
    }


def _key(room: str) -> str:
    return normalize(room)


def _user_key(username: str) -> str:
    return normalize(str(username or "").strip().lstrip("@"))


class CricketGame:
    """File-locked state machine; controller bots deliver events to their own rooms."""

    MIN_PLAYERS = 1
    MAX_PLAYERS = 4
    ROOM_TEAMS = 2
    BALLS_PER_PLAYER = 6
    EVENT_HISTORY = 5000

    def __init__(self, root: str | Path, persist=None, reward=None, bot_name: str = "Talkin2"):
        path = Path(root)
        if path.suffix.lower() != ".json":
            path = path / "cricket_state.json"
        self.state = JsonState(path, _blank, persist=persist)
        self.reward = reward
        self.bot_name = str(bot_name or "Talkin2").strip().lstrip("@") or "Talkin2"

    @staticmethod
    def _participants(match: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            item for item in match.get("rooms", [])
            if isinstance(item, dict) and item.get("key") and item.get("name")
        ]

    @staticmethod
    def _matches(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Load the multi-match map and migrate a legacy single match in place."""
        matches = data.get("matches")
        if not isinstance(matches, dict):
            matches = {}
            data["matches"] = matches
        legacy = data.get("match")
        if isinstance(legacy, dict):
            match_id = str(legacy.get("id") or uuid.uuid4().hex)
            legacy["id"] = match_id
            matches.setdefault(match_id, legacy)
            data["match"] = None
        normalized = {
            str(match_id): match
            for match_id, match in matches.items()
            if isinstance(match, dict)
            and str(match.get("stage") or "").casefold() not in _TERMINAL_MATCH_STAGES
        }
        matches.clear()
        matches.update(normalized)
        return matches

    @classmethod
    def _match_for_room(cls, data: dict[str, Any], room: str) -> dict[str, Any] | None:
        room_key = _key(room)
        if not room_key:
            return None
        return next((
            match for match in cls._matches(data).values()
            if any(str(item.get("key") or "") == room_key for item in cls._participants(match))
        ), None)

    @classmethod
    def _remove_match(cls, data: dict[str, Any], match: dict[str, Any]) -> None:
        matches = data.setdefault("matches", {})
        match_id = str(match.get("id") or "")
        if match_id:
            matches.pop(match_id, None)
        else:
            for key, candidate in list(matches.items()):
                if candidate is match:
                    matches.pop(key, None)

    @classmethod
    def _discard_match_events(cls, data: dict[str, Any], match: dict[str, Any]) -> None:
        """Drop queued messages for the cancelled pair without touching other matches."""
        room_keys = {str(item.get("key") or "") for item in cls._participants(match)}
        data["events"] = [
            event for event in data.get("events", [])
            if not isinstance(event, dict) or str(event.get("room_key") or "") not in room_keys
        ]

    @classmethod
    def _join_candidates(cls, data: dict[str, Any], room: str) -> list[dict[str, Any]]:
        room_key = _key(room)
        candidates = []
        for match in cls._matches(data).values():
            rooms = cls._participants(match)
            target = int(match.get("target_players") or 0)
            if (
                match.get("stage") == "lobby"
                and match.get("mode") != "solo"
                and len(rooms) == 1
                and rooms[0]["key"] != room_key
                and target > 0
                and len(rooms[0].get("players", [])) >= target
            ):
                candidates.append(match)
        # Plain Join selects the oldest complete, unpaired first team. Each
        # match's UUID remains internal and keeps concurrent games isolated.
        return sorted(candidates, key=lambda item: (float(item.get("created_at") or 0), str(item.get("id") or "")))

    def match_for_join(self, room: str) -> dict[str, Any] | None:
        """Resolve a room's match or the oldest waiting opponent match."""
        data = self.state.load()
        existing = self._match_for_room(data, room)
        if existing:
            return existing
        candidates = self._join_candidates(data, room)
        return candidates[0] if candidates else None

    def _emit(
        self,
        data: dict[str, Any],
        rooms: list[dict[str, Any]],
        text: str,
        images: tuple[str, ...] = (),
        *,
        broadcast: bool = False,
    ) -> None:
        events = data.setdefault("events", [])
        broadcast_id = None
        if broadcast and rooms:
            data["next_event_id"] = int(data.get("next_event_id", 0)) + 1
            broadcast_id = data["next_event_id"]
        for participant in rooms:
            if not broadcast:
                data["next_event_id"] = int(data.get("next_event_id", 0)) + 1
            event_id = broadcast_id if broadcast else data["next_event_id"]
            events.append({
                "id": event_id,
                "room": participant["name"],
                "room_key": participant["key"],
                "text": str(text),
                "broadcast": bool(broadcast),
                "images": [
                    name for name in images
                    if name in ASSET_FILES or str(name).startswith("cricket_result_")
                ],
                "created_at": time.time(),
            })
        if len(events) > self.EVENT_HISTORY:
            del events[:-self.EVENT_HISTORY]

    def _emit_room_messages(
        self,
        data: dict[str, Any],
        messages: dict[str, str],
        images: tuple[str, ...] = (),
    ) -> None:
        """Emit short, room-specific messages while keeping one shared match state."""
        events = data.setdefault("events", [])
        for room_key, text in messages.items():
            if not room_key or text is None:
                continue
            data["next_event_id"] = int(data.get("next_event_id", 0)) + 1
            events.append({
                "id": data["next_event_id"],
                "room": room_key,
                "room_key": _key(room_key),
                "text": str(text),
                "images": [
                    name for name in images
                    if name in ASSET_FILES or str(name).startswith("cricket_result_")
                ],
                "created_at": time.time(),
            })
        if len(events) > self.EVENT_HISTORY:
            del events[:-self.EVENT_HISTORY]

    def enabled(self, room: str = "") -> bool:
        data = self.state.load()
        return bool(data.get("enabled", bool(data.get("enabled_rooms") or {})))

    def set_enabled(self, room: str, enabled: bool) -> str:
        room_name, room_key = str(room or "").strip(), _key(room)
        if not room_key:
            return "❌ اسم الغرفة غير صالح."

        def mutate(data: dict[str, Any]) -> str:
            data["enabled"] = bool(enabled)
            data["enabled_rooms"] = {}
            if enabled:
                return f"✅ تم تشغيل الكركيت على مستوى السيرفر من غرفة {room_name}."
            for match in list(self._matches(data).values()):
                self._discard_match_events(data, match)
                self._emit(
                    data, self._participants(match),
                    f"⛔ أوقفت غرفة {room_name} اللعبة؛ أُلغيت المباراة.",
                )
                self._remove_match(data, match)
            return f"⛔ تم إيقاف الكركيت على مستوى السيرفر من غرفة {room_name}."

        return self.state.mutate(mutate)

    def cancel_match(self, room: str) -> str:
        """Cancel the open match from one of its participating rooms."""
        room_key = _key(room)

        def mutate(data: dict[str, Any]) -> str:
            match = self._match_for_room(data, room_key)
            if not isinstance(match, dict):
                return "📭 لا توجد مباراة كركيت مفتوحة لإيقافها."
            participants = self._participants(match)
            if room_key not in {str(item.get("key") or "") for item in participants}:
                return "⛔ لا يمكنك إيقاف مباراة من غرفة غير مشاركة فيها."
            self._discard_match_events(data, match)
            self._emit(data, participants, "⛔ تم إيقاف مباراة الكركيت الحالية من أحد الأعضاء.")
            self._remove_match(data, match)
            return "✅ تم إيقاف مباراة الكركيت الحالية."

        return self.state.mutate(mutate)

    @staticmethod
    def _new_match(room_name: str, room_key: str, stage: str, player_count: int | None = None) -> dict[str, Any]:
        match: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "stage": stage,
            "created_at": time.time(),
            "setup_room": room_key,
            "target_players": player_count,
            "rooms": [{"key": room_key, "name": room_name, "players": []}],
            "mode": "rooms",
            "teams": {},
            "innings": 1,
            "batting_team": "attack",
            "balls": 0,
            "scores": {"attack": 0, "defense": 0},
            "wickets": {"attack": 0, "defense": 0},
            "out_players": {"attack": [], "defense": []},
            "turns": {"attack": 0, "defense": 0},
            "wicket_streak": 0,
            "wicket_streak_bowler": "",
            "choices": {},
            "player_scores": {},
            "active_batters": {"attack": "", "defense": ""},
            "active_bowlers": {"attack": "", "defense": ""},
            "bowler_balls": {"attack": 0, "defense": 0},
        }
        return match

    def begin_setup(self, room: str, *, reset_existing: bool = False) -> str | None:
        room_name, room_key = str(room or "").strip(), _key(room)

        def mutate(data: dict[str, Any]) -> str | None:
            if not data.get("enabled"):
                return "⛔ اللعبة غير مفعّلة؛ فعّل اللعبة أولاً (فعّلها) من خاص الماستر: تشغيل لعبه الكريكيت."
            existing = self._match_for_room(data, room_key)
            if existing and not reset_existing:
                return "⏳ هذه الغرفة مشاركة في مباراة مفتوحة بالفعل؛ أكملها أو أوقفها قبل بدء مباراة أخرى."
            if reset_existing:
                # Reset only the match involving this room. Other room pairs
                # continue playing independently.
                if existing:
                    self._discard_match_events(data, existing)
                    self._emit(
                        data, self._participants(existing),
                        f"⛔ أُلغيت المباراة المفتوحة من غرفة {room_name} لإعداد مباراة جديدة.",
                    )
                    self._remove_match(data, existing)
            match = self._new_match(room_name, room_key, "setup")
            self._matches(data)[match["id"]] = match
            self._emit(
                data,
                self._participants(match),
                "🏏 إعداد مباراة الكركيت\n"
                "اختر عدد اللاعبين داخل هذه الغرفة فقط:\n"
                "1️⃣ لاعب واحد\n2️⃣ لاعبان\n3️⃣ ثلاثة لاعبين\n4️⃣ أربعة لاعبين\n"
                "أرسل الرقم فقط (العدد لكل غرفة). بعد اكتمال الفريق الأول، ترسل الغرفة الثانية Join.\n"
                "📢 سيعلن البوت اسم الغرف وعدد اللاعبين وأسماءهم عند اكتمال الفريقين.",
            )
            return None

        return self.state.mutate(mutate)

    def select_player_count(self, room: str, player_count: int) -> str | None:
        room_key = _key(room)
        try:
            count = int(player_count)
        except (TypeError, ValueError):
            return "❌ اختر 1 أو 2 أو 3 أو 4 لاعبين لكل غرفة."
        if not self.MIN_PLAYERS <= count <= self.MAX_PLAYERS:
            return "❌ عدد اللاعبين في كل غرفة يجب أن يكون من 1 إلى 4."

        def mutate(data: dict[str, Any]) -> str | None:
            match = self._match_for_room(data, room_key)
            if not isinstance(match, dict) or match.get("stage") != "setup":
                return "📭 لا توجد لعبة تنتظر اختيار عدد اللاعبين."
            if room_key != str(match.get("setup_room") or ""):
                return "🔒 اختيار عدد اللاعبين متاح في غرفة بدء اللعبة فقط."
            match["target_players"] = count
            # Regular room setup is always room-vs-room, including one player
            # per side. Only `.cr b` marks the setup as a match against the bot.
            if match.get("mode") != "solo":
                match["mode"] = "rooms"
            match["stage"] = "lobby"
            self._emit(
                data,
                self._participants(match),
                (f"🏏 كركيت | مباراة ضد البوت — المطلوب {count} لاعب(ين) في هذه الغرفة.\n"
                 "أرسل Join من كل لاعب؛ بعد اكتمال العدد تبدأ المباراة ضد البوت."
                 if match.get("mode") == "solo" else
                 f"🏏 كركيت | {count} لاعب(ين) في كل غرفة\n"
                 "أرسل Join من لاعبي الغرفة الأولى. بعد اكتمالها، ترسل الغرفة الثانية Join. "
                 "عند تعدد قوائم الانتظار، يُقرن Join بأقدم فريق أول مكتمل. لا يمكن إكمال الفريقين من الغرفة نفسها."),
            )
            return None

        return self.state.mutate(mutate)

    def begin_bot_setup(self, room: str, *, reset_existing: bool = False) -> str | None:
        """Open a fresh player-count setup for a match against Talkin2."""
        room_name, room_key = str(room or "").strip(), _key(room)

        def mutate(data: dict[str, Any]) -> str | None:
            if not data.get("enabled"):
                return "⛔ اللعبة غير مفعّلة؛ فعّل اللعبة أولاً (فعّلها) من خاص الماستر: تشغيل لعبه الكركيت."
            existing = self._match_for_room(data, room_key)
            if existing and not reset_existing:
                return "⏳ هذه الغرفة مشاركة في مباراة مفتوحة بالفعل؛ أكملها أو أوقفها قبل بدء مباراة أخرى."
            if reset_existing:
                if existing:
                    self._discard_match_events(data, existing)
                    self._emit(data, self._participants(existing), "⛔ أُلغيت المباراة السابقة لبدء مباراة ضد البوت.")
                    self._remove_match(data, existing)
            match = self._new_match(room_name, room_key, "setup")
            match["mode"] = "solo"
            self._matches(data)[match["id"]] = match
            self._emit(
                data,
                self._participants(match),
                "🏏 إعداد مباراة ضد البوت\n"
                "اختر عدد اللاعبين: 1 أو 2 أو 3 أو 4.\n"
                "بعد اختيار العدد، يرسل كل لاعب Join من هذه الغرفة فقط.\n"
                f"🤖 الخصم هو بوت {self.bot_name}.",
            )
            return None

        return self.state.mutate(mutate)

    def start(self, room: str, player_count: int, *, mode: str = "solo") -> str | None:
        """Open a lobby directly; retained for the `cricket N` command."""
        room_name, room_key = str(room or "").strip(), _key(room)
        try:
            count = int(player_count)
        except (TypeError, ValueError):
            return "❌ اكتب عدد اللاعبين في كل غرفة من 1 إلى 4."
        if not self.MIN_PLAYERS <= count <= self.MAX_PLAYERS:
            return "❌ عدد اللاعبين في كل غرفة يجب أن يكون من 1 إلى 4."

        def mutate(data: dict[str, Any]) -> str | None:
            if not bool(data.get("enabled", bool(data.get("enabled_rooms") or {}))):
                return "⛔ اللعبة متوقفة على مستوى السيرفر. فعّلها من خاص الماستر: تشغيل لعبه الكركيت."
            if self._match_for_room(data, room_key):
                return "⏳ هذه الغرفة مشاركة في مباراة مفتوحة بالفعل؛ أكملها أو أوقفها قبل بدء مباراة أخرى."
            match = self._new_match(room_name, room_key, "lobby", count)
            match["mode"] = "rooms" if mode == "rooms" else "solo"
            self._matches(data)[match["id"]] = match
            self._emit(
                data,
                self._participants(match),
                f"🏏 فُتحت مباراة الكركيت في {room_name} — المطلوب {count} لاعب(ين).\n" +
                ("👥 بعد اكتمال الفريق الأول، أرسل لاعبو الغرفة الثانية Join. عند تعدد قوائم الانتظار، يُختار أقدم فريق مكتمل."
                 if match["mode"] == "rooms" else
                 "👤 كل اللاعبين ينضمون من هذه الغرفة فقط بإرسال Join.\n"
                 f"🤖 عند اكتمال العدد تبدأ المباراة تلقائيًا ضد {self.bot_name}."),
            )
            return None

        return self.state.mutate(mutate)

    def open_league_match(self, room1: str, room2: str, team1: str, team2: str, players1, players2) -> str | None:
        """Create a league lobby only after the master explicitly opens it."""
        room1, room2 = str(room1 or "").strip(), str(room2 or "").strip()
        key1, key2 = _key(room1), _key(room2)
        roster1 = [str(p).strip().lstrip("@") for p in players1 if str(p).strip()]
        roster2 = [str(p).strip().lstrip("@") for p in players2 if str(p).strip()]
        def mutate(data):
            if not data.get("enabled"):
                return "⛔ لعبة الكركيت غير مفعلة؛ شغلها الماستر أولاً."
            if not key1 or not key2 or key1 == key2:
                return "❌ غرفتا مباراة الدوري يجب أن تكونا مختلفتين."
            if self._match_for_room(data, key1) or self._match_for_room(data, key2):
                return "⏳ إحدى غرف مباراة الدوري مشاركة في مباراة مفتوحة بالفعل."
            match = self._new_match(room1, key1, "lobby", self.MAX_PLAYERS)
            match["mode"] = "rooms"
            match["league"] = True
            match["league_team_names"] = {key1: str(team1), key2: str(team2)}
            match["rooms"].append({"key": key2, "name": room2, "players": []})
            self._matches(data)[match["id"]] = match
            names1 = "، ".join(f"@{p}" for p in roster1)
            names2 = "، ".join(f"@{p}" for p in roster2)
            self._emit(data, self._participants(match),
                f"🏏 دوري الكركيت — فتح الماستر للمباراة\n"
                f"👥 {team1}: {names1}\n👥 {team2}: {names2}\n"
                "📣 أرسل كل لاعب Join داخل غرفة فريقه. لا تبدأ المباراة إلا بعد اكتمال الفريقين.")
            return None
        return self.state.mutate(mutate)

    def _maybe_start_teams(self, data: dict[str, Any], match: dict[str, Any]) -> bool:
        rooms = self._participants(match)
        target = int(match.get("target_players") or 0)
        if match.get("mode") == "solo":
            if len(rooms) == 1 and target > 0 and len(rooms[0].get("players", [])) >= target:
                match["stage"] = "teams"
                self._emit(
                    data,
                    rooms,
                    f"🤖 انضم بوت {self.bot_name} خصمًا لك. اختر دورك: 1 للهجوم أو 2 للدفاع؛ "
                    "والبوت يأخذ الدور الآخر تلقائيًا.",
                )
                return True
            return False
        if len(rooms) == self.ROOM_TEAMS and target > 0 and all(
            len(item.get("players", [])) >= target for item in rooms
        ):
            match["stage"] = "teams"
            self._emit(
                data,
                rooms,
                f"✅ اكتمل الفريقان ({target} لاعب(ين) في كل غرفة).\n"
                "تختار كل غرفة دورها: 1 للهجوم أو 2 للدفاع. يجب أن يكون هناك فريق من كل نوع.",
            )
            return True
        return False

    def join(self, room: str, sender: str = "") -> str | None:
        room_name, room_key = str(room or "").strip(), _key(room)
        username = str(sender or "").strip().lstrip("@")
        user_key = _user_key(username)
        if not user_key:
            return "❌ تعذر تحديد اسم اللاعب؛ أرسل Join من حسابك داخل الغرفة."

        def mutate(data: dict[str, Any]) -> str | None:
            if not data.get("enabled"):
                return "⛔ فعّل اللعبة أولاً: شغّل الكركيت من خاص الماستر."
            match = self._match_for_room(data, room_key)
            if match is None:
                candidates = self._join_candidates(data, room_name)
                if not candidates:
                    return "📭 لا توجد مباراة مكتملة تنتظر الانضمام الآن."
                match = candidates[0]
            if match.get("stage") != "lobby":
                return "📭 لا توجد قائمة لاعبين مفتوحة لهذه المباراة الآن."
            target = int(match.get("target_players") or 0)
            if target < self.MIN_PLAYERS or target > self.MAX_PLAYERS:
                return "⏳ انتظر اختيار عدد اللاعبين أولاً."
            participants = self._participants(match)
            for other_match in self._matches(data).values():
                for item in self._participants(other_match):
                    if any(_user_key(player) == user_key for player in item.get("players", [])):
                        if str(other_match.get("id")) == str(match.get("id")):
                            return f"✅ @{username} مسجل بالفعل في المباراة."
                        return "⛔ لا يمكن للحساب نفسه المشاركة في مباراتين كركيت متزامنتين."
            participant = next((item for item in participants if item["key"] == room_key), None)
            if participant is None:
                if len(participants) >= self.ROOM_TEAMS:
                    return "⛔ اكتملت غرفتا المباراة."
                first_room = participants[0]
                if len(first_room.get("players", [])) < target:
                    return f"⏳ أكمل الفريق الأول {target} لاعبين أولاً."
                participant = {"key": room_key, "name": room_name, "players": []}
                match.setdefault("rooms", []).append(participant)
                participants = self._participants(match)
                self._emit(
                    data,
                    participants,
                    f"🔗 انضمت غرفة {room_name} إلى المباراة.\n"
                    f"👥 المطلوب {target} لاعب(ين) في كل غرفة.\n"
                    "أرسل Join من لاعبي هذه الغرفة.",
                )
            players = participant.setdefault("players", [])
            if len(players) >= target:
                return f"⛔ اكتمل عدد اللاعبين ({target}) في هذه الغرفة."
            players.append(username)

            if len(participants) == 1:
                if len(players) < target:
                    self._emit(
                        data, [participant],
                        f"✅ انضم @{username}.\n👥 اكتمل {len(players)}/{target} لاعب في الغرفة.\n"
                        "🔗 بعد اكتمال الفريق الأول، ترسل الغرفة الثانية Join؛ وعند تعدد القوائم يُقرن بأقدم فريق مكتمل. "
                        f"أو أكمل العدد هنا للعب ضد {self.bot_name}.",
                    )
                else:
                    if match.get("mode") == "solo":
                        # Direct `cricket N` keeps the original one-room vs Talkin2 flow.
                        match["mode"] = "solo"
                        match["teams"] = {room_key: "attack", BOT_TEAM_KEY: "defense"}
                        self._start_live(data, match, participants)
                    else:
                        # The first team is only a lobby until the opposing room
                        # joins and a captain chooses attack or defense.
                        team_players = [
                            str(player).strip().lstrip("@")
                            for player in participant.get("players", [])
                            if str(player).strip()
                        ]
                        player_count = len(team_players)
                        player_label = "لاعب" if player_count == 1 else "لاعبين"
                        self._emit(
                            data, [participant],
                            f"🏏 بدأت لعبة الكريكيت في غرفة {participant['name']}\n"
                            f"👥 عدد الفريق: {player_count} {player_label}\n"
                            "🔗 للانضمام أرسل Join.",
                            broadcast=True,
                        )
            elif len(participants) == self.ROOM_TEAMS:
                full = all(len(item.get("players", [])) >= target for item in participants)
                if full:
                    match["mode"] = "rooms"
                    match["stage"] = "teams"
                    self._emit(
                        data, participants,
                        f"🏏 اكتمل الفريقان: {target} لاعبين لكل غرفة\n"
                        f"👥 {participants[0]['name']} ضد {participants[1]['name']}\n"
                        "🎯 الفريق الأول يختار: 1 هجوم أو 2 دفاع.\n"
                        "الفريق الثاني يُحدد تلقائيًا.",
                    )
                else:
                    self._emit(
                        data, [participant],
                        f"✅ انضم @{username}.\n👥 اكتمل {len(players)}/{target} لاعب في هذه الغرفة.\n"
                        "⏳ بانتظار اكتمال لاعبي الغرفة الأخرى.",
                    )
            return None

        return self.state.mutate(mutate)

    def play_bot(self, room: str, sender: str) -> str | None:
        room_name, room_key = str(room or "").strip(), _key(room)
        username = str(sender or "").strip().lstrip("@")
        user_key = _user_key(username)

        def mutate(data: dict[str, Any]) -> str | None:
            match = self._match_for_room(data, room_key)
            if not data.get("enabled"):
                return "⛔ فعّل الكركيت أولاً من خاص الماستر: تشغيل لعبه الكركيت."
            if not isinstance(match, dict) or match.get("stage") != "lobby":
                return "📭 لا توجد مباراة تنتظر خصم البوت."
            if int(match.get("target_players") or 0) != 1:
                return "🤖 اللعب مع البوت متاح عند اختيار لاعب واحد لكل فريق فقط."
            rooms = self._participants(match)
            if len(rooms) != 1 or rooms[0]["key"] != room_key:
                return "⛔ خصم البوت متاح في غرفة بدء المباراة قبل انضمام غرفة أخرى."
            players = rooms[0].setdefault("players", [])
            if players and not any(_user_key(player) == user_key for player in players):
                return "⛔ يوجد لاعب مسجل بالفعل؛ أرسل Join بحسابه أو انتظر الغرفة الأخرى."
            if not players:
                if not user_key:
                    return "❌ تعذر تحديد اسم اللاعب."
                players.append(username)
            match["mode"] = "solo"
            self._maybe_start_teams(data, match)
            return None

        return self.state.mutate(mutate)

    def choose_team(self, room: str, team: str) -> str | None:
        room_key = _key(room)
        team_value = str(team or "").casefold().strip()
        if team_value in {"attack", "1", "هجوم"}:
            team_value = "attack"
        elif team_value in {"defense", "2", "دفاع"}:
            team_value = "defense"
        else:
            return "❌ اختر 1 للهجوم أو 2 للدفاع."

        def mutate(data: dict[str, Any]) -> str | None:
            match = self._match_for_room(data, room_key)
            if not isinstance(match, dict):
                return "📭 لا توجد مباراة."

            # Compatibility: a repeated team command after auto-assignment is harmless.
            if match.get("stage") == "live":
                assigned = (match.get("teams") or {}).get(room_key)
                if assigned == team_value:
                    return None
                return "⏳ بدأت المباراة بالفعل."

            if match.get("stage") != "teams":
                return "📭 اللعبة لا تنتظر اختيار الهجوم أو الدفاع."

            participants = self._participants(match)
            if len(participants) != 2:
                return "⏳ بانتظار انضمام الفريق الثاني."
            if room_key != str(match.get("setup_room") or ""):
                return "⏳ الفريق المشغّل للعبة هو من يختار الهجوم أو الدفاع."

            other = next(item for item in participants if item["key"] != room_key)
            teams = match.setdefault("teams", {})
            teams[room_key] = team_value
            teams[other["key"]] = "defense" if team_value == "attack" else "attack"
            self._start_live(data, match, participants)
            return None

        return self.state.mutate(mutate)

    @staticmethod
    def _team_label(team: str) -> str:
        return "الهجوم" if team == "attack" else "الدفاع"

    @staticmethod
    def _room_for_team(match: dict[str, Any], team: str) -> dict[str, Any] | None:
        for participant in CricketGame._participants(match):
            if (match.get("teams") or {}).get(participant["key"]) == team:
                return participant
        return None

    def _next_player(self, match: dict[str, Any], team: str, batting: bool) -> str:
        participant = self._room_for_team(match, team)
        if participant is None:
            return f"🤖 بوت {self.bot_name}"
        players = [str(item) for item in participant.get("players", []) if str(item).strip()]
        if not players:
            return participant["name"]
        active_key = "active_batters" if batting else "active_bowlers"
        active = str(match.get(active_key, {}).get(team, "") or "")
        if active and (batting is False or _user_key(active) not in {
            _user_key(item) for item in match.get("out_players", {}).get(team, [])
        }):
            return active
        turns = match.setdefault("turns", {"attack": 0, "defense": 0})
        start = int(turns.get(team, 0)) % len(players)
        out = {_user_key(item) for item in match.get("out_players", {}).get(team, [])} if batting else set()
        for offset in range(len(players)):
            candidate = players[(start + offset) % len(players)]
            if _user_key(candidate) not in out:
                return candidate
        return ""

    def _set_initial_players(self, match: dict[str, Any]) -> None:
        """Set the first batter and defender for each team at innings start."""
        match["active_batters"] = {}
        match["active_bowlers"] = {}
        match["bowler_balls"] = {"attack": 0, "defense": 0}
        for team in ("attack", "defense"):
            match["active_batters"][team] = self._next_player(match, team, batting=True)
            match["active_bowlers"][team] = self._next_player(match, team, batting=False)

    def _advance_after_ball(self, match: dict[str, Any], batting: str, bowling: str, out_name: str) -> None:
        """Keep batters until OUT; rotate bowlers only after six consecutive balls."""
        batters = match.setdefault("active_batters", {})
        bowlers = match.setdefault("active_bowlers", {})
        bowler_balls = match.setdefault("bowler_balls", {"attack": 0, "defense": 0})
        active_batter = str(batters.get(batting, "") or "")
        if out_name and _user_key(active_batter) == _user_key(out_name):
            participant = self._room_for_team(match, batting)
            players = [str(item) for item in (participant or {}).get("players", []) if str(item).strip()]
            out = {_user_key(item) for item in match.get("out_players", {}).get(batting, [])}
            if players:
                try:
                    index = next(i for i, player in enumerate(players) if _user_key(player) == _user_key(out_name))
                except StopIteration:
                    index = -1
                batters[batting] = next(
                    (players[(index + offset) % len(players)] for offset in range(1, len(players) + 1)
                     if _user_key(players[(index + offset) % len(players)]) not in out),
                    "",
                )
            else:
                batters[batting] = self._next_player(match, batting, batting=True)

        bowler_balls[bowling] = int(bowler_balls.get(bowling, 0)) + 1
        if bowler_balls[bowling] >= self.BALLS_PER_PLAYER:
            participant = self._room_for_team(match, bowling)
            players = [str(item) for item in (participant or {}).get("players", []) if str(item).strip()]
            if len(players) > 1:
                current = _user_key(str(bowlers.get(bowling, "")))
                try:
                    index = next(i for i, player in enumerate(players) if _user_key(player) == current)
                except StopIteration:
                    index = -1
                bowlers[bowling] = players[(index + 1) % len(players)]
            bowler_balls[bowling] = 0

    def _turn_prompt(self, match: dict[str, Any]) -> str:
        """Return a compact generic prompt (kept for older integrations)."""
        batting = str(match.get("batting_team") or "attack")
        bowling = "defense" if batting == "attack" else "attack"
        batter = self._next_player(match, batting, batting=True)
        bowler = self._next_player(match, bowling, batting=False)
        return f"🎯 دورك يا @{batter} — أرسل 0 إلى 6.\n🛡️ بعده دور @{bowler}."

    def _turn_messages(self, match: dict[str, Any]) -> dict[str, str]:
        batting = str(match.get("batting_team") or "attack")
        bowling = "defense" if batting == "attack" else "attack"
        batter = self._next_player(match, batting, batting=True)
        bowler = self._next_player(match, bowling, batting=False)
        messages: dict[str, str] = {}

        if match.get("mode") == "solo":
            human_key = next((key for key in (match.get("teams") or {}) if key != BOT_TEAM_KEY), None)
            human_team = str((match.get("teams") or {}).get(human_key or "", "attack"))
            human_room = self._room_for_team(match, human_team)
            if human_room:
                if batting == human_team:
                    messages[human_room["key"]] = f"🏏 الهجوم\n🎯 دورك يا @{batter}\nأرسل 0 إلى 6"
                else:
                    messages[human_room["key"]] = f"🛡️ الدفاع\n🎯 دورك يا @{bowler}\nأرسل 0 إلى 6"
            return messages

        bat_room = self._room_for_team(match, batting)
        bowl_room = self._room_for_team(match, bowling)
        if bat_room:
            messages[bat_room["key"]] = f"🏏 الهجوم\n🎯 دورك يا @{batter}\nأرسل 0 إلى 6"
        if bowl_room:
            messages[bowl_room["key"]] = "🛡️ الدفاع\n⏳ بانتظار ضربة المهاجم"
        return messages

    def _start_live(self, data: dict[str, Any], match: dict[str, Any], participants: list[dict[str, Any]]) -> None:
        match["stage"] = "live"
        match["innings"] = 1
        match["batting_team"] = str((match.get("teams") or {}).get(str(match.get("setup_room") or ""), "attack"))
        if match["batting_team"] not in {"attack", "defense"}:
            match["batting_team"] = "attack"
        match["balls"] = 0
        match["scores"] = {"attack": 0, "defense": 0}
        match["wickets"] = {"attack": 0, "defense": 0}
        match["out_players"] = {"attack": [], "defense": []}
        match["turns"] = {"attack": 0, "defense": 0}
        match["wicket_streak"] = 0
        match["wicket_streak_bowler"] = ""
        match["choices"] = {}
        self._set_initial_players(match)
        match["player_scores"] = {
            str(player).lstrip("@"): 0
            for item in participants
            for player in item.get("players", [])
            if str(player).strip()
        }
        match["player_balls"] = {str(player).lstrip("@"): 0 for item in participants for player in item.get("players", []) if str(player).strip()}
        match["player_hat_tricks"] = {str(player).lstrip("@"): 0 for item in participants for player in item.get("players", []) if str(player).strip()}
        match["player_ducks"] = {str(player).lstrip("@"): 0 for item in participants for player in item.get("players", []) if str(player).strip()}
        start_text = (
            "🏏 بدأت مباراة الكريكيت\n"
            + self._team_summary(match, "attack") + "\n"
            + self._team_summary(match, "defense") + "\n"
            + f"🎯 يبدأ الهجوم: {self._team_label(match['batting_team'])}. لكل لاعب 6 كرات في كل دور."
        )
        # The live-start notice contains team rosters and belongs only in the
        # participating rooms. The earlier lobby/Join notice is the one global
        # announcement used to find an opposing room.
        self._emit(data, participants, start_text)
        self._emit_room_messages(data, self._turn_messages(match))

    def _team_player_count(self, match: dict[str, Any], team: str) -> int:
        if team == BOT_TEAM_KEY:
            return 1
        if match.get("mode") == "solo" and (match.get("teams") or {}).get(BOT_TEAM_KEY) == team:
            return 1
        participant = self._room_for_team(match, team)
        if participant:
            return max(1, len(participant.get("players", [])))
        return 1

    def _balls_per_innings(self, match: dict[str, Any]) -> int:
        """Give every player six turns; equal-sized rooms therefore share 6 × N balls."""
        players_per_side = max(
            self._team_player_count(match, "attack"),
            self._team_player_count(match, "defense"),
        )
        return self.BALLS_PER_PLAYER * max(1, players_per_side)

    def _team_summary(self, match: dict[str, Any], team: str) -> str:
        participant = self._room_for_team(match, team)
        if participant:
            players = [str(player).strip().lstrip("@") for player in participant.get("players", []) if str(player).strip()]
            names = "، ".join(f"@{player}" for player in players) or "لاعبون قيد الانضمام"
            return f"👥 {self._team_label(team)} — {participant['name']} ({len(players)} لاعبين): {names}"
        if match.get("mode") == "solo" and (match.get("teams") or {}).get(BOT_TEAM_KEY) == team:
            return f"🤖 {self._team_label(team)} — {self.bot_name} (1 لاعب)"
        return f"👥 {self._team_label(team)} — الفريق"

    def _finish(self, data: dict[str, Any], match: dict[str, Any], participants: list[dict[str, Any]]) -> None:
        scores = match.get("scores") or {}
        attack_score = int(scores.get("attack", 0))
        defense_score = int(scores.get("defense", 0))
        winner_team = "attack" if attack_score > defense_score else "defense" if defense_score > attack_score else "tie"
        stats = data.setdefault("stats", {})
        teams_for_stats = match.get("teams") or {}
        def update_stats(players, won):
            for player in players:
                name = str(player).strip().lstrip("@")
                key = _user_key(name)
                row = stats.setdefault(key, {"username": name, "wins": 0, "losses": 0, "matches": 0, "attack_balls": 0, "runs": 0, "hat_tricks": 0, "ducks": 0})
                row["username"] = name
                row["matches"] = int(row.get("matches", 0)) + 1
                row["wins" if won else "losses"] = int(row.get("wins" if won else "losses", 0)) + 1
                row["runs"] = int(row.get("runs", 0)) + int((match.get("player_scores") or {}).get(name, 0) or 0)
                row["attack_balls"] = int(row.get("attack_balls", 0)) + int((match.get("player_balls") or {}).get(name, 0) or 0)
                row["hat_tricks"] = int(row.get("hat_tricks", 0)) + int((match.get("player_hat_tricks") or {}).get(name, 0) or 0)
                row["ducks"] = int(row.get("ducks", 0)) + int((match.get("player_ducks") or {}).get(name, 0) or 0)
        for team_name, team_code in (("attack", "attack"), ("defense", "defense")):
            participant = self._room_for_team(match, team_code)
            players = [str(p).strip() for p in (participant or {}).get("players", []) if str(p).strip()]
            if players:
                update_stats(players, winner_team == team_code)
        teams = match.get("teams") or {}
        player_scores = {str(k).lstrip("@"): int(v) for k, v in (match.get("player_scores") or {}).items()}
        prize = 1_000_000_000

        team_rooms: dict[str, dict[str, Any] | None] = {
            "attack": self._room_for_team(match, "attack"),
            "defense": self._room_for_team(match, "defense"),
        }
        if match.get("mode") == "solo":
            human_team = next((value for key, value in teams.items() if key != BOT_TEAM_KEY), "attack")
            human_room = team_rooms.get(human_team)
            human_players = [str(p).lstrip("@") for p in (human_room or {}).get("players", []) if str(p).strip()]
            bot_players = [f"بوت {self.bot_name}"]
            winner = "تعادل" if winner_team == "tie" else ("الفريق البشري" if winner_team == human_team else f"بوت {self.bot_name}")
            winning_players = human_players if winner_team == human_team else []
            team1_name, team2_name = "الفريق البشري", self.bot_name
            team1_players, team2_players = human_players, bot_players
            team1_score = attack_score if human_team == "attack" else defense_score
            team2_score = defense_score if human_team == "attack" else attack_score
        else:
            attack_room = team_rooms.get("attack") or {}
            defense_room = team_rooms.get("defense") or {}
            team1_name = str(attack_room.get("name") or "الغرفة الأولى")
            team2_name = str(defense_room.get("name") or "الغرفة الثانية")
            league_labels = match.get("league_team_names") or {}
            team1_label = str(league_labels.get(attack_room.get("key"), "فريق الهجوم"))
            team2_label = str(league_labels.get(defense_room.get("key"), "فريق الدفاع"))
            team1_players = [str(p).lstrip("@") for p in attack_room.get("players", []) if str(p).strip()]
            team2_players = [str(p).lstrip("@") for p in defense_room.get("players", []) if str(p).strip()]
            team1_score, team2_score = attack_score, defense_score
            winning_players = team1_players if winner_team == "attack" else team2_players if winner_team == "defense" else []
            winner = "تعادل" if winner_team == "tie" else team1_name if winner_team == "attack" else team2_name

        reward_lines = []
        player_awards: dict[str, int] = {}
        if winning_players:
            base, remainder = divmod(prize, len(winning_players))
            points = data.setdefault("points", {})
            wins = data.setdefault("wins", {})
            for index, player in enumerate(winning_players):
                amount = base + (1 if index < remainder else 0)
                key = _user_key(player)
                points[key] = int(points.get(key, 0)) + amount
                wins[key] = int(wins.get(key, 0)) + 1
                player_awards[str(player).lstrip("@")] = amount
                if self.reward:
                    self.reward(player, amount)
                reward_lines.append(f"💰 @{player} +{amount:,} نقطة")

        result_image = self._render_result_image(
            match=match,
            team1_name=team1_name, team1_players=team1_players,
            team1_score=team1_score, team2_name=team2_name,
            team2_players=team2_players, team2_score=team2_score,
            team1_label=locals().get("team1_label", "الفريق البشري"),
            team2_label=locals().get("team2_label", self.bot_name),
            player_scores=player_scores, player_balls=match.get("player_balls") or {}, player_awards=player_awards, winner=winner,
            prize=prize if winning_players else 0,
        )
        images = (result_image,) if result_image else ()
        self._emit(
            data, participants,
            "🏆 انتهت مباراة الكركيت\n━━━━━━━━━━━━\n"
            f"🥇 {team1_name}: {team1_score} نقطة\n"
            f"🥈 {team2_name}: {team2_score} نقطة\n"
            f"👑 النتيجة: {winner}"
            + (f"\n🎁 الجائزة {prize:,} نقطة\n" + "\n".join(reward_lines) if reward_lines else ""),
            images,
        )
        self._remove_match(data, match)

    def _render_result_image(
        self,
        match: dict[str, Any],
        team1_name: str, team1_players: list[str], team1_score: int,
        team2_name: str, team2_players: list[str], team2_score: int,
        team1_label: str, team2_label: str,
        player_scores: dict[str, int], player_balls: dict[str, int], player_awards: dict[str, int], winner: str, prize: int,
    ) -> str | None:
        try:
            from cricket_result import render_result_image
            return render_result_image(
                match_id=str(match.get("id") or uuid.uuid4().hex),
                team1_name=team1_name, team1_players=team1_players, team1_score=team1_score,
                team2_name=team2_name, team2_players=team2_players, team2_score=team2_score,
                team1_label=team1_label, team2_label=team2_label,
                player_scores=player_scores, player_balls=player_balls, player_awards=player_awards,
                winner=winner, prize=prize,
                output_dir=self.state.path.parent / "cricket_media",
            )
        except Exception:
            return None

    def submit_ball(self, room: str, sender: str, number: int) -> str | None:
        room_key = _key(room)
        username = str(sender or "").strip().lstrip("@")
        user_key = _user_key(username)
        try:
            value = int(number)
        except (TypeError, ValueError):
            return "❌ اختر 0 إلى 6."
        if not 0 <= value <= 6:
            return "❌ اختر 0 إلى 6."

        def mutate(data: dict[str, Any]) -> str | None:
            match = self._match_for_room(data, room_key)
            if not isinstance(match, dict) or match.get("stage") != "live":
                return "📭 لا توجد كرة الآن."
            participants = self._participants(match)
            participant = next((item for item in participants if item["key"] == room_key), None)
            if participant is None:
                return "⛔ هذه الغرفة ليست في المباراة."
            enrolled = {_user_key(player) for player in participant.get("players", [])}
            if not user_key or user_key not in enrolled:
                return "⛔ أرسل Join أولاً."

            teams = match.get("teams") or {}
            team = teams.get(room_key)
            batting = str(match.get("batting_team") or "attack")
            bowling = "defense" if batting == "attack" else "attack"
            if team not in {batting, bowling}:
                return "⛔ فريقك غير محدد."

            batter = self._next_player(match, batting, batting=True)
            bowler = self._next_player(match, bowling, batting=False)
            choices = match.setdefault("choices", {})

            # In solo mode Talkin2 chooses automatically whenever it is batting.
            if match.get("mode") == "solo" and batting == (teams.get(BOT_TEAM_KEY) or "") and not choices.get("bat"):
                choices["bat"] = {
                    "value": random.randint(0, 6),
                    "room_key": BOT_TEAM_KEY,
                        "room": f"بوت {self.bot_name}",
                        "sender": f"بوت {self.bot_name}",
                }

            if not choices.get("bat"):
                if team != batting or _user_key(batter) != user_key:
                    return f"⏳ دور المهاجم @{batter}."
                choices["bat"] = {
                    "value": value, "room_key": room_key,
                    "room": participant["name"], "sender": username,
                }
                if match.get("mode") == "solo":
                    bot_value = random.randint(0, 6)
                    bot_choice = {"value": bot_value, "room_key": BOT_TEAM_KEY, "room": f"بوت {self.bot_name}", "sender": f"بوت {self.bot_name}"}
                    bat_choice, bowl_choice = (choices["bat"], bot_choice) if batting == team else (bot_choice, choices["bat"])
                    return self._resolve_ball(data, match, participants, bat_choice, bowl_choice)

                bowl_room = self._room_for_team(match, bowling)
                if bowl_room:
                    self._emit_room_messages(
                        data,
                        {
                            room_key: f"🏏 تم تسجيل ضربة @{username}",
                            bowl_room["key"]: f"🛡️ دورك يا @{bowler}\nأرسل 0 إلى 6",
                        },
                    )
                return None

            if not choices.get("bowl"):
                if team != bowling or _user_key(bowler) != user_key:
                    return f"⏳ دور المدافع @{bowler}."
                choices["bowl"] = {
                    "value": value, "room_key": room_key,
                    "room": participant["name"], "sender": username,
                }
                return self._resolve_ball(data, match, participants, choices["bat"], choices["bowl"])

            return "⏳ انتظر الكرة التالية."

        return self.state.mutate(mutate)

    def _resolve_ball(
        self, data: dict[str, Any], match: dict[str, Any],
        participants: list[dict[str, Any]], bat_choice: dict[str, Any], bowl_choice: dict[str, Any],
    ) -> None:
        batting = str(match.get("batting_team") or "attack")
        bowling = "defense" if batting == "attack" else "attack"
        bat_value, bowl_value = int(bat_choice["value"]), int(bowl_choice["value"])
        ball_no = int(match.get("balls", 0)) + 1
        wickets = match.setdefault("wickets", {"attack": 0, "defense": 0})
        scores = match.setdefault("scores", {"attack": 0, "defense": 0})
        player_scores = match.setdefault("player_scores", {})
        # The per-ball card writes the submitted number over the action image.
        # A dismissal uses the dedicated OUT card instead of a number tile.
        images = []
        out_name = str(bat_choice.get("sender") or "اللاعب").strip().lstrip("@")
        bowler_name = str(bowl_choice.get("sender") or "المدافع").strip().lstrip("@")

        if bat_value == bowl_value:
            images.append("cricket_out.png")
            wickets[batting] = int(wickets.get(batting, 0)) + 1
            match.setdefault("out_players", {"attack": [], "defense": []}).setdefault(batting, []).append(out_name)
            outcome = f"💥 OUT — @{out_name}"
            # A duck is a dismissal before the batter has scored, even when
            # it happens after earlier balls by other players or in innings 2.
            if int(player_scores.get(out_name, 0)) == 0:
                duck_counts = match.setdefault("player_ducks", {})
                duck_counts[out_name] = int(duck_counts.get(out_name, 0)) + 1
                outcome += " 🦆 بطّة"
            # Talkin2's cricket rule counts a hat-trick when the same defender
            # dismisses two batters consecutively in the current innings. The
            # bowler key prevents two different defenders from being credited.
            previous_bowler = _user_key(match.get("wicket_streak_bowler", ""))
            current_bowler = _user_key(bowler_name)
            if previous_bowler == current_bowler:
                match["wicket_streak"] = int(match.get("wicket_streak", 0)) + 1
            else:
                match["wicket_streak"] = 1
            match["wicket_streak_bowler"] = bowler_name
            if int(match["wicket_streak"]) == 2:
                hat_tricks = match.setdefault("player_hat_tricks", {})
                hat_tricks[bowler_name] = int(hat_tricks.get(bowler_name, 0)) + 1
                outcome += f" 🔥 هاتريك — @{bowler_name}"
        else:
            match["wicket_streak"] = 0
            match["wicket_streak_bowler"] = ""
            scores[batting] = int(scores.get(batting, 0)) + bat_value
            player_scores[out_name] = int(player_scores.get(out_name, 0)) + bat_value
            outcome = f"🏏 +{bat_value} نقطة"

        try:
            from cricket_result import render_ball_image
            batting_room = self._room_for_team(match, batting)
            first_room_key = str(participants[0].get("key") or "") if participants else ""
            # Team identity is fixed by roster order, not by the current role:
            # room/team 1 is always blue and room/team 2 (or the bot) is red.
            team_asset = (
                "cricket_attack_blue.png"
                if batting_room is not None and str(batting_room.get("key") or "") == first_room_key
                else "cricket_defense_red.png"
            )
            images.append(render_ball_image(
                match_id=str(match.get("id") or "match"),
                innings=int(match.get("innings", 1)),
                ball_no=ball_no,
                number=bat_value,
                out=(bat_value == bowl_value),
                team_asset=team_asset,
                output_dir=self.state.path.parent / "cricket_media",
            ))
        except Exception:
            # Gameplay must never fail if an optional image renderer is unavailable.
            images = [team_asset if bat_value != bowl_value else "cricket_out.png"]

        match["balls"] = ball_no
        player_balls = match.setdefault("player_balls", {})
        player_balls[out_name] = int(player_balls.get(out_name, 0)) + 1
        match["choices"] = {}
        turns = match.setdefault("turns", {"attack": 0, "defense": 0})
        turns[batting] = int(turns.get(batting, 0)) + 1
        turns[bowling] = int(turns.get(bowling, 0)) + 1
        self._advance_after_ball(match, batting, bowling, out_name if bat_value == bowl_value else "")
        balls_limit = self._balls_per_innings(match)
        current_wickets = int(wickets.get(batting, 0))
        total = int(scores.get(batting, 0))

        # Show both submitted values so the attacker can see the defender's
        # attempted block. The batter's number image is still delivered first.
        result_text = (
            f"🏏 المهاجم @{out_name} أرسل رقم {bat_value}\n"
            f"🛡️ المدافع @{bowler_name} أرسل رقم {bowl_value} لمحاولة الصد\n"
            f"{outcome}\n📊 {total} نقطة • الكرة {ball_no}/{balls_limit} لهذا الشوط"
        )
        attack_room = self._room_for_team(match, batting)
        defense_room = self._room_for_team(match, bowling)
        result_rooms = [item for item in (attack_room, defense_room) if item]

        attack_score = int(scores.get("attack", 0))
        innings_over = ball_no >= balls_limit or current_wickets >= self._team_player_count(match, batting)
        if innings_over:
            self._emit(data, result_rooms, result_text, tuple(images))
            if int(match.get("innings", 1)) == 1:
                first_innings_score = int(scores.get(batting, 0))
                match["innings"] = 2
                match["batting_team"] = bowling
                match["balls"] = 0
                match["wickets"][bowling] = 0
                match.setdefault("out_players", {})[bowling] = []
                match["turns"] = {"attack": 0, "defense": 0}
                match["wicket_streak"] = 0
                match["wicket_streak_bowler"] = ""
                match["choices"] = {}
                self._set_initial_players(match)
                target = first_innings_score + 1
                self._emit(data, participants, f"🏁 الشوط الأول انتهى\n🎯 الهدف: {target}")
                self._emit_room_messages(data, self._turn_messages(match))
            else:
                self._finish(data, match, participants)
            return None

        self._emit(data, result_rooms, result_text, tuple(images))
        self._emit_room_messages(data, self._turn_messages(match))
        return None

    def get_points(self, username: str) -> int:
        key = _user_key(username)
        data = self.state.load()
        return int((data.get("points") or {}).get(key, 0)) if key else 0

    # Public aliases used by the Talkin controller integration.
    def points_for(self, username: str) -> int:
        return self.get_points(username)

    def leaderboard(self, limit: int = 10) -> list[tuple[str, int, int]]:
        data = self.state.load()
        points = data.get("points") or {}
        wins = data.get("wins") or {}
        rows = [(str(name), int(value), int(wins.get(name, 0))) for name, value in points.items()]
        rows.sort(key=lambda item: (-item[1], -item[2], item[0]))
        return rows[:max(1, int(limit))]

    @staticmethod
    def player_title(attack_balls: int) -> str:
        if 20 <= attack_balls <= 24:
            return "أسطورة المضرب"
        if 15 <= attack_balls <= 18:
            return "ملك الكرات"
        if 8 <= attack_balls <= 12:
            return "محترف الهجوم"
        return "لاعب كركيت"

    def player_stats(self, username: str) -> dict[str, Any]:
        key = _user_key(username)
        data = self.state.load()
        row = dict((data.get("stats") or {}).get(key) or {})
        row.setdefault("username", str(username).strip().lstrip("@"))
        for field in ("wins", "losses", "matches", "attack_balls", "runs", "hat_tricks", "ducks"):
            row[field] = int(row.get(field, 0) or 0)
        row["title"] = self.player_title(row["attack_balls"])
        return row

    def champions(self, limit: int = 10) -> list[dict[str, Any]]:
        data = self.state.load()
        rows = []
        for row in (data.get("stats") or {}).values():
            if isinstance(row, dict):
                item = dict(row)
                item["wins"] = int(item.get("wins", 0) or 0)
                item["losses"] = int(item.get("losses", 0) or 0)
                rows.append(item)
        rows.sort(key=lambda item: (-item["wins"], item["losses"], str(item.get("username", ""))))
        return rows[:max(1, int(limit))]

    def current(self, room: str | None = None) -> dict[str, Any] | None:
        data = self.state.load()
        if room is not None:
            return self._match_for_room(data, room)
        matches = sorted(
            self._matches(data).values(),
            key=lambda item: float(item.get("created_at") or 0),
        )
        return matches[0] if matches else None

    def matches(self) -> list[dict[str, Any]]:
        data = self.state.load()
        return sorted(
            self._matches(data).values(),
            key=lambda item: float(item.get("created_at") or 0),
        )

    def latest_event_id(self, room: str) -> int:
        room_key = _key(room)
        data = self.state.load()
        events = data.get("events", [])
        return max(
            (int(item.get("id", 0)) for item in events
             if isinstance(item, dict) and item.get("room_key") == room_key),
            default=0,
        )

    def events_after(self, room: str, event_id: int) -> list[dict[str, Any]]:
        room_key = _key(room)
        data = self.state.load()
        return [
            dict(item) for item in data.get("events", [])
            if isinstance(item, dict)
            and item.get("room_key") == room_key
            and int(item.get("id", 0)) > int(event_id)
        ]
