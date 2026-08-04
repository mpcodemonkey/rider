"""Queue management: listing, ordering, looping and autoplay."""

from __future__ import annotations

from .. import ui
from ..player import LoopMode
from ..utils import parse_index_selection, plural
from .core import CommandError, Context, command

CATEGORY = "Queue"


@command(
    "queue",
    aliases=("q", "list", "songs"),
    usage="[page]",
    description="Show the queue.",
    category=CATEGORY,
)
async def queue(context: Context) -> None:
    player = await context.require_player()
    page = int(context.argument) if context.argument.isdigit() else 1
    await context.reply(embed=ui.queue_page(player, page))


@command(
    "shuffle",
    aliases=("mix",),
    description="Shuffle the queue.",
    category=CATEGORY,
    dj_only=True,
)
async def shuffle(context: Context) -> None:
    player = await context.require_player()
    if len(player.queue) < 2:
        raise CommandError("There aren't enough tracks queued to shuffle.")
    player.shuffle()
    await context.ok(f"🔀 Shuffled **{len(player.queue)}** tracks.")


@command(
    "clear",
    aliases=("clearqueue", "cq", "empty"),
    description="Remove everything from the queue.",
    category=CATEGORY,
    dj_only=True,
)
async def clear(context: Context) -> None:
    player = await context.require_player()
    removed = player.clear()
    if not removed:
        raise CommandError("The queue is already empty.")
    await context.ok(f"Cleared **{removed}** {plural(removed, 'track')} from the queue.")


@command(
    "remove",
    aliases=("rm", "delete", "del"),
    usage="<position | range>",
    description="Remove queue entries, e.g. 3, 2-5 or 1,4,7.",
    category=CATEGORY,
    dj_only=True,
)
async def remove(context: Context) -> None:
    player = await context.require_player()
    if not context.argument:
        raise CommandError(f"Usage: `{context.prefix}remove 3` or `{context.prefix}remove 2-5`")

    indices = parse_index_selection(context.argument, len(player.queue))
    if not indices:
        raise CommandError("No queue positions matched that.")

    removed = player.remove(indices)
    if len(removed) == 1:
        await context.ok(f"Removed **{removed[0].display_title}**.")
    else:
        await context.ok(f"Removed **{len(removed)}** tracks.")


@command(
    "move",
    aliases=("mv",),
    usage="<from> <to>",
    description="Move a queued track to another position.",
    category=CATEGORY,
    dj_only=True,
)
async def move(context: Context) -> None:
    player = await context.require_player()
    parts = context.argv
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise CommandError(f"Usage: `{context.prefix}move 5 1`")

    source, destination = int(parts[0]) - 1, int(parts[1]) - 1
    track = player.move(source, destination)
    if track is None:
        raise CommandError(f"There's no track at position {parts[0]}.")
    await context.ok(f"Moved **{track.display_title}** to position **{parts[1]}**.")


@command(
    "swap",
    usage="<first> <second>",
    description="Swap two queued tracks.",
    category=CATEGORY,
    dj_only=True,
)
async def swap(context: Context) -> None:
    player = await context.require_player()
    parts = context.argv
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise CommandError(f"Usage: `{context.prefix}swap 2 5`")

    first, second = int(parts[0]) - 1, int(parts[1]) - 1
    items = list(player.queue)
    if not (0 <= first < len(items) and 0 <= second < len(items)):
        raise CommandError("One of those positions isn't in the queue.")

    items[first], items[second] = items[second], items[first]
    player.queue.clear()
    player.queue.extend(items)
    await context.ok(
        f"Swapped **{items[second].display_title}** and **{items[first].display_title}**."
    )


@command(
    "removedupes",
    aliases=("dedupe", "distinct"),
    description="Remove duplicate tracks from the queue.",
    category=CATEGORY,
    dj_only=True,
)
async def removedupes(context: Context) -> None:
    player = await context.require_player()
    removed = player.deduplicate()
    if not removed:
        raise CommandError("There are no duplicates in the queue.")
    await context.ok(f"Removed **{removed}** duplicate {plural(removed, 'track')}.")


@command(
    "removeuser",
    aliases=("removesongs",),
    usage="<@user>",
    description="Remove everything a specific user queued.",
    category=CATEGORY,
    dj_only=True,
)
async def removeuser(context: Context) -> None:
    player = await context.require_player()
    mentions = getattr(context.message, "mentions", None) or []
    if mentions:
        target_id = mentions[0].id
        target_name = mentions[0].username
    elif context.argument.isdigit():
        target_id = int(context.argument)
        target_name = context.argument
    else:
        raise CommandError(f"Usage: `{context.prefix}removeuser @someone`")

    removed = player.remove_by_user(target_id)
    if not removed:
        raise CommandError(f"**{target_name}** has nothing queued.")
    await context.ok(f"Removed **{removed}** {plural(removed, 'track')} queued by **{target_name}**.")


@command(
    "loop",
    aliases=("repeat", "l"),
    usage="[off|track|queue]",
    description="Loop the current track (or toggle it off).",
    category=CATEGORY,
    dj_only=True,
)
async def loop(context: Context) -> None:
    player = await context.require_player()
    argument = context.argument.lower()

    if argument in ("off", "none", "disable", "stop"):
        mode = LoopMode.OFF
    elif argument in ("all", "queue", "q"):
        mode = LoopMode.QUEUE
    elif argument in ("track", "song", "one", "current"):
        mode = LoopMode.TRACK
    elif not argument:
        # No argument toggles track looping, like Jockie.
        mode = LoopMode.OFF if player.loop_mode is LoopMode.TRACK else LoopMode.TRACK
    else:
        raise CommandError(f"Usage: `{context.prefix}loop [off|track|queue]`")

    player.loop_mode = mode
    icons = {LoopMode.OFF: "➡️", LoopMode.TRACK: "🔂", LoopMode.QUEUE: "🔁"}
    await context.ok(f"{icons[mode]} Loop mode: **{mode.label}**.")


@command(
    "loopqueue",
    aliases=("lq", "repeatqueue", "rq"),
    description="Loop the whole queue.",
    category=CATEGORY,
    dj_only=True,
)
async def loopqueue(context: Context) -> None:
    player = await context.require_player()
    if player.loop_mode is LoopMode.QUEUE:
        player.loop_mode = LoopMode.OFF
        await context.ok("➡️ Loop mode: **Off**.")
    else:
        player.loop_mode = LoopMode.QUEUE
        await context.ok("🔁 Loop mode: **Queue**.")


@command(
    "autoplay",
    aliases=("ap",),
    usage="[on|off]",
    description="Keep playing related tracks when the queue runs out.",
    category=CATEGORY,
    dj_only=True,
)
async def autoplay(context: Context) -> None:
    player = await context.require_player()
    argument = context.argument.lower()

    if argument in ("on", "enable", "true", "yes"):
        player.autoplay = True
    elif argument in ("off", "disable", "false", "no"):
        player.autoplay = False
    elif not argument:
        player.autoplay = not player.autoplay
    else:
        raise CommandError(f"Usage: `{context.prefix}autoplay [on|off]`")

    await context.bot.store.update_settings(context.guild_id, autoplay=player.autoplay)
    state = "on" if player.autoplay else "off"
    await context.ok(f"♾️ Autoplay is now **{state}**.")


@command(
    "history",
    aliases=("recent", "played"),
    usage="[page]",
    description="Show recently played tracks.",
    category=CATEGORY,
)
async def history(context: Context) -> None:
    player = await context.require_player()
    page = int(context.argument) if context.argument.isdigit() else 1
    # Newest first reads better than queue order here.
    await context.reply(embed=ui.history_page(list(reversed(player.history)), page))
