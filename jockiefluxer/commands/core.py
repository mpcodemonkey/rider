"""Command framework: registry, context object and the message dispatcher.

``fluxer.py`` ships a minimal prefix-command helper, but a Jockie-compatible
surface needs aliases, sub-commands, DJ permission checks and per-guild
prefixes, so the bot brings its own small router.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from fluxer import Embed

from .. import ui
from ..player import GuildPlayer

if TYPE_CHECKING:
    from ..bot import MusicBot

log = logging.getLogger(__name__)

CommandCallback = Callable[["Context"], Awaitable[None]]


class CommandError(Exception):
    """A user-facing failure; the dispatcher turns it into an error embed."""


@dataclass(slots=True)
class Command:
    name: str
    callback: CommandCallback
    aliases: tuple[str, ...] = ()
    description: str = ""
    usage: str = ""
    category: str = "Music"
    dj_only: bool = False
    guild_only: bool = True
    #: Require the invoker to be in a voice channel.
    requires_voice: bool = False
    #: Require an active player with something loaded.
    requires_playing: bool = False
    hidden: bool = False

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    def signature(self, prefix: str) -> str:
        return f"{prefix}{self.name} {self.usage}".strip()


class CommandRegistry:
    """Name/alias lookup table for all registered commands."""

    def __init__(self) -> None:
        self.commands: list[Command] = []
        self._lookup: dict[str, Command] = {}

    def add(self, command: Command) -> None:
        for name in command.names:
            existing = self._lookup.get(name.lower())
            if existing is not None:
                raise ValueError(
                    f"Command name '{name}' is claimed by both "
                    f"'{existing.name}' and '{command.name}'"
                )
            self._lookup[name.lower()] = command
        self.commands.append(command)

    def get(self, name: str) -> Command | None:
        return self._lookup.get(name.lower())

    def categories(self) -> dict[str, list[Command]]:
        grouped: dict[str, list[Command]] = {}
        for command in self.commands:
            if command.hidden:
                continue
            grouped.setdefault(command.category, []).append(command)
        for entries in grouped.values():
            entries.sort(key=lambda item: item.name)
        return grouped


#: Commands collected at import time by the ``@command`` decorator.
REGISTRY = CommandRegistry()


def command(
    name: str,
    *,
    aliases: tuple[str, ...] | list[str] = (),
    description: str = "",
    usage: str = "",
    category: str = "Music",
    dj_only: bool = False,
    guild_only: bool = True,
    requires_voice: bool = False,
    requires_playing: bool = False,
    hidden: bool = False,
) -> Callable[[CommandCallback], CommandCallback]:
    """Register a command with the global registry."""

    def decorator(func: CommandCallback) -> CommandCallback:
        REGISTRY.add(
            Command(
                name=name,
                callback=func,
                aliases=tuple(aliases),
                description=description or (inspect.getdoc(func) or "").split("\n")[0],
                usage=usage,
                category=category,
                dj_only=dj_only,
                guild_only=guild_only,
                requires_voice=requires_voice,
                requires_playing=requires_playing,
                hidden=hidden,
            )
        )
        return func

    return decorator


@dataclass(slots=True)
class Context:
    """Everything a command handler needs about one invocation."""

    bot: MusicBot
    message: Any  # fluxer.Message
    command: Command
    prefix: str
    invoked_with: str
    argument: str  # raw text after the command name
    guild_id: int
    _argv: list[str] | None = field(default=None, repr=False)

    # -- shortcuts -----------------------------------------------------
    @property
    def author(self):
        return self.message.author

    @property
    def author_id(self) -> int:
        return self.message.author.id

    @property
    def author_name(self) -> str:
        user = self.message.author
        return getattr(user, "global_name", None) or user.username

    @property
    def channel_id(self) -> int:
        return self.message.channel_id

    @property
    def argv(self) -> list[str]:
        """Whitespace-split arguments, honouring quotes where possible."""
        if self._argv is None:
            try:
                self._argv = shlex.split(self.argument)
            except ValueError:
                self._argv = self.argument.split()
        return self._argv

    @property
    def player(self) -> GuildPlayer | None:
        return self.bot.players.get(self.guild_id)

    # -- replies -------------------------------------------------------
    async def send(self, content: str | None = None, *, embed: Embed | None = None):
        return await self.bot.send(self.channel_id, content, embed=embed)

    async def reply(self, content: str | None = None, *, embed: Embed | None = None):
        try:
            return await self.message.reply(content, embed=embed)
        except Exception:
            # Replying fails if the original message was deleted mid-command.
            return await self.send(content, embed=embed)

    async def ok(self, message: str):
        return await self.reply(embed=ui.success(message))

    async def info(self, message: str):
        return await self.reply(embed=ui.info(message))

    async def fail(self, message: str):
        return await self.reply(embed=ui.error(message))

    # -- voice helpers -------------------------------------------------
    def author_voice_channel(self) -> int | None:
        state = self.bot.get_voice_state(self.guild_id, self.author_id)
        return state.channel_id if state else None

    async def require_author_voice(self) -> int:
        channel_id = self.author_voice_channel()
        if channel_id is None:
            raise CommandError("You need to be in a voice channel to use that.")
        return channel_id

    async def require_player(self) -> GuildPlayer:
        player = self.player
        if player is None or not player.is_connected:
            raise CommandError("I'm not connected to a voice channel.")
        return player

    async def require_playing(self) -> GuildPlayer:
        player = await self.require_player()
        if player.current is None:
            raise CommandError("Nothing is playing right now.")
        return player

    async def connect_player(self) -> GuildPlayer:
        """Get the guild player, joining the author's voice channel if needed."""
        channel_id = await self.require_author_voice()
        player = self.player

        if player is not None and player.is_connected:
            # Only allow control from the channel the bot is actually in.
            if player.voice_channel_id != channel_id and self.bot.has_listeners(player):
                raise CommandError("You need to be in my voice channel to do that.")
            player.text_channel_id = self.channel_id
            if player.voice_channel_id != channel_id:
                await player.connect(channel_id)
            return player

        player = await self.bot.players.create(self.guild_id)
        player.text_channel_id = self.channel_id
        await self.bot.apply_guild_settings(player)
        await player.connect(channel_id)
        return player

    # -- permissions ---------------------------------------------------
    async def is_dj(self) -> bool:
        return await self.bot.is_dj(self.guild_id, self.author_id, self.channel_id)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
async def dispatch(bot: MusicBot, message: Any) -> None:
    """Parse a message and run the matching command, if any."""
    if getattr(message.author, "bot", False):
        return
    content = (message.content or "").strip()
    if not content:
        return

    guild_id = await bot.resolve_guild_id(message)
    if guild_id is None:
        return  # DMs carry no player state, so there is nothing to run

    prefix = await bot.matching_prefix(content, guild_id)
    if prefix is None:
        return

    remainder = content[len(prefix):].lstrip()
    if not remainder:
        return

    name, _, argument = remainder.partition(" ")
    entry = REGISTRY.get(name)
    if entry is None:
        return

    context = Context(
        bot=bot,
        message=message,
        command=entry,
        prefix=prefix,
        invoked_with=name.lower(),
        argument=argument.strip(),
        guild_id=guild_id,
    )

    try:
        if entry.dj_only and not await context.is_dj():
            raise CommandError(
                "That's a DJ command. You need the DJ role, the **Manage Channels** "
                "permission, or to be the only listener in the channel."
            )
        if entry.requires_voice:
            await context.require_author_voice()
        if entry.requires_playing:
            await context.require_playing()

        await entry.callback(context)
    except CommandError as exc:
        await _safe_reply(context, ui.error(str(exc)))
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("command '%s' failed", entry.name)
        await _safe_reply(
            context, ui.error("Something went wrong running that command.")
        )


async def _safe_reply(context: Context, embed: Embed) -> None:
    try:
        await context.reply(embed=embed)
    except Exception:
        log.debug("could not deliver error response", exc_info=True)


__all__ = [
    "Command",
    "CommandError",
    "CommandRegistry",
    "Context",
    "REGISTRY",
    "command",
    "dispatch",
]
