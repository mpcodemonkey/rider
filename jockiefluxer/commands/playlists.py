"""Saved playlists and favourites.

Playlists belong to the user who made them, so a DM can carry the same
"campaign ambience" list into any server the bot is in.
"""

from __future__ import annotations

from .. import ui
from ..store import FAVOURITES
from ..track import LoadType, Track
from ..utils import plural
from .core import CommandError, Context, command

CATEGORY = "Playlists"
MAX_NAME_LENGTH = 48


def _clean_name(raw: str) -> str:
    name = raw.strip().strip('"')
    if not name:
        raise CommandError("Give the playlist a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise CommandError(f"Playlist names are limited to {MAX_NAME_LENGTH} characters.")
    if name == FAVOURITES:
        raise CommandError("That name is reserved.")
    return name


async def _load_into_queue(context: Context, tracks: list[Track], label: str) -> None:
    """Queue a stored playlist's tracks for the invoking user."""
    if not tracks:
        raise CommandError(f"**{label}** is empty.")

    player = await context.connect_player()
    copies = [track.copy_for(context.author_id, context.author_name) for track in tracks]
    added = player.enqueue(copies)
    await context.reply(
        embed=ui.added_playlist(label, added, truncated=len(copies) - added)
    )


async def _current_or_query(context: Context, argument: str) -> list[Track]:
    """Resolve what to save: an explicit query, or whatever is playing."""
    if argument:
        result = await context.bot.sources.load(argument)
        if result.load_type is LoadType.ERROR:
            raise CommandError(result.error or "That link could not be loaded.")
        if result.load_type is LoadType.EMPTY or not result.tracks:
            raise CommandError(f"Nothing found for **{argument}**.")
        return result.tracks

    player = context.player
    if player is None or player.current is None:
        raise CommandError("Nothing is playing — pass a song or link instead.")
    return [player.current]


# ---------------------------------------------------------------------------
# playlist
# ---------------------------------------------------------------------------
@command(
    "playlist",
    aliases=("pl", "playlists"),
    usage="<create|delete|list|show|add|remove|play|rename|clear> [name] [song]",
    description="Manage your saved playlists.",
    category=CATEGORY,
)
async def playlist(context: Context) -> None:
    parts = context.argv
    action = parts[0].lower() if parts else "list"
    store = context.bot.store

    if action in ("list", "l") and len(parts) == 1:
        entries = await store.list_playlists(context.author_id)
        await context.reply(embed=ui.playlist_list(entries, context.author_name))
        return

    if action in ("create", "new", "make"):
        name = _clean_name(" ".join(parts[1:]))
        if not await store.create_playlist(context.author_id, name):
            raise CommandError(f"You already have a playlist called **{name}**.")
        await context.ok(
            f"Created **{name}**. Add tracks with `{context.prefix}playlist add {name} <song>`."
        )
        return

    if action in ("delete", "remove-playlist", "destroy"):
        name = _clean_name(" ".join(parts[1:]))
        if not await store.delete_playlist(context.author_id, name):
            raise CommandError(f"You don't have a playlist called **{name}**.")
        await context.ok(f"Deleted **{name}**.")
        return

    if action in ("rename",):
        if len(parts) < 3:
            raise CommandError(f"Usage: `{context.prefix}playlist rename <old> <new>`")
        old, new = _clean_name(parts[1]), _clean_name(" ".join(parts[2:]))
        if not await store.rename_playlist(context.author_id, old, new):
            raise CommandError(f"Couldn't rename **{old}** — check the name isn't taken.")
        await context.ok(f"Renamed **{old}** to **{new}**.")
        return

    if action in ("show", "view", "info"):
        if len(parts) < 2:
            raise CommandError(f"Usage: `{context.prefix}playlist show <name> [page]`")
        page = 1
        name_parts = parts[1:]
        if len(name_parts) > 1 and name_parts[-1].isdigit():
            page = int(name_parts[-1])
            name_parts = name_parts[:-1]
        name = _clean_name(" ".join(name_parts))

        tracks = await store.get_playlist(context.author_id, name)
        if tracks is None:
            raise CommandError(f"You don't have a playlist called **{name}**.")
        await context.reply(embed=ui.playlist_contents(name, tracks, page))
        return

    if action in ("add", "save"):
        if len(parts) < 2:
            raise CommandError(f"Usage: `{context.prefix}playlist add <name> [song]`")
        name = _clean_name(parts[1])
        query = " ".join(parts[2:])

        async with context.bot.typing(context.channel_id):
            tracks = await _current_or_query(context, query)
        total = await store.add_to_playlist(context.author_id, name, tracks)
        count = len(tracks)
        await context.ok(
            f"Added **{count}** {plural(count, 'track')} to **{name}** ({total} total)."
        )
        return

    if action in ("remove", "rm", "del"):
        if len(parts) < 3 or not parts[-1].isdigit():
            raise CommandError(f"Usage: `{context.prefix}playlist remove <name> <position>`")
        name = _clean_name(" ".join(parts[1:-1]))
        removed = await store.remove_from_playlist(
            context.author_id, name, int(parts[-1]) - 1
        )
        if removed is None:
            raise CommandError("No such playlist or position.")
        await context.ok(f"Removed **{removed.display_title}** from **{name}**.")
        return

    if action in ("clear", "empty"):
        name = _clean_name(" ".join(parts[1:]))
        removed = await store.clear_playlist(context.author_id, name)
        if removed is None:
            raise CommandError(f"You don't have a playlist called **{name}**.")
        await context.ok(f"Cleared **{removed}** {plural(removed, 'track')} from **{name}**.")
        return

    if action in ("play", "load", "queue", "start"):
        name = _clean_name(" ".join(parts[1:]))
        tracks = await store.get_playlist(context.author_id, name)
        if tracks is None:
            raise CommandError(f"You don't have a playlist called **{name}**.")
        await _load_into_queue(context, tracks, name)
        return

    # No recognised sub-command: treat the whole argument as a playlist to play.
    name = _clean_name(context.argument)
    tracks = await store.get_playlist(context.author_id, name)
    if tracks is None:
        raise CommandError(
            f"Unknown option **{action}**. Try "
            f"`{context.prefix}playlist list|create|add|remove|play|delete`."
        )
    await _load_into_queue(context, tracks, name)


# ---------------------------------------------------------------------------
# favourites
# ---------------------------------------------------------------------------
@command(
    "favourites",
    aliases=("favorites", "fav", "favs", "favourite", "favorite"),
    usage="[add|remove|list|play] [song]",
    description="Your personal favourites list.",
    category=CATEGORY,
)
async def favourites(context: Context) -> None:
    parts = context.argv
    action = parts[0].lower() if parts else "list"
    store = context.bot.store

    if action in ("add", "save", "+"):
        async with context.bot.typing(context.channel_id):
            tracks = await _current_or_query(context, " ".join(parts[1:]))
        total = await store.add_to_playlist(context.author_id, FAVOURITES, tracks)
        count = len(tracks)
        await context.ok(
            f"⭐ Added **{count}** {plural(count, 'track')} to your favourites ({total} total)."
        )
        return

    if action in ("remove", "rm", "del", "-"):
        if len(parts) < 2 or not parts[1].isdigit():
            raise CommandError(f"Usage: `{context.prefix}favourites remove <position>`")
        removed = await store.remove_from_playlist(
            context.author_id, FAVOURITES, int(parts[1]) - 1
        )
        if removed is None:
            raise CommandError("There's nothing at that position.")
        await context.ok(f"Removed **{removed.display_title}** from your favourites.")
        return

    if action in ("clear",):
        removed = await store.clear_playlist(context.author_id, FAVOURITES)
        if not removed:
            raise CommandError("Your favourites list is already empty.")
        await context.ok(f"Cleared **{removed}** {plural(removed, 'track')}.")
        return

    tracks = await store.get_playlist(context.author_id, FAVOURITES) or []

    if action in ("play", "load", "queue"):
        await _load_into_queue(context, tracks, "Favourites")
        return

    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    if not tracks:
        await context.info(
            f"You haven't favourited anything yet. Try `{context.prefix}fav add` "
            "while something is playing."
        )
        return
    await context.reply(embed=ui.playlist_contents("⭐ Favourites", tracks, page))
