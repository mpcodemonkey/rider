"""Embed builders. Formatting mirrors Jockie's output so the bot feels familiar."""

from __future__ import annotations

from fluxer import Embed

from .player import GuildPlayer, LoopMode
from .track import Track
from .utils import format_duration, progress_bar, truncate

COLOR_PRIMARY = 0x7C4DFF
COLOR_SUCCESS = 0x2ECC71
COLOR_WARNING = 0xF1C40F
COLOR_ERROR = 0xE74C3C

QUEUE_PAGE_SIZE = 10


def _requester(track: Track) -> str:
    return track.requester_name or "Unknown"


def error(message: str) -> Embed:
    return Embed(description=f"❌ {message}", color=COLOR_ERROR)


def success(message: str) -> Embed:
    return Embed(description=f"✅ {message}", color=COLOR_SUCCESS)


def info(message: str) -> Embed:
    return Embed(description=message, color=COLOR_PRIMARY)


def warning(message: str) -> Embed:
    return Embed(description=f"⚠️ {message}", color=COLOR_WARNING)


def now_playing(player: GuildPlayer, track: Track, *, compact: bool = False) -> Embed:
    """The "Now playing" card, with a progress bar for on-demand lookups."""
    embed = Embed(
        title="Now playing",
        description=f"**{track.markdown_link}**",
        color=COLOR_PRIMARY,
    )
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)

    if not compact:
        if track.is_live:
            embed.add_field(name="Duration", value="🔴 LIVE", inline=True)
        else:
            position = format_duration(player.position)
            total = track.duration_text
            embed.add_field(
                name="Duration",
                value=f"`{position} / {total}`\n{progress_bar(player.position, track.duration or 0)}",
                inline=False,
            )

    if track.uploader:
        embed.add_field(name="Author", value=truncate(track.uploader, 40), inline=True)
    embed.add_field(name="Requested by", value=_requester(track), inline=True)
    embed.add_field(name="Volume", value=f"{player.volume}%", inline=True)

    if player.loop_mode is not LoopMode.OFF:
        embed.add_field(name="Loop", value=player.loop_mode.label, inline=True)
    if player.filters:
        embed.add_field(
            name="Filters", value=", ".join(sorted(player.filters)), inline=True
        )
    if player.is_paused:
        embed.set_footer(text="Playback is paused")

    return embed


def added_track(track: Track, position: int, *, at_front: bool = False) -> Embed:
    """Confirmation shown when a single track lands in the queue."""
    embed = Embed(
        title="Added to queue",
        description=f"**{track.markdown_link}**",
        color=COLOR_SUCCESS,
    )
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    embed.add_field(name="Duration", value=track.duration_text, inline=True)
    embed.add_field(
        name="Position", value="Next up" if at_front else str(position), inline=True
    )
    embed.add_field(name="Requested by", value=_requester(track), inline=True)
    return embed


def added_playlist(
    name: str, count: int, *, url: str | None = None, truncated: int = 0
) -> Embed:
    title = f"[{name}]({url})" if url else name
    embed = Embed(
        title="Added playlist to queue",
        description=f"**{title}** — {count} tracks",
        color=COLOR_SUCCESS,
    )
    if truncated:
        embed.set_footer(text=f"{truncated} tracks were dropped (queue limit reached)")
    return embed


def queue_page(player: GuildPlayer, page: int) -> Embed:
    """One page of the queue, 1-indexed like Jockie's."""
    total = len(player.queue)
    pages = max(1, -(-total // QUEUE_PAGE_SIZE))
    page = max(1, min(page, pages))
    start = (page - 1) * QUEUE_PAGE_SIZE

    embed = Embed(title="Queue", color=COLOR_PRIMARY)

    if player.current is not None:
        played = format_duration(player.position)
        embed.add_field(
            name="Now playing",
            value=(
                f"**{player.current.markdown_link}**\n"
                f"`{played} / {player.current.duration_text}` • {_requester(player.current)}"
            ),
            inline=False,
        )

    if total == 0:
        embed.add_field(
            name="Up next", value="*Nothing queued — add something with `play`.*", inline=False
        )
    else:
        lines = []
        for offset, track in enumerate(list(player.queue)[start : start + QUEUE_PAGE_SIZE]):
            lines.append(
                f"`{start + offset + 1}.` {track.markdown_link} "
                f"`{track.duration_text}` • {_requester(track)}"
            )
        embed.add_field(name="Up next", value="\n".join(lines), inline=False)

    footer = [f"Page {page}/{pages}", f"{total} in queue"]
    if player.queue_duration:
        footer.append(f"{format_duration(player.queue_duration)} total")
    if player.loop_mode is not LoopMode.OFF:
        footer.append(f"Loop: {player.loop_mode.label}")
    if player.autoplay:
        footer.append("Autoplay on")
    embed.set_footer(text=" • ".join(footer))
    return embed


def search_results(tracks: list[Track], prefix: str) -> Embed:
    lines = [
        f"`{index}.` {track.markdown_link} `{track.duration_text}`"
        for index, track in enumerate(tracks, start=1)
    ]
    embed = Embed(
        title="Search results",
        description="\n".join(lines),
        color=COLOR_PRIMARY,
    )
    embed.set_footer(
        text=f"Reply with a number (1-{len(tracks)}) within 30s, or 'cancel' to stop."
    )
    return embed


def history_page(tracks: list[Track], page: int) -> Embed:
    pages = max(1, -(-len(tracks) // QUEUE_PAGE_SIZE))
    page = max(1, min(page, pages))
    start = (page - 1) * QUEUE_PAGE_SIZE
    window = tracks[start : start + QUEUE_PAGE_SIZE]

    embed = Embed(
        title="Recently played",
        description="\n".join(
            f"`{start + offset + 1}.` {track.markdown_link} `{track.duration_text}`"
            for offset, track in enumerate(window)
        )
        or "*Nothing played yet.*",
        color=COLOR_PRIMARY,
    )
    embed.set_footer(text=f"Page {page}/{pages}")
    return embed


def playlist_list(name_counts: list[tuple[str, int]], owner: str) -> Embed:
    if not name_counts:
        return info(
            "You don't have any saved playlists yet. Create one with "
            "`playlist create <name>`."
        )
    lines = [
        f"`{index}.` **{name}** — {count} tracks"
        for index, (name, count) in enumerate(name_counts, start=1)
    ]
    return Embed(
        title=f"{owner}'s playlists",
        description="\n".join(lines),
        color=COLOR_PRIMARY,
    )


def playlist_contents(name: str, tracks: list[Track], page: int) -> Embed:
    pages = max(1, -(-len(tracks) // QUEUE_PAGE_SIZE))
    page = max(1, min(page, pages))
    start = (page - 1) * QUEUE_PAGE_SIZE
    window = tracks[start : start + QUEUE_PAGE_SIZE]

    embed = Embed(
        title=f"Playlist: {name}",
        description="\n".join(
            f"`{start + offset + 1}.` {track.markdown_link} `{track.duration_text}`"
            for offset, track in enumerate(window)
        )
        or "*This playlist is empty.*",
        color=COLOR_PRIMARY,
    )
    embed.set_footer(text=f"Page {page}/{pages} • {len(tracks)} tracks")
    return embed
