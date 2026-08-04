"""Command modules.

Importing this package registers every command with :data:`core.REGISTRY`.
"""

from __future__ import annotations

from . import filters, misc, playback, playlists, queue, settings  # noqa: F401
from .core import REGISTRY, Command, CommandError, Context, command, dispatch

__all__ = [
    "Command",
    "CommandError",
    "Context",
    "REGISTRY",
    "command",
    "dispatch",
]
