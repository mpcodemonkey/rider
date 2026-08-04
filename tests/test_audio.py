"""Audio pipeline tests, including a real ffmpeg round trip where available."""

from __future__ import annotations

import asyncio
import struct

import pytest

from jockiefluxer.audio import (
    BYTES_PER_FRAME,
    FRAME_MS,
    SAMPLE_RATE,
    FFmpegReader,
    apply_gain,
    build_filter_chain,
)

from .conftest import find_ffmpeg, requires_ffmpeg


def _samples(data: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(data) // 2}h", data))


def test_apply_gain_is_a_no_op_at_100_percent():
    data = struct.pack("<4h", 100, -100, 32767, -32768)
    assert apply_gain(data, 100) is data


def test_apply_gain_halves_and_silences():
    data = struct.pack("<4h", 1000, -1000, 200, -200)
    assert _samples(apply_gain(data, 50)) == [500, -500, 100, -100]
    assert _samples(apply_gain(data, 0)) == [0, 0, 0, 0]


def test_apply_gain_clamps_instead_of_wrapping():
    """Boosting must saturate, not overflow into the opposite sign."""
    data = struct.pack("<2h", 30000, -30000)
    result = _samples(apply_gain(data, 200))
    assert result == [32767, -32768]


def test_build_filter_chain_is_none_when_no_filters():
    assert build_filter_chain({}) is None


def test_build_filter_chain_composes_effects():
    chain = build_filter_chain({"bassboost": 10.0, "nightcore": 1.0})
    assert "bass=g=10.0" in chain
    assert "asetrate=60000" in chain  # 48000 * 1.25
    assert chain.count(",") >= 1


def test_speed_filter_splits_into_valid_atempo_stages():
    """atempo only accepts 0.5-2.0, so extreme values must be chained."""
    chain = build_filter_chain({"speed": 4.0})
    stages = [part for part in chain.split(",") if part.startswith("atempo=")]
    assert len(stages) == 2
    product = 1.0
    for stage in stages:
        product *= float(stage.split("=")[1])
    assert product == pytest.approx(4.0)

    slow = build_filter_chain({"speed": 0.25})
    product = 1.0
    for stage in slow.split(","):
        product *= float(stage.split("=")[1])
    assert product == pytest.approx(0.25)


def test_pitch_filter_keeps_duration_by_compensating_tempo():
    chain = build_filter_chain({"pitch": 1.5})
    assert f"asetrate={int(SAMPLE_RATE * 1.5)}" in chain
    tempo = float(chain.split("atempo=")[1])
    assert tempo == pytest.approx(1 / 1.5, abs=1e-3)


def test_unknown_filters_are_ignored():
    assert build_filter_chain({"not-a-filter": 1.0}) is None


# ---------------------------------------------------------------------------
# Real ffmpeg
# ---------------------------------------------------------------------------
@requires_ffmpeg
async def test_reader_decodes_a_tone_into_full_frames():
    """One second of a synthesised tone should yield ~50 frames of 20 ms."""
    reader = FFmpegReader(
        "sine=frequency=440:duration=1",
        ffmpeg_path=find_ffmpeg(),
        is_local=True,
        before_options="-f lavfi",
    )
    await reader.start()

    frames = []
    while True:
        frame = await asyncio.wait_for(reader.read(), timeout=30)
        if frame is None:
            break
        frames.append(frame)
    await reader.close()

    assert all(len(frame) == BYTES_PER_FRAME for frame in frames)
    # 1000 ms / 20 ms == 50 frames, allowing a frame either way for padding.
    assert 49 <= len(frames) <= 51
    # A 440 Hz tone is definitely not silence.
    assert any(any(_samples(frame)) for frame in frames)


@requires_ffmpeg
async def test_reader_seeks_past_the_start():
    """Seeking into the second half must return roughly half the audio."""
    reader = FFmpegReader(
        "sine=frequency=440:duration=2",
        ffmpeg_path=find_ffmpeg(),
        is_local=True,
        before_options="-f lavfi",
        start_ms=1000,
    )
    await reader.start()

    count = 0
    while True:
        frame = await asyncio.wait_for(reader.read(), timeout=30)
        if frame is None:
            break
        count += 1
    await reader.close()

    assert 45 <= count <= 55, f"expected ~50 frames after seeking, got {count}"


@requires_ffmpeg
async def test_reader_applies_a_filter_chain():
    """A volume=0 filter chain proves the -af argument actually reaches ffmpeg."""
    reader = FFmpegReader(
        "sine=frequency=440:duration=0.5",
        ffmpeg_path=find_ffmpeg(),
        is_local=True,
        before_options="-f lavfi",
        filter_chain="volume=0",
    )
    await reader.start()

    frames = []
    while True:
        frame = await asyncio.wait_for(reader.read(), timeout=30)
        if frame is None:
            break
        frames.append(frame)
    await reader.close()

    assert frames
    assert all(not any(_samples(frame)) for frame in frames)


@requires_ffmpeg
async def test_reader_close_unblocks_a_pending_read():
    """Closing mid-stream must not leave the streamer parked on read()."""
    reader = FFmpegReader(
        "sine=frequency=440:duration=600",
        ffmpeg_path=find_ffmpeg(),
        is_local=True,
        before_options="-f lavfi",
    )
    await reader.start()
    await asyncio.wait_for(reader.read(), timeout=30)

    await reader.close()
    assert await asyncio.wait_for(reader.read(), timeout=5) is None


@requires_ffmpeg
async def test_reader_reports_a_missing_source_without_hanging():
    reader = FFmpegReader(
        "/nonexistent/definitely-not-a-file.mp3",
        ffmpeg_path=find_ffmpeg(),
        is_local=True,
    )
    await reader.start()
    assert await asyncio.wait_for(reader.read(), timeout=30) is None
    assert reader.stderr_text()  # ffmpeg told us why
    await reader.close()


def test_frame_geometry_is_consistent():
    assert BYTES_PER_FRAME == SAMPLE_RATE * FRAME_MS // 1000 * 2 * 2
