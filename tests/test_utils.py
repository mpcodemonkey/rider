from __future__ import annotations

import pytest

from jockiefluxer.utils import (
    clean_url,
    format_duration,
    is_url,
    parse_index_selection,
    parse_time,
    progress_bar,
    truncate,
)


@pytest.mark.parametrize(
    ("milliseconds", "expected"),
    [
        (0, "0:00"),
        (1_000, "0:01"),
        (61_000, "1:01"),
        (222_000, "3:42"),
        (3_600_000, "1:00:00"),
        (3_882_000, "1:04:42"),
        (None, "?:??"),
    ],
)
def test_format_duration(milliseconds, expected):
    assert format_duration(milliseconds) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("90", 90_000),
        ("1:30", 90_000),
        ("1:02:03", 3_723_000),
        ("1m30s", 90_000),
        ("2h", 7_200_000),
        ("45s", 45_000),
        ("0", 0),
    ],
)
def test_parse_time(value, expected):
    assert parse_time(value) == expected


@pytest.mark.parametrize("value", ["", "banana", "1:2:3:4", "--", "1:xx"])
def test_parse_time_rejects_garbage(value):
    assert parse_time(value) is None


def test_parse_index_selection_handles_singles_ranges_and_lists():
    assert parse_index_selection("3", 10) == [2]
    assert parse_index_selection("2-5", 10) == [1, 2, 3, 4]
    assert parse_index_selection("1,4,7", 10) == [0, 3, 6]
    # Reversed ranges still work, and out-of-range values are dropped.
    assert parse_index_selection("5-2", 10) == [1, 2, 3, 4]
    assert parse_index_selection("8-20", 10) == [7, 8, 9]
    assert parse_index_selection("0", 10) == []
    assert parse_index_selection("nope", 10) == []


def test_parse_index_selection_deduplicates_overlaps():
    assert parse_index_selection("1-3,2,3", 10) == [0, 1, 2]


def test_progress_bar_marks_position():
    assert progress_bar(0, 100, width=10).startswith("🔘")
    assert progress_bar(99, 100, width=10).endswith("🔘")
    assert progress_bar(0, 0) == "🔴 LIVE"


def test_url_helpers_strip_angle_brackets():
    assert clean_url("<https://example.com/x>") == "https://example.com/x"
    assert is_url("https://youtu.be/abc")
    assert not is_url("just a search phrase")
    # A bare word that merely contains "http" is not a URL.
    assert not is_url("nothttps://x")


def test_truncate_adds_ellipsis_only_when_needed():
    assert truncate("short", 10) == "short"
    assert truncate("a" * 20, 10).endswith("…")
    assert len(truncate("a" * 20, 10)) == 10


def test_escape_link_label_protects_brackets():
    """Titles like "Song [Official Video]" must not break the embed link."""
    from jockiefluxer.utils import escape_link_label

    assert escape_link_label("Song [Official Video]") == "Song \\[Official Video\\]"
    assert escape_link_label("plain title") == "plain title"


def test_track_markdown_link_escapes_the_label():
    from jockiefluxer.track import Track

    track = Track(title="Tavern [Loop]", url="https://example.com/x")
    link = track.markdown_link
    assert link == "[Tavern \\[Loop\\]](https://example.com/x)"
    # The URL half is untouched.
    assert link.endswith("(https://example.com/x)")
