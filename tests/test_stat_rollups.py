"""StatsEngine rollups (ticket 19): per-position aggregates, line/unit
stats over a unit's full simultaneous on-ice overlap, and sum-then-compute
aggregation over a hand-picked set of games -- all against hand-built
`GameData`, like ticket 18's own engine tests."""

from __future__ import annotations

import pytest
from stats_fixtures import AWAY, HOME, GameBuilder

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import Position, ShotOutcome, UnitType

# -- position rollups ---------------------------------------------------


def test_position_rollup_pools_each_teams_skaters_by_position():
    game = GameBuilder()
    d1 = game.player(HOME, 2, position=Position.DEFENSE)
    d2 = game.player(HOME, 4, position=Position.DEFENSE)
    center = game.player(HOME, 14, position=Position.CENTER)
    game.shift(HOME, d1, on=True)
    game.shift(HOME, center, on=True)
    game.shot(HOME, ShotOutcome.GOAL)  # d1, center on
    game.shot(AWAY)  # d1, center on
    game.shift(HOME, d1, on=False)
    game.shift(HOME, d2, on=True)
    game.shot(HOME)  # d2, center on
    report = stats_engine.skater_stats(game.build())

    rollup = {
        (stats.team_id, stats.position): stats
        for stats in stats_engine.position_rollup(report)
    }

    defense = rollup[(HOME, Position.DEFENSE)]
    assert defense.skaters == 2
    # d1: 1 CF / 1 CA, d2: 1 CF / 0 CA -- pooled, then the % computed.
    assert (defense.corsi.for_, defense.corsi.against) == (2, 1)
    assert defense.corsi.percentage == pytest.approx(2 / 3)
    assert defense.plus_minus == 1
    assert defense.per_skater(defense.corsi.for_) == pytest.approx(1.0)
    center_stats = rollup[(HOME, Position.CENTER)]
    assert center_stats.skaters == 1
    assert (center_stats.corsi.for_, center_stats.corsi.against) == (2, 1)
    assert set(rollup) == {(HOME, Position.DEFENSE), (HOME, Position.CENTER)}


def test_position_rollup_groups_skaters_with_no_position_set_under_none():
    game = GameBuilder()
    game.player(HOME, 14, position=None)

    (rollup,) = stats_engine.position_rollup(stats_engine.skater_stats(game.build()))

    assert (rollup.position, rollup.skaters) == (None, 1)


def test_position_rollup_keeps_teams_apart_and_leaves_out_excluded_opponents():
    game = GameBuilder(opponent_shifts_complete=True)
    game.player(HOME, 14, position=Position.CENTER)
    game.player(AWAY, 91, position=Position.CENTER)
    data = game.build()

    flagged = stats_engine.position_rollup(stats_engine.skater_stats(data))
    data.game.opponent_shifts_complete = False
    unflagged = stats_engine.position_rollup(stats_engine.skater_stats(data))

    assert [(stats.team_id, stats.skaters) for stats in flagged] == [
        (HOME, 1),
        (AWAY, 1),
    ]
    assert [(stats.team_id, stats.skaters) for stats in unflagged] == [(HOME, 1)]


# -- line / unit stats --------------------------------------------------


def _unit(report, team_id, unit_type, number):
    return next(
        stats
        for stats in report.units
        if (stats.team_id, stats.unit_type, stats.unit_number)
        == (team_id, unit_type, number)
    )


def test_line_stats_count_only_the_members_simultaneous_overlap():
    game = GameBuilder()
    c = game.player(HOME, 14)
    lw = game.player(HOME, 17, position=Position.LEFT_WING)
    rw = game.player(HOME, 19, position=Position.RIGHT_WING)
    game.unit(HOME, UnitType.FORWARD_LINE, 1, c, lw, rw)
    game.faceoff(0.0, at=0)
    game.shift(HOME, c, on=True, at=1000)
    game.shift(HOME, lw, on=True, at=1000)
    game.shot(HOME, at=2000)  # rw not on yet -- not a line shot
    game.shift(HOME, rw, on=True, at=3000)
    game.shot(HOME, at=4000)  # all three on
    game.shot(AWAY, ShotOutcome.GOAL, at=5000)  # all three on
    game.shift(HOME, c, on=False, at=6000)
    game.shot(HOME, at=7000)  # c off -- not a line shot
    game.faceoff(0.0, at=8000)
    game.shift(HOME, lw, on=False, at=9000)
    game.shift(HOME, rw, on=False, at=9000)

    report = stats_engine.unit_stats(game.build())

    line = _unit(report, HOME, UnitType.FORWARD_LINE, 1)
    assert line.player_ids == frozenset({c, lw, rw})
    assert (line.corsi.for_, line.corsi.against) == (1, 1)
    assert (line.goals.for_, line.goals.against) == (0, 1)
    # Together 3000-6000 of footage, but the goal at 5000 stopped play
    # until the 8000 faceoff: 2000 ms of live game clock.
    assert line.time_together_ms == 2000


def test_unit_stats_default_to_each_unit_types_natural_strength():
    game = GameBuilder()
    home_pp = game.player(HOME, 14)
    away_pk = game.player(AWAY, 91)
    game.unit(HOME, UnitType.POWER_PLAY, 1, home_pp)
    game.unit(HOME, UnitType.FORWARD_LINE, 1, home_pp)
    game.unit(HOME, UnitType.PENALTY_KILL, 1, home_pp)
    game.unit(AWAY, UnitType.PENALTY_KILL, 1, away_pk)
    game.shift(HOME, home_pp, on=True)
    game.shift(AWAY, away_pk, on=True)
    game.shot(HOME, strength="5v4")  # home PP, away PK
    game.shot(HOME, strength="5v3")  # home PP, away PK
    game.shot(HOME, strength="4v5")  # home PK
    game.shot(HOME, strength="5v5")
    data = game.build()
    data.game.opponent_shifts_complete = True

    report = stats_engine.unit_stats(data)
    all_situations = stats_engine.unit_stats(data, strength_state=None)

    assert _unit(report, HOME, UnitType.POWER_PLAY, 1).corsi.for_ == 2
    assert _unit(report, HOME, UnitType.PENALTY_KILL, 1).corsi.for_ == 1
    assert _unit(report, HOME, UnitType.FORWARD_LINE, 1).corsi.for_ == 1
    assert _unit(report, AWAY, UnitType.PENALTY_KILL, 1).corsi.against == 2
    assert _unit(all_situations, HOME, UnitType.POWER_PLAY, 1).corsi.for_ == 4


def test_opponent_units_are_excluded_and_reported_until_shifts_are_complete():
    game = GameBuilder(opponent_shifts_complete=False)
    home_player = game.player(HOME, 14)
    away_player = game.player(AWAY, 91)
    game.unit(HOME, UnitType.FORWARD_LINE, 1, home_player)
    game.unit(AWAY, UnitType.FORWARD_LINE, 1, away_player)

    report = stats_engine.unit_stats(game.build())

    assert [stats.team_id for stats in report.units] == [HOME]
    (excluded,) = report.excluded
    assert (excluded.team_id, excluded.unit_type, excluded.unit_number) == (
        AWAY,
        UnitType.FORWARD_LINE,
        1,
    )
    assert excluded.reason == stats_engine.OPPONENT_SHIFTS_INCOMPLETE


# -- multi-game aggregation ---------------------------------------------

KIM = 500
RIVAL = 600
RIVAL_GOALIE = 601


def _game(game_id, *, flagged, home=HOME, away=AWAY):
    """Team HOME's #14 Kim vs. team AWAY's #91 Rival (goalie #35), all on
    for the whole game; `home`/`away` pick which team is on which side."""
    game = GameBuilder(
        game_id=game_id, opponent_shifts_complete=flagged, home=home, away=away
    )
    game.player(HOME, 14, player_id=KIM)
    game.player(AWAY, 91, player_id=RIVAL)
    game.player(AWAY, 35, player_id=RIVAL_GOALIE, position=Position.GOALIE)
    game.unit(HOME, UnitType.FORWARD_LINE, 1, KIM)
    game.unit(AWAY, UnitType.FORWARD_LINE, 1, RIVAL)
    game.shift(HOME, KIM, on=True)
    game.shift(AWAY, RIVAL, on=True)
    game.shift(AWAY, RIVAL_GOALIE, on=True)
    return game


def test_multi_game_team_stats_sum_counts_then_compute_percentages():
    first = _game(1, flagged=True)
    first.shot(HOME)
    second = _game(2, flagged=True)
    second.shot(HOME)
    for _ in range(3):
        second.shot(AWAY)

    combined = stats_engine.combined_team_stats([first.build(), second.build()], HOME)

    assert (combined.corsi.for_, combined.corsi.against) == (2, 3)
    # 2 / 5 from the summed counts -- not the 62.5% mean of 100% and 25%.
    assert combined.corsi.percentage == pytest.approx(0.4)


def test_multi_game_team_stats_skip_games_the_team_did_not_play():
    first = _game(1, flagged=True)
    first.shot(HOME)
    other = _game(2, flagged=True, home=3, away=4)
    other.shot(3)

    combined = stats_engine.combined_team_stats([first.build(), other.build()], HOME)

    assert (combined.corsi.for_, combined.corsi.against) == (1, 0)


def test_mixed_flag_aggregate_computes_on_ice_stats_over_the_flagged_subset():
    flagged = _game(1, flagged=True)
    flagged.shot(AWAY)
    unflagged = _game(2, flagged=False)
    unflagged.shot(AWAY)
    unflagged.shot(AWAY)
    games = [flagged.build(), unflagged.build()]

    skaters = stats_engine.combined_skater_stats(games)
    units = stats_engine.combined_unit_stats(games)
    coverage = stats_engine.on_ice_coverage(games)

    by_player = {stats.player_id: stats for stats in skaters.skaters}
    assert by_player[RIVAL].corsi.for_ == 1  # game 1 only
    assert by_player[KIM].corsi.against == 3  # home: both games
    assert skaters.excluded == []  # Rival counted somewhere, so not excluded
    (rival_line,) = [stats for stats in units.units if stats.team_id == AWAY]
    assert rival_line.corsi.for_ == 1
    assert (coverage[AWAY].included, coverage[AWAY].excluded) == ((1,), (2,))
    assert (coverage[HOME].included, coverage[HOME].excluded) == ((1, 2), ())


def test_a_skater_excluded_in_every_selected_game_is_reported_once():
    games = [_game(1, flagged=False).build(), _game(2, flagged=False).build()]

    report = stats_engine.combined_skater_stats(games)

    assert [stats.player_id for stats in report.skaters] == [KIM]
    assert [excluded.player_id for excluded in report.excluded] == [RIVAL]


def test_multi_game_goalie_stats_sum_across_games():
    first = _game(1, flagged=False)
    first.shot(HOME, ShotOutcome.GOAL)
    second = _game(2, flagged=False)
    for _ in range(3):
        second.shot(HOME)

    (goalie,) = stats_engine.combined_goalie_stats([first.build(), second.build()])

    assert (goalie.shots_against, goalie.goals_against) == (4, 1)
    assert goalie.save_percentage == pytest.approx(0.75)


def test_coverage_follows_the_team_across_home_and_away_games():
    as_home = _game(1, flagged=False, home=AWAY, away=HOME)
    as_away = _game(2, flagged=False)

    coverage = stats_engine.on_ice_coverage([as_home.build(), as_away.build()])

    assert (coverage[AWAY].included, coverage[AWAY].excluded) == ((1,), (2,))
    assert (coverage[HOME].included, coverage[HOME].excluded) == ((2,), (1,))
