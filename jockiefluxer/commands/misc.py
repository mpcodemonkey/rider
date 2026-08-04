"""Help, diagnostics and lyrics."""

from __future__ import annotations

import platform
import time

import aiohttp
from fluxer import Embed

from .. import __version__, ui
from ..utils import format_duration, truncate
from .core import REGISTRY, CommandError, Context, command

CATEGORY = "General"
LYRICS_ENDPOINT = "https://api.lyrics.ovh/v1"
LYRICS_LIMIT = 3800

_STARTED_AT = time.time()


@command(
    "help",
    aliases=("h", "commands", "cmds"),
    usage="[command]",
    description="Show the command list, or details for one command.",
    category=CATEGORY,
)
async def help_command(context: Context) -> None:
    prefix = context.prefix

    if context.argument:
        name = context.argument.split()[0].lower()
        entry = REGISTRY.get(name)
        if entry is None:
            raise CommandError(f"There's no command called **{name}**.")

        embed = Embed(
            title=f"{prefix}{entry.name}",
            description=entry.description or "*No description.*",
            color=ui.COLOR_PRIMARY,
        )
        embed.add_field(name="Usage", value=f"`{entry.signature(prefix)}`", inline=False)
        if entry.aliases:
            embed.add_field(
                name="Aliases",
                value=", ".join(f"`{prefix}{alias}`" for alias in entry.aliases),
                inline=False,
            )
        embed.add_field(name="Category", value=entry.category, inline=True)
        if entry.dj_only:
            embed.add_field(name="Permissions", value="DJ only", inline=True)
        await context.reply(embed=embed)
        return

    embed = Embed(
        title="Commands",
        description=(
            f"Prefix: `{prefix}` — you can also just @mention me.\n"
            f"Use `{prefix}help <command>` for details on any one of these."
        ),
        color=ui.COLOR_PRIMARY,
    )
    for category, entries in REGISTRY.categories().items():
        embed.add_field(
            name=category,
            value=" ".join(f"`{entry.name}`" for entry in entries),
            inline=False,
        )
    embed.set_footer(text=f"jockiefluxer v{__version__} • {len(REGISTRY.commands)} commands")
    await context.reply(embed=embed)


@command("ping", description="Check that the bot is responsive.", category=CATEGORY)
async def ping(context: Context) -> None:
    started = time.perf_counter()
    message = await context.reply(embed=ui.info("Pinging…"))
    elapsed = (time.perf_counter() - started) * 1000

    embed = ui.info(f"🏓 Pong! Round trip: **{elapsed:.0f} ms**")
    try:
        # edit_message takes pre-serialised embeds, unlike send_message.
        await message.edit(embeds=[embed.to_dict()])
    except Exception:
        await context.reply(embed=embed)


@command(
    "info",
    aliases=("about", "stats", "botinfo"),
    description="Show bot and runtime information.",
    category=CATEGORY,
)
async def info(context: Context) -> None:
    bot = context.bot
    players = list(bot.players)
    playing = sum(1 for player in players if player.is_playing)
    uptime = format_duration(int((time.time() - _STARTED_AT) * 1000))

    embed = Embed(
        title="jockiefluxer",
        description="A self-hosted, Jockie-compatible music bot for Fluxer.",
        color=ui.COLOR_PRIMARY,
    )
    embed.add_field(name="Version", value=__version__, inline=True)
    embed.add_field(name="Uptime", value=uptime, inline=True)
    embed.add_field(name="Guilds", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Voice connections", value=str(len(players)), inline=True)
    embed.add_field(name="Playing now", value=str(playing), inline=True)
    embed.add_field(
        name="Python", value=platform.python_version(), inline=True
    )
    embed.set_footer(text=f"Use {context.prefix}help to see every command.")
    await context.reply(embed=embed)


@command(
    "lyrics",
    aliases=("ly",),
    usage="[song]",
    description="Look up lyrics for the current or a named track.",
    category=CATEGORY,
)
async def lyrics(context: Context) -> None:
    query = context.argument
    if not query:
        player = context.player
        if player is None or player.current is None:
            raise CommandError(f"Nothing is playing — try `{context.prefix}lyrics <song>`.")
        query = player.current.title

    artist, title = _split_artist_title(query)
    if artist is None:
        raise CommandError(
            "I need an artist to search lyrics. Try `lyrics <artist> - <title>`."
        )

    async with context.bot.typing(context.channel_id):
        text = await _fetch_lyrics(artist, title)

    if text is None:
        raise CommandError(f"No lyrics found for **{artist} - {title}**.")

    embed = Embed(
        title=truncate(f"{artist} - {title}", 100),
        description=text[:LYRICS_LIMIT] + ("\n\n*(truncated)*" if len(text) > LYRICS_LIMIT else ""),
        color=ui.COLOR_PRIMARY,
    )
    embed.set_footer(text="Lyrics via lyrics.ovh")
    await context.reply(embed=embed)


def _split_artist_title(query: str) -> tuple[str | None, str]:
    """Best-effort split of a track title into artist and song name."""
    for separator in (" - ", " – ", " — ", " by "):
        if separator in query:
            left, _, right = query.partition(separator)
            return left.strip(), right.strip()
    return None, query.strip()


async def _fetch_lyrics(artist: str, title: str) -> str | None:
    url = f"{LYRICS_ENDPOINT}/{artist}/{title}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                payload = await response.json(content_type=None)
    except Exception:
        return None

    text = (payload or {}).get("lyrics")
    return text.strip() if text else None
