"""Command routing, plus the Jockie compatibility contract."""

from __future__ import annotations

import pytest

from jockiefluxer.commands import REGISTRY, dispatch
from jockiefluxer.commands.core import Command, CommandRegistry, command


class FakeUser:
    def __init__(self, user_id: int = 1, username: str = "gm", bot: bool = False):
        self.id = user_id
        self.username = username
        self.global_name = username
        self.bot = bot


class FakeMessage:
    def __init__(self, content: str, *, author: FakeUser | None = None, guild_id=7):
        self.content = content
        self.author = author or FakeUser()
        self.channel_id = 100
        self.guild_id = guild_id
        self.attachments: list = []
        self.mentions: list = []
        self.replies: list = []

    async def reply(self, content=None, *, embed=None):
        self.replies.append((content, embed))
        return self


class FakeStore:
    def __init__(self, prefix=None):
        self._prefix = prefix

    async def get_prefix(self, guild_id):
        return self._prefix


class FakeBot:
    """Minimal MusicBot stand-in for dispatch tests."""

    def __init__(self, config, prefix=None):
        self.config = config
        self.store = FakeStore(prefix)
        self.user = FakeUser(user_id=999, username="jockie")
        self.sent: list = []
        self.dj = True

    async def resolve_guild_id(self, message):
        return message.guild_id

    async def matching_prefix(self, content, guild_id):
        from jockiefluxer.bot import MusicBot

        return await MusicBot.matching_prefix(self, content, guild_id)

    async def send(self, channel_id, content=None, *, embed=None):
        self.sent.append((channel_id, content, embed))
        return None

    async def is_dj(self, guild_id, user_id, channel_id):
        return self.dj

    def get_voice_state(self, guild_id, user_id):
        return None

    @property
    def players(self):
        class _Players:
            def get(self, guild_id):
                return None

        return _Players()


@pytest.fixture
def bot(config):
    return FakeBot(config)


def embed_text(message: FakeMessage) -> str:
    _, embed = message.replies[-1]
    return embed.description or ""


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------
def test_registry_has_no_duplicate_names():
    seen: dict[str, str] = {}
    for entry in REGISTRY.commands:
        for name in entry.names:
            assert name.lower() not in seen, (
                f"'{name}' is claimed by both {seen.get(name.lower())} and {entry.name}"
            )
            seen[name.lower()] = entry.name


def test_registry_rejects_a_colliding_alias():
    registry = CommandRegistry()

    async def noop(context):
        pass

    registry.add(Command(name="play", callback=noop, aliases=("p",)))
    with pytest.raises(ValueError, match="claimed by both"):
        registry.add(Command(name="pause", callback=noop, aliases=("p",)))


def test_every_command_has_a_description():
    missing = [entry.name for entry in REGISTRY.commands if not entry.description]
    assert not missing, f"commands without a description: {missing}"


# ---------------------------------------------------------------------------
# Jockie compatibility
# ---------------------------------------------------------------------------
JOCKIE_COMMANDS = [
    # Playback
    "play", "p", "playnext", "pn", "playnow", "search", "pause", "resume",
    "skip", "s", "next", "forceskip", "fs", "skipto", "jump", "previous", "prev",
    "stop", "seek", "forward", "rewind", "replay", "nowplaying", "np", "playing",
    "volume", "vol", "connect", "join", "disconnect", "dc", "leave", "grab", "save",
    # Queue
    "queue", "q", "shuffle", "clear", "clearqueue", "cq", "remove", "rm", "move",
    "loop", "repeat", "loopqueue", "lq", "autoplay", "history", "removedupes",
    # Filters
    "bassboost", "bb", "nightcore", "vaporwave", "speed", "pitch", "8d", "karaoke",
    "tremolo", "vibrato", "filters", "clearfilters",
    # Playlists
    "playlist", "pl", "favourites", "favorites", "fav",
    # Settings / general
    "prefix", "247", "24/7", "dj", "settings", "help", "ping", "info", "lyrics",
]


@pytest.mark.parametrize("name", JOCKIE_COMMANDS)
def test_jockie_command_name_is_available(name):
    """Every command a Jockie user is likely to type must resolve to something."""
    assert REGISTRY.get(name) is not None, f"missing Jockie command: {name}"


def test_command_lookup_is_case_insensitive():
    assert REGISTRY.get("PLAY") is REGISTRY.get("play")
    assert REGISTRY.get("NowPlaying") is REGISTRY.get("nowplaying")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
async def test_dispatch_ignores_messages_without_the_prefix(bot):
    message = FakeMessage("just chatting about the campaign")
    await dispatch(bot, message)
    assert not message.replies


async def test_dispatch_ignores_other_bots(bot):
    message = FakeMessage("m!ping", author=FakeUser(user_id=2, bot=True))
    await dispatch(bot, message)
    assert not message.replies


async def test_dispatch_ignores_unknown_commands(bot):
    message = FakeMessage("m!definitelynotacommand")
    await dispatch(bot, message)
    assert not message.replies


async def test_dispatch_runs_a_matching_command(bot):
    message = FakeMessage("m!help")
    await dispatch(bot, message)
    assert message.replies


async def test_dispatch_honours_a_custom_guild_prefix(config):
    bot = FakeBot(config, prefix="!")
    ran = FakeMessage("!help")
    await dispatch(bot, ran)
    assert ran.replies

    # The default prefix still works alongside the custom one.
    fallback = FakeMessage("m!help")
    await dispatch(bot, fallback)
    assert fallback.replies


async def test_dispatch_accepts_an_at_mention_as_a_prefix(bot):
    message = FakeMessage("<@999> help")
    await dispatch(bot, message)
    assert message.replies


async def test_dispatch_reports_command_errors_as_embeds(bot):
    # "queue" needs a connected player, which the fake bot never has.
    message = FakeMessage("m!queue")
    await dispatch(bot, message)
    assert "❌" in embed_text(message)
    assert "not connected" in embed_text(message).lower()


async def test_dispatch_blocks_dj_commands_for_non_djs(bot):
    bot.dj = False
    message = FakeMessage("m!stop")
    await dispatch(bot, message)
    assert "DJ" in embed_text(message)


async def test_dispatch_enforces_the_voice_requirement(bot):
    message = FakeMessage("m!play something")
    await dispatch(bot, message)
    assert "voice channel" in embed_text(message).lower()


async def test_dispatch_survives_a_command_that_raises(bot):
    @command("boom-test", hidden=True, description="test only")
    async def boom(context):
        raise RuntimeError("kaboom")

    try:
        message = FakeMessage("m!boom-test")
        await dispatch(bot, message)
        assert "went wrong" in embed_text(message)
    finally:
        REGISTRY.commands.remove(REGISTRY.get("boom-test"))
        REGISTRY._lookup.pop("boom-test")


async def test_argument_is_everything_after_the_command_name(bot, monkeypatch):
    captured = {}

    async def fake_help(context):
        captured["argument"] = context.argument
        captured["argv"] = context.argv

    monkeypatch.setattr(REGISTRY.get("help"), "callback", fake_help)
    await dispatch(bot, FakeMessage("m!help  play  now  "))
    assert captured["argument"] == "play  now"
    assert captured["argv"] == ["play", "now"]


async def test_argv_handles_quoted_arguments(bot, monkeypatch):
    captured = {}

    async def fake_help(context):
        captured["argv"] = context.argv

    monkeypatch.setattr(REGISTRY.get("help"), "callback", fake_help)
    await dispatch(bot, FakeMessage('m!help "tavern ambience" 2'))
    assert captured["argv"] == ["tavern ambience", "2"]


async def test_argv_falls_back_when_quotes_are_unbalanced(bot, monkeypatch):
    """An unterminated quote must not blow up the command."""
    captured = {}

    async def fake_help(context):
        captured["argv"] = context.argv

    monkeypatch.setattr(REGISTRY.get("help"), "callback", fake_help)
    await dispatch(bot, FakeMessage('m!help "unclosed quote'))
    assert captured["argv"] == ['"unclosed', "quote"]


async def test_help_for_a_specific_command_lists_its_aliases(bot):
    message = FakeMessage("m!help play")
    await dispatch(bot, message)
    _, embed = message.replies[-1]
    aliases = next(field for field in embed.fields if field["name"] == "Aliases")
    assert "`m!p`" in aliases["value"]


async def test_prefix_matching_is_case_insensitive(bot):
    """Jockie answers to both m! and M!, so this bot must too."""
    for content in ("m!help", "M!help"):
        message = FakeMessage(content)
        await dispatch(bot, message)
        assert message.replies, f"{content} was ignored"


async def test_case_insensitive_prefix_keeps_the_argument_intact(bot, monkeypatch):
    captured = {}

    async def fake_help(context):
        captured["argument"] = context.argument
        captured["prefix"] = context.prefix

    monkeypatch.setattr(REGISTRY.get("help"), "callback", fake_help)
    await dispatch(bot, FakeMessage("M!help PLAY"))
    assert captured["argument"] == "PLAY"
    assert captured["prefix"] == "M!"
