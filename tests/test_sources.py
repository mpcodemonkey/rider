"""Source parsing tests. No network: yt-dlp payload shapes are supplied directly."""

from __future__ import annotations

import pytest

from jockiefluxer.sources import SourceManager
from jockiefluxer.sources.spotify import SpotifySource, parse_spotify_url
from jockiefluxer.sources.ytdlp import YTDLPSource
from jockiefluxer.track import LoadType, Track


@pytest.fixture
def ytdlp(config) -> YTDLPSource:
    return YTDLPSource(config)


FULL_VIDEO = {
    "id": "dQw4w9WgXcQ",
    "title": "Tavern Ambience",
    "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "duration": 212.5,
    "uploader": "Ambience Co",
    "thumbnail": "https://img/thumb.jpg",
    "extractor_key": "Youtube",
    "url": "https://stream.example/audio.m4a",
}

FLAT_ENTRY = {
    "id": "abc123",
    "title": "Battle Music",
    "duration": 180,
    "ie_key": "Youtube",
}


# ---------------------------------------------------------------------------
# Track construction
# ---------------------------------------------------------------------------
def test_full_video_becomes_a_resolved_track(ytdlp):
    track = ytdlp._make_track(FULL_VIDEO, flat=False)
    assert track.title == "Tavern Ambience"
    assert track.duration == 212_500  # seconds -> milliseconds
    assert track.uploader == "Ambience Co"
    assert track.stream_url == "https://stream.example/audio.m4a"
    assert track.needs_resolving is False


def test_flat_playlist_entry_stays_lazy(ytdlp):
    track = ytdlp._make_track(FLAT_ENTRY, flat=True)
    assert track.title == "Battle Music"
    assert track.duration == 180_000
    assert track.stream_url is None
    assert track.needs_resolving is True
    # A YouTube id is enough to rebuild a resolvable URL later.
    assert track.resolve_query == "https://www.youtube.com/watch?v=abc123"


def test_live_streams_have_no_duration(ytdlp):
    track = ytdlp._make_track({**FULL_VIDEO, "is_live": True}, flat=False)
    assert track.is_live is True
    assert track.duration is None
    assert track.duration_text == "LIVE"


def test_live_status_string_is_also_recognised(ytdlp):
    track = ytdlp._make_track({**FULL_VIDEO, "live_status": "is_live"}, flat=False)
    assert track.is_live is True


def test_entry_without_any_identifier_is_dropped(ytdlp):
    assert ytdlp._make_track({"title": "orphan"}, flat=True) is None


def test_thumbnail_falls_back_to_the_thumbnails_list(ytdlp):
    payload = {**FULL_VIDEO, "thumbnail": None, "thumbnails": [{"url": "a"}, {"url": "b"}]}
    assert ytdlp._make_track(payload, flat=False).thumbnail == "b"


# ---------------------------------------------------------------------------
# Stream selection
# ---------------------------------------------------------------------------
def test_pick_stream_url_prefers_the_top_level_url(ytdlp):
    assert ytdlp._pick_stream_url(FULL_VIDEO) == "https://stream.example/audio.m4a"


def test_pick_stream_url_prefers_audio_only_formats(ytdlp):
    info = {
        "formats": [
            {"acodec": "mp4a", "vcodec": "avc1", "abr": 200, "url": "video+audio"},
            {"acodec": "opus", "vcodec": "none", "abr": 128, "url": "audio-only"},
        ]
    }
    assert ytdlp._pick_stream_url(info) == "audio-only"


def test_pick_stream_url_picks_the_highest_bitrate(ytdlp):
    info = {
        "formats": [
            {"acodec": "opus", "vcodec": "none", "abr": 64, "url": "low"},
            {"acodec": "opus", "vcodec": "none", "abr": 160, "url": "high"},
        ]
    }
    assert ytdlp._pick_stream_url(info) == "high"


def test_pick_stream_url_falls_back_to_muxed_formats(ytdlp):
    info = {"formats": [{"acodec": "mp4a", "vcodec": "avc1", "abr": 128, "url": "muxed"}]}
    assert ytdlp._pick_stream_url(info) == "muxed"


def test_pick_stream_url_returns_none_when_there_is_no_audio(ytdlp):
    info = {"formats": [{"acodec": "none", "vcodec": "avc1", "url": "silent"}]}
    assert ytdlp._pick_stream_url(info) is None


# ---------------------------------------------------------------------------
# YouTube ids (used by autoplay)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc123", "abc123"),
        ("https://www.youtube.com/watch?v=abc123&list=RDxyz", "abc123"),
        ("https://youtu.be/abc123", "abc123"),
        ("https://youtu.be/abc123?t=30", "abc123"),
        ("https://soundcloud.com/artist/track", None),
    ],
)
def test_youtube_id_extraction(url, expected):
    assert YTDLPSource._youtube_id(Track(title="t", url=url)) == expected


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://open.spotify.com/track/abc123", ("track", "abc123")),
        ("https://open.spotify.com/album/xyz", ("album", "xyz")),
        ("https://open.spotify.com/playlist/p1?si=x", ("playlist", "p1")),
        ("https://open.spotify.com/intl-de/track/abc123", ("track", "abc123")),
        ("spotify:track:abc123", ("track", "abc123")),
        ("https://youtube.com/watch?v=x", None),
        ("just some text", None),
    ],
)
def test_parse_spotify_url(value, expected):
    assert parse_spotify_url(value) == expected


def test_spotify_track_payload_becomes_a_searchable_track():
    payload = {
        "name": "Bard's Lament",
        "artists": [{"name": "The Minstrels"}],
        "duration_ms": 195_000,
        "external_urls": {"spotify": "https://open.spotify.com/track/abc"},
        "album": {"images": [{"url": "https://img/cover.jpg"}]},
    }
    track = SpotifySource._track_from_payload(payload)
    assert track.title == "The Minstrels - Bard's Lament"
    assert track.duration == 195_000
    assert track.thumbnail == "https://img/cover.jpg"
    # Playback happens by searching YouTube for the artist and title.
    assert track.resolve_query == "The Minstrels Bard's Lament"
    assert track.source == "spotify"


def test_spotify_payload_without_a_name_is_skipped():
    assert SpotifySource._track_from_payload({}) is None
    assert SpotifySource._track_from_payload({"type": "episode", "name": "x"}) is None


async def test_spotify_without_credentials_explains_itself(config):
    manager = SourceManager(config)
    try:
        result = await manager.load("https://open.spotify.com/track/abc123")
        assert result.load_type is LoadType.ERROR
        assert "SPOTIFY_CLIENT_ID" in result.error
    finally:
        await manager.close()


# ---------------------------------------------------------------------------
# Routing and local files
# ---------------------------------------------------------------------------
async def test_local_paths_are_ignored_unless_enabled(config, tmp_path):
    song = tmp_path / "horns.mp3"
    song.write_bytes(b"not really audio")

    manager = SourceManager(config)
    try:
        assert manager._as_local_path(str(song)) is None  # disabled by default
        config.allow_local_files = True
        assert manager._as_local_path(str(song)) == song
    finally:
        await manager.close()


async def test_local_directory_loads_as_a_playlist(config, tmp_path):
    config.allow_local_files = True
    folder = tmp_path / "ambience"
    folder.mkdir()
    for name in ("a.mp3", "b.flac", "notes.txt"):
        (folder / name).write_bytes(b"x")

    manager = SourceManager(config)
    try:
        result = await manager.load(str(folder))
        assert result.load_type is LoadType.PLAYLIST
        # The .txt file is not an audio extension and must be skipped.
        assert sorted(track.title for track in result.tracks) == ["a", "b"]
    finally:
        await manager.close()


async def test_attachments_are_playable_immediately(config):
    manager = SourceManager(config)
    try:
        result = await manager.load_attachment("https://cdn/x.mp3", "tavern.mp3")
        track = result.tracks[0]
        assert track.title == "tavern"
        assert track.needs_resolving is False
        assert await manager.resolve_stream(track) == "https://cdn/x.mp3"
    finally:
        await manager.close()


async def test_empty_query_loads_nothing(config):
    manager = SourceManager(config)
    try:
        assert (await manager.load("   ")).load_type is LoadType.EMPTY
    finally:
        await manager.close()


# ---------------------------------------------------------------------------
# Track serialisation
# ---------------------------------------------------------------------------
def test_track_round_trips_through_a_dict_without_the_stream_url():
    track = Track(
        title="Tavern",
        url="https://example.com/x",
        duration=1000,
        resolve_query="https://example.com/x",
    )
    track.mark_resolved("https://signed-and-expiring")

    restored = Track.from_dict(track.to_dict())
    assert restored.title == "Tavern"
    assert restored.duration == 1000
    # Signed URLs expire, so they must never be persisted.
    assert restored.stream_url is None
    assert restored.needs_resolving is True


def test_stale_stream_urls_are_re_resolved():
    import time

    track = Track(title="x", resolve_query="q")
    track.mark_resolved("https://signed")
    assert track.needs_resolving is False

    track.resolved_at = time.time() - (4 * 60 * 60)  # older than the TTL
    assert track.needs_resolving is True


def test_copy_for_reassigns_the_requester():
    original = Track(title="x", requester_id=1, requester_name="gm")
    copy = original.copy_for(2, "player2")
    assert copy.requester_id == 2
    assert copy.requester_name == "player2"
    assert original.requester_id == 1


# ---------------------------------------------------------------------------
# player_client / cookies
# ---------------------------------------------------------------------------
def test_default_player_clients_support_cookies(config):
    """Regression: 'android' ignores cookiefile entirely, so it must not lead."""
    assert config.ytdlp_player_clients == ["web", "tv"]


def test_build_opts_passes_the_configured_player_clients(ytdlp, config):
    config.ytdlp_player_clients = ["web", "tv", "android"]
    ytdlp = YTDLPSource(config)
    opts = ytdlp._build_opts(flat=False)
    assert opts["extractor_args"]["youtube"]["player_client"] == ["web", "tv", "android"]


def test_cookiefile_is_only_set_when_configured(ytdlp, config):
    assert "cookiefile" not in ytdlp._build_opts(flat=False)
    config.ytdlp_cookiefile = "/config/cookies.txt"
    ytdlp = YTDLPSource(config)
    assert ytdlp._build_opts(flat=False)["cookiefile"] == "/config/cookies.txt"
