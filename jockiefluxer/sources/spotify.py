"""Spotify link support.

Spotify does not hand out playable audio, so — exactly like Jockie — we read the
*metadata* through the Web API and then play the matching track from YouTube.

Without ``SPOTIFY_CLIENT_ID`` / ``SPOTIFY_CLIENT_SECRET`` configured this source
stays dormant and Spotify links fall through to a plain text search.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import aiohttp

from ..config import Config
from ..track import LoadResult, Track

log = logging.getLogger(__name__)

SPOTIFY_URL_RE = re.compile(
    r"(?:https?://)?open\.spotify\.com/(?:intl-[a-z]{2}/)?"
    r"(?P<kind>track|album|playlist|artist)/(?P<id>[A-Za-z0-9]+)",
    re.IGNORECASE,
)
SPOTIFY_URI_RE = re.compile(
    r"^spotify:(?P<kind>track|album|playlist|artist):(?P<id>[A-Za-z0-9]+)$",
    re.IGNORECASE,
)

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
PAGE_SIZE = 100


def parse_spotify_url(value: str) -> tuple[str, str] | None:
    """Return ``(kind, id)`` for a Spotify link/URI, or ``None``."""
    match = SPOTIFY_URL_RE.search(value) or SPOTIFY_URI_RE.match(value.strip())
    if not match:
        return None
    return match.group("kind").lower(), match.group("id")


class SpotifySource:
    """Resolves Spotify links into searchable track metadata."""

    name = "spotify"

    def __init__(self, config: Config) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.config.spotify_client_id and self.config.spotify_client_secret)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
        return self._session

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token

            session = await self._ensure_session()
            auth = aiohttp.BasicAuth(
                self.config.spotify_client_id or "",
                self.config.spotify_client_secret or "",
            )
            async with session.post(
                TOKEN_URL, data={"grant_type": "client_credentials"}, auth=auth
            ) as response:
                response.raise_for_status()
                payload = await response.json()

            self._token = payload["access_token"]
            # Refresh a minute early so an in-flight request never 401s.
            self._token_expires_at = time.time() + payload.get("expires_in", 3600) - 60
            return self._token

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        session = await self._ensure_session()
        token = await self._access_token()
        async with session.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        ) as response:
            response.raise_for_status()
            return await response.json()

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    @staticmethod
    def _track_from_payload(item: dict[str, Any]) -> Track | None:
        if not item or item.get("type") == "episode":
            return None
        name = item.get("name")
        if not name:
            return None
        artists = ", ".join(
            artist["name"] for artist in item.get("artists", []) if artist.get("name")
        )
        images = (item.get("album") or {}).get("images") or []

        title = f"{artists} - {name}" if artists else name
        return Track(
            title=title,
            url=(item.get("external_urls") or {}).get("spotify"),
            duration=item.get("duration_ms"),
            uploader=artists or None,
            thumbnail=images[0]["url"] if images else None,
            source="spotify",
            # Played by searching YouTube for the artist/title pair.
            resolve_query=f"{artists} {name}".strip() if artists else name,
        )

    async def _paginate(self, path: str, **params: Any) -> list[dict[str, Any]]:
        """Walk a paged Spotify collection up to ``playlist_limit`` items."""
        items: list[dict[str, Any]] = []
        offset = 0
        while len(items) < self.config.playlist_limit:
            page = await self._get(
                path, limit=PAGE_SIZE, offset=offset, **params
            )
            batch = page.get("items") or []
            items.extend(batch)
            if len(batch) < PAGE_SIZE or not page.get("next"):
                break
            offset += PAGE_SIZE
        return items[: self.config.playlist_limit]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def load(self, kind: str, spotify_id: str) -> LoadResult:
        if not self.enabled:
            return LoadResult.failed(
                "Spotify links need SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to be set."
            )

        try:
            if kind == "track":
                payload = await self._get(f"/tracks/{spotify_id}")
                track = self._track_from_payload(payload)
                return LoadResult.track(track) if track else LoadResult.empty()

            if kind == "album":
                album = await self._get(f"/albums/{spotify_id}")
                items = await self._paginate(f"/albums/{spotify_id}/tracks")
                images = album.get("images") or []
                tracks: list[Track] = []
                for item in items:
                    track = self._track_from_payload(item)
                    if track is None:
                        continue
                    if not track.thumbnail and images:
                        track.thumbnail = images[0]["url"]
                    tracks.append(track)
                if not tracks:
                    return LoadResult.empty()
                return LoadResult.playlist(
                    tracks,
                    name=album.get("name") or "Spotify album",
                    url=(album.get("external_urls") or {}).get("spotify"),
                )

            if kind == "playlist":
                playlist = await self._get(f"/playlists/{spotify_id}", fields="name,external_urls")
                items = await self._paginate(f"/playlists/{spotify_id}/tracks")
                tracks = []
                for item in items:
                    track = self._track_from_payload(item.get("track") or {})
                    if track is not None:
                        tracks.append(track)
                if not tracks:
                    return LoadResult.empty()
                return LoadResult.playlist(
                    tracks,
                    name=playlist.get("name") or "Spotify playlist",
                    url=(playlist.get("external_urls") or {}).get("spotify"),
                )

            if kind == "artist":
                payload = await self._get(
                    f"/artists/{spotify_id}/top-tracks", market="US"
                )
                tracks = []
                for item in payload.get("tracks") or []:
                    track = self._track_from_payload(item)
                    if track is not None:
                        tracks.append(track)
                if not tracks:
                    return LoadResult.empty()
                return LoadResult.playlist(tracks, name="Top tracks")

        except aiohttp.ClientResponseError as exc:
            log.warning("Spotify API error for %s/%s: %s", kind, spotify_id, exc)
            if exc.status in (401, 403):
                return LoadResult.failed("Spotify rejected the configured credentials.")
            if exc.status == 404:
                return LoadResult.failed("That Spotify link doesn't exist (or is private).")
            return LoadResult.failed(f"Spotify returned HTTP {exc.status}.")
        except Exception as exc:
            log.warning("Spotify lookup failed: %s", exc)
            return LoadResult.failed("Could not reach Spotify.")

        return LoadResult.empty()
