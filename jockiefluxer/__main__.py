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

    bot = MusicBot(config)
    try:
        bot.run(config.token)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
