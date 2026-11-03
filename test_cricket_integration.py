from pathlib import Path

from cricket_integration import CricketIntegration


def test_master_toggle_and_player_count_are_persistent(tmp_path: Path):
    messages = []
    writes = []

    def save(path, value):
        writes.append((Path(path).name, value.get("enabled")))

    integration = CricketIntegration(
        tmp_path,
        persist=save,
        is_master=lambda username: username.casefold() == "master",
        is_verified=lambda username: username.casefold() in {"master", "member"},
        send_room_text=lambda room, text: messages.append((room, text)),
        send_room_media=lambda *_args: None,
        public_base=lambda: "https://bot.example",
    )

    assert integration.handle("Room A", "master", ".cr 1", is_private=True) is True
    assert integration.game.current() is None
    assert integration.handle("Room A", "member", ".cr 1") is True
    assert integration.game.current()["stage"] == "setup"
    assert integration.handle("Room A", "member", ".cr 2") is True
    assert integration.game.current()["stage"] == "lobby"
    assert writes

    restarted = CricketIntegration(
        tmp_path,
        persist=save,
        is_master=lambda username: username.casefold() == "master",
        send_room_text=lambda room, text: messages.append((room, text)),
        send_room_media=lambda *_args: None,
        public_base=lambda: "https://bot.example",
    )
    assert restarted.game.enabled("Room A") is True
    assert restarted.game.current()["target_players"] == 2


def test_only_master_can_toggle_cricket(tmp_path: Path):
    messages = []
    integration = CricketIntegration(
        tmp_path,
        is_master=lambda username: username == "master",
        send_room_text=lambda room, text: messages.append((room, text)),
        send_room_media=lambda *_args: None,
        public_base=lambda: "https://bot.example",
    )

    assert integration.handle("Room A", "player", "تشغيل لعبه الكركيت", is_private=True) is True
    assert integration.game.enabled("Room A") is False
    assert "للماستر" in messages[-1][1]
    assert integration.handle("Room A", "master", ".cr 1") is True
    assert integration.game.enabled("Room A") is False
    assert "فعّلها" in messages[-1][1]
    assert integration.handle("Room A", "master", ".cr 1", is_private=True) is True
    assert integration.game.enabled("Room A") is True
    assert integration.game.current() is None
    assert integration.handle("Room A", "master", ".cr 1") is True
    assert integration.game.current()["stage"] == "setup"
    assert integration.handle("Room A", "player", ".ct 0") is True
    assert integration.game.enabled("Room A") is True
    assert integration.handle("Room A", "player", ".cr 0") is True
    assert integration.game.enabled("Room A") is True
    assert integration.handle("Room A", "master", ".cr 0", is_private=True) is True
    assert integration.game.enabled("Room A") is False
