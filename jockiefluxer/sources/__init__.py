"""Source resolution: turns whatever the user typed into playable tracks."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..config import Config
from ..track import LoadResult, LoadType, Track
from ..utils import clean_url, is_url
from .spotify import SpotifySource, parse_spotify_url
from .ytdlp import YTDLPError, YTDLPSource

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".ogg", ".oga", ".opus", ".m4a",
    ".aac", ".wma", ".webm", ".mp4", ".mkv", ".aiff", ".alac",
}


class SourceManager:
    """Routes queries to the right backend and resolves stream URLs."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.ytdlp = YTDLPSource(config)
        self.spotify = SpotifySource(config)

    async def close(self) -> None:
        await self.spotify.close()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    async def load(self, query: str) -> LoadResult:
        """Resolve a user query (URL, search phrase or local path)."""
        query = query.strip()
        if not query:
            return LoadResult.empty()

        url = clean_url(query)
        if url:
            query = url

        spotify_target = parse_spotify_url(query)
        if spotify_target is not None:
            kind, spotify_id = spotify_target
            result = await self.spotify.load(kind, spotify_id)
            # Without credentials configured, degrade to a plain YouTube search
            # rather than failing outright.
            if result.load_type is LoadType.ERROR and not self.spotify.enabled:
                return result
            return result

        local = self._as_local_path(query)
        if local is not None:
            return self._load_local(local)

        return await self.ytdlp.load(query)

    async def search(self, query: str, limit: int = 10) -> list[Track]:
        return await self.ytdlp.search(query, limit=limit)

    async def load_attachment(self, url: str, filename: str) -> LoadResult:
        """Play a file uploaded straight into the chat."""
        title = Path(filename).stem or filename
        track = Track(
            title=title,
            url=url,
            duration=None,
            source="attachment",
            resolve_query=url,
        )
        track.mark_resolved(url)
        return LoadResult.track(track)

    # ------------------------------------------------------------------
    # Stream resolution
    # ------------------------------------------------------------------
    async def resolve_stream(self, track: Track) -> str:
        """Return a URL/path ffmpeg can read, resolving lazily if needed."""
        if not track.needs_resolving and track.stream_url:
            return track.stream_url

        if track.source in ("local", "attachment") and track.resolve_query:
            track.mark_resolved(track.resolve_query)
            return track.resolve_query

        return await self.ytdlp.resolve_stream(track)

    async def related(self, track: Track, exclude: set[str]) -> Track | None:
        """Suggest a follow-up track for autoplay."""
        return await self.ytdlp.related(track, exclude)

    # ------------------------------------------------------------------
    # Local files
    # ------------------------------------------------------------------
    def _as_local_path(self, query: str) -> Path | None:
        if is_url(query) or not self.config.allow_local_files:
            return None
        candidate = Path(os.path.expanduser(query))
        if candidate.suffix.lower() not in AUDIO_EXTENSIONS and not candidate.is_dir():
            return None
        try:
            if candidate.exists():
                return candidate
        except OSError:
            return None
        return None

    def _load_local(self, path: Path) -> LoadResult:
        if path.is_dir():
            files = sorted(
                child
                for child in path.iterdir()
                if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS
            )
            if not files:
                return LoadResult.empty()
            tracks = [self._local_track(child) for child in files]
            return LoadResult.playlist(tracks, name=path.name)

        return LoadResult.track(self._local_track(path))

    @staticmethod
    def _local_track(path: Path) -> Track:
        track = Track(
            title=path.stem,
            url=None,
            source="local",
            resolve_query=str(path),
        )
        track.mark_resolved(str(path))
        return track


__all__ = [
    "SourceManager",
    "SpotifySource",
    "YTDLPError",
    "YTDLPSource",
    "parse_spotify_url",
]
