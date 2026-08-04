"""Persistence tests against a real (temporary) SQLite database."""

from __future__ import annotations

import pytest

from jockiefluxer.store import FAVOURITES, Store
from jockiefluxer.track import Track


@pytest.fixture
async def store(tmp_path):
    store = Store(str(tmp_path / "test.db"))
    await store.connect()
    yield store
    await store.close()


def make_track(title: str) -> Track:
    return Track(
        title=title,
        url=f"https://example.com/{title}",
        duration=120_000,
        source="test",
        resolve_query=f"https://example.com/{title}",
    )


# ---------------------------------------------------------------------------
# Guild settings
# ---------------------------------------------------------------------------
async def test_settings_default_when_never_written(store):
    settings = await store.get_settings(42)
    assert settings.guild_id == 42
    assert settings.prefix is None
    assert settings.dj_role_id is None
    assert settings.stay_connected is False
    assert settings.announce is True


async def test_settings_round_trip(store):
    await store.update_settings(42, prefix="!", volume=80, dj_role_id=99)
    settings = await store.get_settings(42)
    assert (settings.prefix, settings.volume, settings.dj_role_id) == ("!", 80, 99)


async def test_partial_updates_leave_other_fields_alone(store):
    await store.update_settings(42, prefix="!", volume=80)
    await store.update_settings(42, volume=120)
    settings = await store.get_settings(42)
    assert settings.prefix == "!"
    assert settings.volume == 120


async def test_booleans_survive_the_round_trip(store):
    await store.update_settings(42, stay_connected=True, announce=False, autoplay=True)
    settings = await store.get_settings(42)
    assert settings.stay_connected is True
    assert settings.announce is False
    assert settings.autoplay is True


async def test_prefix_cache_reflects_updates(store):
    await store.update_settings(42, prefix="?")
    assert await store.get_prefix(42) == "?"
    await store.update_settings(42, prefix=None)
    assert await store.get_prefix(42) is None


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------
async def test_create_and_list_playlists(store):
    assert await store.create_playlist(1, "ambience")
    assert await store.list_playlists(1) == [("ambience", 0)]


async def test_duplicate_playlist_names_are_rejected_per_user(store):
    assert await store.create_playlist(1, "ambience")
    assert not await store.create_playlist(1, "ambience")
    # A different user may reuse the name.
    assert await store.create_playlist(2, "ambience")


async def test_add_tracks_and_read_them_back(store):
    await store.add_to_playlist(1, "battle", [make_track("drums"), make_track("horns")])
    tracks = await store.get_playlist(1, "battle")
    assert [track.title for track in tracks] == ["drums", "horns"]
    assert tracks[0].duration == 120_000
    assert tracks[0].url == "https://example.com/drums"


async def test_adding_creates_the_playlist_on_demand(store):
    total = await store.add_to_playlist(1, "new", [make_track("a")])
    assert total == 1
    assert await store.get_playlist(1, "new") is not None


async def test_add_appends_rather_than_replacing(store):
    await store.add_to_playlist(1, "p", [make_track("a")])
    total = await store.add_to_playlist(1, "p", [make_track("b")])
    assert total == 2
    assert [track.title for track in await store.get_playlist(1, "p")] == ["a", "b"]


async def test_remove_resequences_remaining_positions(store):
    await store.add_to_playlist(
        1, "p", [make_track("a"), make_track("b"), make_track("c")]
    )
    removed = await store.remove_from_playlist(1, "p", 1)
    assert removed.title == "b"

    # Removing index 1 again must now hit "c", proving positions were rewritten.
    assert [track.title for track in await store.get_playlist(1, "p")] == ["a", "c"]
    second = await store.remove_from_playlist(1, "p", 1)
    assert second.title == "c"


async def test_remove_out_of_range_returns_none(store):
    await store.add_to_playlist(1, "p", [make_track("a")])
    assert await store.remove_from_playlist(1, "p", 9) is None
    assert await store.remove_from_playlist(1, "missing", 0) is None


async def test_deleting_a_playlist_removes_its_tracks(store):
    await store.add_to_playlist(1, "p", [make_track("a")])
    assert await store.delete_playlist(1, "p")
    assert await store.get_playlist(1, "p") is None
    assert await store.list_playlists(1) == []


async def test_rename_playlist(store):
    await store.add_to_playlist(1, "old", [make_track("a")])
    assert await store.rename_playlist(1, "old", "new")
    assert await store.get_playlist(1, "old") is None
    assert len(await store.get_playlist(1, "new")) == 1


async def test_rename_onto_an_existing_name_fails(store):
    await store.create_playlist(1, "a")
    await store.create_playlist(1, "b")
    assert not await store.rename_playlist(1, "a", "b")


async def test_clear_empties_without_deleting(store):
    await store.add_to_playlist(1, "p", [make_track("a"), make_track("b")])
    assert await store.clear_playlist(1, "p") == 2
    assert await store.get_playlist(1, "p") == []


async def test_favourites_are_hidden_from_the_playlist_listing(store):
    await store.add_to_playlist(1, FAVOURITES, [make_track("a")])
    await store.create_playlist(1, "visible")
    assert await store.list_playlists(1) == [("visible", 0)]
    assert len(await store.get_playlist(1, FAVOURITES)) == 1


async def test_playlists_are_scoped_to_their_owner(store):
    await store.add_to_playlist(1, "mine", [make_track("a")])
    assert await store.get_playlist(2, "mine") is None


async def test_settings_and_playlists_persist_across_reconnects(store, tmp_path):
    await store.update_settings(7, prefix="~")
    await store.add_to_playlist(1, "p", [make_track("a")])
    await store.close()

    reopened = Store(store.path)
    await reopened.connect()
    try:
        assert (await reopened.get_settings(7)).prefix == "~"
        assert len(await reopened.get_playlist(1, "p")) == 1
    finally:
        await reopened.close()
