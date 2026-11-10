"""Talkin2 adapter for the standalone persistent cricket engine."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable

from .cricket_game import CricketGame
from .cricket_league import CricketLeague


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_CONTROL_CHARS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه"})


class CricketIntegration:
    """Route cricket commands and deliver engine events through Talkin2."""

    # Legacy control aliases are intentionally rejected outside the exact DM flow.
    START_COMMANDS = {
        "تشغيل الكركت", "تشغيل الكركيت", "تشغيل الكريكيت", "cricket on", ".cr 1",
    }
    STOP_COMMANDS = {
        "إيقاف الكركت", "ايقاف الكركت", "إيقاف الكركيت", "ايقاف الكركيت",
        "إيقاف الكريكيت", "ايقاف الكريكيت", "cricket off", ".ct 0",
    }
    PRIVATE_START_COMMANDS = {
        "تشغيل لعبه الكركت", "تشغيل لعبه الكركيت", "تشغيل لعبه الكريكت", "تشغيل لعبه الكريكيت",
        "start cricket game", ".cr 1", ".cr1",
    }
    PRIVATE_STOP_COMMANDS = {
        "ايقاف لعبه الكركت", "ايقاف لعبه الكركيت", "ايقاف لعبه الكريكت", "ايقاف لعبه الكريكيت",
        "stop cricket game", ".cr 0", ".cr0",
    }

    def __init__(
        self,
        data_dir: str | Path,
        *,
        persist: Callable | None = None,
        is_master: Callable[[str], bool],
        is_configured_master: Callable[[str], bool] | None = None,
        is_verified: Callable[[str], bool] | None = None,
        send_room_text: Callable[[str, str], object],
        send_room_media: Callable[[str, str, str], object],
        send_all_rooms_text: Callable[[str], object] | None = None,
        send_private_text: Callable[[str, str], object] | None = None,
        public_base: Callable[[], str],
        reward: Callable[[str, int], object] | None = None,
        bot_name: str = "Talkin2",
        log: Callable[..., object] = print,
    ):
        self.game = CricketGame(
            Path(data_dir) / "cricket_state.json",
            persist=persist,
            reward=reward,
            bot_name=bot_name,
        )
        self.league = CricketLeague(Path(data_dir) / "cricket_league.json", persist=persist)
        self.is_master = is_master
        self.is_configured_master = is_configured_master or is_master
        self.is_verified = is_verified or (lambda _username: True)
        self.send_room_text = send_room_text
        self.send_room_media = send_room_media
        self.send_all_rooms_text = send_all_rooms_text
        self.send_private_text = send_private_text
        self.public_base = public_base
        self.log = log
        self._cursors: dict[str, int] = {}
        self._resume_pending = False
        self._broadcast_cursor = self.game.latest_event_id("")

        # Start from the last persisted room events so a restart does not replay
        # any match. Every live room pair is re-prompted on first contact.
        matches = self.game.matches()
        if matches:
            self._resume_pending = True
        for match in matches:
            for room in self._match_rooms(match):
                key = self._room_key(room)
                try:
                    self._cursors[key] = self.game.latest_event_id(room)
                except Exception as exc:
                    self.log("[CRICKET] cursor restore failed", room, repr(exc))
                    self._cursors[key] = 0

    @staticmethod
    def _low(value: str) -> str:
        return str(value or "").strip().casefold().translate(_DIGITS)

    @classmethod
    def _control_key(cls, value: str) -> str:
        return cls._low(value).translate(_CONTROL_CHARS)

    @staticmethod
    def _room_key(room: str) -> str:
        return " ".join(str(room or "").strip().casefold().split())

    def _prime_room_cursor(self, room: str, *, force: bool = False) -> None:
        """Skip archived room events before creating a new match in that room."""
        name = str(room or "").strip()
        key = self._room_key(name)
        if name and key and (force or key not in self._cursors):
            self._cursors[key] = self.game.latest_event_id(name)

    @staticmethod
    def _match_rooms(match: dict | list[dict] | None) -> list[str]:
        match_list = match if isinstance(match, list) else [match]
        rooms: list[str] = []
        seen: set[str] = set()
        for current in match_list:
            if not isinstance(current, dict):
                continue
            for item in current.get("rooms", []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                key = CricketIntegration._room_key(name)
                if name and key not in seen:
                    seen.add(key)
                    rooms.append(name)
        return rooms

    def _asset_url(self, filename: str) -> str:
        base = str(self.public_base() or "").rstrip("/")
        if not base:
            return ""
        if str(filename).startswith("cricket_result_"):
            return f"{base}/cricket-media/{filename}"
        return f"{base}/assets/{filename}"

    def deliver(self, room: str, *, skip_text_event_ids: set[int] | None = None) -> None:
        room = str(room or "").strip()
        if not room:
            return
        room_key = self._room_key(room)
        cursor = int(self._cursors.get(room_key, 0))
        skip_text_event_ids = skip_text_event_ids or set()
        try:
            events = self.game.events_after(room, cursor)
            for event in events:
                for filename in event.get("images", []) or []:
                    url = self._asset_url(filename)
                    if url:
                        try:
                            self.send_room_media(room, url, "image")
                        except Exception as exc:
                            self.log("[CRICKET] image delivery failed", room, repr(exc))
                text = str(event.get("text") or "").strip()
                event_id = int(event.get("id", cursor) or cursor)
                if event.get("broadcast"):
                    if text and event_id > self._broadcast_cursor:
                        # Start-of-match notices carry one shared event ID across
                        # participant rooms, so a bot-wide announcement is sent
                        # once even though each participant cursor sees it.
                        if self.send_all_rooms_text:
                            self.send_all_rooms_text(text)
                        else:
                            self.send_room_text(room, text)
                        self._broadcast_cursor = event_id
                elif text and event_id not in skip_text_event_ids:
                    self.send_room_text(room, text)
                cursor = max(cursor, event_id)
            self._cursors[room_key] = cursor
        except Exception as exc:
            self.log("[CRICKET] event delivery failed", room, repr(exc))

    def deliver_rooms(
        self,
        rooms: Iterable[str],
        *,
        skip_text_event_ids_by_room: dict[str, set[int]] | None = None,
    ) -> None:
        skip_text_event_ids_by_room = skip_text_event_ids_by_room or {}
        sent: set[str] = set()
        for room in rooms:
            name = str(room or "").strip()
            key = self._room_key(name)
            if name and key not in sent:
                sent.add(key)
                self.deliver(name, skip_text_event_ids=skip_text_event_ids_by_room.get(key, set()))

    def _prime_new_room_cursor(self, room: str, match: dict | None) -> None:
        """Skip historical events when a room joins an already-open match."""
        room_key = self._room_key(room)
        if not room_key:
            return
        if not isinstance(match, dict):
            self._cursors[room_key] = self.game.latest_event_id(room)
            return
        current_rooms = {self._room_key(item) for item in self._match_rooms(match)}
        if room_key not in current_rooms:
            # The Join action will create fresh events for this room. Starting
            # at the current high-water mark prevents old match images from
            # being replayed before those new events are delivered.
            self._cursors[room_key] = self.game.latest_event_id(room)

    def _deliver_transition(
        self,
        previous_match: dict | None,
        fallback_room: str = "",
        *,
        skip_text_event_ids_by_room: dict[str, set[int]] | None = None,
    ) -> None:
        # Events are generated independently for both room keys. Deliver them
        # together after each action so the opposing team receives its prompt.
        rooms = self._match_rooms(previous_match)
        rooms.extend(self._match_rooms(self.game.current(fallback_room) if fallback_room else None))
        if fallback_room:
            rooms.append(str(fallback_room).strip())
        self.deliver_rooms(rooms, skip_text_event_ids_by_room=skip_text_event_ids_by_room)

    def _resume_after_restart(self) -> None:
        if not self._resume_pending:
            return
        self._resume_pending = False
        for match in self.game.matches():
            stage = str(match.get("stage") or "")
            turn_messages = {}
            pending_bowler_key = ""
            pending_bowler_name = ""
            if stage == "live":
                choices = match.get("choices") or {}
                if choices.get("bat") and not choices.get("bowl"):
                    batting = str(match.get("batting_team") or "attack")
                    bowling = "defense" if batting == "attack" else "attack"
                    try:
                        bowler = self.game._next_player(match, bowling, batting=False)
                        bowl_room = self.game._room_for_team(match, bowling)
                        pending_bowler_key = str((bowl_room or {}).get("key") or "")
                        pending_bowler_name = str(bowler or "")
                    except Exception as exc:
                        self.log("[CRICKET] resume bowler lookup failed", repr(exc))
                else:
                    try:
                        turn_messages = self.game._turn_messages(match)
                    except Exception as exc:
                        self.log("[CRICKET] resume turn lookup failed", repr(exc))
            for item in match.get("rooms", []):
                if not isinstance(item, dict):
                    continue
                room = str(item.get("name") or "").strip()
                if not room:
                    continue
                if stage == "live":
                    if pending_bowler_key:
                        if str(item.get("key") or "") == pending_bowler_key:
                            prompt = f"🛡️ استؤنفت الكرة؛ دورك يا @{pending_bowler_name}. أرسل 0 إلى 6"
                        else:
                            prompt = "✅ حُفظ اختيار الهجوم لهذه الكرة؛ انتظر رد فريق الدفاع."
                    else:
                        prompt = str(turn_messages.get(item.get("key")) or "").strip()
                    text = "🔄 استؤنفت مباراة الكركيت بعد إعادة تشغيل البوت.\n" + prompt
                elif stage == "setup":
                    text = "🔄 استؤنف إعداد مباراة الكركيت. اختر عدد اللاعبين من 1 إلى 4."
                elif stage == "teams":
                    text = "🔄 استؤنفت مباراة الكركيت. ترسل غرفة الإعداد 1 للهجوم أو 2 للدفاع؛ ويُعيّن دور الغرفة الثانية تلقائيًا."
                else:
                    target = int(match.get("target_players") or 1)
                    text = (
                        f"🔄 استؤنفت قائمة الكركيت؛ المطلوب {target} لاعب(ين) في كل غرفة. "
                        "تنضم الغرفة الثانية بإرسال Join؛ وعند تعدد القوائم يُقرن الطلب بأقدم فريق مكتمل."
                    )
                try:
                    self.send_room_text(room, text)
                except Exception as exc:
                    self.log("[CRICKET] resume prompt failed", room, repr(exc))

    def _send_private(self, sender: str, room: str, text: str) -> None:
        if self.send_private_text:
            self.send_private_text(sender, text)
        elif room:
            self.send_room_text(room, text)

    def _private_toggle(self, room: str, sender: str, enabled: bool) -> bool:
        if not self.is_configured_master(sender):
            self._send_private(sender, room, "🔒 تشغيل وإيقاف الكركيت متاحان للماستر المحدد في متغيرات السيرفر فقط.")
            return True
        if not room:
            self._send_private(sender, room, "❌ لم أجد غرفة متصلة لبدء لعبة الكركيت.")
            return True

        previous_match = self.game.matches()
        result = self.game.set_enabled(room, enabled)
        if str(result or "").startswith(("❌", "⛔")) and enabled:
            self._send_private(sender, room, str(result))
            return True

        if enabled:
            if not previous_match:
                confirmation = "✅ تم تشغيل ملفات لعبة الكركيت. أرسل .cr 1 داخل الغرفة لفتح إعداد المباراة للأعضاء."
            else:
                confirmation = "✅ ملفات لعبة الكركيت مفعّلة؛ المباراة المفتوحة مستمرة دون تغيير."
        else:
            if str(result or "").startswith("❌"):
                self._send_private(sender, room, str(result))
                return True
            confirmation = "⛔ تم إيقاف لعبة الكركيت وإلغاء المباراة المفتوحة."

        self._deliver_transition(previous_match)
        self._send_private(sender, room, confirmation)
        return True

    def _private_league_command(self, room: str, sender: str, text: str) -> bool:
        """Master-only league administration; registration stays in DM."""
        low = self._control_key(text)
        menu_key = self._control_key(sender)
        menu_pages = getattr(self, "_master_command_menu_pages", None)
        if not isinstance(menu_pages, dict):
            menu_pages = self._master_command_menu_pages = {}
        if low in {"ns", "n", "التالي", "القائمة التالية", "next"} and menu_key in menu_pages:
            if not self.is_configured_master(sender):
                menu_pages.pop(menu_key, None)
                return True
            page = int(menu_pages.get(menu_key) or 2)
            menu_pages.pop(menu_key, None)
            if page == 2:
                menu_pages[menu_key] = 3
                self._send_private(sender, room, (
                    "📋 قائمة أوامر الماستر — 2\n"
                    "━━━━━━━━━━━━\n"
                    "🔨 المزاد العالمي\n"
                    "• تشغيل لعبة مزاد\n"
                    "• إيقاف لعبة مزاد\n"
                    "• افتح المزاد — من الغرفة\n"
                    "• ابدأ المزاد — من الغرفة\n"
                    "• اغلاق المزاد — إيقاف الجولة وتسوية الفائز\n"
                    "• إغلاق المزاد — إيقاف الجولة وتسوية الفائز\n"
                    "━━━━━━━━━━━━\n"
                    "🏏 أوامر فتح مباريات الدوري\n"
                    "• حالة الدوري\n"
                    "• افتح مباراة الدوري@رمز المباراة\n"
                    "• تغيير غرفة فريق@اسم الفريق@الغرفة الجديدة\n"
                    "• تغير لاعب في الفريق@اسم الفريق@لاعب سابق@لاعب جديد\n"
                    "━━━━━━━━━━━━\n"
                    "🏆 جائزة النهائي: 1000b للفريق الفائز.\n\n"
                    "📌 أرسل ns لصفحة أوامر الإدارة."
                ))
            else:
                self._send_private(sender, room, (
                    "📋 قائمة أوامر الماستر — 3 (الإدارة)\n"
                    "━━━━━━━━━━━━\n"
                    "🏠 الغرف والبث\n"
                    "• دخول@اسم الغرفة أو دخول@غرفة1 غرفة2\n"
                    "• دخول الكل — دخول غرف السيرفر\n"
                    "• غرفي / myrooms — عرض الغرف\n"
                    "• خروج@اسم الغرفة أو خروج — مغادرة غرفة\n"
                    "• رسالة غرفه@النص — بث للغرف\n"
                    "• رسالة خاص@النص — بث خاص للأعضاء\n"
                    "• say النص — إرسال نص في الغرفة\n"
                    "━━━━━━━━━━━━\n"
                    "🛡️ الإدارة والحماية\n"
                    "• حماية — قائمة حماية الغرفة\n"
                    "• mf@كلمة / clear@mf — كلمات الفلتر\n"
                    "• amf@كلمة — إضافة كلمة محظورة\n"
                    "• inv / دعوات / invmsg@النص — الدعوات\n"
                    "• m@ / a@ / o@ / k@ / u@ — إدارة الرتب\n"
                    "• mas@اسم / umas@اسم — إدارة الماستر\n"
                    "• sb@اسم@المبلغ / -sb@اسم@المبلغ — تحويل وخصم نقاط\n"
                    "• سجل تحويل / سجل تحويلاتي — السجلات\n"
                    "━━━━━━━━━━━━\n"
                    "📝 الأسماء والردود\n"
                    "• فحص اسم / اصلاح اسم / حذف اسم\n"
                    "• +ds@حرف@الرد — ردود المغادرة\n"
                    "• توديع@on أو توديع@off\n"
                    "• +sr@المفتاح@الرد — رد تلقائي\n"
                    "• l@sr — عرض الردود المحفوظة\n"
                    "━━━━━━━━━━━━\n"
                    "✅ انتهت قوائم الماستر."
                ))
            return True
        if low in {"اوامر الدوري", "أوامر الدوري", "اوامر المزاد", "أوامر المزاد", "اوامر الدوري والمزاد", "أوامر الدوري والمزاد", "league auction commands"}:
            if not self.is_configured_master(sender):
                self._send_private(sender, room, "🔒 قائمة أوامر الدوري والمزاد للماستر المحدد فقط.")
                return True
            menu_pages[menu_key] = 2
            menu = (
                "📋 قائمة أوامر الماستر — 1\n"
                "━━━━━━━━━━━━\n"
                "🏏 دوري الكركيت\n"
                "• سجل فريق@اسم الفريق@الغرفة@لاعب1\n"
                "  لاعب2 لاعب3 لاعب4\n"
                "• حالة الدوري\n"
                "• فتح الدوري\n"
                "• افتح مباراة الدوري@رمز المباراة\n"
                "• تغيير غرفة فريق@اسم الفريق@الغرفة الجديدة\n"
                "• تغير لاعب في الفريق@اسم الفريق@لاعب سابق@لاعب جديد\n"
                "━━━━━━━━━━━━\n"
                "📌 للقائمة الثانية أرسل ns"
            )
            self._send_private(sender, room, menu)
            return True
        if low in {"حاله الدوري", "حالة الدوري", "league status", "دوري الكركيت"}:
            if not self.is_configured_master(sender):
                self._send_private(sender, room, "🔒 إدارة دوري الكركيت للماستر المحدد فقط.")
                return True
            self._send_private(sender, room, self.league.summary())
            return True
        if low in {"فتح الدوري", "افتح الدوري", "open cricket league", "open league"}:
            if not self.is_configured_master(sender):
                self._send_private(sender, room, "🔒 فتح دوري الكركيت للماستر المحدد فقط.")
                return True
            result = self.league.open()
            if not result.get("ok"):
                self._send_private(sender, room, f"❌ {result.get('error')}")
            else:
                self._send_private(sender, room, "✅ تم فتح دوري الكركيت وتحديد جدول التصفيات:\n" + self.league.summary())
            return True
        open_match = re.fullmatch(r"(?:افتح مباراة الدوري|فتح مباراة الدوري|ابدأ مباراة الدوري|start league match)@?\s*([a-f0-9]{4,}|[^@\s]+)", str(text or "").strip(), re.I)
        if open_match:
            if not self.is_configured_master(sender):
                self._send_private(sender, room, "🔒 فتح مباريات الدوري للماستر المحدد فقط.")
                return True
            selected = self.league.open_match(open_match.group(1))
            if not selected.get("ok"):
                self._send_private(sender, room, f"❌ {selected.get('error')}")
                return True
            match = selected["match"]
            teams = {str(item.get("name")): item for item in self.league.status().get("teams", {}).values()}
            team1, team2 = teams.get(str(match.get("team1"))), teams.get(str(match.get("team2")))
            if not team1 or not team2:
                self._send_private(sender, room, "❌ بيانات فريقي المباراة غير مكتملة.")
                return True
            previous = self.game.current(str(team1.get("room") or ""))
            result = self.game.open_league_match(
                team1.get("room"), team2.get("room"), team1.get("name"), team2.get("name"),
                team1.get("players", []), team2.get("players", []),
            )
            if result:
                self._send_private(sender, room, result)
            else:
                self._deliver_transition(previous, str(team1.get("room") or ""))
                self._send_private(sender, room, f"✅ فُتحت المباراة {match.get('id', '')[:6]} في الغرفتين، وتم إرسال أسماء اللاعبين وطلب Join.")
            return True
        register = re.fullmatch(r"(?:سجل فريق|تسجيل فريق|register team)@([^@]+)@([^@]+)@(.+)", str(text or "").strip(), re.I | re.S)
        if register:
            if not self.is_configured_master(sender):
                self._send_private(sender, room, "🔒 تسجيل فرق الدوري للماستر المحدد فقط.")
                return True
            team, team_room, raw_players = register.groups()
            # Players may be sent one per line or separated by spaces; commas
            # remain accepted for backward compatibility.
            players = [item.strip() for item in re.split(r"[\s,،|]+", raw_players) if item.strip()]
            result = self.league.register(team, team_room, players)
            self._send_private(sender, room, (f"✅ تم تسجيل فريق {team} في الغرفة {team_room} ({len(players)}/4)." if result.get("ok") else f"❌ {result.get('error')}"))
            return True
        change = re.fullmatch(r"(?:غير غرفة فريق|تغيير غرفة فريق|change league room)@([^@]+)@(.+)", str(text or "").strip(), re.I | re.S)
        if change:
            if not self.is_configured_master(sender):
                self._send_private(sender, room, "🔒 تغيير غرف الدوري للماستر المحدد فقط.")
                return True
            result = self.league.change_room(change.group(1), change.group(2).strip())
            self._send_private(sender, room, "✅ تم تغيير غرفة الفريق." if result.get("ok") else f"❌ {result.get('error')}")
            return True
        change_player = re.fullmatch(
            r"(?:تغير لاعب في الفريق|تغيير لاعب في الفريق|غير لاعب في الفريق|change league player)@([^@]+)@([^@]+)@(.+)",
            str(text or "").strip(), re.I | re.S,
        )
        if change_player:
            if not self.is_configured_master(sender):
                self._send_private(sender, room, "🔒 تغيير لاعبي فرق الدوري للماستر المحدد فقط.")
                return True
            team_name, previous_player, new_player = change_player.groups()
            result = self.league.change_player(team_name, previous_player, new_player)
            self._send_private(sender, room, "✅ تم تغيير لاعب الفريق." if result.get("ok") else f"❌ {result.get('error')}")
            return True
        return False

    def _verified_room_setup(self, room: str, sender: str, *, bot_match: bool = False) -> bool:
        if not self.is_verified(sender):
            self.send_room_text(room, "🔒 لعبة الكركيت متاحة للأعضاء الموثقين فقط.")
            return True
        previous_match = self.game.current(room)
        if previous_match is None:
            self._prime_room_cursor(room, force=True)
        result = (self.game.begin_bot_setup(room)
                  if bot_match else self.game.begin_setup(room))
        self._reply_error(room, result)
        if result is None:
            self._deliver_transition(previous_match, room)
        return True

    def _reply_error(self, room: str, result: str | None) -> None:
        if result:
            self.send_room_text(room, str(result))

    def _verified_room_start(self, room: str, sender: str, player_count: int, *, room_match: bool = False) -> bool:
        if not self.is_verified(sender):
            self.send_room_text(room, "🔒 لعبة الكركيت متاحة للأعضاء الموثقين فقط.")
            return True
        previous_match = self.game.current(room)
        if previous_match is None or previous_match.get("stage") == "setup":
            self._prime_room_cursor(room, force=True)
        if isinstance(previous_match, dict) and previous_match.get("stage") == "setup":
            result = self.game.select_player_count(room, player_count)
        else:
            result = self.game.start(room, player_count, mode="rooms" if room_match else "solo")
        self._reply_error(room, result)
        if result is None:
            self._deliver_transition(previous_match, room)
        return True

    def handle(self, room: str, sender: str, text: str, *, is_private: bool = False) -> bool:
        """Return True when the message belongs to cricket."""
        low = self._low(text)
        control = self._control_key(text)
        room = str(room or "").strip()
        sender = str(sender or "").strip().lstrip("@")
        if low in {"لعبي", "my cricket", "my cricket stats"}:
            stats = self.game.player_stats(sender)
            message = (f"🏏 إحصائيات @{stats['username']}\n"
                       f"🏆 الفوز: {stats['wins']} | الخسارة: {stats['losses']} | المباريات: {stats['matches']}\n"
                       f"🎯 كرات الهجوم: {stats['attack_balls']} | النقاط: {stats['runs']}\n"
                       f"🔥 هاتريك: {stats['hat_tricks']} | 🦆 بطّة: {stats['ducks']}\n"
                       f"👑 اللقب: {stats['title']}")
            self._send_private(sender, room, message) if is_private else self.send_room_text(room, message)
            return True
        if low in {"ابطال الكركيت", "أبطال الكركيت", "cricket champions", "cricket top"}:
            rows = self.game.champions(15)
            lines = ["🏆 أبطال الكركيت — أفضل 15 لاعبًا"]
            lines.extend(
                f"{i}. @{str(row.get('username', ''))[:18]} ف:{int(row.get('wins', 0))} خ:{int(row.get('losses', 0))}"
                for i, row in enumerate(rows, 1)
            )
            message = "\n".join(lines)
            if len(message.encode("utf-8")) > 980:
                message = message.encode("utf-8")[:977].decode("utf-8", "ignore") + "..."
            self._send_private(sender, room, message) if is_private else self.send_room_text(room, message)
            return True
        join_aliases = {
            "انضمام كركيت", "انضمام كريكيت", "انضمام كركت",
            "انضمام لعبه كركيت", "انضمام لعبه كريكيت", "انضمام لعبه الكركيت",
            "join cricket", "join cricket game",
        }
        join_command = low in {"join", "انضمام"} or control in join_aliases
        defer_resume = not is_private and join_command
        if not defer_resume:
            self._resume_after_restart()

        manual_league_open = re.fullmatch(
            r"(?:افتح مباراة الدوري|فتح مباراة الدوري|ابدأ مباراة الدوري|start league match)@?\s*([a-f0-9]{4,}|[^@\s]+)",
            str(text or "").strip(), re.I,
        )
        if (is_private or manual_league_open) and self._private_league_command(room, sender, text):
            return True

        if control in self.PRIVATE_START_COMMANDS | self.PRIVATE_STOP_COMMANDS:
            if is_private:
                return self._private_toggle(room, sender, control in self.PRIVATE_START_COMMANDS)
            if control in self.PRIVATE_START_COMMANDS and control in {".cr 1", ".cr1"}:
                return self._verified_room_setup(room, sender)
            if control in self.PRIVATE_STOP_COMMANDS and control in {".cr 0", ".cr0"}:
                if not self.is_verified(sender):
                    self.send_room_text(room, "🔒 إيقاف مباراة الكركيت متاح للأعضاء الموثقين فقط.")
                    return True
                previous_match = self.game.current(room)
                result = self.game.cancel_match(room)
                self._reply_error(room, result)
                if not str(result or "").startswith(("❌", "⛔", "📭")):
                    self._deliver_transition(previous_match, room)
                return True
            else:
                self.send_room_text(room, "🔒 أرسل أمر تشغيل/إيقاف الكركيت في خاص البوت؛ الأمر مخصص للماستر المحدد في المتغيرات.")
                return True

        if is_private and (low in self.START_COMMANDS or low in self.STOP_COMMANDS):
            self._send_private(sender, room, "📌 استخدم «.cr 1» للتشغيل أو «.cr 0» للإيقاف من الخاص للماستر.")
            return True

        if low in self.START_COMMANDS:
            self.send_room_text(room, "🔒 التشغيل والإيقاف من خاص البوت للماستر المحدد فقط: «.cr 1» للتشغيل و«.cr 0» للإيقاف.")
            return True

        if low in self.STOP_COMMANDS:
            if not is_private:
                self.send_room_text(room, "🔒 إيقاف الكركيت من خاص الماستر فقط: «.cr 0».")
                return True
            self._send_private(sender, room, "📌 استخدم «.cr 0» من الخاص للماستر المحدد في المتغيرات.")
            return True

        solo_start = re.fullmatch(r"(?:\.cricket|cricket|كركيت|كريكت|كريكيت)\s+([1-4])", low)
        if solo_start:
            if is_private:
                self._send_private(sender, room, "📌 افتح مباراة cricket 1..4 داخل الغرفة، وليس في الخاص.")
                return True
            return self._verified_room_start(room, sender, int(solo_start.group(1)))

        room_count = re.fullmatch(r"\.cr\s*([1-4])", low)
        if room_count and not is_private:
            count = int(room_count.group(1))
            if count == 1:
                return self._verified_room_setup(room, sender)
            return self._verified_room_start(room, sender, count, room_match=True)
        if re.fullmatch(r"\.cr\s*b", low) and not is_private:
            return self._verified_room_setup(room, sender, bot_match=True)

        # Private messages can toggle the service only. Player actions remain room-scoped.
        if is_private:
            return False

        match = self.game.current(room)
        if join_command and not isinstance(match, dict):
            match = self.game.match_for_join(room)
        if not isinstance(match, dict) and not join_command:
            return False

        stage = str((match or {}).get("stage") or "")
        room_key = self._room_key(room)
        participant_keys = {
            self._room_key(item.get("name"))
            for item in (match or {}).get("rooms", [])
            if isinstance(item, dict) and item.get("name")
        }
        is_action = (
            join_command
            or (stage == "setup" and low in {"1", "2", "3", "4"})
            or (stage == "lobby" and low in {"bot", "بوت", "ضد البوت", "solo", "vs bot"})
            or (stage == "teams" and low in {"1", "2"})
            or (stage == "live" and room_key in participant_keys
                and re.fullmatch(r"[0-6]", low) is not None)
        )
        if not is_action:
            return False
        if not self.is_verified(sender):
            self.send_room_text(room, "🔒 لعبة الكركيت متاحة للأعضاء الموثقين فقط.")
            if defer_resume:
                self._resume_after_restart()
            return True

        previous_match = match
        result: str | None = None
        skip_text_event_ids_by_room: dict[str, set[int]] = {}
        if join_command:
            self._prime_new_room_cursor(room, previous_match)
            result = self.game.join(room, sender)
            if result is None:
                # Confirm the committed Join before any team-wide announcements,
                # image delivery, or restart prompts can delay the player.
                updated_match = self.game.current(room) or {}
                participants = [item for item in updated_match.get("rooms", []) if isinstance(item, dict)]
                team_index = next(
                    (index for index, item in enumerate(participants)
                     if self._room_key(item.get("name")) == room_key),
                    -1,
                )
                if updated_match.get("mode") == "solo":
                    team_label = "فريقك"
                else:
                    team_label = "الفريق الأول" if team_index <= 0 else "الفريق الثاني"
                try:
                    self.send_room_text(room, f"🏏 تم الانضمام إلى {team_label} يا @{sender}.")
                except Exception as exc:
                    self.log("[CRICKET] immediate Join confirmation failed", room, repr(exc))
                try:
                    cursor = int(self._cursors.get(room_key, 0))
                    duplicate_text = f"✅ انضم @{sender}."
                    duplicate_ids = {
                        int(event.get("id", 0)) for event in self.game.events_after(room, cursor)
                        if not event.get("broadcast") and duplicate_text in str(event.get("text") or "")
                    }
                    if duplicate_ids:
                        skip_text_event_ids_by_room[room_key] = duplicate_ids
                except Exception as exc:
                    self.log("[CRICKET] fast Join event scan failed", room, repr(exc))
        elif match.get("stage") == "setup" and low in {"1", "2", "3", "4"}:
            result = self.game.select_player_count(room, int(low))
        elif match.get("stage") == "lobby" and low in {"bot", "بوت", "ضد البوت", "solo", "vs bot"}:
            result = self.game.play_bot(room, sender)
        elif match.get("stage") == "teams" and low in {"1", "2"}:
            result = self.game.choose_team(room, "attack" if low == "1" else "defense")
        elif match.get("stage") == "live" and re.fullmatch(r"[0-6]", low):
            result = self.game.submit_ball(room, sender, int(low))
        else:
            return False

        self._reply_error(room, result)
        self._deliver_transition(
            previous_match, room,
            skip_text_event_ids_by_room=skip_text_event_ids_by_room,
        )
        if defer_resume:
            self._resume_after_restart()
        return True
