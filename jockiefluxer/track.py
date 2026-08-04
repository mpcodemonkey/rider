"""The :class:`Track` model and the result object returned by source resolvers."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any

from .utils import escape_link_label, format_duration, truncate

# Stream URLs handed out by YouTube and friends are signed and expire.  Anything
# older than this is re-resolved before playback rather than handed to ffmpeg.
STREAM_URL_TTL = 3 * 60 * 60


class LoadType(enum.Enum):
    TRACK = "track"
    PLAYLIST = "playlist"
    SEARCH = "search"
    EMPTY = "empty"
    ERROR = "error"


@dataclass(slots=True)
class Track:
    """A single playable item.

    Tracks arriving from a playlist are *lazy*: they carry only the identifier
    needed to resolve a stream URL, and :meth:`needs_resolving` stays true until
    the player is about to play them.  That keeps ``m!play <200-song playlist>``
    fast instead of doing 200 network round trips up front.
    """

    title: str
    url: str | None = None
    stream_url: str | None = None
    duration: int | None = None  # milliseconds; None for live streams
    uploader: str | None = None
    thumbnail: str | None = None
    source: str = "unknown"
    is_live: bool = False

    requester_id: int | None = None
    requester_name: str | None = None

    # Query used to (re-)resolve this track when the stream URL is missing/stale.
    resolve_query: str | None = None
    resolved_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display_title(self) -> str:
        return truncate(self.title, 70)

    @property
    def duration_text(self) -> str:
        return "LIVE" if self.is_live else format_duration(self.duration)

    @property
    def markdown_link(self) -> str:
        if self.url:
            return f"[{escape_link_label(self.display_title)}]({self.url})"
        return self.display_title

    @property
    def needs_resolving(self) -> bool:
        if not self.stream_url:
            return True
        if self.resolved_at is None:
            return True
        return (time.time() - self.resolved_at) > STREAM_URL_TTL

    def mark_resolved(self, stream_url: str) -> None:
        self.stream_url = stream_url
        self.resolved_at = time.time()

    def invalidate(self) -> None:
        """Force the next playback attempt to re-resolve the stream URL."""
        self.stream_url = None
        self.resolved_at = None

    def copy_for(self, requester_id: int | None, requester_name: str | None) -> Track:
        """Duplicate this track for a new requester (used by playlists/replay)."""
        clone = Track(
            title=self.title,
            url=self.url,
            stream_url=self.stream_url,
            duration=self.duration,
            uploader=self.uploader,
            thumbnail=self.thumbnail,
            source=self.source,
            is_live=self.is_live,
            requester_id=requester_id,
            requester_name=requester_name,
            resolve_query=self.resolve_query,
            resolved_at=self.resolved_at,
            extra=dict(self.extra),
        )
        return clone

    def to_dict(self) -> dict[str, Any]:
        """Serialised form used for saved playlists (no volatile stream URL)."""
        return {
            "title": self.title,
            "url": self.url,
            "duration": self.duration,
            "uploader": self.uploader,
            "thumbnail": self.thumbnail,
            "source": self.source,
            "is_live": self.is_live,
            "resolve_query": self.resolve_query,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Track:
        return cls(
            title=data.get("title") or "Unknown track",
            url=data.get("url"),
            duration=data.get("duration"),
            uploader=data.get("uploader"),
            thumbnail=data.get("thumbnail"),
            source=data.get("source") or "unknown",
            is_live=bool(data.get("is_live")),
            resolve_query=data.get("resolve_query") or data.get("url"),
        )


@dataclass(slots=True)
class LoadResult:
    """What a source resolver hands back to the ``play`` command."""

    load_type: LoadType
    tracks: list[Track] = field(default_factory=list)
    playlist_name: str | None = None
    playlist_url: str | None = None
    error: str | None = None

    @classmethod
    def track(cls, track: Track) -> LoadResult:
        return cls(LoadType.TRACK, [track])

    @classmethod
    def playlist(
        cls, tracks: list[Track], name: str, url: str | None = None
    ) -> LoadResult:
        return cls(LoadType.PLAYLIST, tracks, playlist_name=name, playlist_url=url)

    @classmethod
    def search(cls, tracks: list[Track]) -> LoadResult:
        return cls(LoadType.SEARCH, tracks)

    @classmethod
    def empty(cls) -> LoadResult:
        return cls(LoadType.EMPTY)

    @classmethod
    def failed(cls, error: str) -> LoadResult:
        return cls(LoadType.ERROR, error=error)
