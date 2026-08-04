"""Configuration loading.

Settings come from environment variables (optionally seeded from a ``.env``
file sitting next to the project).  Every value has a sane default so that a
fresh install only really needs ``FLUXER_TOKEN``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_TRUTHY = {"1", "true", "yes", "y", "on"}


def _load_dotenv(path: Path) -> None:
    """Populate ``os.environ`` from a .env file without clobbering real env vars."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return value if value not in (None, "") else default


def _env_opt(key: str) -> str | None:
    value = os.environ.get(key)
    return value if value not in (None, "") else None


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in _TRUTHY


def _env_list(key: str, default: list[str]) -> list[str]:
    raw = os.environ.get(key)
    if raw in (None, ""):
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(slots=True)
class Config:
    """Runtime configuration for a single bot instance."""

    # -- Connection --
    token: str = ""
    api_url: str | None = None

    # -- Commands --
    prefix: str = "m!"
    extra_prefixes: list[str] = field(default_factory=list)
    respond_to_mention: bool = True

    # -- Playback defaults --
    default_volume: int = 100
    max_volume: int = 200
    max_queue_size: int = 5000
    # Seconds of silence before the bot leaves; 0 disables the timer.
    idle_timeout: int = 300
    empty_channel_timeout: int = 60
    # Announce "Now playing" in the channel the track was requested from.
    announce_now_playing: bool = True

    # -- Sources --
    search_provider: str = "ytsearch"
    ytdlp_format: str = "bestaudio/best"
    ytdlp_cookiefile: str | None = None
    ytdlp_proxy: str | None = None
    # Number of playlist entries pulled per request; guards against 10k-entry mixes.
    playlist_limit: int = 500
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    allow_local_files: bool = False

    # -- Infrastructure --
    ffmpeg_path: str = "ffmpeg"
    database_path: str = "data/jockiefluxer.db"
    log_level: str = "INFO"

    @property
    def prefixes(self) -> list[str]:
        """All literal prefixes this instance answers to, longest first."""
        seen: list[str] = []
        for candidate in [self.prefix, *self.extra_prefixes]:
            if candidate and candidate not in seen:
                seen.append(candidate)
        return sorted(seen, key=len, reverse=True)

    @classmethod
    def from_env(cls, dotenv: str | os.PathLike[str] | None = ".env") -> Config:
        if dotenv is not None:
            _load_dotenv(Path(dotenv))

        prefix = _env_str("BOT_PREFIX", "m!")

        return cls(
            token=_env_str("FLUXER_TOKEN", ""),
            api_url=_env_opt("FLUXER_API_URL"),
            prefix=prefix,
            extra_prefixes=_env_list("BOT_EXTRA_PREFIXES", []),
            respond_to_mention=_env_bool("BOT_RESPOND_TO_MENTION", True),
            default_volume=_env_int("DEFAULT_VOLUME", 100),
            max_volume=_env_int("MAX_VOLUME", 200),
            max_queue_size=_env_int("MAX_QUEUE_SIZE", 5000),
            idle_timeout=_env_int("IDLE_TIMEOUT", 300),
            empty_channel_timeout=_env_int("EMPTY_CHANNEL_TIMEOUT", 60),
            announce_now_playing=_env_bool("ANNOUNCE_NOW_PLAYING", True),
            search_provider=_env_str("SEARCH_PROVIDER", "ytsearch"),
            ytdlp_format=_env_str("YTDLP_FORMAT", "bestaudio/best"),
            ytdlp_cookiefile=_env_opt("YTDLP_COOKIEFILE"),
            ytdlp_proxy=_env_opt("YTDLP_PROXY"),
            playlist_limit=_env_int("PLAYLIST_LIMIT", 500),
            spotify_client_id=_env_opt("SPOTIFY_CLIENT_ID"),
            spotify_client_secret=_env_opt("SPOTIFY_CLIENT_SECRET"),
            allow_local_files=_env_bool("ALLOW_LOCAL_FILES", False),
            ffmpeg_path=_env_str("FFMPEG_PATH", "ffmpeg"),
            database_path=_env_str("DATABASE_PATH", "data/jockiefluxer.db"),
            log_level=_env_str("LOG_LEVEL", "INFO"),
        )

    def validate(self) -> None:
        if not self.token:
            raise SystemExit(
                "FLUXER_TOKEN is not set. Copy .env.example to .env and add your bot token."
            )
        if not 0 < self.max_volume <= 1000:
            raise SystemExit("MAX_VOLUME must be between 1 and 1000.")
        if not 0 <= self.default_volume <= self.max_volume:
            raise SystemExit("DEFAULT_VOLUME must be between 0 and MAX_VOLUME.")
