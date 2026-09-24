"""Game clock derivation (see CONTEXT.md's Game clock entry) against
hand-built transient events: period number plus time remaining, where
only live play -- faceoff to the next event that stops play -- runs the
clock down."""

from __future__ import annotations

from hockey_analyzer.domain.enums import ShotOutcome, ShotType
from hockey_analyzer.domain.game_clock import (
    PERIOD_LENGTH_MS,
    GameClock,
    game_clocks,
)
from hockey_analyzer.domain.models import (
    Faceoff,
    Penalty,
    PeriodEnd,
    PeriodStart,
    ShiftChange,
    ShotAttempt,
    Stoppage,
)

SECOND = 1000


def _events(*pairs):
    """(seconds, event) pairs -> events with ids in the order given."""
    events = []
    for index, (seconds, event) in enumerate(pairs, start=1):
        event.id = index
        event.video_timestamp = seconds * SECOND
        events.append(event)
    return events


def _faceoff():
    return Faceoff(
        faceoff_participant_a_unknown=True, faceoff_participant_b_unknown=True
    )


def _shot(outcome=ShotOutcome.SAVED):
    return ShotAttempt(
        shot_outcome=outcome, shot_type=ShotType.WRIST, shooter_unknown=True
    )


def _remaining(seconds_elapsed):
    return PERIOD_LENGTH_MS - seconds_elapsed * SECOND


def test_clock_runs_from_the_opening_faceoff():
    events = _events(
        (10, PeriodStart(period_number=1)),
        (30, _faceoff()),
        (95, _shot()),
    )

    clocks = game_clocks(events)

    assert clocks[1] == GameClock(period=1, remaining_ms=PERIOD_LENGTH_MS)
    assert clocks[2] == GameClock(period=1, remaining_ms=PERIOD_LENGTH_MS)
    assert clocks[3] == GameClock(period=1, remaining_ms=_remaining(65))


def test_stoppage_gaps_do_not_run_the_clock():
    events = _events(
        (0, PeriodStart(period_number=1)),
        (0, _faceoff()),
        (60, Stoppage()),
        (200, ShiftChange(shift_player_unknown=True, shift_on_ice=True)),
        (240, _faceoff()),
        (250, _shot()),
    )

    clocks = game_clocks(events)

    # Stopped at 60s of play; the dead 180s don't count.
    assert clocks[3].remaining_ms == _remaining(60)
    assert clocks[4].remaining_ms == _remaining(60)
    assert clocks[6].remaining_ms == _remaining(70)


def test_goals_and_penalties_stop_the_clock_without_a_stoppage_event():
    events = _events(
        (0, PeriodStart(period_number=1)),
        (0, _faceoff()),
        (100, _shot(ShotOutcome.GOAL)),
        (150, _faceoff()),
        (160, Penalty(penalty_player_unknown=True)),
        (400, _faceoff()),
        (410, _shot()),
    )

    clocks = game_clocks(events)

    assert clocks[7].remaining_ms == _remaining(100 + 10 + 10)


def test_each_period_starts_a_fresh_clock():
    events = _events(
        (0, PeriodStart(period_number=1)),
        (0, _faceoff()),
        (1200, PeriodEnd(period_number=1)),
        (2000, PeriodStart(period_number=2)),
        (2000, _faceoff()),
        (2045, _shot()),
    )

    clocks = game_clocks(events)

    assert clocks[3] == GameClock(period=1, remaining_ms=0)
    assert clocks[6] == GameClock(period=2, remaining_ms=_remaining(45))


def test_period_without_a_number_is_counted_from_period_starts():
    events = _events(
        (0, PeriodStart()),
        (100, PeriodStart()),
        (100, _faceoff()),
        (110, _shot()),
    )

    assert game_clocks(events)[4].period == 2


def test_remaining_time_never_goes_negative():
    events = _events(
        (0, PeriodStart(period_number=1)),
        (0, _faceoff()),
        (1500, _shot()),
    )

    assert game_clocks(events)[3].remaining_ms == 0


def test_events_before_any_period_start_have_no_game_clock():
    events = _events((5, _faceoff()), (10, PeriodStart(period_number=1)))

    clocks = game_clocks(events)

    assert 1 not in clocks
    assert 2 in clocks


def test_events_are_read_in_video_order_whatever_order_they_are_given_in():
    events = _events(
        (0, PeriodStart(period_number=1)),
        (0, _faceoff()),
        (30, _shot()),
    )

    assert game_clocks(list(reversed(events)))[3].remaining_ms == _remaining(30)
