"""RAPM stint construction (ticket 20) against hand-built fixture data: the
intervals of constant on-ice skaters (both teams) and strength state that
a future RAPM regression takes as rows -- see CONTEXT.md's Stint entry."""

from __future__ import annotations

from stats_fixtures import AWAY, HOME, GameBuilder

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import Position, ShotOutcome


def _bounds(stints):
    return [(stint.start_ms, stint.end_ms) for stint in stints]


def test_a_home_roster_change_cuts_a_new_stint():
    game = GameBuilder(opponent_shifts_complete=True)
    kim = game.player(HOME, 14)
    lee = game.player(HOME, 15)
    game.shift(HOME, kim, True, at=0)
    game.faceoff(0.0, at=0)
    game.shift(HOME, kim, False, at=10_000)
    game.shift(HOME, lee, True, at=10_000)
    game.period_end(1, at=30_000)

    stints = stats_engine.stints(game.build())

    assert _bounds(stints) == [(0, 10_000), (10_000, 30_000)]
    assert [stint.home_skaters for stint in stints] == [
        frozenset({kim}),
        frozenset({lee}),
    ]
    assert [stint.duration_ms for stint in stints] == [10_000, 20_000]


def test_an_away_roster_change_cuts_a_new_stint_too():
    game = GameBuilder(opponent_shifts_complete=True)
    kim = game.player(HOME, 14)
    rival = game.player(AWAY, 91)
    game.shift(HOME, kim, True, at=0)
    game.faceoff(0.0, at=0)
    game.shift(AWAY, rival, True, at=5_000)
    game.period_end(1, at=20_000)

    stints = stats_engine.stints(game.build())

    assert _bounds(stints) == [(0, 5_000), (5_000, 20_000)]
    assert [stint.home_skaters for stint in stints] == [frozenset({kim})] * 2
    assert [stint.away_skaters for stint in stints] == [
        frozenset(),
        frozenset({rival}),
    ]


def test_a_strength_state_change_cuts_a_new_stint_without_a_shift_change():
    game = GameBuilder(opponent_shifts_complete=True)
    kim = game.player(HOME, 14)
    game.shift(HOME, kim, True, at=0)
    game.faceoff(0.0, at=0)
    # A penalty expiring on the fly: nobody changes, but the next logged
    # event already reads a different strength state.
    game.shot(HOME, strength="4v5", at=4_000)
    game.shot(HOME, strength="5v5", at=6_000)
    game.period_end(1, at=10_000)

    stints = stats_engine.stints(game.build(), strength_state=None)

    assert _bounds(stints) == [(0, 4_000), (4_000, 6_000), (6_000, 10_000)]
    assert [stint.strength_state for stint in stints] == ["5v5", "4v5", "5v5"]
    assert [stint.shot_attempts.for_ for stint in stints] == [0, 1, 1]
    assert [stint.home_skaters for stint in stints] == [frozenset({kim})] * 3


def test_stints_default_to_5v5_like_every_other_on_ice_stat():
    game = GameBuilder(opponent_shifts_complete=True)
    game.faceoff(0.0, at=0)
    game.stoppage(at=10_000)
    game.faceoff(0.0, strength="5v4", at=12_000)
    game.period_end(1, at=20_000)
    data = game.build()

    assert [s.strength_state for s in stats_engine.stints(data)] == ["5v5"]
    assert [
        s.strength_state for s in stats_engine.stints(data, strength_state="5v4")
    ] == ["5v4"]


def test_stint_duration_excludes_stoppage_gaps():
    game = GameBuilder(opponent_shifts_complete=True)
    kim = game.player(HOME, 14)
    lee = game.player(HOME, 15)
    game.shift(HOME, kim, True, at=0)
    game.faceoff(0.0, at=0)
    game.stoppage(at=10_000)  # icing: dead 10-25s, nobody changes
    game.faceoff(0.0, at=25_000)
    game.stoppage(at=40_000)  # dead 40-60s, line change at 50s
    game.shift(HOME, kim, False, at=50_000)
    game.shift(HOME, lee, True, at=50_000)
    game.faceoff(0.0, at=60_000)
    game.period_end(1, at=70_000)

    stints = stats_engine.stints(game.build())

    # The whistle at 10s doesn't cut a stint -- only the line change does
    # -- but neither dead stretch counts toward either stint's duration.
    assert _bounds(stints) == [(0, 50_000), (50_000, 70_000)]
    assert [stint.duration_ms for stint in stints] == [25_000, 10_000]


def test_away_skaters_are_unknown_until_opponent_shifts_are_complete():
    game = GameBuilder(opponent_shifts_complete=False)
    kim = game.player(HOME, 14)
    rival = game.player(AWAY, 91)
    game.shift(HOME, kim, True, at=0)
    game.faceoff(0.0, at=0)
    game.shift(AWAY, rival, True, at=5_000)  # untrusted: doesn't cut
    game.shot(AWAY, at=8_000)
    game.period_end(1, at=20_000)

    stints = stats_engine.stints(game.build())

    assert _bounds(stints) == [(0, 20_000)]
    assert stints[0].home_skaters == frozenset({kim})
    assert stints[0].away_skaters is None
    assert stints[0].shot_attempts.against == 1


def test_goalies_are_not_skaters_and_a_goalie_change_does_not_cut():
    game = GameBuilder(opponent_shifts_complete=True)
    kim = game.player(HOME, 14)
    starter = game.player(HOME, 30, position=Position.GOALIE)
    backup = game.player(HOME, 35, position=Position.GOALIE)
    game.shift(HOME, kim, True, at=0)
    game.shift(HOME, starter, True, at=0)
    game.faceoff(0.0, at=0)
    game.shift(HOME, starter, False, at=5_000)
    game.shift(HOME, backup, True, at=5_000)
    game.period_end(1, at=20_000)

    stints = stats_engine.stints(game.build())

    assert _bounds(stints) == [(0, 20_000)]
    assert stints[0].home_skaters == frozenset({kim})


def test_shot_attempt_differential_is_aggregated_per_stint_over_a_small_game():
    game = GameBuilder(opponent_shifts_complete=True)
    kim = game.player(HOME, 14)
    lee = game.player(HOME, 15)
    rival = game.player(AWAY, 91)
    game.shift(HOME, kim, True, at=0)
    game.shift(AWAY, rival, True, at=0)
    game.faceoff(0.0, at=0)
    game.shot(HOME, ShotOutcome.SAVED, at=3_000)
    game.shot(AWAY, ShotOutcome.BLOCKED, at=5_000)  # still an attempt
    game.shot(HOME, ShotOutcome.MISSED, at=8_000)
    game.shot(HOME, ShotOutcome.GOAL, at=10_000)
    # Logged after the goal at the same instant: the goal stays Kim's.
    game.shift(HOME, kim, False, at=10_000)
    game.shift(HOME, lee, True, at=10_000)
    game.faceoff(0.0, at=12_000)
    game.shot(AWAY, ShotOutcome.SAVED, at=15_000)
    game.shot(AWAY, ShotOutcome.GOAL, at=18_000)
    game.period_end(1, at=20_000)

    stints = stats_engine.stints(game.build())

    assert [
        (stint.home_skaters, stint.away_skaters, stint.duration_ms) for stint in stints
    ] == [
        (frozenset({kim}), frozenset({rival}), 10_000),
        (frozenset({lee}), frozenset({rival}), 6_000),
    ]
    assert [
        (stint.shot_attempts.for_, stint.shot_attempts.against) for stint in stints
    ] == [(3, 1), (0, 2)]
    assert [stint.shot_attempts.differential for stint in stints] == [2, -2]


def test_a_shot_with_no_strength_state_counts_toward_no_stint_like_corsi():
    game = GameBuilder(opponent_shifts_complete=True)
    game.faceoff(0.0, at=0)
    game.shot(HOME, at=2_000)
    game.shot(HOME, strength=None, at=4_000)
    game.period_end(1, at=10_000)
    data = game.build()

    (stint,) = stats_engine.stints(data)

    assert stint.shot_attempts.for_ == 1
    assert stats_engine.team_stats(data, HOME).corsi.for_ == 1
