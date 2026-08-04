"""Server configuration: prefix, DJ role, 24/7 mode and announcements."""

from __future__ import annotations

import re

from fluxer import Embed

from .. import ui
from .core import CommandError, Context, command

CATEGORY = "Settings"
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
MAX_PREFIX_LENGTH = 8


@command(
    "prefix",
    usage="[new prefix]",
    description="Show or change the command prefix for this server.",
    category=CATEGORY,
    dj_only=True,
)
async def prefix(context: Context) -> None:
    if not context.argument:
        current = await context.bot.guild_prefix(context.guild_id)
        await context.info(
            f"My prefix here is `{current}`. Change it with "
            f"`{context.prefix}prefix <new>`, or reset it with `{context.prefix}prefix reset`."
        )
        return

    if not await context.bot.has_guild_permission(
        context.guild_id, context.author_id, manage_guild=True
    ):
        raise CommandError("You need the **Manage Server** permission to change the prefix.")

    value = context.argument.strip().strip('"')
    if value.lower() in ("reset", "default"):
        await context.bot.store.update_settings(context.guild_id, prefix=None)
        await context.ok(f"Prefix reset to `{context.bot.config.prefix}`.")
        return

    if len(value) > MAX_PREFIX_LENGTH:
        raise CommandError(f"Prefixes can be at most {MAX_PREFIX_LENGTH} characters.")
    if any(character.isspace() for character in value):
        raise CommandError("Prefixes can't contain spaces.")

    await context.bot.store.update_settings(context.guild_id, prefix=value)
    await context.ok(f"Prefix set to `{value}`. Try `{value}help`.")


@command(
    "247",
    aliases=("24/7", "24_7", "stay"),
    usage="[on|off]",
    description="Stay in the voice channel even when idle.",
    category=CATEGORY,
    dj_only=True,
)
async def stay_247(context: Context) -> None:
    player = await context.require_player()
    argument = context.argument.lower()

    if argument in ("on", "enable", "true", "yes"):
        player.stay_connected = True
    elif argument in ("off", "disable", "false", "no"):
        player.stay_connected = False
    elif not argument:
        player.stay_connected = not player.stay_connected
    else:
        raise CommandError(f"Usage: `{context.prefix}247 [on|off]`")

    await context.bot.store.update_settings(
        context.guild_id, stay_connected=player.stay_connected
    )
    if player.stay_connected:
        await context.ok("🕒 24/7 mode **on** — I'll stay in the channel.")
    else:
        await context.ok("🕒 24/7 mode **off** — I'll leave when idle.")


@command(
    "dj",
    aliases=("djrole",),
    usage="[@role|off]",
    description="Set the DJ role that gates playback commands.",
    category=CATEGORY,
)
async def dj(context: Context) -> None:
    settings = await context.bot.store.get_settings(context.guild_id)

    if not context.argument:
        if settings.dj_role_id is None:
            await context.info(
                "No DJ role is set, so **everyone** can use the playback commands.\n"
                f"Set one with `{context.prefix}dj @role`."
            )
        else:
            await context.info(f"The DJ role is <@&{settings.dj_role_id}>.")
        return

    if not await context.bot.has_guild_permission(
        context.guild_id, context.author_id, manage_guild=True
    ):
        raise CommandError("You need the **Manage Server** permission to set the DJ role.")

    if context.argument.lower() in ("off", "none", "clear", "reset"):
        await context.bot.store.update_settings(context.guild_id, dj_role_id=None)
        await context.ok("DJ role cleared — everyone can use the playback commands again.")
        return

    match = ROLE_MENTION_RE.search(context.argument)
    if match:
        role_id = int(match.group(1))
    elif context.argument.isdigit():
        role_id = int(context.argument)
    else:
        role_id = await context.bot.find_role_id(context.guild_id, context.argument)
        if role_id is None:
            raise CommandError(f"I couldn't find a role called **{context.argument}**.")

    await context.bot.store.update_settings(context.guild_id, dj_role_id=role_id)
    await context.ok(f"DJ role set to <@&{role_id}>.")


@command(
    "announce",
    aliases=("nowplayingmessages",),
    usage="[on|off]",
    description="Toggle the 'Now playing' messages.",
    category=CATEGORY,
    dj_only=True,
)
async def announce(context: Context) -> None:
    settings = await context.bot.store.get_settings(context.guild_id)
    argument = context.argument.lower()

    if argument in ("on", "enable", "true", "yes"):
        enabled = True
    elif argument in ("off", "disable", "false", "no"):
        enabled = False
    elif not argument:
        enabled = not settings.announce
    else:
        raise CommandError(f"Usage: `{context.prefix}announce [on|off]`")

    await context.bot.store.update_settings(context.guild_id, announce=enabled)
    state = "on" if enabled else "off"
    await context.ok(f"📢 Now-playing messages are **{state}**.")


@command(
    "settings",
    aliases=("setup", "config"),
    description="Show this server's configuration.",
    category=CATEGORY,
)
async def settings(context: Context) -> None:
    stored = await context.bot.store.get_settings(context.guild_id)
    player = context.player
    config = context.bot.config

    embed = Embed(title="Server settings", color=ui.COLOR_PRIMARY)
    embed.add_field(
        name="Prefix", value=f"`{stored.prefix or config.prefix}`", inline=True
    )
    embed.add_field(
        name="Default volume",
        value=f"{stored.volume if stored.volume is not None else config.default_volume}%",
        inline=True,
    )
    embed.add_field(
        name="DJ role",
        value=f"<@&{stored.dj_role_id}>" if stored.dj_role_id else "*not set (everyone)*",
        inline=True,
    )
    embed.add_field(
        name="24/7 mode", value="on" if stored.stay_connected else "off", inline=True
    )
    embed.add_field(
        name="Now-playing messages",
        value="on" if stored.announce else "off",
        inline=True,
    )
    embed.add_field(
        name="Autoplay", value="on" if stored.autoplay else "off", inline=True
    )
    embed.add_field(
        name="Idle timeout",
        value=f"{config.idle_timeout}s" if config.idle_timeout else "disabled",
        inline=True,
    )
    if player is not None and player.is_connected:
        embed.add_field(
            name="Connected to", value=f"<#{player.voice_channel_id}>", inline=True
        )
    embed.set_footer(text=f"Change these with {context.prefix}prefix, {context.prefix}dj, …")
    await context.reply(embed=embed)
