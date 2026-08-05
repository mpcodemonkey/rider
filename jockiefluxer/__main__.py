"""Entry point: ``python -m jockiefluxer``."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys

from . import __version__
from .bot import MusicBot
from .config import Config

log = logging.getLogger("jockiefluxer")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # These are chatty at DEBUG and rarely what you're looking for.
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("livekit").setLevel(logging.WARNING)


def _check_dependencies(config: Config) -> None:
    if shutil.which(config.ffmpeg_path) is None:
        raise SystemExit(
            f"ffmpeg not found (looked for '{config.ffmpeg_path}').\n"
            "Install it with your package manager, or set FFMPEG_PATH."
        )
    try:
        import livekit.rtc  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Voice support is missing. Install it with:\n"
            "    pip install 'fluxer.py[voice]'"
        ) from exc
    try:
        import yt_dlp  # noqa: F401
    except ImportError as exc:
        raise SystemExit("yt-dlp is missing. Install it with:\n    pip install yt-dlp") from exc


def _check_cookiefile(config: Config) -> None:
    """Log whether YouTube cookies are actually wired in.

    Mounting cookies.txt into the container and pointing YTDLP_COOKIEFILE at
    it are two separate steps — this makes it obvious in the startup log
    when only one of them happened, instead of that surfacing later as an
    unexplained "Sign in to confirm you're not a bot".
    """
    if not config.ytdlp_cookiefile:
        log.info(
            "YTDLP_COOKIEFILE is not set — YouTube requests are unauthenticated. "
            "If you start seeing 'Sign in to confirm you're not a bot', this is "
            "the first thing to fix."
        )
        return

    from .sources.ytdlp import diagnose_cookiefile

    warnings = diagnose_cookiefile(config.ytdlp_cookiefile)
    if warnings:
        for warning in warnings:
            log.warning(warning)
    else:
        log.info("YTDLP_COOKIEFILE found and looks valid: %s", config.ytdlp_cookiefile)


def _check_player_clients(config: Config) -> None:
    from .sources.ytdlp import validate_player_clients

    for warning in validate_player_clients(config.ytdlp_player_clients):
        log.warning(warning)
    log.info("YouTube player clients (in order): %s", ", ".join(config.ytdlp_player_clients))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jockiefluxer",
        description="A drop-in replacement for Jockie Music, for self-hosted Fluxer.",
    )
    parser.add_argument("--env-file", default=".env", help="path to the .env file (default: .env)")
    parser.add_argument("--prefix", help="override the command prefix")
    parser.add_argument("--api-url", help="override the Fluxer API base URL")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING or ERROR")
    parser.add_argument("--version", action="version", version=f"jockiefluxer {__version__}")
    args = parser.parse_args(argv)

    config = Config.from_env(args.env_file)
    if args.prefix:
        config.prefix = args.prefix
    if args.api_url:
        config.api_url = args.api_url
    if args.log_level:
        config.log_level = args.log_level

    _configure_logging(config.log_level)
    config.validate()
    _check_dependencies(config)

    log.info("Starting jockiefluxer %s", __version__)
    log.info("API: %s", config.api_url or "https://api.fluxer.app/v1 (default)")
    _check_cookiefile(config)
    _check_player_clients(config)

    bot = MusicBot(config)
    try:
        bot.run(config.token)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
