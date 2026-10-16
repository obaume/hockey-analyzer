from __future__ import annotations

from hockey_analyzer.domain.event_navigation import Direction, event_stop

# -- grouping ----------------------------------------------------------------


def test_events_at_the_same_instant_are_one_stop():
    # A line change: one shift_change per player at the same timestamp.
    timestamps = [10_000, 10_000, 10_000, 20_000]

    assert event_stop(timestamps, 5_000, Direction.NEXT) == 10_000
    assert event_stop(timestamps, 10_000, Direction.NEXT) == 20_000


def test_events_500_ms_apart_are_two_stops():
    timestamps = [10_000, 10_500]

    assert event_stop(timestamps, 10_000, Direction.NEXT) == 10_500
    assert event_stop(timestamps, 10_500, Direction.PREVIOUS) == 10_000


def test_timestamps_need_not_arrive_in_order():
    timestamps = [30_000, 10_000, 20_000]

    assert event_stop(timestamps, 15_000, Direction.NEXT) == 20_000
    assert event_stop(timestamps, 15_000, Direction.PREVIOUS) == 10_000
    assert event_stop(timestamps, 15_000, Direction.FIRST) == 10_000
    assert event_stop(timestamps, 15_000, Direction.LAST) == 30_000


# -- next ----------------------------------------------------------------------


def test_next_is_the_first_stop_after_the_playhead():
    assert event_stop([10_000, 20_000, 30_000], 12_000, Direction.NEXT) == 20_000


def test_next_from_a_seek_that_reports_a_few_ms_early_moves_on():
    # Landed on 20 000 but the player reports 19 997.
    assert event_stop([10_000, 20_000, 30_000], 19_997, Direction.NEXT) == 30_000


def test_next_one_frame_before_a_stop_goes_to_it():
    # Paused on 20 000, then stepped one frame (30 fps) back.
    assert event_stop([10_000, 20_000, 30_000], 19_967, Direction.NEXT) == 20_000


def test_next_past_the_last_stop_is_nothing():
    assert event_stop([10_000, 20_000], 25_000, Direction.NEXT) is None
    assert event_stop([10_000, 20_000], 20_000, Direction.NEXT) is None


# -- previous ------------------------------------------------------------------


def test_previous_while_paused_is_the_most_recent_stop_before_the_playhead():
    assert event_stop([10_000, 20_000], 20_400, Direction.PREVIOUS) == 20_000


def test_previous_while_paused_on_a_stop_goes_to_the_one_before():
    assert event_stop([10_000, 20_000], 20_000, Direction.PREVIOUS) == 10_000


def test_previous_one_frame_past_a_stop_goes_back_to_it():
    # Paused on 20 000, then stepped one frame (30 fps) forward.
    assert event_stop([10_000, 20_000], 20_033, Direction.PREVIOUS) == 20_000


def test_previous_while_playing_skips_a_stop_less_than_a_second_back():
    timestamps = [10_000, 20_000]

    assert event_stop(timestamps, 20_400, Direction.PREVIOUS, playing=True) == 10_000


def test_previous_while_playing_goes_back_to_a_stop_a_second_or_more_back():
    timestamps = [10_000, 20_000]

    assert event_stop(timestamps, 21_000, Direction.PREVIOUS, playing=True) == 20_000
    assert event_stop(timestamps, 25_000, Direction.PREVIOUS, playing=True) == 20_000


def test_previous_while_playing_skips_only_the_most_recent_stop():
    # Both stops are within a second; only the latest one is skipped.
    timestamps = [19_500, 20_000]

    assert event_stop(timestamps, 20_300, Direction.PREVIOUS, playing=True) == 19_500


def test_previous_before_the_first_stop_is_nothing():
    assert event_stop([10_000, 20_000], 5_000, Direction.PREVIOUS) is None
    assert event_stop([10_000, 20_000], 10_000, Direction.PREVIOUS) is None


def test_previous_while_playing_just_past_the_only_stop_is_nothing():
    assert event_stop([10_000], 10_400, Direction.PREVIOUS, playing=True) is None


# -- first / last --------------------------------------------------------------


def test_first_and_last_ignore_the_playhead():
    timestamps = [10_000, 20_000, 30_000]

    for playhead in (0, 20_000, 99_000):
        assert event_stop(timestamps, playhead, Direction.FIRST) == 10_000
        assert event_stop(timestamps, playhead, Direction.LAST) == 30_000


# -- no events -----------------------------------------------------------------


def test_no_events_means_nowhere_to_go():
    for direction in Direction:
        assert event_stop([], 10_000, direction) is None
        assert event_stop([], 10_000, direction, playing=True) is None
