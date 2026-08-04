"""Small formatting and parsing helpers shared across the bot."""

from __future__ import annotations

import re
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")

_TIME_UNIT_RE = re.compile(
    r"(?:(?P<hours>\d+)\s*h)?\s*(?:(?P<minutes>\d+)\s*m)?\s*(?:(?P<seconds>\d+)\s*s)?$",
    re.IGNORECASE,
)
_LINK_LABEL_RE = re.compile(r"([\[\]])")
_URL_RE = re.compile(r"^<?(https?://\S+?)>?$", re.IGNORECASE)


def format_duration(milliseconds: int | float | None) -> str:
    """Render a duration the way Jockie does: ``3:42`` or ``1:03:42``."""
    if milliseconds is None:
        return "?:??"
    total_seconds = max(0, int(milliseconds // 1000))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def parse_time(value: str) -> int | None:
    """Parse a user-supplied timestamp into milliseconds.

    Accepts ``90``, ``1:30``, ``1:02:03``, ``1m30s`` and ``2h``.  Returns
    ``None`` when the input is not a recognisable duration.
    """
    text = value.strip().lower()
    if not text:
        return None

    if ":" in text:
        parts = text.split(":")
        if len(parts) > 3 or not all(part.strip().isdigit() for part in parts):
            return None
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + int(part)
        return seconds * 1000

    if text.isdigit():
        return int(text) * 1000

    match = _TIME_UNIT_RE.fullmatch(text)
    if not match or not any(match.groupdict().values()):
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return ((hours * 3600) + (minutes * 60) + seconds) * 1000


def progress_bar(position: int, duration: int, width: int = 20) -> str:
    """A ``────🔘────`` style playback bar."""
    if not duration or duration <= 0:
        return "🔴 LIVE"
    ratio = min(max(position / duration, 0.0), 1.0)
    marker = min(int(ratio * width), width - 1)
    return "─" * marker + "🔘" + "─" * (width - marker - 1)


def escape_link_label(text: str) -> str:
    """Make a track title safe to use as a ``[label](url)`` link label.

    Real titles are full of things like ``[Official Video]``; an unescaped
    bracket swallows the rest of the link when the client renders it.
    """
    return _LINK_LABEL_RE.sub(r"\\\1", text)


def truncate(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def clean_url(value: str) -> str | None:
    """Strip Fluxer's ``<...>`` link suppression and validate it looks like a URL."""
    match = _URL_RE.match(value.strip())
    return match.group(1) if match else None


def is_url(value: str) -> bool:
    return clean_url(value) is not None


def chunked(items: Sequence[T], size: int) -> list[Sequence[T]]:
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[index : index + size] for index in range(0, len(items), size)]


def human_join(items: Iterable[str], conjunction: str = "and") -> str:
    values = list(items)
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return f"{', '.join(values[:-1])} {conjunction} {values[-1]}"


def parse_index_selection(argument: str, length: int) -> list[int]:
    """Parse ``3``, ``2-5`` or ``1,4,7`` into sorted zero-based indices.

    Out-of-range values are dropped so callers only ever see usable indices.
    """
    indices: set[int] = set()
    for part in argument.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part[1:]:
            start_text, _, end_text = part.partition("-")
            if not (start_text.isdigit() and end_text.isdigit()):
                continue
            start, end = int(start_text), int(end_text)
            if start > end:
                start, end = end, start
            indices.update(range(start, end + 1))
        elif part.isdigit():
            indices.add(int(part))

    return sorted(index - 1 for index in indices if 1 <= index <= length)


def plural(count: int, singular: str, suffix: str = "s") -> str:
    return singular if count == 1 else f"{singular}{suffix}"
