"""Audio pipeline: ffmpeg -> PCM frames -> LiveKit.

``fluxer.py`` ships a simple "spawn ffmpeg and publish it" helper, but a music
bot needs more than that: accurate position tracking, seeking, live volume
changes and gapless track transitions.  So we publish a single raw
``livekit.rtc.AudioSource`` for the whole voice session and push 20 ms PCM
frames into it ourselves.

Layout:

* :class:`FFmpegReader`  — one ffmpeg process, decoding one track to s16le.
* :class:`AudioStreamer` — owns the LiveKit track and the frame pacing loop.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
import warnings
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
CHANNELS = 2
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 960
BYTES_PER_FRAME = SAMPLES_PER_FRAME * CHANNELS * 2  # s16le stereo
SILENCE_FRAME = b"\x00" * BYTES_PER_FRAME

# Roughly two seconds of read-ahead, which rides out network hiccups on
# YouTube/SoundCloud streams without adding noticeable seek latency.
PREBUFFER_FRAMES = 100

# HTTP sources drop connections regularly; make ffmpeg retry instead of ending
# the track early.
_RECONNECT_ARGS = [
    "-reconnect", "1",
    "-reconnect_streamed", "1",
    "-reconnect_delay_max", "5",
]


# ---------------------------------------------------------------------------
# Volume scaling
# ---------------------------------------------------------------------------
def _make_scaler() -> Callable[[bytes, float], bytes]:
    """Pick the fastest available int16 gain implementation.

    ``audioop`` (C, stdlib through 3.12) is preferred, then numpy, then a pure
    Python fallback so the bot never hard-depends on either.
    """
    try:
        with warnings.catch_warnings():
            # audioop is deprecated in 3.12 and gone in 3.13; the fallbacks
            # below cover that, so the warning is just noise here.
            warnings.simplefilter("ignore", DeprecationWarning)
            import audioop  # type: ignore[import-not-found]

        def scale_audioop(data: bytes, factor: float) -> bytes:
            return audioop.mul(data, 2, factor)

        return scale_audioop
    except ImportError:
        pass

    try:
        import numpy as np

        def scale_numpy(data: bytes, factor: float) -> bytes:
            samples = np.frombuffer(data, dtype="<i2").astype(np.float32) * factor
            return np.clip(samples, -32768, 32767).astype("<i2").tobytes()

        return scale_numpy
    except ImportError:
        pass

    import array

    def scale_python(data: bytes, factor: float) -> bytes:
        samples = array.array("h")
        samples.frombytes(data)
        for index, sample in enumerate(samples):
            scaled = int(sample * factor)
            samples[index] = -32768 if scaled < -32768 else 32767 if scaled > 32767 else scaled
        return samples.tobytes()

    return scale_python


_scale_pcm = _make_scaler()


def apply_gain(data: bytes, volume: int) -> bytes:
    """Scale a PCM frame to ``volume`` percent (100 = untouched)."""
    if volume == 100:
        return data
    if volume == 0:
        return b"\x00" * len(data)
    return _scale_pcm(data, volume / 100.0)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def _atempo(factor: float) -> str:
    """Build an ``atempo`` chain; the filter only accepts 0.5–2.0 per stage."""
    factor = max(0.25, min(4.0, factor))
    stages: list[float] = []
    remaining = factor
    while remaining > 2.0:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return ",".join(f"atempo={stage:.4f}" for stage in stages)


def _pitch(factor: float) -> str:
    """Shift pitch without changing tempo."""
    factor = max(0.5, min(2.0, factor))
    return (
        f"asetrate={int(SAMPLE_RATE * factor)},aresample={SAMPLE_RATE},"
        f"{_atempo(1 / factor)}"
    )


BASSBOOST_LEVELS = {
    "off": 0.0,
    "none": 0.0,
    "low": 5.0,
    "medium": 10.0,
    "high": 15.0,
    "extreme": 20.0,
    "insane": 25.0,
}

#: Named effects; each entry is a callable taking the effect's numeric argument.
FILTER_BUILDERS: dict[str, Callable[[float], str]] = {
    # Jockie-style named effects
    "bassboost": lambda level: f"bass=g={level:.1f}:f=110:w=0.6",
    "treble": lambda level: f"treble=g={level:.1f}",
    "nightcore": lambda _: f"asetrate={int(SAMPLE_RATE * 1.25)},aresample={SAMPLE_RATE}",
    "vaporwave": lambda _: (
        f"asetrate={int(SAMPLE_RATE * 0.8)},aresample={SAMPLE_RATE},aecho=0.8:0.9:500:0.2"
    ),
    "daycore": lambda _: f"asetrate={int(SAMPLE_RATE * 0.85)},aresample={SAMPLE_RATE}",
    "8d": lambda _: "apulsator=hz=0.09",
    "tremolo": lambda depth: f"tremolo=f=6.5:d={depth:.2f}",
    "vibrato": lambda depth: f"vibrato=f=6.5:d={depth:.2f}",
    "karaoke": lambda _: "pan=stereo|c0=c0-c1|c1=c1-c0",
    "echo": lambda _: "aecho=0.8:0.9:1000:0.3",
    "reverb": lambda _: "aecho=0.8:0.88:60:0.4",
    "muffle": lambda _: "lowpass=f=800",
    "phone": lambda _: "highpass=f=400,lowpass=f=3000",
    "distortion": lambda _: "acrusher=level_in=4:level_out=6:bits=12:mode=log:aa=1",
    # Numeric effects
    "speed": _atempo,
    "pitch": _pitch,
}

#: Effects that take a numeric argument, with their default and bounds.
NUMERIC_FILTERS = {
    "bassboost": (10.0, -20.0, 25.0),
    "treble": (5.0, -20.0, 20.0),
    "speed": (1.0, 0.25, 4.0),
    "pitch": (1.0, 0.5, 2.0),
    "tremolo": (0.5, 0.0, 1.0),
    "vibrato": (0.5, 0.0, 1.0),
}


def build_filter_chain(filters: dict[str, float]) -> str | None:
    """Render the active effects into a single ``-af`` argument."""
    fragments: list[str] = []
    for name, value in filters.items():
        builder = FILTER_BUILDERS.get(name)
        if builder is None:
            continue
        try:
            fragment = builder(value)
        except Exception:
            log.warning("Could not build filter %r with value %r", name, value)
            continue
        if fragment:
            fragments.append(fragment)
    return ",".join(fragments) if fragments else None


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------
class FFmpegReader:
    """Decodes one source into 20 ms PCM frames on a background task."""

    def __init__(
        self,
        source: str,
        *,
        ffmpeg_path: str = "ffmpeg",
        start_ms: int = 0,
        filter_chain: str | None = None,
        is_local: bool = False,
        before_options: str | None = None,
    ) -> None:
        self.source = source
        self.ffmpeg_path = ffmpeg_path
        self.start_ms = max(0, start_ms)
        self.filter_chain = filter_chain
        self.is_local = is_local
        self.before_options = before_options

        self._process: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=PREBUFFER_FRAMES)
        self._pump_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = b""
        self._closed = False
        self.error: Exception | None = None

    def _build_args(self) -> list[str]:
        args = [self.ffmpeg_path, "-nostdin", "-loglevel", "error"]
        if not self.is_local:
            args += _RECONNECT_ARGS
        if self.before_options:
            args += shlex.split(self.before_options)
        if self.start_ms:
            # Input seeking: fast, and accurate enough for a music bot.
            args += ["-ss", f"{self.start_ms / 1000:.3f}"]
        args += ["-i", self.source, "-vn"]
        if self.filter_chain:
            args += ["-af", self.filter_chain]
        args += [
            "-f", "s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "pipe:1",
        ]
        return args

    async def start(self) -> None:
        args = self._build_args()
        log.debug("spawning ffmpeg: %s", " ".join(args))
        try:
            self._process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"ffmpeg executable not found at '{self.ffmpeg_path}'. "
                "Install ffmpeg or set FFMPEG_PATH."
            ) from exc
        self._pump_task = asyncio.create_task(self._pump())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _pump(self) -> None:
        """Read fixed-size frames from ffmpeg into the prebuffer queue."""
        assert self._process is not None and self._process.stdout is not None
        stdout = self._process.stdout
        try:
            try:
                while True:
                    chunk = await stdout.readexactly(BYTES_PER_FRAME)
                    await self._queue.put(chunk)
            except asyncio.IncompleteReadError as exc:
                if exc.partial:
                    # Pad the tail so every frame handed out is a full 20 ms.
                    await self._queue.put(
                        exc.partial + b"\x00" * (BYTES_PER_FRAME - len(exc.partial))
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = exc
                log.warning("ffmpeg read error for %s: %s", self.source[:80], exc)

            # A blocking put guarantees the end-of-stream sentinel is delivered
            # even when the consumer is still working through the prebuffer.
            await self._queue.put(None)
        except asyncio.CancelledError:
            pass

    async def _drain_stderr(self) -> None:
        """Keep ffmpeg's stderr pipe empty, retaining the tail for diagnostics."""
        assert self._process is not None and self._process.stderr is not None
        stderr = self._process.stderr
        try:
            while True:
                chunk = await stderr.read(2048)
                if not chunk:
                    return
                self._stderr_tail = (self._stderr_tail + chunk)[-4096:]
        except (asyncio.CancelledError, Exception):
            return

    async def read(self) -> bytes | None:
        """Next PCM frame, or ``None`` once the source is exhausted."""
        if self._closed:
            return None
        return await self._queue.get()

    def stderr_text(self) -> str:
        """Whatever ffmpeg last complained about (used in error messages)."""
        return self._stderr_tail.decode("utf-8", "replace").strip()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        process = self._process
        self._process = None

        # Kill before cancelling the readers: that closes ffmpeg's pipes, so
        # the tasks unwind on EOF instead of mid-read.
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

        for task in (self._pump_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._pump_task = None
        self._stderr_task = None

        # Unblock a consumer that is parked on read(); if the queue happens to
        # be full it is not parked anyway and the stale frame gets discarded by
        # the streamer's generation check.
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

        if process is not None:
            # communicate() drains the pipes as well as reaping the process.
            # Awaiting wait() on its own deadlocks when a pipe reader was
            # cancelled mid-read: the process is gone but the transport is
            # never torn down.
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
                if stderr:
                    self._stderr_tail = (self._stderr_tail + stderr)[-4096:]
            except asyncio.TimeoutError:
                log.warning("ffmpeg did not exit after kill")
            except Exception:
                log.debug("error reaping ffmpeg", exc_info=True)


# ---------------------------------------------------------------------------
# Streamer
# ---------------------------------------------------------------------------
class AudioStreamer:
    """Publishes one LiveKit audio track and paces PCM frames into it.

    The published track outlives individual songs, which is what makes track
    transitions gapless — only the underlying :class:`FFmpegReader` is swapped.
    """

    def __init__(
        self,
        voice_client,
        *,
        ffmpeg_path: str = "ffmpeg",
        on_track_end: Callable[[Exception | None], Awaitable[None]] | None = None,
    ) -> None:
        self._voice = voice_client
        self._ffmpeg_path = ffmpeg_path
        self._on_track_end = on_track_end

        self._source = None  # rtc.AudioSource, created on start()
        self._reader: FFmpegReader | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._reader_lock = asyncio.Lock()

        self._paused = asyncio.Event()
        self._paused.set()  # set == playing
        self._closed = False

        self._volume = 100
        self._frames_played = 0
        self._seek_offset_ms = 0
        # Incremented whenever the reader is swapped, so a finishing reader
        # can't be mistaken for the current one.
        self._generation = 0

    # -- properties ----------------------------------------------------
    @property
    def is_playing(self) -> bool:
        return self._reader is not None

    @property
    def is_paused(self) -> bool:
        return not self._paused.is_set()

    @property
    def position_ms(self) -> int:
        return self._seek_offset_ms + self._frames_played * FRAME_MS

    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, volume: int) -> None:
        """Volume is applied per frame in Python, so this takes effect instantly."""
        self._volume = max(0, volume)

    # -- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        """Create and publish the LiveKit track, then begin pacing frames."""
        if self._source is not None:
            return
        import livekit.rtc as rtc

        self._source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
        await self._voice.play(self._source)
        self._loop_task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._closed = True
        self._paused.set()
        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
        self._loop_task = None
        async with self._reader_lock:
            if self._reader is not None:
                await self._reader.close()
                self._reader = None
        self._source = None

    # -- playback ------------------------------------------------------
    async def play(
        self,
        source: str,
        *,
        start_ms: int = 0,
        filter_chain: str | None = None,
        is_local: bool = False,
    ) -> None:
        """Swap in a new source, starting at ``start_ms``."""
        reader = FFmpegReader(
            source,
            ffmpeg_path=self._ffmpeg_path,
            start_ms=start_ms,
            filter_chain=filter_chain,
            is_local=is_local,
        )
        await reader.start()

        async with self._reader_lock:
            old = self._reader
            self._reader = reader
            self._generation += 1
            self._frames_played = 0
            self._seek_offset_ms = start_ms
            self._paused.set()
        if old is not None:
            await old.close()

    async def stop(self) -> None:
        """Stop the current source without tearing down the published track."""
        async with self._reader_lock:
            reader = self._reader
            self._reader = None
            self._generation += 1
            self._frames_played = 0
            self._seek_offset_ms = 0
            self._paused.set()
        if reader is not None:
            await reader.close()

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    # -- the pacing loop -----------------------------------------------
    async def _run(self) -> None:
        """Push one frame every 20 ms, forever, until closed.

        Silence is emitted while idle or paused so the published track stays
        healthy and listeners don't hear the stream drop out between songs.
        """
        import livekit.rtc as rtc

        deadline = time.monotonic()
        while not self._closed:
            frame_bytes = SILENCE_FRAME
            ended_reader: FFmpegReader | None = None
            end_error: Exception | None = None

            if self._paused.is_set():
                reader = self._reader
                generation = self._generation
                if reader is not None:
                    data = await reader.read()
                    if self._generation != generation:
                        # A newer source was installed while we were awaiting,
                        # so whatever this reader returned is stale: emit
                        # silence for this tick and pick the new reader up next.
                        pass
                    elif data is None:
                        # Track finished (or failed) — retire it.
                        async with self._reader_lock:
                            if self._generation == generation and self._reader is reader:
                                self._reader = None
                                ended_reader = reader
                                end_error = reader.error
                    else:
                        frame_bytes = apply_gain(data, self._volume)
                        self._frames_played += 1

            if self._source is not None:
                frame = rtc.AudioFrame(
                    data=frame_bytes,
                    sample_rate=SAMPLE_RATE,
                    num_channels=CHANNELS,
                    samples_per_channel=SAMPLES_PER_FRAME,
                )
                try:
                    await self._source.capture_frame(frame)
                except Exception as exc:
                    log.warning("failed to publish audio frame: %s", exc)

            if ended_reader is not None:
                await ended_reader.close()
                if self._on_track_end is not None:
                    # Fire-and-forget so a slow "next track" lookup never
                    # stalls the 20 ms cadence.
                    asyncio.create_task(self._safe_track_end(end_error))

            deadline += FRAME_MS / 1000
            delay = deadline - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                # We fell behind (GC pause, slow disk); resync rather than
                # sprinting to catch up and blowing out the LiveKit buffer.
                deadline = time.monotonic()

    async def _safe_track_end(self, error: Exception | None) -> None:
        assert self._on_track_end is not None
        try:
            await self._on_track_end(error)
        except Exception:
            log.exception("error in track-end handler")
