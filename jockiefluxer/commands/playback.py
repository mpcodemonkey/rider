"""Playback commands: play, transport controls, seeking and volume."""

from __future__ import annotations

from .. import ui
from ..track import LoadType, Track
from ..utils import format_duration, parse_time
from .core import CommandError, Context, command

SEARCH_RESULT_COUNT = 10
SEARCH_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
async def _enqueue(
    context: Context, query: str, *, at_front: bool = False, play_now: bool = False
) -> None:
    """Shared body of play / playnext / playnow."""
    player = await context.connect_player()

    async with context.bot.typing(context.channel_id):
        result = await context.bot.sources.load(query)

    if result.load_type is LoadType.ERROR:
        raise CommandError(result.error or "That link could not be loaded.")
    if result.load_type is LoadType.EMPTY or not result.tracks:
        raise CommandError(f"Nothing found for **{query}**.")

    for track in result.tracks:
        track.requester_id = context.author_id
        track.requester_name = context.author_name

    if result.load_type is LoadType.PLAYLIST:
        added = player.enqueue(result.tracks, at_front=at_front)
        await context.reply(
            embed=ui.added_playlist(
                result.playlist_name or "Playlist",
                added,
                url=result.playlist_url,
                truncated=len(result.tracks) - added,
            )
        )
    else:
        track = result.tracks[0]
        if not player.enqueue([track], at_front=at_front):
            raise CommandError("The queue is full.")
        # Only announce a queue position when the track has to wait its turn.
        if player.is_playing or player.queue:
            await context.reply(
                embed=ui.added_track(
                    track,
                    position=1 if at_front else len(player.queue),
                    at_front=at_front,
                )
            )

    if play_now and player.is_playing:
        await player.skip()


@command(
    "play",
    aliases=("p", "add"),
    usage="<song or URL>",
    description="Play a track, or add it to the queue.",
    requires_voice=True,
)
async def play(context: Context) -> None:
    query = context.argument
    attachments = getattr(context.message, "attachments", None) or []

    if not query and attachments:
        attachment = attachments[0]
        player = await context.connect_player()
        result = await context.bot.sources.load_attachment(
            attachment.url, getattr(attachment, "filename", "attachment")
        )
        track = result.tracks[0]
        track.requester_id = context.author_id
        track.requester_name = context.author_name
        player.enqueue([track])
        if player.is_playing or player.queue:
            await context.reply(embed=ui.added_track(track, len(player.queue)))
        return

    if not query:
        # Bare "play" resumes, matching Jockie.
        player = context.player
        if player is not None and player.is_paused:
            player.resume()
            await context.ok("Resumed playback.")
            return
        raise CommandError(f"Give me something to play — `{context.prefix}play <song>`.")

    await _enqueue(context, query)


@command(
    "playnext",
    aliases=("pn", "playtop", "ptop"),
    usage="<song or URL>",
    description="Add a track to the front of the queue.",
    requires_voice=True,
    dj_only=True,
)
async def playnext(context: Context) -> None:
    if not context.argument:
        raise CommandError(f"Usage: `{context.prefix}playnext <song>`")
    await _enqueue(context, context.argument, at_front=True)


@command(
    "playnow",
    aliases=("pnow",),
    usage="<song or URL>",
    description="Play a track immediately, pushing the current one back.",
    requires_voice=True,
    dj_only=True,
)
async def playnow(context: Context) -> None:
    if not context.argument:
        raise CommandError(f"Usage: `{context.prefix}playnow <song>`")
    await _enqueue(context, context.argument, at_front=True, play_now=True)


@command(
    "search",
    aliases=("find",),
    usage="<query>",
    description="Search and pick a result to queue.",
    requires_voice=True,
)
async def search(context: Context) -> None:
    if not context.argument:
        raise CommandError(f"Usage: `{context.prefix}search <query>`")

    async with context.bot.typing(context.channel_id):
        results = await context.bot.sources.search(
            context.argument, limit=SEARCH_RESULT_COUNT
        )
    if not results:
        raise CommandError(f"Nothing found for **{context.argument}**.")

    prompt = await context.reply(embed=ui.search_results(results, context.prefix))

    reply = await context.bot.wait_for_message(
        context.channel_id, context.author_id, timeout=SEARCH_TIMEOUT
    )
    if reply is None:
        await context.info("Search timed out.")
        return

    choice = (reply.content or "").strip().lower()
    if choice in ("cancel", "stop", "no"):
        await context.info("Search cancelled.")
        return
    if not choice.isdigit() or not 1 <= int(choice) <= len(results):
        await context.fail("That wasn't one of the result numbers.")
        return

    track = results[int(choice) - 1]
    track.requester_id = context.author_id
    track.requester_name = context.author_name

    player = await context.connect_player()
    if not player.enqueue([track]):
        raise CommandError("The queue is full.")
    if player.is_playing or player.queue:
        await context.send(embed=ui.added_track(track, len(player.queue)))

    if prompt is not None:
        try:
            await prompt.delete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
@command("pause", description="Pause playback.", dj_only=True)
async def pause(context: Context) -> None:
    player = await context.require_playing()
    if not player.pause():
        raise CommandError("Playback is already paused.")
    await context.ok("Paused playback.")


@command(
    "resume",
    aliases=("unpause", "continue"),
    description="Resume playback.",
    dj_only=True,
)
async def resume(context: Context) -> None:
    player = await context.require_player()
    if not player.resume():
        raise CommandError("Playback isn't paused.")
    await context.ok("Resumed playback.")


@command(
    "skip",
    aliases=("s", "next", "voteskip"),
    usage="[amount]",
    description="Skip the current track (votes if you're not a DJ).",
)
async def skip(context: Context) -> None:
    player = await context.require_playing()
    assert player.current is not None

    amount = 1
    if context.argument:
        if not context.argument.isdigit() or int(context.argument) < 1:
            raise CommandError("Give me a positive number of tracks to skip.")
        amount = int(context.argument)

    # DJs and whoever queued the track skip outright; everyone else votes.
    privileged = await context.is_dj() or player.current.requester_id == context.author_id
    if not privileged:
        await _vote_skip(context, player)
        return

    skipped = await player.skip(amount)
    if skipped is None:
        raise CommandError("Nothing to skip.")
    if amount > 1:
        await context.ok(f"Skipped **{amount}** tracks.")
    else:
        await context.ok(f"Skipped **{skipped.display_title}**.")


async def _vote_skip(context: Context, player) -> None:
    listeners = context.bot.listener_ids(player)
    required = max(1, (len(listeners) // 2) + 1)

    if context.author_id in player.skip_votes:
        raise CommandError("You've already voted to skip this track.")
    player.skip_votes.add(context.author_id)

    votes = len(player.skip_votes & listeners) if listeners else len(player.skip_votes)
    if votes >= required:
        skipped = await player.skip()
        title = skipped.display_title if skipped else "the track"
        await context.ok(f"Vote passed — skipped **{title}**.")
        return

    await context.info(f"🗳️ Skip vote: **{votes}/{required}**.")


@command(
    "forceskip",
    aliases=("fs",),
    usage="[amount]",
    description="Skip immediately, no vote.",
    dj_only=True,
)
async def forceskip(context: Context) -> None:
    player = await context.require_playing()

    amount = 1
    if context.argument:
        if not context.argument.isdigit() or int(context.argument) < 1:
            raise CommandError("Give me a positive number of tracks to skip.")
        amount = int(context.argument)

    skipped = await player.skip(amount)
    if skipped is None:
        raise CommandError("Nothing to skip.")
    await context.ok(
        f"Skipped **{amount}** tracks."
        if amount > 1
        else f"Skipped **{skipped.display_title}**."
    )


@command(
    "skipto",
    aliases=("jump", "jumpto"),
    usage="<position>",
    description="Jump straight to a queue position.",
    dj_only=True,
)
async def skipto(context: Context) -> None:
    player = await context.require_playing()
    if not context.argument.isdigit():
        raise CommandError(f"Usage: `{context.prefix}skipto <position>`")

    target = await player.skip_to(int(context.argument) - 1)
    if target is None:
        raise CommandError(f"There's no track at position {context.argument}.")
    await context.ok(f"Jumping to **{target.display_title}**.")


@command(
    "previous",
    aliases=("prev", "back"),
    description="Play the previous track again.",
    dj_only=True,
)
async def previous(context: Context) -> None:
    player = await context.require_player()
    track = await player.play_previous()
    if track is None:
        raise CommandError("There's nothing in the history yet.")
    await context.ok(f"Playing **{track.display_title}** again.")


@command(
    "replay",
    aliases=("restart",),
    description="Restart the current track from the beginning.",
    dj_only=True,
)
async def replay(context: Context) -> None:
    player = await context.require_playing()
    if not await player.seek(0):
        raise CommandError("This track can't be restarted.")
    await context.ok("Restarted the track.")


@command(
    "stop",
    description="Stop playback and clear the queue.",
    dj_only=True,
)
async def stop(context: Context) -> None:
    player = await context.require_player()
    await player.stop()
    await context.ok("Stopped playback and cleared the queue.")


@command(
    "seek",
    usage="<time>",
    description="Jump to a timestamp, e.g. 1:30 or 90s.",
    dj_only=True,
)
async def seek(context: Context) -> None:
    player = await context.require_playing()
    if not context.argument:
        raise CommandError(f"Usage: `{context.prefix}seek 1:30`")

    position = parse_time(context.argument)
    if position is None:
        raise CommandError("I couldn't read that timestamp. Try `1:30`, `90` or `1m30s`.")
    if player.current is not None and player.current.is_live:
        raise CommandError("You can't seek in a live stream.")

    if not await player.seek(position):
        raise CommandError("Seeking failed.")
    await context.ok(f"Jumped to `{format_duration(position)}`.")


@command(
    "forward",
    aliases=("fwd", "ff"),
    usage="[time]",
    description="Skip ahead (default 10 seconds).",
    dj_only=True,
)
async def forward(context: Context) -> None:
    await _relative_seek(context, 1)


@command(
    "rewind",
    aliases=("rwd", "backward"),
    usage="[time]",
    description="Skip backwards (default 10 seconds).",
    dj_only=True,
)
async def rewind(context: Context) -> None:
    await _relative_seek(context, -1)


async def _relative_seek(context: Context, direction: int) -> None:
    player = await context.require_playing()
    if player.current is not None and player.current.is_live:
        raise CommandError("You can't seek in a live stream.")

    delta = parse_time(context.argument) if context.argument else 10_000
    if delta is None:
        raise CommandError("I couldn't read that duration. Try `30` or `1:00`.")

    target = max(0, player.position + direction * delta)
    if not await player.seek(target):
        raise CommandError("Seeking failed.")
    await context.ok(f"Now at `{format_duration(target)}`.")


# ---------------------------------------------------------------------------
# Status and volume
# ---------------------------------------------------------------------------
@command(
    "nowplaying",
    aliases=("np", "playing", "current", "song"),
    description="Show what's playing right now.",
)
async def nowplaying(context: Context) -> None:
    player = await context.require_playing()
    assert player.current is not None
    await context.reply(embed=ui.now_playing(player, player.current))


@command(
    "volume",
    aliases=("vol", "v"),
    usage="[0-200]",
    description="Show or set the playback volume.",
)
async def volume(context: Context) -> None:
    player = await context.require_player()

    if not context.argument:
        await context.info(f"🔊 Volume is **{player.volume}%**.")
        return

    if not await context.is_dj():
        raise CommandError("Only DJs can change the volume.")

    raw = context.argument.rstrip("%")
    if not raw.isdigit():
        raise CommandError(f"Usage: `{context.prefix}volume 50`")

    requested = int(raw)
    if requested > context.bot.config.max_volume:
        raise CommandError(
            f"Volume is capped at {context.bot.config.max_volume}% on this server."
        )

    applied = player.set_volume(requested)
    await context.bot.store.update_settings(context.guild_id, volume=applied)
    await context.ok(f"🔊 Volume set to **{applied}%**.")


@command(
    "connect",
    aliases=("join", "summon"),
    description="Bring the bot into your voice channel.",
    requires_voice=True,
)
async def connect(context: Context) -> None:
    player = await context.connect_player()
    channel_id = player.voice_channel_id
    await context.ok(f"Joined <#{channel_id}>.")


@command(
    "disconnect",
    aliases=("dc", "leave", "bye"),
    description="Leave the voice channel and clear the queue.",
    dj_only=True,
)
async def disconnect(context: Context) -> None:
    player = await context.require_player()
    await context.bot.players.discard(player.guild_id)
    await context.ok("Disconnected. 👋")


@command(
    "grab",
    aliases=("save", "bookmark"),
    description="Send the current track to your DMs.",
)
async def grab(context: Context) -> None:
    player = await context.require_playing()
    track: Track = player.current  # type: ignore[assignment]

    embed = ui.now_playing(player, track, compact=True)
    embed.title = "Saved track"
    sent = await context.bot.send_dm(context.author_id, embed=embed)
    if not sent:
        raise CommandError("I couldn't DM you — check whether your DMs are open.")
    await context.ok("Sent it to your DMs. 📬")
