"""Per-guild playback: queue state machine plus the driver task."""

from __future__ import annotations

import asyncio
import enum
import logging
import random
from collections import deque
from typing import TYPE_CHECKING

from .audio import AudioStreamer, build_filter_chain
from .config import Config
from .track import Track

if TYPE_CHECKING:
    from .bot import MusicBot

log = logging.getLogger(__name__)

HISTORY_LIMIT = 100
# Consecutive failures before we stop walking the queue and just give up.
MAX_CONSECUTIVE_FAILURES = 5


class LoopMode(enum.Enum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"

    @property
    def label(self) -> str:
        return {"off": "Off", "track": "Track", "queue": "Queue"}[self.value]


class GuildPlayer:
    """Owns the voice connection, the queue and the playback task for one guild.

    The driver is a single long-lived task (:meth:`_run`).  Everything else —
    commands, track-end callbacks, timeouts — just mutates state and pokes an
    event, which keeps the concurrency story small enough to reason about.
    """

    def __init__(self, bot: MusicBot, guild_id: int, config: Config) -> None:
        self.bot = bot
        self.guild_id = guild_id
        self.config = config

        self.queue: deque[Track] = deque()
        self.history: deque[Track] = deque(maxlen=HISTORY_LIMIT)
        self.current: Track | None = None

        self.loop_mode = LoopMode.OFF
        self.autoplay = False
        self.stay_connected = False  # 24/7 mode
        self.volume = config.default_volume
        self.filters: dict[str, float] = {}

        self.voice_channel_id: int | None = None
        self.text_channel_id: int | None = None
        #: User IDs that voted to skip the current track; reset on every change.
        self.skip_votes: set[int] = set()

        self._voice = None
        self._streamer: AudioStreamer | None = None
        self._task: asyncio.Task[None] | None = None

        self._wake = asyncio.Event()
        self._track_done = asyncio.Event()
        self._forced_next: Track | None = None
        self._skipping = False
        self._destroyed = False
        self._failures = 0
        self._empty_since: float | None = None
        self._announced_end = False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._voice is not None and not self._destroyed

    @property
    def is_playing(self) -> bool:
        return self.current is not None and self._streamer is not None

    @property
    def is_paused(self) -> bool:
        return self._streamer is not None and self._streamer.is_paused

    @property
    def position(self) -> int:
        """Playback position of the current track, in milliseconds."""
        return self._streamer.position_ms if self._streamer else 0

    @property
    def queue_duration(self) -> int:
        """Total milliseconds queued, ignoring live streams."""
        return sum(track.duration or 0 for track in self.queue)

    def is_empty(self) -> bool:
        return self.current is None and not self.queue

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    async def connect(self, channel_id: int) -> None:
        """Join (or move to) a voice channel and make sure the driver runs."""
        if self._voice is not None and self.voice_channel_id == channel_id:
            return

        if self._voice is not None:
            # Moving channels: tear the LiveKit room down and rebuild it.
            await self._teardown_voice()

        self._voice = await self.bot.join_voice(self.guild_id, channel_id)
        self.voice_channel_id = channel_id

        self._streamer = AudioStreamer(
            self._voice,
            ffmpeg_path=self.config.ffmpeg_path,
            on_track_end=self._on_track_end,
        )
        self._streamer.set_volume(self.volume)
        await self._streamer.start()

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _teardown_voice(self) -> None:
        if self._streamer is not None:
            await self._streamer.close()
            self._streamer = None
        if self._voice is not None:
            try:
                await self._voice.disconnect()
            except Exception:
                log.debug("voice disconnect failed", exc_info=True)
            self._voice = None
        self.voice_channel_id = None

    async def destroy(self) -> None:
        """Stop everything and leave the channel."""
        self._destroyed = True
        self.queue.clear()
        self.current = None
        self._wake.set()
        self._track_done.set()

        # destroy() is sometimes reached *from* the driver task (idle timeout),
        # in which case cancelling and awaiting it would deadlock on itself.
        task = self._task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self._teardown_voice()

    # ------------------------------------------------------------------
    # Queue mutation
    # ------------------------------------------------------------------
    def enqueue(self, tracks: list[Track], *, at_front: bool = False) -> int:
        """Add tracks, honouring ``max_queue_size``. Returns how many were added."""
        space = max(0, self.config.max_queue_size - len(self.queue))
        accepted = tracks[:space]
        if at_front:
            self.queue.extendleft(reversed(accepted))
        else:
            self.queue.extend(accepted)
        if accepted:
            self._wake.set()
        return len(accepted)

    def clear(self) -> int:
        removed = len(self.queue)
        self.queue.clear()
        return removed

    def shuffle(self) -> None:
        items = list(self.queue)
        random.shuffle(items)
        self.queue = deque(items)

    def remove(self, indices: list[int]) -> list[Track]:
        """Remove queue entries by zero-based index."""
        items = list(self.queue)
        removed = [items[index] for index in indices if 0 <= index < len(items)]
        keep = [item for position, item in enumerate(items) if position not in set(indices)]
        self.queue = deque(keep)
        return removed

    def move(self, source: int, destination: int) -> Track | None:
        items = list(self.queue)
        if not (0 <= source < len(items)):
            return None
        track = items.pop(source)
        destination = max(0, min(destination, len(items)))
        items.insert(destination, track)
        self.queue = deque(items)
        return track

    def deduplicate(self) -> int:
        """Drop queue entries pointing at something already queued."""
        seen: set[str] = set()
        keep: list[Track] = []
        for track in self.queue:
            key = track.url or track.resolve_query or track.title
            if key in seen:
                continue
            seen.add(key)
            keep.append(track)
        removed = len(self.queue) - len(keep)
        self.queue = deque(keep)
        return removed

    def remove_by_user(self, user_id: int) -> int:
        keep = [track for track in self.queue if track.requester_id != user_id]
        removed = len(self.queue) - len(keep)
        self.queue = deque(keep)
        return removed

    # ------------------------------------------------------------------
    # Transport controls
    # ------------------------------------------------------------------
    async def skip(self, count: int = 1) -> Track | None:
        """Skip the current track (and ``count - 1`` queued ones)."""
        skipped = self.current
        for _ in range(max(0, count - 1)):
            if self.queue:
                self.queue.popleft()
        self._skipping = True
        await self._end_current()
        return skipped

    async def skip_to(self, index: int) -> Track | None:
        """Jump to the queue entry at zero-based ``index``."""
        if not (0 <= index < len(self.queue)):
            return None
        for _ in range(index):
            self.queue.popleft()
        target = self.queue[0]
        self._skipping = True
        await self._end_current()
        return target

    async def play_previous(self) -> Track | None:
        """Replay the most recent track from history."""
        if not self.history:
            return None
        previous = self.history.pop()
        if self.current is not None:
            self.queue.appendleft(self.current)
        self._forced_next = previous
        self._skipping = True
        await self._end_current()
        return previous

    async def stop(self) -> None:
        """Clear the queue and stop playback, staying connected."""
        self.queue.clear()
        self.loop_mode = LoopMode.OFF
        self._skipping = True
        await self._end_current()

    def pause(self) -> bool:
        if self._streamer is None or self._streamer.is_paused:
            return False
        self._streamer.pause()
        return True

    def resume(self) -> bool:
        if self._streamer is None or not self._streamer.is_paused:
            return False
        self._streamer.resume()
        return True

    async def seek(self, position_ms: int) -> bool:
        """Restart the current track at ``position_ms``."""
        if self.current is None or self._streamer is None:
            return False
        if self.current.is_live:
            return False
        duration = self.current.duration
        position_ms = max(0, position_ms)
        if duration and position_ms >= duration:
            await self.skip()
            return True
        return await self._start_stream(self.current, start_ms=position_ms)

    def set_volume(self, volume: int) -> int:
        self.volume = max(0, min(volume, self.config.max_volume))
        if self._streamer is not None:
            self._streamer.set_volume(self.volume)
        return self.volume

    async def apply_filters(self) -> None:
        """Re-encode the current track so filter changes take effect."""
        if self.current is None or self._streamer is None:
            return
        await self._start_stream(self.current, start_ms=self.position)

    # ------------------------------------------------------------------
    # Driver
    # ------------------------------------------------------------------
    async def _end_current(self) -> None:
        """Cut the current source short; the driver takes it from there."""
        if self._streamer is not None:
            await self._streamer.stop()
        self._track_done.set()

    async def _on_track_end(self, error: Exception | None) -> None:
        if error is not None:
            log.warning("guild %s: playback error: %s", self.guild_id, error)
        self._track_done.set()

    def _next_track(self) -> Track | None:
        """Advance the queue according to loop mode, then return what to play."""
        if self._forced_next is not None:
            track, self._forced_next = self._forced_next, None
            # The caller already decided what happens to the outgoing track.
            self.current = None
            self._skipping = False
            return track

        finished, self.current = self.current, None
        skipped, self._skipping = self._skipping, False

        if finished is not None:
            if self.loop_mode is LoopMode.TRACK and not skipped:
                return finished
            if self.loop_mode is LoopMode.QUEUE:
                # Requeue a fresh copy so the played-at metadata stays clean.
                self.queue.append(finished)
            self.history.append(finished)

        if self.queue:
            return self.queue.popleft()
        return None

    async def _autoplay_track(self) -> Track | None:
        """Find a follow-up when autoplay is on and the queue ran dry."""
        seed = self.history[-1] if self.history else None
        if seed is None:
            return None
        exclude = {track.url for track in self.history if track.url}
        try:
            candidate = await self.bot.sources.related(seed, exclude)
        except Exception:
            log.debug("autoplay lookup failed", exc_info=True)
            return None
        if candidate is None:
            return None
        candidate.requester_id = seed.requester_id
        candidate.requester_name = "Autoplay"
        return candidate

    async def _start_stream(self, track: Track, *, start_ms: int = 0) -> bool:
        """Resolve the track if needed and hand it to the streamer."""
        if self._streamer is None:
            return False
        try:
            stream_url = await self.bot.sources.resolve_stream(track)
        except Exception as exc:
            log.warning("could not resolve %r: %s", track.title, exc)
            await self._report(f"❌ Could not load **{track.display_title}** — {exc}")
            return False

        try:
            await self._streamer.play(
                stream_url,
                start_ms=start_ms,
                filter_chain=build_filter_chain(self.filters),
                is_local=track.source in ("local",),
            )
        except Exception as exc:
            log.warning("could not start playback for %r: %s", track.title, exc)
            await self._report(f"❌ Could not play **{track.display_title}** — {exc}")
            return False
        return True

    async def _run(self) -> None:
        """The playback driver. One iteration == one track."""
        try:
            while not self._destroyed:
                track = self._next_track()

                if track is None and self.autoplay:
                    track = await self._autoplay_track()

                if track is None:
                    if not await self._wait_for_work():
                        return
                    continue

                self.current = track
                self.skip_votes.clear()
                self._track_done.clear()

                if not await self._start_stream(track):
                    self._failures += 1
                    self.current = None
                    if self._failures >= MAX_CONSECUTIVE_FAILURES:
                        self._failures = 0
                        self.queue.clear()
                        await self._report(
                            "⏹️ Too many tracks failed in a row, so I stopped the queue."
                        )
                    continue

                self._failures = 0
                self._announced_end = False
                await self._announce_now_playing(track)
                await self._track_done.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("guild %s: player loop crashed", self.guild_id)

    async def _wait_for_work(self) -> bool:
        """Idle until something is queued. Returns False if we should shut down."""
        self.current = None
        self._wake.clear()

        if self.history and not self._announced_end:
            self._announced_end = True
            await self._report("✅ The queue has ended.")

        timeout = self.config.idle_timeout if not self.stay_connected else None
        if timeout is not None and timeout <= 0:
            timeout = None

        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            await self._report("👋 Leaving the voice channel — nothing left to play.")
            await self.bot.players.discard(self.guild_id)
            return False

    # ------------------------------------------------------------------
    # Channel-occupancy handling
    # ------------------------------------------------------------------
    async def on_listeners_changed(self, listener_count: int) -> None:
        """Called when someone joins/leaves the bot's voice channel."""
        if self.stay_connected or self._destroyed:
            return

        loop = asyncio.get_running_loop()
        if listener_count > 0:
            self._empty_since = None
            return
        if self._empty_since is not None:
            return

        self._empty_since = loop.time()
        timeout = self.config.empty_channel_timeout
        if timeout <= 0:
            return

        async def leave_if_still_empty() -> None:
            await asyncio.sleep(timeout)
            if self._destroyed or self.stay_connected or self._empty_since is None:
                return
            await self._report("👋 Everyone left, so I did too.")
            await self.bot.players.discard(self.guild_id)

        asyncio.create_task(leave_if_still_empty())

    # ------------------------------------------------------------------
    # Announcements
    # ------------------------------------------------------------------
    async def _announce_now_playing(self, track: Track) -> None:
        if not self.config.announce_now_playing or self.text_channel_id is None:
            return
        await self.bot.announce_now_playing(self, track)

    async def _report(self, message: str) -> None:
        if self.text_channel_id is None:
            return
        await self.bot.send_plain(self.text_channel_id, message)


class PlayerManager:
    """Registry of per-guild players."""

    def __init__(self, bot: MusicBot, config: Config) -> None:
        self.bot = bot
        self.config = config
        self._players: dict[int, GuildPlayer] = {}
        self._lock = asyncio.Lock()

    def get(self, guild_id: int) -> GuildPlayer | None:
        return self._players.get(guild_id)

    async def create(self, guild_id: int) -> GuildPlayer:
        async with self._lock:
            player = self._players.get(guild_id)
            if player is None:
                player = GuildPlayer(self.bot, guild_id, self.config)
                self._players[guild_id] = player
            return player

    async def discard(self, guild_id: int) -> None:
        player = self._players.pop(guild_id, None)
        if player is not None:
            await player.destroy()

    async def close(self) -> None:
        for guild_id in list(self._players):
            await self.discard(guild_id)

    def __iter__(self):
        return iter(self._players.values())

    def __len__(self) -> int:
        return len(self._players)
