"""The bot client: event wiring, permission checks and small helpers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, AsyncIterator

from fluxer import Client, Embed, Intents, Permissions

from . import ui
from .commands import dispatch
from .config import Config
from .player import GuildPlayer, PlayerManager
from .sources import SourceManager
from .store import Store
from .track import Track

log = logging.getLogger(__name__)

#: How long cached guild role data stays fresh, in seconds.
ROLE_CACHE_TTL = 300


class MusicBot(Client):
    """A Fluxer client that behaves like Jockie Music."""

    def __init__(self, config: Config) -> None:
        super().__init__(
            intents=(
                Intents.GUILDS
                | Intents.GUILD_MESSAGES
                | Intents.GUILD_VOICE_STATES
                | Intents.GUILD_MEMBERS
                | Intents.MESSAGE_CONTENT
                | Intents.DIRECT_MESSAGES
            ),
            api_url=config.api_url,
            retry_forever=True,
        )
        self.config = config
        self.store = Store(config.database_path)
        self.sources = SourceManager(config)
        self.players = PlayerManager(self, config)

        self._guild_of_channel: dict[int, int | None] = {}
        self._role_cache: dict[int, tuple[float, dict[int, Any]]] = {}
        self._waiters: dict[tuple[int, int], list[asyncio.Future]] = {}

        self.event(self.on_ready)
        self.event(self.on_message)
        self.event(self.on_voice_state_update)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        await self.store.connect()

    async def close(self) -> None:
        await self.players.close()
        await self.sources.close()
        await self.store.close()
        await super().close()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    async def on_ready(self) -> None:
        user = self.user
        log.info(
            "Connected as %s (%s) in %d guild(s)",
            getattr(user, "username", "?"),
            getattr(user, "id", "?"),
            len(self.guilds),
        )
        log.info("Command prefix: %s", self.config.prefix)

    async def on_message(self, message: Any) -> None:
        self._resolve_waiters(message)
        await dispatch(self, message)

    async def on_voice_state_update(self, state: Any) -> None:
        """Track who is listening, and notice when the bot gets disconnected."""
        guild_id = state.guild_id
        if guild_id is None:
            return
        player = self.players.get(guild_id)
        if player is None or not player.is_connected:
            return

        bot_id = getattr(self.user, "id", None)
        if state.user_id == bot_id:
            # Someone moved or kicked the bot out of the channel.
            if state.channel_id is None:
                await self.players.discard(guild_id)
            else:
                player.voice_channel_id = state.channel_id
            return

        await player.on_listeners_changed(len(self.listener_ids(player)))

    # ------------------------------------------------------------------
    # Prefixes
    # ------------------------------------------------------------------
    async def guild_prefix(self, guild_id: int) -> str:
        return await self.store.get_prefix(guild_id) or self.config.prefix

    async def matching_prefix(self, content: str, guild_id: int) -> str | None:
        """Return the prefix ``content`` starts with, if any.

        Matching is case-insensitive so ``m!play`` and ``M!play`` both work,
        the way they do with Jockie.
        """
        custom = await self.store.get_prefix(guild_id)
        candidates = [custom] if custom else []
        candidates.extend(self.config.prefixes)

        lowered = content.lower()
        for candidate in candidates:
            if candidate and lowered.startswith(candidate.lower()):
                # Return the text as typed so the length lines up exactly.
                return content[: len(candidate)]

        if self.config.respond_to_mention and self.user is not None:
            for mention in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
                if content.startswith(mention):
                    # Swallow the space after the mention too.
                    return content[: len(mention)] + (
                        " " if content[len(mention) : len(mention) + 1] == " " else ""
                    )
        return None

    # ------------------------------------------------------------------
    # Guild/channel resolution
    # ------------------------------------------------------------------
    async def resolve_guild_id(self, message: Any) -> int | None:
        """Find the guild a message belongs to, caching channel lookups."""
        if message.guild_id is not None:
            self._guild_of_channel[message.channel_id] = message.guild_id
            return message.guild_id

        channel_id = message.channel_id
        if channel_id in self._guild_of_channel:
            return self._guild_of_channel[channel_id]

        cached = self._channels.get(channel_id)
        if cached is not None and cached.guild_id is not None:
            self._guild_of_channel[channel_id] = cached.guild_id
            return cached.guild_id

        try:
            channel = await self.fetch_channel(str(channel_id))
        except Exception:
            self._guild_of_channel[channel_id] = None
            return None

        self._guild_of_channel[channel_id] = channel.guild_id
        return channel.guild_id

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    async def send(
        self, channel_id: int, content: str | None = None, *, embed: Embed | None = None
    ):
        if self._http is None:
            return None
        try:
            data = await self._http.send_message(channel_id, content=content, embed=embed)
        except Exception:
            log.debug("could not send to channel %s", channel_id, exc_info=True)
            return None

        from fluxer.models import Message

        return Message.from_data(data, self._http)

    async def send_plain(self, channel_id: int, message: str):
        return await self.send(channel_id, embed=ui.info(message))

    async def send_dm(self, user_id: int, *, embed: Embed | None = None, content: str | None = None) -> bool:
        if self._http is None:
            return False
        try:
            channel = await self._http.create_dm(user_id)
            await self._http.send_message(
                int(channel["id"]), content=content, embed=embed
            )
            return True
        except Exception:
            log.debug("could not DM user %s", user_id, exc_info=True)
            return False

    @contextlib.asynccontextmanager
    async def typing(self, channel_id: int) -> AsyncIterator[None]:
        """Show a typing indicator while a slow lookup runs."""
        try:
            if self._http is not None:
                await self._http.trigger_typing(channel_id)
        except Exception:
            log.debug("typing indicator failed", exc_info=True)
        try:
            yield
        finally:
            pass

    # ------------------------------------------------------------------
    # Waiting for a follow-up message (used by `search`)
    # ------------------------------------------------------------------
    async def wait_for_message(
        self, channel_id: int, author_id: int, *, timeout: float = 30.0
    ) -> Any | None:
        key = (channel_id, author_id)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(key, []).append(future)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            waiters = self._waiters.get(key)
            if waiters and future in waiters:
                waiters.remove(future)
            if waiters is not None and not waiters:
                self._waiters.pop(key, None)

    def _resolve_waiters(self, message: Any) -> None:
        waiters = self._waiters.get((message.channel_id, message.author.id))
        if not waiters:
            return
        for future in list(waiters):
            if not future.done():
                future.set_result(message)

    # ------------------------------------------------------------------
    # Voice channel occupancy
    # ------------------------------------------------------------------
    def listener_ids(self, player: GuildPlayer) -> set[int]:
        """Non-bot users currently sitting in the player's voice channel."""
        if player.voice_channel_id is None:
            return set()
        bot_id = getattr(self.user, "id", None)
        return {
            state.user_id
            for state in self.get_guild_voice_states(player.guild_id)
            if state.channel_id == player.voice_channel_id and state.user_id != bot_id
        }

    def has_listeners(self, player: GuildPlayer) -> bool:
        return bool(self.listener_ids(player))

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------
    async def _guild_roles(self, guild_id: int) -> dict[int, Any]:
        cached = self._role_cache.get(guild_id)
        if cached is not None and (time.time() - cached[0]) < ROLE_CACHE_TTL:
            return cached[1]
        if self._http is None:
            return {}
        try:
            payload = await self._http.get_guild_roles(guild_id)
        except Exception:
            log.debug("could not fetch roles for guild %s", guild_id, exc_info=True)
            return {}

        roles = {int(role["id"]): role for role in payload}
        self._role_cache[guild_id] = (time.time(), roles)
        return roles

    async def find_role_id(self, guild_id: int, name: str) -> int | None:
        wanted = name.strip().lstrip("@").lower()
        for role_id, role in (await self._guild_roles(guild_id)).items():
            if (role.get("name") or "").lower() == wanted:
                return role_id
        return None

    async def member_permissions(self, guild_id: int, user_id: int) -> int:
        """Union of the permission bits granted by a member's roles."""
        if self._http is None:
            return 0

        guild = self.get_guild(guild_id)
        if guild is not None and guild.owner_id == user_id:
            return int(Permissions.ADMINISTRATOR)

        try:
            member = await self._http.get_guild_member(guild_id, user_id)
        except Exception:
            return 0

        roles = await self._guild_roles(guild_id)
        permissions = 0
        for role_id in member.get("roles", []):
            role = roles.get(int(role_id))
            if role is not None:
                permissions |= int(role.get("permissions") or 0)
        # The @everyone role shares the guild's ID and isn't in member.roles.
        everyone = roles.get(guild_id)
        if everyone is not None:
            permissions |= int(everyone.get("permissions") or 0)
        return permissions

    async def has_guild_permission(
        self, guild_id: int, user_id: int, *, manage_guild: bool = False
    ) -> bool:
        permissions = await self.member_permissions(guild_id, user_id)
        if permissions & int(Permissions.ADMINISTRATOR):
            return True
        if manage_guild:
            return bool(permissions & int(Permissions.MANAGE_GUILD))
        return bool(permissions & int(Permissions.MANAGE_CHANNELS))

    async def member_has_role(self, guild_id: int, user_id: int, role_id: int) -> bool:
        if self._http is None:
            return False
        try:
            member = await self._http.get_guild_member(guild_id, user_id)
        except Exception:
            return False
        return role_id in {int(entry) for entry in member.get("roles", [])}

    async def is_dj(self, guild_id: int, user_id: int, channel_id: int) -> bool:
        """DJ restrictions only kick in once a server actually configures a DJ role."""
        settings = await self.store.get_settings(guild_id)
        if settings.dj_role_id is None:
            return True
        if await self.member_has_role(guild_id, user_id, settings.dj_role_id):
            return True
        if await self.has_guild_permission(guild_id, user_id):
            return True

        # Whoever is alone with the bot is implicitly in charge.
        player = self.players.get(guild_id)
        if player is not None:
            listeners = self.listener_ids(player)
            if listeners == {user_id}:
                return True
        return False

    # ------------------------------------------------------------------
    # Player helpers
    # ------------------------------------------------------------------
    async def apply_guild_settings(self, player: GuildPlayer) -> None:
        """Seed a fresh player from the stored per-guild settings."""
        settings = await self.store.get_settings(player.guild_id)
        if settings.volume is not None:
            player.set_volume(settings.volume)
        player.stay_connected = settings.stay_connected
        player.autoplay = settings.autoplay

    async def announce_now_playing(self, player: GuildPlayer, track: Track) -> None:
        settings = await self.store.get_settings(player.guild_id)
        if not settings.announce or player.text_channel_id is None:
            return
        await self.send(
            player.text_channel_id, embed=ui.now_playing(player, track, compact=True)
        )
