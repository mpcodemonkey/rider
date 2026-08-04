"""SQLite persistence for guild settings, saved playlists and favourites.

``sqlite3`` is synchronous, so every call hops onto a worker thread.  A single
connection guarded by a lock is plenty for a self-hosted bot and keeps the
write path simple.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .track import Track

log = logging.getLogger(__name__)

#: Reserved playlist name backing the ``favourites`` commands.
FAVOURITES = "__favourites__"

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id        INTEGER PRIMARY KEY,
    prefix          TEXT,
    volume          INTEGER,
    dj_role_id      INTEGER,
    stay_connected  INTEGER NOT NULL DEFAULT 0,
    announce        INTEGER NOT NULL DEFAULT 1,
    autoplay        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE(owner_id, name)
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    payload     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_playlist_tracks
    ON playlist_tracks(playlist_id, position);
"""


@dataclass(slots=True)
class GuildSettings:
    guild_id: int
    prefix: str | None = None
    volume: int | None = None
    dj_role_id: int | None = None
    stay_connected: bool = False
    announce: bool = True
    autoplay: bool = False


class Store:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        # Prefixes are read on every single message, so keep them in memory.
        self._prefix_cache: dict[int, str | None] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def _open() -> sqlite3.Connection:
            connection = sqlite3.connect(self.path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(SCHEMA)
            connection.commit()
            return connection

        self._connection = await asyncio.to_thread(_open)
        log.info("Database ready at %s", self.path)

    async def close(self) -> None:
        if self._connection is not None:
            connection, self._connection = self._connection, None
            await asyncio.to_thread(connection.close)

    def _require(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Store.connect() was never awaited")
        return self._connection

    async def _run(self, func, *args):
        async with self._lock:
            return await asyncio.to_thread(func, self._require(), *args)

    # ------------------------------------------------------------------
    # Guild settings
    # ------------------------------------------------------------------
    async def get_settings(self, guild_id: int) -> GuildSettings:
        def _query(connection: sqlite3.Connection) -> GuildSettings:
            row = connection.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            if row is None:
                return GuildSettings(guild_id=guild_id)
            return GuildSettings(
                guild_id=row["guild_id"],
                prefix=row["prefix"],
                volume=row["volume"],
                dj_role_id=row["dj_role_id"],
                stay_connected=bool(row["stay_connected"]),
                announce=bool(row["announce"]),
                autoplay=bool(row["autoplay"]),
            )

        settings = await self._run(_query)
        self._prefix_cache[guild_id] = settings.prefix
        return settings

    async def update_settings(self, guild_id: int, **fields) -> None:
        allowed = {
            "prefix", "volume", "dj_role_id", "stay_connected", "announce", "autoplay",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return

        columns = ", ".join(f"{key} = ?" for key in updates)
        values = [
            int(value) if isinstance(value, bool) else value for value in updates.values()
        ]

        def _write(connection: sqlite3.Connection) -> None:
            connection.execute(
                "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,)
            )
            connection.execute(
                f"UPDATE guild_settings SET {columns} WHERE guild_id = ?",
                (*values, guild_id),
            )
            connection.commit()

        await self._run(_write)
        if "prefix" in updates:
            self._prefix_cache[guild_id] = updates["prefix"]

    async def get_prefix(self, guild_id: int) -> str | None:
        """Cached prefix lookup — this runs for every message the bot sees."""
        if guild_id in self._prefix_cache:
            return self._prefix_cache[guild_id]
        settings = await self.get_settings(guild_id)
        return settings.prefix

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------
    async def list_playlists(self, owner_id: int) -> list[tuple[str, int]]:
        """Return ``(name, track_count)`` for a user's playlists."""

        def _query(connection: sqlite3.Connection) -> list[tuple[str, int]]:
            rows = connection.execute(
                """
                SELECT p.name AS name, COUNT(t.playlist_id) AS count
                FROM playlists p
                LEFT JOIN playlist_tracks t ON t.playlist_id = p.id
                WHERE p.owner_id = ? AND p.name != ?
                GROUP BY p.id
                ORDER BY p.created_at
                """,
                (owner_id, FAVOURITES),
            ).fetchall()
            return [(row["name"], row["count"]) for row in rows]

        return await self._run(_query)

    async def create_playlist(self, owner_id: int, name: str) -> bool:
        """Create an empty playlist. Returns False if the name is taken."""

        def _write(connection: sqlite3.Connection) -> bool:
            try:
                connection.execute(
                    "INSERT INTO playlists (owner_id, name, created_at) VALUES (?, ?, ?)",
                    (owner_id, name, time.time()),
                )
                connection.commit()
                return True
            except sqlite3.IntegrityError:
                return False

        return await self._run(_write)

    async def delete_playlist(self, owner_id: int, name: str) -> bool:
        def _write(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                "DELETE FROM playlists WHERE owner_id = ? AND name = ?", (owner_id, name)
            )
            connection.commit()
            return cursor.rowcount > 0

        return await self._run(_write)

    async def rename_playlist(self, owner_id: int, name: str, new_name: str) -> bool:
        def _write(connection: sqlite3.Connection) -> bool:
            try:
                cursor = connection.execute(
                    "UPDATE playlists SET name = ? WHERE owner_id = ? AND name = ?",
                    (new_name, owner_id, name),
                )
                connection.commit()
                return cursor.rowcount > 0
            except sqlite3.IntegrityError:
                return False

        return await self._run(_write)

    async def get_playlist(self, owner_id: int, name: str) -> list[Track] | None:
        """Return the playlist's tracks, or ``None`` if it doesn't exist."""

        def _query(connection: sqlite3.Connection) -> list[Track] | None:
            row = connection.execute(
                "SELECT id FROM playlists WHERE owner_id = ? AND name = ?",
                (owner_id, name),
            ).fetchone()
            if row is None:
                return None
            rows = connection.execute(
                "SELECT payload FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (row["id"],),
            ).fetchall()
            return [Track.from_dict(json.loads(entry["payload"])) for entry in rows]

        return await self._run(_query)

    async def add_to_playlist(
        self, owner_id: int, name: str, tracks: list[Track], *, create: bool = True
    ) -> int | None:
        """Append tracks. Returns the new length, or ``None`` if missing."""

        def _write(connection: sqlite3.Connection) -> int | None:
            row = connection.execute(
                "SELECT id FROM playlists WHERE owner_id = ? AND name = ?",
                (owner_id, name),
            ).fetchone()
            if row is None:
                if not create:
                    return None
                cursor = connection.execute(
                    "INSERT INTO playlists (owner_id, name, created_at) VALUES (?, ?, ?)",
                    (owner_id, name, time.time()),
                )
                playlist_id = cursor.lastrowid
                next_position = 0
            else:
                playlist_id = row["id"]
                position_row = connection.execute(
                    "SELECT COALESCE(MAX(position), -1) AS last FROM playlist_tracks WHERE playlist_id = ?",
                    (playlist_id,),
                ).fetchone()
                next_position = position_row["last"] + 1

            connection.executemany(
                "INSERT INTO playlist_tracks (playlist_id, position, payload) VALUES (?, ?, ?)",
                [
                    (playlist_id, next_position + offset, json.dumps(track.to_dict()))
                    for offset, track in enumerate(tracks)
                ],
            )
            connection.commit()
            total = connection.execute(
                "SELECT COUNT(*) AS total FROM playlist_tracks WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()["total"]
            return total

        return await self._run(_write)

    async def remove_from_playlist(
        self, owner_id: int, name: str, index: int
    ) -> Track | None:
        """Remove the track at zero-based ``index`` and resequence positions."""

        def _write(connection: sqlite3.Connection) -> Track | None:
            row = connection.execute(
                "SELECT id FROM playlists WHERE owner_id = ? AND name = ?",
                (owner_id, name),
            ).fetchone()
            if row is None:
                return None
            playlist_id = row["id"]
            entries = connection.execute(
                "SELECT rowid, payload FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (playlist_id,),
            ).fetchall()
            if not (0 <= index < len(entries)):
                return None

            target = entries[index]
            connection.execute("DELETE FROM playlist_tracks WHERE rowid = ?", (target["rowid"],))
            remaining = [entry for offset, entry in enumerate(entries) if offset != index]
            connection.executemany(
                "UPDATE playlist_tracks SET position = ? WHERE rowid = ?",
                [(position, entry["rowid"]) for position, entry in enumerate(remaining)],
            )
            connection.commit()
            return Track.from_dict(json.loads(target["payload"]))

        return await self._run(_write)

    async def clear_playlist(self, owner_id: int, name: str) -> int | None:
        def _write(connection: sqlite3.Connection) -> int | None:
            row = connection.execute(
                "SELECT id FROM playlists WHERE owner_id = ? AND name = ?",
                (owner_id, name),
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ?", (row["id"],)
            )
            connection.commit()
            return cursor.rowcount

        return await self._run(_write)
