"""StatsEngine (ticket 18) against hand-built fixture data: transient ORM
objects assembled into a `GameData` in memory, never touching a database,
so each test states exactly the events its expected numbers come from."""

from __future__ import annotations

import pytest
from stats_fixtures import AWAY, HOME, GameBuilder

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import Position, ShotOutcome, ShotType

# -- team Corsi / Fenwick ---------------------------------------------


def test_team_corsi_counts_every_attempt_and_fenwick_excludes_blocked():
    game = GameBuilder()
    game.shot(HOME, ShotOutcome.GOAL)
    game.shot(HOME, ShotOutcome.SAVED)
    game.shot(HOME, ShotOutcome.MISSED)
    game.shot(HOME, ShotOutcome.BLOCKED)
    game.shot(AWAY, ShotOutcome.SAVED)
    game.shot(AWAY, ShotOutcome.BLOCKED)

    stats = stats_engine.team_stats(game.build(), HOME)

    assert (stats.corsi.for_, stats.corsi.against) == (4, 2)
    assert stats.corsi.differential == 2
    assert stats.corsi.percentage == pytest.approx(4 / 6)
    assert (stats.fenwick.for_, stats.fenwick.against) == (3, 1)
    assert stats.fenwick.percentage == pytest.approx(0.75)


def test_team_stats_filter_by_strength_state_defaulting_to_5v5():
    game = GameBuilder()
    game.shot(HOME, strength="5v5")
    game.shot(HOME, strength="5v4")
    game.shot(AWAY, strength="4v5")
    game.shot(AWAY, strength=None)
    data = game.build()

    even = stats_engine.team_stats(data, HOME)
    power_play = stats_engine.team_stats(data, HOME, strength_state="5v4")
    all_situations = stats_engine.team_stats(data, HOME, strength_state=None)

    assert (even.corsi.for_, even.corsi.against) == (1, 0)
    assert (power_play.corsi.for_, power_play.corsi.against) == (1, 0)
    assert (all_situations.corsi.for_, all_situations.corsi.against) == (2, 2)


def test_shot_with_no_team_set_counts_for_nobody():
    game = GameBuilder()
    game.shot(HOME)
    game.shot(AWAY).shot_team_id = None

    stats = stats_engine.team_stats(game.build(), HOME)

    assert (stats.corsi.for_, stats.corsi.against) == (1, 0)


# -- PDO ----------------------------------------------------------------


def test_pdo_is_shooting_plus_save_percentage_over_shots_on_goal_only():
    game = GameBuilder()
    # Home: 1 goal on 4 shots on goal (misses/blocks don't count) = .250
    game.shot(HOME, ShotOutcome.GOAL)
    for _ in range(3):
        game.shot(HOME, ShotOutcome.SAVED)
    game.shot(HOME, ShotOutcome.MISSED)
    game.shot(HOME, ShotOutcome.BLOCKED)
    # Away: 1 goal on 5 shots on goal -> home save% = .800
    game.shot(AWAY, ShotOutcome.GOAL)
    for _ in range(4):
        game.shot(AWAY, ShotOutcome.SAVED)
    game.shot(AWAY, ShotOutcome.MISSED)

    stats = stats_engine.team_stats(game.build(), HOME)

    assert stats.shooting_percentage == pytest.approx(0.25)
    assert stats.save_percentage == pytest.approx(0.8)
    assert stats.pdo == pytest.approx(1050.0)


def test_pdo_is_undefined_without_shots_on_goal_both_ways():
    game = GameBuilder()
    game.shot(HOME, ShotOutcome.GOAL)
    game.shot(AWAY, ShotOutcome.MISSED)

    stats = stats_engine.team_stats(game.build(), HOME)

    assert stats.shooting_percentage == pytest.approx(1.0)
    assert stats.save_percentage is None
    assert stats.pdo is None


# -- individual on-ice Corsi / Fenwick / +/- ---------------------------


def _skater(data, player_id, **kwargs):
    return next(
        stats
        for stats in stats_engine.skater_stats(data, **kwargs).skaters
        if stats.player_id == player_id
    )


def test_on_ice_corsi_counts_only_attempts_during_the_players_shifts():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shot(HOME)  # before Kim's shift
    game.shift(HOME, kim, on=True)
    game.shot(HOME)
    game.shot(AWAY, ShotOutcome.BLOCKED)
    game.shot(AWAY, ShotOutcome.SAVED)
    game.shift(HOME, kim, on=False)
    game.shot(AWAY)  # after it

    kim_stats = _skater(game.build(), kim)

    assert (kim_stats.corsi.for_, kim_stats.corsi.against) == (1, 2)
    assert (kim_stats.fenwick.for_, kim_stats.fenwick.against) == (1, 1)


def test_a_shift_change_at_the_same_instant_as_a_shot_applies_in_log_order():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shift(HOME, kim, on=True, at=1000)
    game.shot(HOME, at=5000)
    game.shift(HOME, kim, on=False, at=5000)  # logged after the shot

    assert _skater(game.build(), kim).corsi.for_ == 1


def test_plus_minus_counts_on_ice_goals_strictly_at_the_active_filter():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shift(HOME, kim, on=True)
    game.shot(HOME, ShotOutcome.GOAL, strength="5v5")
    game.shot(HOME, ShotOutcome.GOAL, strength="5v4")  # power-play goal
    game.shot(AWAY, ShotOutcome.GOAL, strength="5v5")
    game.shot(AWAY, ShotOutcome.GOAL, strength="5v5")
    game.shot(AWAY, ShotOutcome.SAVED, strength="5v5")
    data = game.build()

    assert _skater(data, kim).plus_minus == -1
    assert _skater(data, kim, strength_state="5v4").plus_minus == 1
    assert _skater(data, kim, strength_state=None).plus_minus == 0


def test_goalies_are_not_reported_as_skaters():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    goalie = game.player(HOME, 30, position=Position.GOALIE)

    player_ids = {s.player_id for s in stats_engine.skater_stats(game.build()).skaters}

    assert player_ids == {kim}
    assert goalie not in player_ids


# -- opponent_shifts_complete gating -------------------------------------


def _one_shift_each(game):
    home_player = game.player(HOME, 14)
    away_player = game.player(AWAY, 91)
    game.shift(HOME, home_player, on=True)
    game.shift(AWAY, away_player, on=True)
    game.shot(HOME)
    game.shot(AWAY)
    return home_player, away_player


def test_opponent_skaters_are_excluded_and_reported_until_shifts_are_complete():
    game = GameBuilder(opponent_shifts_complete=False)
    home_player, away_player = _one_shift_each(game)

    report = stats_engine.skater_stats(game.build())

    assert [s.player_id for s in report.skaters] == [home_player]
    assert [(e.player_id, e.team_id) for e in report.excluded] == [(away_player, AWAY)]
    assert "opponent shifts" in report.excluded[0].reason


def test_opponent_skaters_get_individual_stats_once_shifts_are_complete():
    game = GameBuilder(opponent_shifts_complete=True)
    home_player, away_player = _one_shift_each(game)

    report = stats_engine.skater_stats(game.build())

    assert {s.player_id for s in report.skaters} == {home_player, away_player}
    assert report.excluded == []
    away_stats = _skater(game.build(), away_player)
    assert (away_stats.corsi.for_, away_stats.corsi.against) == (1, 1)


def test_team_stats_are_never_gated_by_opponent_shift_completeness():
    game = GameBuilder(opponent_shifts_complete=False)
    _one_shift_each(game)

    away = stats_engine.team_stats(game.build(), AWAY)

    assert (away.corsi.for_, away.corsi.against) == (1, 1)


# -- unknown player references -----------------------------------------


def test_unknown_shift_player_is_reported_per_team_without_touching_known_stats():
    game = GameBuilder(opponent_shifts_complete=True)
    home_player, away_player = _one_shift_each(game)
    game.shift(HOME, None, on=True, unknown=True)
    unset = game.shift(HOME, home_player, on=True)
    unset.shift_on_ice = None  # on/off never filled in: just as unresolved
    game.shot(HOME)
    data = game.build()

    # Whoever the unknown player was is missing that ice time -- reported
    # for that team alone, never folded into a known player's numbers.
    assert stats_engine.unresolved_shift_changes(data) == {HOME: 2}
    assert _skater(data, home_player).corsi.for_ == 2
    assert _skater(data, away_player).corsi.against == 2
    assert stats_engine.team_stats(data, HOME).corsi.for_ == 2


def test_unknown_shooter_does_not_degrade_on_ice_stats():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shift(HOME, kim, on=True)
    game.shot(HOME, ShotOutcome.GOAL)  # the builder's shooters are unknown
    data = game.build()

    assert _skater(data, kim).plus_minus == 1
    assert stats_engine.unresolved_shift_changes(data) == {}


# -- zone starts ---------------------------------------------------------

OFFENSIVE_DOT_FOR_HOME = 69.0  # home shoots toward +x in period 1
DEFENSIVE_DOT_FOR_HOME = -69.0


def _zone_starts(data, player_id, **kwargs):
    return _skater(data, player_id, **kwargs).zone_starts


def test_zone_starts_count_faceoff_anchored_shift_starts_by_zone():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shot(HOME)  # establishes home attacking toward +x
    game.stoppage()
    game.shift(HOME, kim, on=True)
    game.faceoff(OFFENSIVE_DOT_FOR_HOME)
    game.stoppage()
    game.shift(HOME, kim, on=False)
    game.shift(HOME, kim, on=True)
    game.faceoff(OFFENSIVE_DOT_FOR_HOME)
    game.stoppage()
    game.shift(HOME, kim, on=False)
    game.shift(HOME, kim, on=True)
    game.faceoff(DEFENSIVE_DOT_FOR_HOME)

    starts = _zone_starts(game.build(), kim)

    assert (starts.offensive, starts.defensive) == (2, 1)
    assert starts.percentage == pytest.approx(2 / 3)


def test_on_the_fly_shift_starts_are_excluded_not_bucketed():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shot(HOME)
    game.faceoff(OFFENSIVE_DOT_FOR_HOME)  # play is live from here
    game.shift(HOME, kim, on=True)  # on the fly
    game.faceoff(OFFENSIVE_DOT_FOR_HOME)  # (tagged without its stoppage)

    starts = _zone_starts(game.build(), kim)

    assert (starts.offensive, starts.defensive, starts.undetermined) == (0, 0, 0)
    assert starts.percentage is None


def test_neutral_zone_faceoff_starts_are_excluded_from_the_denominator():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shot(HOME)
    game.stoppage()
    game.shift(HOME, kim, on=True)
    game.faceoff(0.0)
    game.stoppage()
    game.shift(HOME, kim, on=False)
    game.shift(HOME, kim, on=True)
    game.faceoff(OFFENSIVE_DOT_FOR_HOME)

    starts = _zone_starts(game.build(), kim)

    assert (starts.offensive, starts.defensive) == (1, 0)
    assert starts.percentage == pytest.approx(1.0)


def test_goal_and_period_start_each_stop_play_for_zone_start_purposes():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.period_start(1)
    game.shift(HOME, kim, on=True)  # opening shift, before the first draw
    game.faceoff(0.0)
    game.shot(HOME, ShotOutcome.GOAL)  # implies a whistle
    game.shift(HOME, kim, on=False)
    game.shift(HOME, kim, on=True)
    game.faceoff(DEFENSIVE_DOT_FOR_HOME)

    starts = _zone_starts(game.build(), kim)

    # Opening draw is neutral (excluded); post-goal draw is defensive.
    assert (starts.offensive, starts.defensive) == (0, 1)


def test_player_changed_off_before_the_draw_gets_no_zone_start():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shot(HOME)
    game.stoppage()
    game.shift(HOME, kim, on=True)
    game.shift(HOME, kim, on=False)
    game.faceoff(OFFENSIVE_DOT_FOR_HOME)

    starts = _zone_starts(game.build(), kim)

    assert (starts.offensive, starts.defensive) == (0, 0)


def test_attacking_direction_is_inferred_per_period_from_shot_locations():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.period_start(1)
    game.shot(HOME, x=60.0)
    game.shift(HOME, kim, on=True)
    game.faceoff(69.0)  # period 1: +x is home's offensive end
    game.period_end(1)
    game.shift(HOME, kim, on=False)
    game.period_start(2)
    game.shot(HOME, x=-60.0)  # ends switched
    game.shot(HOME, x=-40.0)
    game.shot(HOME, x=10.0)  # a stray neutral-zone dump-in
    game.shift(HOME, kim, on=True)
    game.faceoff(69.0)  # period 2: +x is now home's defensive end

    starts = _zone_starts(game.build(), kim)

    assert (starts.offensive, starts.defensive) == (1, 1)


def test_attacking_direction_falls_back_to_opposite_of_the_other_teams():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shot(AWAY, x=-60.0)  # only the away team shot: it attacks -x
    game.stoppage()
    game.shift(HOME, kim, on=True)
    game.faceoff(69.0)

    assert _zone_starts(game.build(), kim).offensive == 1


def test_oriented_shots_turn_every_attempt_toward_its_teams_attacking_end():
    game = GameBuilder()
    game.period_start(1)
    first = game.shot(HOME, x=60.0, y=5.0)
    game.period_end(1)
    game.period_start(2)
    switched = game.shot(HOME, x=-70.0, y=5.0)  # ends switched: rink turned
    second = game.shot(HOME, x=-50.0, y=0.0)
    away = game.shot(AWAY, x=40.0, y=-3.0)  # away attacks +x in period 2

    assert stats_engine.oriented_shots(game.build()) == [
        (first, 60.0, 5.0),
        (switched, 70.0, -5.0),
        (second, 50.0, 0.0),
        (away, 40.0, -3.0),
    ]


def test_oriented_shots_leave_out_attempts_whose_direction_is_undetermined():
    game = GameBuilder()
    game.shot(HOME, x=0.0)  # on the center line: votes for neither end
    game.shot(AWAY).shot_x = None  # no location at all

    assert stats_engine.oriented_shots(game.build()) == []


def test_zone_start_with_no_way_to_tell_direction_is_reported_undetermined():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.stoppage()
    game.shift(HOME, kim, on=True)
    game.faceoff(69.0)  # no shots at all in this period

    starts = _zone_starts(game.build(), kim)

    assert (starts.offensive, starts.defensive, starts.undetermined) == (0, 0, 1)
    assert starts.percentage is None


def test_zone_starts_follow_the_faceoffs_strength_state():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shot(HOME)
    game.stoppage()
    game.shift(HOME, kim, on=True)
    game.faceoff(OFFENSIVE_DOT_FOR_HOME, strength="5v4")
    data = game.build()

    assert _zone_starts(data, kim).offensive == 0
    assert _zone_starts(data, kim, strength_state="5v4").offensive == 1


# -- goalie stats --------------------------------------------------------


def _goalie(data, player_id, **kwargs):
    return next(
        stats
        for stats in stats_engine.goalie_stats(data, **kwargs)
        if stats.player_id == player_id
    )


def test_save_percentage_counts_shots_on_goal_faced_while_in_net():
    game = GameBuilder()
    goalie = game.player(HOME, 30, position=Position.GOALIE)
    game.shot(AWAY, ShotOutcome.SAVED)  # before he's tagged on
    game.shift(HOME, goalie, on=True)
    game.shot(AWAY, ShotOutcome.GOAL)
    for _ in range(3):
        game.shot(AWAY, ShotOutcome.SAVED)
    game.shot(AWAY, ShotOutcome.MISSED)  # never reached him
    game.shot(AWAY, ShotOutcome.BLOCKED)
    game.shot(HOME, ShotOutcome.SAVED)  # his own team's shot
    game.shift(HOME, goalie, on=False)  # pulled
    game.shot(AWAY, ShotOutcome.GOAL)  # empty net

    stats = _goalie(game.build(), goalie)

    assert (stats.shots_against, stats.saves, stats.goals_against) == (4, 3, 1)
    assert stats.save_percentage == pytest.approx(0.75)


def test_goalie_stats_default_to_all_situations_with_5v5_override():
    game = GameBuilder()
    goalie = game.player(HOME, 30, position=Position.GOALIE)
    game.shift(HOME, goalie, on=True)
    game.shot(AWAY, ShotOutcome.SAVED, strength="5v5")
    game.shot(AWAY, ShotOutcome.GOAL, strength="4v5")
    data = game.build()

    assert _goalie(data, goalie).shots_against == 2
    assert _goalie(data, goalie, strength_state="5v5").shots_against == 1


def test_goals_against_average_uses_derived_game_clock_minutes():
    game = GameBuilder()
    goalie = game.player(HOME, 30, position=Position.GOALIE)
    game.period_start(1, at=0)
    game.shift(HOME, goalie, on=True, at=0)
    game.faceoff(0.0, at=0)
    game.shot(AWAY, ShotOutcome.GOAL, at=300_000)  # live 0-300s
    game.faceoff(0.0, at=400_000)
    game.shot(AWAY, ShotOutcome.SAVED, at=500_000)
    game.stoppage(at=1_000_000)  # live 400-1000s
    game.faceoff(0.0, at=1_100_000)
    game.period_end(1, at=1_400_000)  # live 1100-1400s

    stats = _goalie(game.build(), goalie)

    assert stats.minutes_played == pytest.approx(20.0)
    assert stats.goals_against_average == pytest.approx(3.0)


def test_filtered_goals_against_average_uses_minutes_at_that_strength_only():
    game = GameBuilder()
    goalie = game.player(HOME, 30, position=Position.GOALIE)
    game.shift(HOME, goalie, on=True, at=0)
    game.faceoff(0.0, at=0, strength="5v5")
    game.shot(AWAY, ShotOutcome.SAVED, at=300_000, strength="5v5")
    game.stoppage(at=600_000)  # 10 live minutes at 5v5
    game.faceoff(-69.0, at=700_000, strength="4v5")  # home penalty kill
    game.shot(AWAY, ShotOutcome.GOAL, at=1_000_000, strength="4v5")  # 5 min
    game.faceoff(0.0, at=1_100_000, strength="5v5")
    game.period_end(1, at=1_400_000)  # 5 more at 5v5
    data = game.build()

    even = _goalie(data, goalie, strength_state="5v5")
    short_handed = _goalie(data, goalie, strength_state="4v5")

    assert even.minutes_played == pytest.approx(15.0)
    assert even.goals_against_average == pytest.approx(0.0)
    assert short_handed.minutes_played == pytest.approx(5.0)
    assert short_handed.goals_against_average == pytest.approx(12.0)
    assert _goalie(data, goalie).minutes_played == pytest.approx(20.0)


def test_strength_change_during_live_play_splits_the_goalies_minutes():
    game = GameBuilder()
    goalie = game.player(HOME, 30, position=Position.GOALIE)
    game.shift(HOME, goalie, on=True, at=0)
    game.faceoff(69.0, at=0, strength="5v4")
    # Penalty expires on the fly; the next event carries the new state.
    game.shot(HOME, ShotOutcome.MISSED, at=120_000, strength="5v5")
    game.period_end(1, at=600_000)
    data = game.build()

    assert _goalie(data, goalie, strength_state="5v4").minutes_played == (
        pytest.approx(2.0)
    )
    assert _goalie(data, goalie, strength_state="5v5").minutes_played == (
        pytest.approx(8.0)
    )


def test_goals_against_average_is_undefined_with_no_time_in_net():
    game = GameBuilder()
    goalie = game.player(HOME, 30, position=Position.GOALIE)

    stats = _goalie(game.build(), goalie)

    assert stats.minutes_played == 0
    assert stats.goals_against_average is None
    assert stats.save_percentage is None


def test_high_danger_save_percentage_uses_only_high_danger_shots():
    game = GameBuilder()
    goalie = game.player(HOME, 30, position=Position.GOALIE)
    game.shift(HOME, goalie, on=True)
    # Away attacks -x; the net mouth is at x=-85.4 on an IIHF rink.
    game.shot(AWAY, ShotOutcome.GOAL, x=-80.0, y=2.0)  # high danger
    game.shot(AWAY, ShotOutcome.SAVED, x=-78.0, y=-5.0)  # high danger
    game.shot(AWAY, ShotOutcome.SAVED, x=-78.0, y=5.0)  # high danger
    game.shot(AWAY, ShotOutcome.SAVED, x=-40.0, y=0.0)  # from the point

    stats = _goalie(game.build(), goalie)

    assert (stats.high_danger_shots_against, stats.high_danger_saves) == (3, 2)
    assert stats.high_danger_save_percentage == pytest.approx(2 / 3)
    assert stats.save_percentage == pytest.approx(0.75)


def test_goalie_stats_are_computed_for_both_teams_regardless_of_the_flag():
    game = GameBuilder(opponent_shifts_complete=False)
    home_goalie = game.player(HOME, 30, position=Position.GOALIE)
    away_goalie = game.player(AWAY, 35, position=Position.GOALIE)
    game.shift(HOME, home_goalie, on=True)
    game.shift(AWAY, away_goalie, on=True)
    game.shot(HOME, ShotOutcome.SAVED)

    stats = {s.player_id: s for s in stats_engine.goalie_stats(game.build())}

    assert stats[home_goalie].shots_against == 0
    assert stats[away_goalie].shots_against == 1


# -- shot-quality breakdown ------------------------------------------------


def test_shot_quality_breaks_a_teams_attempts_down_by_type_context_and_danger():
    game = GameBuilder()
    game.shot(HOME, shot_type=ShotType.WRIST, x=80.0, rush=True)  # high danger
    game.shot(HOME, shot_type=ShotType.WRIST, x=50.0, screened=True)
    game.shot(HOME, shot_type=ShotType.SLAP, x=40.0, rebound=True, one_timer=True)
    no_location = game.shot(HOME, shot_type=ShotType.TIP, rebound=True)
    no_location.shot_x = no_location.shot_y = None
    game.shot(AWAY, shot_type=ShotType.BACKHAND)
    game.shot(HOME, shot_type=ShotType.SNAP, strength="5v4")  # filtered out

    quality = stats_engine.team_stats(game.build(), HOME).shot_quality

    assert quality.attempts == 4
    assert quality.by_type == {ShotType.WRIST: 2, ShotType.SLAP: 1, ShotType.TIP: 1}
    assert quality.by_context == {
        "rush": 1,
        "rebound": 2,
        "screened": 1,
        "one_timer": 1,
    }
    # Share is over attempts with a clicked location only.
    assert (quality.high_danger, quality.located) == (1, 3)
    assert quality.high_danger_share == pytest.approx(1 / 3)
