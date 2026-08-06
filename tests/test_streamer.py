"""End-to-end streamer tests: real ffmpeg, real LiveKit AudioSource, fake room.

These run in real time (the streamer paces frames at 20 ms), so the clips are
deliberately short.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from jockiefluxer.audio import FRAME_MS, AudioStreamer

from .conftest import find_ffmpeg, requires_ffmpeg

pytestmark = requires_ffmpeg


@pytest.fixture(scope="module")
def tones(tmp_path_factory) -> dict[float, str]:
    """Real WAV files, so the streamer is exercised exactly as it runs live."""
    directory = tmp_path_factory.mktemp("tones")
    paths: dict[float, str] = {}
    for seconds in (0.4, 1.0, 10.0):
        path = directory / f"tone-{seconds}.wav"
        subprocess.run(
            [
                find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                "-ar", "48000", "-ac", "2", str(path),
            ],
            check=True,
        )
        paths[seconds] = str(path)
    return paths


class FakeVoiceClient:
    """Stands in for fluxer.py's VoiceClient; records what gets published."""

    def __init__(self):
        self.published: list = []
        self.disconnected = False

    async def play(self, source, *, after=None):
        self.published.append(source)

    async def disconnect(self):
        self.disconnected = True


async def make_streamer(ended: asyncio.Event | None = None, pacing_window_frames: int | None = None):
    voice = FakeVoiceClient()

    async def on_end(error):
        if ended is not None:
            ended.set()

    kwargs = {}
    if pacing_window_frames is not None:
        kwargs["pacing_window_frames"] = pacing_window_frames
    streamer = AudioStreamer(
        voice, ffmpeg_path=find_ffmpeg(), on_track_end=on_end, **kwargs
    )
    await streamer.start()
    return streamer, voice


async def test_streamer_publishes_exactly_one_track():
    streamer, voice = await make_streamer()
    try:
        assert len(voice.published) == 1
        assert streamer.is_playing is False  # nothing loaded yet
    finally:
        await streamer.close()


async def test_streamer_plays_a_clip_and_reports_its_position(tones):
    ended = asyncio.Event()
    streamer, _ = await make_streamer(ended)
    try:
        await streamer.play(tones[0.4], is_local=True)
        assert streamer.is_playing

        await asyncio.wait_for(ended.wait(), timeout=15)
        # 400 ms of audio at 20 ms per frame, give or take a frame.
        assert 360 <= streamer.position_ms <= 440, streamer.position_ms
        assert streamer.is_playing is False
    finally:
        await streamer.close()


async def test_streamer_position_reflects_a_seek_offset(tones):
    ended = asyncio.Event()
    streamer, _ = await make_streamer(ended)
    try:
        await streamer.play(tones[10.0], is_local=True, start_ms=1500)
        await asyncio.sleep(0.2)
        # Position counts from the seek point, not from zero.
        assert streamer.position_ms >= 1500
    finally:
        await streamer.close()


async def test_swapping_tracks_reuses_the_published_track(tones):
    """Track changes must not republish — that's what keeps transitions gapless."""
    streamer, voice = await make_streamer()
    try:
        await streamer.play(tones[10.0], is_local=True)
        await asyncio.sleep(0.1)
        await streamer.play(tones[10.0], is_local=True)
        await asyncio.sleep(0.1)

        assert len(voice.published) == 1
        assert streamer.is_playing
        # The counter restarts for the new source.
        assert streamer.position_ms < 1000
    finally:
        await streamer.close()


async def test_swapping_does_not_fire_track_end_for_the_replaced_source(tones):
    ended = asyncio.Event()
    streamer, _ = await make_streamer(ended)
    try:
        await streamer.play(tones[10.0], is_local=True)
        await asyncio.sleep(0.1)
        await streamer.play(tones[10.0], is_local=True)
        await asyncio.sleep(0.3)

        assert not ended.is_set(), "retiring a replaced source must not end the track"
    finally:
        await streamer.close()


async def test_pause_freezes_the_position_and_resume_continues(tones):
    streamer, _ = await make_streamer()
    try:
        await streamer.play(tones[10.0], is_local=True)
        await asyncio.sleep(0.2)

        streamer.pause()
        assert streamer.is_paused
        paused_at = streamer.position_ms
        await asyncio.sleep(0.3)
        assert streamer.position_ms == paused_at, "position advanced while paused"

        streamer.resume()
        await asyncio.sleep(0.2)
        assert streamer.position_ms > paused_at
    finally:
        await streamer.close()


async def test_stop_clears_playback_without_ending_the_session(tones):
    streamer, voice = await make_streamer()
    try:
        await streamer.play(tones[10.0], is_local=True)
        await asyncio.sleep(0.1)

        await streamer.stop()
        assert streamer.is_playing is False
        assert streamer.position_ms == 0

        # The session survives, so the next track can start immediately.
        await streamer.play(tones[1.0], is_local=True)
        assert streamer.is_playing
        assert len(voice.published) == 1
    finally:
        await streamer.close()


async def test_a_failing_source_still_ends_the_track():
    """A bad URL must hand control back rather than wedging the queue."""
    ended = asyncio.Event()
    streamer, _ = await make_streamer(ended)
    try:
        await streamer.play("/nonexistent/never-existed.mp3", is_local=True)
        await asyncio.wait_for(ended.wait(), timeout=15)
        assert streamer.is_playing is False
    finally:
        await streamer.close()


async def test_close_is_idempotent(tones):
    streamer, _ = await make_streamer()
    await streamer.play(tones[10.0], is_local=True)
    await streamer.close()
    await streamer.close()
    assert streamer.is_playing is False


async def test_frame_pacing_is_close_to_real_time(tones):
    """The loop must not sprint; playback speed depends on accurate pacing."""
    ended = asyncio.Event()
    streamer, _ = await make_streamer(ended)
    try:
        started = asyncio.get_running_loop().time()
        await streamer.play(tones[1.0], is_local=True)
        await asyncio.wait_for(ended.wait(), timeout=15)
        elapsed = asyncio.get_running_loop().time() - started

        # One second of audio should take about one second to stream.
        assert 0.85 <= elapsed <= 1.5, f"streamed 1s of audio in {elapsed:.2f}s"
        assert FRAME_MS == 20
    finally:
        await streamer.close()


async def test_swapping_sources_returns_promptly(tones):
    """Regression: tearing down the old ffmpeg used to block play() for ~5s.

    Awaiting Process.wait() after killing a process whose pipe reader was
    cancelled mid-read deadlocks, which stalled every single track change.
    """
    streamer, _ = await make_streamer()
    try:
        await streamer.play(tones[10.0], is_local=True)
        await asyncio.sleep(0.2)  # let ffmpeg fill and block on the pipe

        started = asyncio.get_running_loop().time()
        await streamer.play(tones[10.0], is_local=True)
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 1.0, f"swapping sources took {elapsed:.2f}s"
        # The new source starts from the top, not 5 seconds in.
        assert streamer.position_ms < 500
    finally:
        await streamer.close()


async def test_close_returns_promptly_mid_playback(tones):
    streamer, _ = await make_streamer()
    await streamer.play(tones[10.0], is_local=True)
    await asyncio.sleep(0.2)

    started = asyncio.get_running_loop().time()
    await streamer.close()
    assert asyncio.get_running_loop().time() - started < 1.0


# ---------------------------------------------------------------------------
# Pacing-lateness detection (diagnoses stuttering caused by host CPU
# contention, distinguishing it from a LiveKit/transport-level issue)
# ---------------------------------------------------------------------------
async def test_pacing_lateness_is_detected_and_logged(tones, caplog):
    """A deliberately stalled event loop (simulating CPU contention on the
    host) must be caught and reported, not silently absorbed."""
    import livekit.rtc as rtc

    real_capture = rtc.AudioSource.capture_frame
    calls = {"n": 0}

    async def slow_capture_frame(self, frame):
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            await asyncio.sleep(0.04)  # twice the 20ms frame budget
        return await real_capture(self, frame)

    rtc.AudioSource.capture_frame = slow_capture_frame
    try:
        streamer, _ = await make_streamer(pacing_window_frames=20)
        try:
            with caplog.at_level("WARNING", logger="jockiefluxer.audio"):
                await streamer.play(tones[10.0], is_local=True)
                await asyncio.sleep(1.0)
        finally:
            await streamer.close()
    finally:
        rtc.AudioSource.capture_frame = real_capture

    messages = [r.message for r in caplog.records]
    assert any("fell behind schedule" in m for m in messages)
    assert any("stuttering" in m for m in messages)


async def test_pacing_lateness_does_not_false_positive_under_normal_playback(tones, caplog):
    streamer, _ = await make_streamer(pacing_window_frames=20)
    try:
        with caplog.at_level("WARNING", logger="jockiefluxer.audio"):
            await streamer.play(tones[10.0], is_local=True)
            await asyncio.sleep(1.0)
    finally:
        await streamer.close()

    messages = [r.message for r in caplog.records]
    assert not any("fell behind schedule" in m for m in messages)
