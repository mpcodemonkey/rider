"""Queue mechanics and loop-mode behaviour, exercised without a voice connection."""

from __future__ import annotations

import pytest

from jockiefluxer.player import GuildPlayer, LoopMode
from jockiefluxer.track import Track


class FakeBot:
    """Just enough of MusicBot for the queue logic to run."""

    def __init__(self, config):
        self.config = config
        self.messages: list[str] = []
        self.sources = None

    async def send_plain(self, channel_id, message):
        self.messages.append(message)

    async def announce_now_playing(self, player, track):
        pass


def make_track(title: str, *, url: str | None = None, requester: int = 1) -> Track:
    return Track(
        title=title,
        url=url or f"https://example.com/{title}",
        duration=180_000,
        requester_id=requester,
        requester_name=f"user{requester}",
        resolve_query=url or f"https://example.com/{title}",
    )


@pytest.fixture
def player(config) -> GuildPlayer:
    return GuildPlayer(FakeBot(config), guild_id=1, config=config)


def titles(player: GuildPlayer) -> list[str]:
    return [track.title for track in player.queue]


# ---------------------------------------------------------------------------
# Queue mutation
# ---------------------------------------------------------------------------
def test_enqueue_appends_in_order(player):
    added = player.enqueue([make_track("a"), make_track("b")])
    assert added == 2
    assert titles(player) == ["a", "b"]


def test_enqueue_at_front_preserves_relative_order(player):
    player.enqueue([make_track("a"), make_track("b")])
    player.enqueue([make_track("x"), make_track("y")], at_front=True)
    assert titles(player) == ["x", "y", "a", "b"]


def test_enqueue_respects_the_queue_limit(player):
    player.config.max_queue_size = 3
    added = player.enqueue([make_track(str(index)) for index in range(10)])
    assert added == 3
    assert len(player.queue) == 3


def test_remove_takes_multiple_indices_at_once(player):
    player.enqueue([make_track(letter) for letter in "abcde"])
    removed = player.remove([1, 3])
    assert [track.title for track in removed] == ["b", "d"]
    assert titles(player) == ["a", "c", "e"]


def test_move_reorders_the_queue(player):
    player.enqueue([make_track(letter) for letter in "abcd"])
    moved = player.move(3, 0)
    assert moved.title == "d"
    assert titles(player) == ["d", "a", "b", "c"]


def test_move_returns_none_for_a_bad_index(player):
    player.enqueue([make_track("a")])
    assert player.move(5, 0) is None
    assert titles(player) == ["a"]


def test_move_clamps_an_out_of_range_destination(player):
    player.enqueue([make_track(letter) for letter in "abc"])
    player.move(0, 99)
    assert titles(player) == ["b", "c", "a"]


def test_deduplicate_keeps_the_first_occurrence(player):
    player.enqueue(
        [
            make_track("a", url="https://x/1"),
            make_track("b", url="https://x/2"),
            make_track("c", url="https://x/1"),
        ]
    )
    assert player.deduplicate() == 1
    assert titles(player) == ["a", "b"]


def test_remove_by_user_only_drops_that_users_tracks(player):
    player.enqueue(
        [
            make_track("a", requester=1),
            make_track("b", requester=2),
            make_track("c", requester=1),
        ]
    )
    assert player.remove_by_user(1) == 2
    assert titles(player) == ["b"]


def test_shuffle_keeps_every_track(player):
    player.enqueue([make_track(str(index)) for index in range(20)])
    before = sorted(titles(player))
    player.shuffle()
    assert sorted(titles(player)) == before


def test_queue_duration_sums_track_lengths(player):
    player.enqueue([make_track("a"), make_track("b")])
    assert player.queue_duration == 360_000


# ---------------------------------------------------------------------------
# Loop modes
# ---------------------------------------------------------------------------
def test_next_track_pops_in_order_and_records_history(player):
    player.enqueue([make_track("a"), make_track("b")])

    first = player._next_track()
    assert first.title == "a"
    player.current = first

    second = player._next_track()
    assert second.title == "b"
    assert [track.title for track in player.history] == ["a"]


def test_loop_track_replays_the_same_track(player):
    player.enqueue([make_track("a"), make_track("b")])
    player.loop_mode = LoopMode.TRACK

    player.current = player._next_track()
    assert player.current.title == "a"

    repeated = player._next_track()
    assert repeated.title == "a"
    # Looping must not consume the rest of the queue.
    assert titles(player) == ["b"]


def test_skipping_escapes_track_loop(player):
    """Skip should advance even while a single track is looping."""
    player.enqueue([make_track("a"), make_track("b")])
    player.loop_mode = LoopMode.TRACK
    player.current = player._next_track()

    player._skipping = True
    assert player._next_track().title == "b"


def test_loop_queue_sends_finished_tracks_to_the_back(player):
    player.enqueue([make_track("a"), make_track("b")])
    player.loop_mode = LoopMode.QUEUE

    player.current = player._next_track()  # a
    assert player._next_track().title == "b"
    # "a" went to the back rather than being discarded.
    assert titles(player) == ["a"]


def test_next_track_returns_none_when_exhausted(player):
    player.enqueue([make_track("a")])
    player.current = player._next_track()
    assert player._next_track() is None


def test_forced_next_takes_priority_and_clears_itself(player):
    player.enqueue([make_track("a")])
    forced = make_track("forced")
    player._forced_next = forced

    assert player._next_track() is forced
    assert player._forced_next is None
    # The queued track is untouched and plays next.
    assert player._next_track().title == "a"


def test_forced_next_resets_the_skip_flag(player):
    """A previous() jump must not leave the skip flag set for the next track."""
    player._forced_next = make_track("forced")
    player._skipping = True
    player._next_track()
    assert player._skipping is False


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------
def test_set_volume_clamps_to_the_configured_maximum(player):
    player.config.max_volume = 200
    assert player.set_volume(500) == 200
    assert player.set_volume(-10) == 0
    assert player.set_volume(75) == 75
