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
    assert config.ytdlp_player_clients == ["tv", "web"]


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


# ---------------------------------------------------------------------------
# Cookiefile diagnostics
# ---------------------------------------------------------------------------
from jockiefluxer.sources.ytdlp import diagnose_cookiefile  # noqa: E402


def test_diagnose_missing_file_names_the_configured_path(tmp_path):
    warnings = diagnose_cookiefile(str(tmp_path / "does-not-exist.txt"))
    assert len(warnings) == 1
    assert "no file exists" in warnings[0]


def test_diagnose_empty_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("")
    assert "is empty" in diagnose_cookiefile(str(path))[0]


def test_diagnose_json_export_is_flagged(tmp_path):
    """A common mistake: exporting JSON cookies instead of Netscape format."""
    path = tmp_path / "cookies.txt"
    path.write_text('[{"name": "SID", "value": "x", "domain": ".youtube.com"}]')
    warnings = diagnose_cookiefile(str(path))
    assert "JSON" in warnings[0]


def test_diagnose_comments_only_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n# This file was generated\n\n")
    assert "no cookie entries" in diagnose_cookiefile(str(path))[0]


def test_diagnose_flags_missing_youtube_cookies(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".google.com\tTRUE\t/\tTRUE\t0\tNID\tabc123\n"
    )
    warnings = diagnose_cookiefile(str(path))
    assert "no youtube.com cookies" in warnings[0]


def test_diagnose_valid_netscape_cookiefile_has_no_warnings(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tAFmmF2abc\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\txyz789\n"
    )
    assert diagnose_cookiefile(str(path)) == []


def test_diagnose_flags_cookies_that_arent_actually_a_login_session(tmp_path):
    """Regression, verified against yt-dlp's real is_authenticated property:
    consent/tracking cookies are present on every YouTube visit, logged in
    or not, so "has some youtube.com cookies" is not the same question as
    "is this an authenticated session". Getting this wrong is exactly what
    produces "Sign in to confirm you're not a bot" despite a configured,
    present, correctly-formatted cookiefile.
    """
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tCONSENT\tYES+cb\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR_INFO1_LIVE\tabc123\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tf6=40000000\n"
    )
    warnings = diagnose_cookiefile(str(path))
    assert warnings
    assert "logged-in session" in warnings[0]


def test_diagnose_requires_both_login_info_and_a_sid_cookie(tmp_path):
    """Either alone doesn't authenticate - matches yt-dlp's own AND logic."""
    login_info_only = tmp_path / "a.txt"
    login_info_only.write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tx\n"
    )
    assert diagnose_cookiefile(str(login_info_only)) != []

    sapisid_only = tmp_path / "b.txt"
    sapisid_only.write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tx\n"
    )
    assert diagnose_cookiefile(str(sapisid_only)) != []


def test_diagnose_accepts_any_sid_family_cookie(tmp_path):
    """YouTube falls back to __Secure-3PAPISID/__Secure-1PAPISID when plain
    SAPISID is absent - yt-dlp accepts any of the three, so this must too.
    """
    for cookie_name in ("SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID"):
        path = tmp_path / f"{cookie_name}.txt"
        path.write_text(
            "# Netscape HTTP Cookie File\n"
            f".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tx\n"
            f".youtube.com\tTRUE\t/\tTRUE\t0\t{cookie_name}\ty\n"
        )
        assert diagnose_cookiefile(str(path)) == [], f"failed for {cookie_name}"


def test_diagnose_flags_an_unwritable_cookiefile(tmp_path, monkeypatch):
    """Regression: yt-dlp persists renewed session cookies back to this file
    after every request (that's what keeps cookies working without manual
    re-export) - if it can't write, that has to show up at startup, not as
    a raw OSError buried in mid-session logs.
    """
    path = tmp_path / "cookies.txt"
    # A genuinely-authenticated file, so the only warning in play is the
    # writability one this test is actually about.
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tx\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\ty\n"
    )

    import jockiefluxer.sources.ytdlp as ytdlp_module

    monkeypatch.setattr(ytdlp_module.os, "access", lambda *a, **k: False)
    warnings = diagnose_cookiefile(str(path))
    assert any("not writable" in warning for warning in warnings)


def test_diagnose_valid_writable_cookiefile_has_no_warnings(tmp_path):
    """The writability check must not false-positive on the normal case."""
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tx\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\ty\n"
    )
    assert diagnose_cookiefile(str(path)) == []


# ---------------------------------------------------------------------------
# Player client validation (typo protection)
# ---------------------------------------------------------------------------
from jockiefluxer.sources.ytdlp import validate_player_clients  # noqa: E402


def test_validate_player_clients_accepts_known_names():
    assert validate_player_clients(["tv", "web"]) == []
    assert validate_player_clients(["web", "mweb", "android", "ios"]) == []


def test_validate_player_clients_flags_a_typo():
    warnings = validate_player_clients(["tv", "wbe"])  # typo'd 'web'
    assert len(warnings) == 1
    assert "wbe" in warnings[0]
    assert "tv" in warnings[0]  # known clients are listed for reference


def test_validate_player_clients_flags_internal_only_names():
    """Names starting with '_' are internal variants users can't select."""
    from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS

    internal_names = [name for name in INNERTUBE_CLIENTS if name.startswith("_")]
    if internal_names:
        assert validate_player_clients([internal_names[0]]) != []


def test_validate_player_clients_reports_every_bad_entry():
    warnings = validate_player_clients(["nope", "also_bad", "tv"])
    assert len(warnings) == 2


# ---------------------------------------------------------------------------
# PO token provider wiring
# ---------------------------------------------------------------------------
def test_pot_provider_extractor_args_absent_when_unconfigured(ytdlp):
    opts = ytdlp._build_opts(flat=False)
    assert "youtubepot-bgutilhttp" not in opts["extractor_args"]


def test_pot_provider_extractor_args_present_when_configured(config):
    config.ytdlp_pot_provider_url = "http://bgutil-pot-provider:4416"
    ytdlp = YTDLPSource(config)
    opts = ytdlp._build_opts(flat=False)
    assert opts["extractor_args"]["youtubepot-bgutilhttp"] == {
        "base_url": ["http://bgutil-pot-provider:4416"]
    }


def test_pot_provider_extractor_args_resolve_through_real_yt_dlp(config):
    """The dict shape has to match what yt-dlp's own config resolution
    expects — verified against a real YoutubeDL instance and the exact
    lookup the bgutil plugin performs, not just eyeballed."""
    from yt_dlp import YoutubeDL

    config.ytdlp_pot_provider_url = "http://bgutil-pot-provider:4416"
    ytdlp = YTDLPSource(config)
    opts = ytdlp._build_opts(flat=False)

    with YoutubeDL(opts) as ydl:
        ie = ydl.get_info_extractor("Youtube")
        resolved = ie._configuration_arg(
            "base_url", ie_key="youtubepot-bgutilhttp", default=[None]
        )[0]
    assert resolved == "http://bgutil-pot-provider:4416"


# ---------------------------------------------------------------------------
# PO token provider health check
# ---------------------------------------------------------------------------
import http.server  # noqa: E402
import json as _json  # noqa: E402
import threading  # noqa: E402

from jockiefluxer.sources.ytdlp import check_pot_provider  # noqa: E402


class _PingHandler(http.server.BaseHTTPRequestHandler):
    response_body = b'{"version": "1.3.1"}'
    status = 200

    def do_GET(self):
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *args):
        pass


def _run_server(handler_cls):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_check_pot_provider_reports_healthy_server():
    server, thread = _run_server(_PingHandler)
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        assert check_pot_provider(url) is None
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_check_pot_provider_reports_connection_refused():
    # retries=0: this test is about the error message shape, not the retry
    # behaviour (which has its own tests below) - no need to actually wait.
    problem = check_pot_provider("http://127.0.0.1:1", timeout=1.0, retries=0)
    assert problem is not None
    assert "Could not reach" in problem


def test_check_pot_provider_reports_invalid_response_body():
    class GarbageHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not json")

        def log_message(self, *args):
            pass

    server, thread = _run_server(GarbageHandler)
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        # retries=0: message-shape test, not retry orchestration.
        problem = check_pot_provider(url, retries=0)
        assert problem is not None
        assert "not with the expected JSON" in problem
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Retry behaviour (real timing already verified manually against a genuine
# delayed socket; these cover the orchestration logic deterministically)
# ---------------------------------------------------------------------------
def test_check_pot_provider_retries_until_the_server_comes_up(monkeypatch):
    import jockiefluxer.sources.ytdlp as ytdlp_module

    attempts = {"count": 0}

    def flaky_ping(base_url, timeout):
        attempts["count"] += 1
        if attempts["count"] < 3:
            return "not up yet"
        return None

    monkeypatch.setattr(ytdlp_module, "_ping_pot_provider", flaky_ping)

    sleeps = []
    result = check_pot_provider(
        "http://bgutil-pot-provider:4416", retries=4, retry_delay=2.0,
        sleep=sleeps.append,
    )
    assert result is None
    assert attempts["count"] == 3
    assert sleeps == [2.0, 2.0]  # slept between attempts 1->2 and 2->3, not after success


def test_check_pot_provider_gives_up_after_exhausting_retries(monkeypatch):
    import jockiefluxer.sources.ytdlp as ytdlp_module

    monkeypatch.setattr(
        ytdlp_module, "_ping_pot_provider", lambda base_url, timeout: "still down"
    )

    sleeps = []
    result = check_pot_provider(
        "http://bgutil-pot-provider:4416", retries=3, retry_delay=1.0,
        sleep=sleeps.append,
    )
    assert result == "still down"
    assert sleeps == [1.0, 1.0, 1.0]  # exactly `retries` sleeps, then stops


def test_check_pot_provider_does_not_sleep_when_already_up(monkeypatch):
    import jockiefluxer.sources.ytdlp as ytdlp_module

    monkeypatch.setattr(ytdlp_module, "_ping_pot_provider", lambda base_url, timeout: None)

    sleeps = []
    result = check_pot_provider(
        "http://bgutil-pot-provider:4416", retries=4, retry_delay=99.0,
        sleep=sleeps.append,
    )
    assert result is None
    assert sleeps == []


# ---------------------------------------------------------------------------
# Cookie persistence resilience
# ---------------------------------------------------------------------------
def test_extract_sync_survives_a_cookie_save_failure(config, monkeypatch):
    """Regression: YoutubeDL.close() persists renewed cookies to disk and
    raises OSError if that write fails (e.g. a read-only mount). Using
    `with YoutubeDL(...) as ydl:` would let that exception replace an
    already-successful extract_info() result. It must not.
    """
    import yt_dlp

    def fake_extract_info(self, query, download=False):
        return {"id": "abc123", "title": "Tavern Ambience"}

    def failing_close(self):
        raise OSError("[Errno 30] Read-only file system: '/config/cookies.txt'")

    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", fake_extract_info)
    monkeypatch.setattr(yt_dlp.YoutubeDL, "close", failing_close)

    config.ytdlp_cookiefile = "/config/cookies.txt"
    ytdlp = YTDLPSource(config)

    info, error = ytdlp._extract_sync("some query", flat=False)
    assert info == {"id": "abc123", "title": "Tavern Ambience"}
    assert error is None


def test_extract_sync_propagates_extract_info_failures_normally(config, monkeypatch):
    """The close()-failure guard must not swallow a genuine extraction error."""
    import yt_dlp

    def failing_extract_info(self, query, download=False):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", failing_extract_info)

    ytdlp = YTDLPSource(config)
    with pytest.raises(RuntimeError, match="network exploded"):
        ytdlp._extract_sync("some query", flat=False)
