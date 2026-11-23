"""Force-join verification: every channel, two per row, video included."""

from __future__ import annotations

import asyncio

import config
import core.database as database
import core.middlewares as middlewares
from ui import keyboards, renderer

CHANNELS = [
    {"channel_id": "-1003791098639", "channel_name": "One", "channel_link": "https://t.me/one"},
    {"channel_id": "-1003715365551", "channel_name": "Two", "channel_link": "https://t.me/two"},
    {"channel_id": "-1004445390575", "channel_name": "Three", "channel_link": "https://t.me/three"},
    {"channel_id": "-1003965937636", "channel_name": "Four", "channel_link": "https://t.me/four"},
]


class FakeChat:
    username = "sakura_updates"
    invite_link = None


class FakeBot:
    def __init__(self) -> None:
        self.chat_calls: list[str] = []

    async def get_chat(self, chat_id: str) -> FakeChat:
        self.chat_calls.append(str(chat_id))
        return FakeChat()


def test_keyboard_is_two_per_row_with_verify_last() -> None:
    markup = keyboards.verify_keyboard(CHANNELS)
    rows = markup.inline_keyboard

    assert [len(r) for r in rows] == [2, 2, 1]
    assert [b.url for b in rows[0]] == ["https://t.me/one", "https://t.me/two"]
    assert [b.url for b in rows[1]] == ["https://t.me/three", "https://t.me/four"]
    assert rows[-1][0].callback_data == "verify:now"


def test_keyboard_keeps_channel_without_a_stored_link() -> None:
    markup = keyboards.verify_keyboard([{"channel_id": "-1001", "channel_name": "NoLink"}])
    rows = markup.inline_keyboard

    assert len(rows[0]) == 1
    assert rows[0][0].url is None
    assert rows[0][0].callback_data == "verify:now"


def test_missing_links_are_resolved_from_telegram() -> None:
    bot = FakeBot()
    resolved = asyncio.run(
        middlewares.resolve_channel_links(bot, [{"channel_id": "-1003791098639", "channel_name": ""}])
    )

    assert resolved[0]["channel_link"] == "https://t.me/sakura_updates"
    assert resolved[0]["channel_name"] == "-1003791098639"


def test_card_carries_every_channel_and_its_link() -> None:
    card = renderer.render_verify_card(CHANNELS)
    payload = card.model_dump_json()

    for name, link in (("One", "t.me/one"), ("Two", "t.me/two"), ("Three", "t.me/three"), ("Four", "t.me/four")):
        assert name in payload
        assert link in payload


def test_verify_screen_includes_the_configured_video(monkeypatch) -> None:
    async def fake_get_config(key: str, default=None):
        return "VERIFY_VIDEO_FILE_ID" if key == "verify_video" else default

    monkeypatch.setattr(middlewares.database, "get_config", fake_get_config)

    card, markup = asyncio.run(middlewares.build_verify_screen(FakeBot(), CHANNELS))

    assert "VERIFY_VIDEO_FILE_ID" in repr(card)
    assert [len(r) for r in markup.inline_keyboard] == [2, 2, 1]



class FakeUser:
    id = 42


class FakeChatObj:
    id = 42


class FakeMessage:
    from_user = FakeUser()
    chat = FakeChatObj()


class GateBot(FakeBot):
    """Reports every channel as unjoined and records the card it was sent."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[dict] = []

    async def get_chat_member(self, chat_id, user_id):
        return type("Member", (), {"status": "left"})()

    async def send_rich_message(self, **kwargs):
        self.sent.append(kwargs)
        return None


def test_gate_sends_every_link_and_the_verify_video(monkeypatch) -> None:
    async def fake_get_config(key: str, default=None):
        return "VERIFY_VIDEO_FILE_ID" if key == "verify_video" else default

    async def fake_channels():
        return CHANNELS

    monkeypatch.setattr(middlewares.database, "get_config", fake_get_config)
    monkeypatch.setattr(middlewares, "active_channels", fake_channels)

    bot = GateBot()
    data = {"bot": bot}

    async def nope(event, data):
        raise AssertionError("the gate must not reach the handler")

    asyncio.run(middlewares.ForceJoinMiddleware()(nope, FakeMessage(), data))

    assert len(bot.sent) == 1
    sent = bot.sent[0]
    assert "VERIFY_VIDEO_FILE_ID" in repr(sent["rich_message"])
    rows = sent["reply_markup"].inline_keyboard
    assert [len(r) for r in rows] == [2, 2, 1]
    assert [b.url for r in rows[:-1] for b in r] == [c["channel_link"] for c in CHANNELS]


def test_gate_falls_back_to_configured_channels(monkeypatch) -> None:
    def broken():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(database, "channels", broken)

    channels = asyncio.run(middlewares.active_channels())

    assert [c["channel_id"] for c in channels] == list(config.FORCE_JOIN_CHANNELS)
    assert all(c["is_active"] for c in channels)
