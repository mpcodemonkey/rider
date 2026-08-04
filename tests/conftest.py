from __future__ import annotations

import os
import shutil

import pytest

from jockiefluxer.config import Config


def find_ffmpeg() -> str | None:
    """Locate ffmpeg, honouring FFMPEG_PATH so CI can point at a static build."""
    candidate = os.environ.get("FFMPEG_PATH")
    if candidate and (shutil.which(candidate) or os.path.isfile(candidate)):
        return candidate
    return shutil.which("ffmpeg")


requires_ffmpeg = pytest.mark.skipif(
    find_ffmpeg() is None, reason="ffmpeg is not installed"
)


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        token="test-token",
        database_path=str(tmp_path / "test.db"),
        ffmpeg_path=find_ffmpeg() or "ffmpeg",
        default_volume=100,
        max_queue_size=100,
    )
