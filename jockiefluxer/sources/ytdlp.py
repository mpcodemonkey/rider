"""yt-dlp backed source.

Handles YouTube, SoundCloud, Bandcamp, Vimeo, Twitch, direct audio links and
anything else yt-dlp knows about, plus text search and YouTube-mix based
autoplay.

All yt-dlp work happens on a worker thread — the library is entirely
synchronous and a cold YouTube extraction can take seconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..config import Config
from ..track import LoadResult, Track
from ..utils import is_url

log = logging.getLogger(__name__)

# Extractors whose "duration" is meaningless / infinite.
_LIVE_KEYS = ("is_live", "live_status")


class YTDLPError(RuntimeError):
    """Raised when yt-dlp could not produce anything playable."""


def diagnose_cookiefile(path: str) -> list[str]:
    """Sanity-check a configured YTDLP_COOKIEFILE, without validating yt-dlp
    would actually accept it (yt-dlp does that itself when it loads).

    This exists because a misconfigured cookiefile fails *silently*: yt-dlp
    just runs unauthenticated and YouTube's response looks identical to any
    other "Sign in to confirm you're not a bot" block. Mounting the file into
    the container and pointing YTDLP_COOKIEFILE at it are two separate steps,
    and it's easy to do one without the other — this turns that mistake into
    a log line instead of a mystery.
    """
    warnings: list[str] = []
    file_path = Path(path)

    if not file_path.is_file():
        warnings.append(
            f"YTDLP_COOKIEFILE is set to '{path}' but no file exists there. "
            "If you're running in Docker, check the volume mount in "
            "docker-compose.yml is uncommented and points at the same path."
        )
        return warnings

    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warnings.append(f"YTDLP_COOKIEFILE at '{path}' could not be read: {exc}")
        return warnings

    stripped = text.strip()
    if not stripped:
        warnings.append(f"YTDLP_COOKIEFILE at '{path}' is empty.")
        return warnings

    if stripped.startswith("{") or stripped.startswith("["):
        warnings.append(
            f"YTDLP_COOKIEFILE at '{path}' looks like JSON, not the Netscape format "
            "yt-dlp expects. Re-export with a tool that produces a Netscape "
            "cookies.txt, e.g. the 'Get cookies.txt LOCALLY' browser extension."
        )
        return warnings

    data_lines = [
        line for line in text.splitlines() if line.strip() and not line.startswith("#")
    ]
    if not data_lines:
        warnings.append(
            f"YTDLP_COOKIEFILE at '{path}' has no cookie entries — only "
            "comments or blank lines. Re-export it."
        )
    elif not any("youtube.com" in line for line in data_lines):
        warnings.append(
            f"YTDLP_COOKIEFILE at '{path}' has no youtube.com cookies. Make sure "
            "you were logged into youtube.com (not just google.com) when you "
            "exported it."
        )

    if not os.access(file_path, os.W_OK):
        warnings.append(
            f"YTDLP_COOKIEFILE at '{path}' is not writable. yt-dlp writes "
            "renewed session cookies back to this file after every request — "
            "that's what keeps cookies working for weeks without manual "
            "re-export — but it can't do that here. If this is a Docker "
            "bind mount, drop any ':ro' suffix in docker-compose.yml and "
            "make sure the container's entrypoint had a chance to fix "
            "ownership (restart the container if you just added the mount)."
        )

    return warnings


def validate_player_clients(clients: list[str]) -> list[str]:
    """Catch a typo'd YTDLP_PLAYER_CLIENTS entry before it wastes a debugging
    session — an unknown client name is silently dropped by yt-dlp rather
    than rejected, which just quietly reduces the fallback chain.
    """
    try:
        from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS
    except ImportError:
        return []  # yt-dlp not installed; _check_dependencies() already covers this

    # Names starting with "_" are internal variants users can't select.
    known = {name for name in INNERTUBE_CLIENTS if not name.startswith("_")}
    unknown = [name for name in clients if name not in known]
    if not unknown:
        return []
    return [
        f"YTDLP_PLAYER_CLIENTS has an unrecognised client '{name}' — it will be "
        f"silently ignored by yt-dlp. Known clients: {', '.join(sorted(known))}."
        for name in unknown
    ]


def check_pot_provider(base_url: str, timeout: float = 3.0) -> str | None:
    """Ping a configured bgutil-ytdlp-pot-provider server.

    Returns ``None`` if it responded and looks healthy, or a human-readable
    problem description otherwise. This hits the same ``/ping`` endpoint the
    yt-dlp plugin itself checks before every PO token request, so "reachable
    here" really does mean "yt-dlp will be able to reach it too" — same
    network path, same URL.
    """
    url = f"{base_url.rstrip('/')}/ping"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.URLError as exc:
        return (
            f"Could not reach the PO token provider at '{base_url}': {exc.reason}. "
            "Check the server is running and the URL/port match your "
            "docker-compose service name."
        )
    except (TimeoutError, OSError) as exc:
        return f"Could not reach the PO token provider at '{base_url}': {exc}"
    except (json.JSONDecodeError, ValueError):
        return (
            f"The PO token provider at '{base_url}' responded, but not with "
            "the expected JSON — is this really a bgutil-ytdlp-pot-provider server?"
        )

    version = payload.get("version", "unknown")
    return None if version else f"PO token provider at '{base_url}' returned no version."


class _ErrorCapture:
    """A yt-dlp logger that keeps the last error instead of printing it.

    ``ignoreerrors`` is on so that one dead video doesn't sink a 200-track
    playlist, but that also makes ``extract_info`` return ``None`` on hard
    failures.  Capturing the message lets the bot say *why* rather than
    reporting a bare "nothing found".
    """

    def __init__(self) -> None:
        self.last_error: str | None = None

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        log.debug("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        self.last_error = str(message).replace("ERROR: ", "").strip()
        log.debug("yt-dlp error: %s", self.last_error)


class YTDLPSource:
    """Thin async wrapper around ``yt_dlp.YoutubeDL``."""

    name = "ytdlp"

    def __init__(self, config: Config) -> None:
        self.config = config
        self._flat_opts = self._build_opts(flat=True)
        self._full_opts = self._build_opts(flat=False)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _build_opts(self, *, flat: bool) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "format": self.config.ytdlp_format,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "ignoreerrors": True,
            "no_color": True,
            "cachedir": False,
            "retries": 3,
            "socket_timeout": 20,
            "geo_bypass": True,
            "default_search": self.config.search_provider,
            # Playlists are flattened so a 500-track playlist costs one request.
            "extract_flat": "in_playlist" if flat else False,
            "playlistend": self.config.playlist_limit,
            # See Config.ytdlp_player_clients: 'tv' leads because it's the
            # only client that both honors cookies and needs no PO token,
            # which this bot doesn't provide. Getting this order wrong
            # produces two different failures depending on which property
            # is missing: no cookies -> "Sign in to confirm you're not a
            # bot"; no PO token -> "Requested format is not available".
            "extractor_args": {
                "youtube": {"player_client": self.config.ytdlp_player_clients}
            },
        }
        if self.config.ytdlp_pot_provider_url:
            # Read by bgutil-ytdlp-pot-provider's HTTP plugin (self-registers
            # with yt-dlp once the pip package is installed - no other wiring
            # needed here). Value must be a list per yt-dlp's extractor-args
            # convention.
            opts["extractor_args"]["youtubepot-bgutilhttp"] = {
                "base_url": [self.config.ytdlp_pot_provider_url]
            }
        if self.config.ytdlp_cookiefile:
            opts["cookiefile"] = self.config.ytdlp_cookiefile
        if self.config.ytdlp_proxy:
            opts["proxy"] = self.config.ytdlp_proxy
        return opts

    def _extract_sync(
        self, query: str, *, flat: bool
    ) -> tuple[dict[str, Any] | None, str | None]:
        from yt_dlp import YoutubeDL  # imported lazily so --help works without it

        capture = _ErrorCapture()
        opts = {**(self._flat_opts if flat else self._full_opts), "logger": capture}
        ydl = YoutubeDL(opts)
        try:
            info = ydl.extract_info(query, download=False)
        finally:
            # YoutubeDL.close() persists any renewed session cookies back to
            # YTDLP_COOKIEFILE — this is what keeps cookies fresh without
            # manual re-export. If the file isn't writable (bad mount,
            # ownership mismatch, full disk) that write raises OSError; a
            # `with YoutubeDL(...)` block would let that replace a perfectly
            # good `info` result with a confusing filesystem error, so this
            # is deliberately outside the try body and caught on its own.
            try:
                ydl.close()
            except OSError as exc:
                log.warning(
                    "Could not save updated cookies to %r (%s). YouTube "
                    "cookies won't auto-refresh until this is fixed — the "
                    "usual cause is a read-only mount or ownership mismatch.",
                    self.config.ytdlp_cookiefile, exc,
                )
        return info, capture.last_error

    async def _extract(
        self, query: str, *, flat: bool
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Extract metadata. Returns ``(info, error_message)``."""
        return await asyncio.to_thread(self._extract_sync, query, flat=flat)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _is_live(info: dict[str, Any]) -> bool:
        for key in _LIVE_KEYS:
            value = info.get(key)
            if value is True or value == "is_live":
                return True
        return False

    @staticmethod
    def _thumbnail(info: dict[str, Any]) -> str | None:
        if info.get("thumbnail"):
            return info["thumbnail"]
        thumbnails = info.get("thumbnails") or []
        if thumbnails:
            return thumbnails[-1].get("url")
        return None

    @staticmethod
    def _webpage_url(info: dict[str, Any]) -> str | None:
        url = info.get("webpage_url") or info.get("original_url")
        if url:
            return url
        # Flat playlist entries only carry an id plus an ie_key.
        video_id = info.get("id")
        if video_id and info.get("ie_key") in ("Youtube", "YoutubeTab"):
            return f"https://www.youtube.com/watch?v={video_id}"
        return info.get("url")

    def _make_track(self, info: dict[str, Any], *, flat: bool) -> Track | None:
        if not info:
            return None
        # yt-dlp reports durations in (possibly fractional) seconds.
        raw_duration = info.get("duration")
        duration = int(float(raw_duration) * 1000) if raw_duration else None
        webpage_url = self._webpage_url(info)
        is_live = self._is_live(info)

        track = Track(
            title=info.get("title") or info.get("id") or "Unknown track",
            url=webpage_url,
            duration=None if is_live else duration,
            uploader=info.get("uploader") or info.get("channel") or info.get("artist"),
            thumbnail=self._thumbnail(info),
            source=(info.get("extractor_key") or info.get("ie_key") or "ytdlp").lower(),
            is_live=is_live,
            resolve_query=webpage_url or info.get("id"),
        )
        if not flat:
            stream_url = self._pick_stream_url(info)
            if stream_url:
                track.mark_resolved(stream_url)
        if not track.resolve_query:
            return None
        return track

    @staticmethod
    def _pick_stream_url(info: dict[str, Any]) -> str | None:
        """Pull a directly playable URL out of a full extraction result."""
        if info.get("url"):
            return info["url"]
        requested = info.get("requested_formats") or []
        for fmt in requested:
            if fmt.get("acodec") not in (None, "none") and fmt.get("url"):
                return fmt["url"]
        # Fall back to the best audio-bearing format yt-dlp knows about.
        formats = info.get("formats") or []
        audio_only = [
            fmt
            for fmt in formats
            if fmt.get("acodec") not in (None, "none")
            and fmt.get("vcodec") in (None, "none")
            and fmt.get("url")
        ]
        pool = audio_only or [
            fmt
            for fmt in formats
            if fmt.get("acodec") not in (None, "none") and fmt.get("url")
        ]
        if not pool:
            return None
        return max(pool, key=lambda fmt: fmt.get("abr") or fmt.get("tbr") or 0)["url"]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def load(self, query: str) -> LoadResult:
        """Resolve a URL or a search phrase into a :class:`LoadResult`."""
        target = query if is_url(query) else f"{self.config.search_provider}1:{query}"
        try:
            info, error = await self._extract(target, flat=True)
        except Exception as exc:  # yt-dlp raises a wide variety of errors
            log.warning("yt-dlp failed to load %r: %s", query, exc)
            return LoadResult.failed(str(exc).replace("ERROR: ", ""))

        if not info:
            # Distinguish "the site said no" from "there were no results".
            return LoadResult.failed(error) if error else LoadResult.empty()

        entries = info.get("entries")
        if entries is None:
            track = self._make_track(info, flat=False)
            return LoadResult.track(track) if track else LoadResult.empty()

        tracks = [
            track
            for track in (self._make_track(entry, flat=True) for entry in entries if entry)
            if track is not None
        ]
        if not tracks:
            return LoadResult.empty()

        # A search returns a synthetic playlist; treat single hits as a track.
        if not is_url(query):
            return LoadResult.track(tracks[0])
        if len(tracks) == 1 and not info.get("title"):
            return LoadResult.track(tracks[0])

        return LoadResult.playlist(
            tracks,
            name=info.get("title") or "Playlist",
            url=info.get("webpage_url") or (query if is_url(query) else None),
        )

    async def search(self, query: str, limit: int = 10) -> list[Track]:
        """Return up to ``limit`` search candidates for the ``search`` command."""
        target = f"{self.config.search_provider}{limit}:{query}"
        try:
            info, _ = await self._extract(target, flat=True)
        except Exception as exc:
            log.warning("yt-dlp search failed for %r: %s", query, exc)
            return []
        if not info:
            return []
        entries = info.get("entries") or []
        return [
            track
            for track in (self._make_track(entry, flat=True) for entry in entries if entry)
            if track is not None
        ]

    async def resolve_stream(self, track: Track) -> str:
        """Fill in (or refresh) a track's direct stream URL."""
        query = track.resolve_query or track.url
        if not query:
            raise YTDLPError(f"No way to resolve '{track.title}'.")

        target = query if is_url(query) else f"{self.config.search_provider}1:{query}"
        info, error = await self._extract(target, flat=False)
        if info and info.get("entries"):
            entries = [entry for entry in info["entries"] if entry]
            info = entries[0] if entries else None
        if not info:
            raise YTDLPError(error or f"Could not resolve '{track.title}'.")

        stream_url = self._pick_stream_url(info)
        if not stream_url:
            raise YTDLPError(f"No playable audio stream for '{track.title}'.")

        # Backfill metadata that a flat playlist entry never carried.
        if not track.duration and info.get("duration"):
            track.duration = int(float(info["duration"]) * 1000)
        if not track.thumbnail:
            track.thumbnail = self._thumbnail(info)
        if not track.uploader:
            track.uploader = info.get("uploader") or info.get("channel")
        if not track.url:
            track.url = self._webpage_url(info)
        track.is_live = track.is_live or self._is_live(info)

        track.mark_resolved(stream_url)
        return stream_url

    async def related(self, track: Track, exclude: set[str]) -> Track | None:
        """Pick a follow-up track for autoplay using YouTube's mix radio."""
        video_id = self._youtube_id(track)
        if not video_id:
            return None

        radio = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
        try:
            info, _ = await self._extract(radio, flat=True)
        except Exception as exc:
            log.debug("autoplay lookup failed: %s", exc)
            return None
        if not info:
            return None

        for entry in info.get("entries") or []:
            if not entry:
                continue
            candidate = self._make_track(entry, flat=True)
            if candidate is None or candidate.url in exclude:
                continue
            if self._youtube_id(candidate) == video_id:
                continue
            return candidate
        return None

    @staticmethod
    def _youtube_id(track: Track) -> str | None:
        url = track.url or track.resolve_query or ""
        if "youtube.com/watch" in url and "v=" in url:
            return url.split("v=")[1].split("&")[0]
        if "youtu.be/" in url:
            return url.split("youtu.be/")[1].split("?")[0]
        return None
