"""Config loading, especially anything users configure via .env."""

from __future__ import annotations

import os

import pytest

from jockiefluxer.config import Config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip any jockiefluxer env vars so from_env() sees only what a test sets."""
    for key in list(os.environ):
        if key.startswith(("FLUXER_", "BOT_", "YTDLP_", "SPOTIFY_")) or key in (
            "DEFAULT_VOLUME", "MAX_VOLUME", "MAX_QUEUE_SIZE", "IDLE_TIMEOUT",
            "EMPTY_CHANNEL_TIMEOUT", "ANNOUNCE_NOW_PLAYING", "SEARCH_PROVIDER",
            "PLAYLIST_LIMIT", "ALLOW_LOCAL_FILES", "FFMPEG_PATH", "DATABASE_PATH",
            "LOG_LEVEL",
        ):
            monkeypatch.delenv(key, raising=False)


def test_defaults_favour_cookie_aware_youtube_clients():
    """'android' silently drops any configured cookiefile; it must not be first."""
    config = Config.from_env(dotenv=None)
    assert config.ytdlp_player_clients == ["web", "tv"]
    assert "android" not in config.ytdlp_player_clients[:1]


def test_player_clients_are_configurable_via_env(monkeypatch):
    monkeypatch.setenv("YTDLP_PLAYER_CLIENTS", "web,mweb,android")
    config = Config.from_env(dotenv=None)
    assert config.ytdlp_player_clients == ["web", "mweb", "android"]


def test_player_clients_env_strips_whitespace(monkeypatch):
    monkeypatch.setenv("YTDLP_PLAYER_CLIENTS", " web , tv ")
    config = Config.from_env(dotenv=None)
    assert config.ytdlp_player_clients == ["web", "tv"]


def test_cookiefile_is_unset_by_default():
    assert Config.from_env(dotenv=None).ytdlp_cookiefile is None


def test_cookiefile_is_read_from_env(monkeypatch):
    monkeypatch.setenv("YTDLP_COOKIEFILE", "/config/cookies.txt")
    assert Config.from_env(dotenv=None).ytdlp_cookiefile == "/config/cookies.txt"


def test_validate_requires_a_token():
    config = Config()
    with pytest.raises(SystemExit, match="FLUXER_TOKEN"):
        config.validate()


def test_cookiefile_env_and_volume_mount_are_independent(tmp_path, monkeypatch):
    """Regression: mounting cookies.txt into the container doesn't configure
    the bot to use it — YTDLP_COOKIEFILE has to be set separately. This is
    the most common reason cookies silently don't apply.
    """
    cookiefile = tmp_path / "cookies.txt"
    cookiefile.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\n")

    # File exists on disk, but the env var was never set.
    config = Config.from_env(dotenv=None)
    assert config.ytdlp_cookiefile is None

    monkeypatch.setenv("YTDLP_COOKIEFILE", str(cookiefile))
    config = Config.from_env(dotenv=None)
    assert config.ytdlp_cookiefile == str(cookiefile)
