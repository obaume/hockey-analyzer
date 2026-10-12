"""Faceoff win % (FO%) against hand-built fixture data: per team, per
player, and by zone (see CONTEXT.md's Decided faceoff and Faceoff win %
entries)."""

from __future__ import annotations

import pytest
from stats_fixtures import AWAY, HOME, GameBuilder

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import Side


def _team(report, team_id):
    return next(team for team in report.teams if team.team_id == team_id)


def test_team_faceoff_win_percentage_counts_every_decided_faceoff():
    game = GameBuilder()
    game.faceoff(0.0, winner=Side.HOME)
    game.faceoff(0.0, winner=Side.HOME)
    game.faceoff(0.0, winner=Side.AWAY)

    report = stats_engine.faceoff_stats(game.build())

    home, away = _team(report, HOME).faceoffs, _team(report, AWAY).faceoffs
    assert (home.won, home.lost) == (2, 1)
    assert (away.won, away.lost) == (1, 2)
    assert home.percentage == pytest.approx(2 / 3)
    assert away.percentage == pytest.approx(1 / 3)


def test_undecided_faceoffs_are_counted_alongside_never_as_a_loss():
    game = GameBuilder()
    game.faceoff(0.0, winner=Side.HOME)
    game.faceoff(0.0)  # no winner recorded

    report = stats_engine.faceoff_stats(game.build())

    home, away = _team(report, HOME).faceoffs, _team(report, AWAY).faceoffs
    assert (home.won, home.lost, home.undecided) == (1, 0, 1)
    assert (away.won, away.lost, away.undecided) == (0, 1, 1)
    assert home.percentage == pytest.approx(1.0)


def _player(report, player_id):
    return next(player for player in report.players if player.player_id == player_id)


def _won_lost(record):
    return record.won, record.lost


def test_player_faceoff_win_percentage_counts_the_draws_they_took():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    lee = game.player(AWAY, 8)
    game.faceoff(0.0, home=kim, away=lee, winner=Side.HOME)
    game.faceoff(0.0, home=kim, away=lee, winner=Side.AWAY)
    game.faceoff(0.0, home=kim, away=lee, winner=Side.HOME)
    game.faceoff(0.0, home=kim, away=lee)  # undecided

    report = stats_engine.faceoff_stats(game.build())

    kim_stats, lee_stats = _player(report, kim), _player(report, lee)
    assert (kim_stats.team_id, kim_stats.jersey_number) == (HOME, 14)
    assert (lee_stats.team_id, lee_stats.jersey_number) == (AWAY, 8)
    assert (kim_stats.faceoffs.won, kim_stats.faceoffs.lost) == (2, 1)
    assert kim_stats.faceoffs.undecided == 1
    assert lee_stats.faceoffs.percentage == pytest.approx(1 / 3)


def test_unknown_opponent_does_not_matter_but_unknown_own_participant_is_unattributed():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.faceoff(0.0, home=kim, winner=Side.HOME)  # away taker unknown
    game.faceoff(0.0, home=kim, winner=Side.AWAY)
    game.faceoff(0.0, winner=Side.HOME)  # home taker unknown too

    report = stats_engine.faceoff_stats(game.build())

    assert _won_lost(_player(report, kim).faceoffs) == (1, 1)
    assert [player.player_id for player in report.players] == [kim]
    assert _team(report, HOME).unattributed == 1
    assert _team(report, AWAY).unattributed == 3
    # The team's own percentage still counts every decided draw.
    assert _team(report, HOME).faceoffs.won == 2


OFFENSIVE_DOT_FOR_HOME = 69.0  # once a home shot at +x sets the direction


def test_faceoffs_by_zone_follow_each_sides_attacking_direction():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    lee = game.player(AWAY, 8)
    game.period_start(1)
    game.shot(HOME, x=60.0)  # home attacks +x this period, away -x
    game.faceoff(OFFENSIVE_DOT_FOR_HOME, home=kim, away=lee, winner=Side.HOME)
    game.faceoff(OFFENSIVE_DOT_FOR_HOME, home=kim, away=lee, winner=Side.AWAY)
    game.faceoff(-OFFENSIVE_DOT_FOR_HOME, home=kim, away=lee, winner=Side.HOME)
    game.faceoff(0.0, home=kim, away=lee, winner=Side.AWAY)

    report = stats_engine.faceoff_stats(game.build())

    kim_stats, lee_stats = _player(report, kim).faceoffs, _player(report, lee).faceoffs
    assert _won_lost(kim_stats.offensive) == (1, 1)
    assert _won_lost(kim_stats.defensive) == (1, 0)
    assert _won_lost(kim_stats.neutral) == (0, 1)
    assert _won_lost(lee_stats.offensive) == (0, 1)
    assert _won_lost(lee_stats.defensive) == (1, 1)
    assert _won_lost(lee_stats.neutral) == (1, 0)
    assert _won_lost(_team(report, HOME).faceoffs.offensive) == (1, 1)
    assert kim_stats.offensive.percentage == pytest.approx(0.5)


def test_unlocated_or_directionless_draws_are_undetermined_but_still_count():
    game = GameBuilder()
    game.period_start(1)
    game.faceoff(OFFENSIVE_DOT_FOR_HOME, winner=Side.HOME)  # no shots: no direction
    game.period_start(2)
    game.shot(HOME, x=60.0)
    game.faceoff(None, winner=Side.AWAY)  # never located

    home = _team(stats_engine.faceoff_stats(game.build()), HOME).faceoffs

    assert _won_lost(home.undetermined) == (1, 1)
    assert _won_lost(home.offensive) == (0, 0)
    assert (home.won, home.lost) == (1, 1)


def test_faceoff_stats_default_to_all_situations_with_5v5_override():
    game = GameBuilder()
    game.faceoff(0.0, winner=Side.HOME)
    game.faceoff(0.0, winner=Side.HOME, strength="5v4")
    data = game.build()

    assert _team(stats_engine.faceoff_stats(data), HOME).faceoffs.won == 2
    even = stats_engine.faceoff_stats(data, strength_state="5v5")
    assert _team(even, HOME).faceoffs.won == 1


def test_combined_faceoff_stats_sum_counts_across_games_then_compute():
    first = GameBuilder(game_id=1)
    kim = first.player(HOME, 14, player_id=500)
    first.faceoff(0.0, home=kim, winner=Side.HOME)
    second = GameBuilder(game_id=2)
    second.player(HOME, 41, player_id=500)  # new number, same player
    for winner in (Side.AWAY, Side.AWAY, Side.HOME):
        second.faceoff(0.0, home=kim, winner=winner)
    second.faceoff(0.0, winner=Side.HOME)  # unknown home taker

    report = stats_engine.combined_faceoff_stats([first.build(), second.build()])

    kim_stats = _player(report, kim)
    assert kim_stats.jersey_number == 41
    assert (kim_stats.faceoffs.won, kim_stats.faceoffs.lost) == (2, 2)
    assert kim_stats.faceoffs.percentage == pytest.approx(0.5)  # not avg(100%, 33%)
    home = _team(report, HOME)
    assert (home.faceoffs.won, home.faceoffs.lost, home.unattributed) == (3, 2, 1)


def test_faceoff_stats_are_not_gated_by_opponent_shift_completeness():
    game = GameBuilder(opponent_shifts_complete=False)
    lee = game.player(AWAY, 8)
    game.faceoff(0.0, away=lee, winner=Side.AWAY)

    report = stats_engine.faceoff_stats(game.build())

    assert _player(report, lee).faceoffs.won == 1
