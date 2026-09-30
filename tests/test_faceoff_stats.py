"""StatsEngine faceoff win % (ticket 66): per player and per team, over
decided faceoffs only, with a by-zone breakdown -- see CONTEXT.md's
Decided faceoff and Faceoff win % (FO%) entries."""

from __future__ import annotations

import pytest
from stats_fixtures import AWAY, HOME, GameBuilder

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import Side
from hockey_analyzer.domain.stats_engine import FaceoffCounts

# IIHF blue lines sit at x = +/-24.6.
OFFENSIVE_FOR_HOME = 60.0  # home attacks +x in the fixtures' period 1
NEUTRAL = 0.0


def _player(data, player_id, **kwargs):
    return next(
        stats
        for stats in stats_engine.player_faceoff_stats(data, **kwargs)
        if stats.player_id == player_id
    )


def test_player_faceoff_win_pct_counts_decided_draws_only():
    game = GameBuilder()
    center = game.player(HOME, 14)
    rival = game.player(AWAY, 91)
    for winner in (Side.HOME, Side.HOME, Side.HOME, Side.AWAY, None):
        game.faceoff(NEUTRAL, home=center, away=rival, winner=winner)

    faceoffs = _player(game.build(), center).faceoffs

    assert (faceoffs.won, faceoffs.decided, faceoffs.undecided) == (3, 4, 1)
    assert faceoffs.percentage == pytest.approx(0.75)


def test_a_player_who_took_no_decided_draw_has_no_percentage():
    game = GameBuilder()
    center = game.player(HOME, 14)
    game.faceoff(NEUTRAL, home=center)

    faceoffs = _player(game.build(), center).faceoffs

    assert (faceoffs.decided, faceoffs.undecided) == (0, 1)
    assert faceoffs.percentage is None


def test_only_players_who_took_a_draw_are_listed():
    game = GameBuilder()
    center = game.player(HOME, 14)
    game.player(HOME, 17)
    game.faceoff(NEUTRAL, home=center, winner=Side.HOME)

    listed = stats_engine.player_faceoff_stats(game.build())

    assert [stats.player_id for stats in listed] == [center]
    assert (listed[0].team_id, listed[0].jersey_number) == (HOME, 14)


def test_an_unknown_opposing_participant_still_counts_for_the_player():
    game = GameBuilder()
    center = game.player(HOME, 14)
    game.faceoff(NEUTRAL, home=center, away=None, winner=Side.AWAY)

    faceoffs = _player(game.build(), center).faceoffs

    assert (faceoffs.won, faceoffs.decided) == (0, 1)


def test_opposing_players_are_computed_without_opponent_shifts_complete():
    game = GameBuilder(opponent_shifts_complete=False)
    rival = game.player(AWAY, 91)
    game.faceoff(NEUTRAL, away=rival, winner=Side.AWAY)
    game.faceoff(NEUTRAL, away=rival, winner=Side.HOME)

    faceoffs = _player(game.build(), rival).faceoffs

    assert faceoffs.percentage == pytest.approx(0.5)


def test_team_faceoff_win_pct_covers_every_decided_draw_whoever_took_it():
    game = GameBuilder()
    center = game.player(HOME, 14)
    game.faceoff(NEUTRAL, home=center, winner=Side.HOME)
    game.faceoff(NEUTRAL, home=None, winner=Side.HOME)
    game.faceoff(NEUTRAL, home=center, winner=Side.AWAY)
    game.faceoff(NEUTRAL, home=center)
    data = game.build()

    home = stats_engine.team_faceoff_stats(data, HOME)
    away = stats_engine.team_faceoff_stats(data, AWAY)

    assert (home.faceoffs.won, home.faceoffs.decided) == (2, 3)
    assert (away.faceoffs.won, away.faceoffs.decided) == (1, 3)
    assert home.faceoffs.undecided == away.faceoffs.undecided == 1
    assert home.faceoffs.percentage + away.faceoffs.percentage == pytest.approx(1.0)


def test_a_decided_draw_with_an_unknown_home_participant_is_unattributed():
    game = GameBuilder()
    rival = game.player(AWAY, 91)
    game.faceoff(NEUTRAL, home=None, away=rival, winner=Side.HOME)
    # An undecided draw is nobody's result, so it's not unattributed.
    game.faceoff(NEUTRAL, home=None, away=rival)
    data = game.build()

    home = stats_engine.team_faceoff_stats(data, HOME)

    assert (home.faceoffs.won, home.faceoffs.decided) == (1, 1)
    assert home.unattributed == 1
    assert stats_engine.team_faceoff_stats(data, AWAY).unattributed == 0
    # A home-team unknown is expected to be resolved: an incomplete caveat.
    assert stats_engine.unresolved_faceoff_participants(data) == {HOME: 1}
    assert [stats.player_id for stats in stats_engine.player_faceoff_stats(data)] == [
        rival
    ]


def test_an_unknown_away_participant_is_counted_but_never_a_caveat():
    game = GameBuilder()
    center = game.player(HOME, 14)
    game.faceoff(NEUTRAL, home=center, away=None, winner=Side.AWAY)
    data = game.build()

    assert stats_engine.team_faceoff_stats(data, AWAY).unattributed == 1
    assert stats_engine.team_faceoff_stats(data, HOME).unattributed == 0
    assert stats_engine.unresolved_faceoff_participants(data) == {}


def test_faceoffs_default_to_all_situations_with_5v5_as_an_override():
    game = GameBuilder()
    center = game.player(HOME, 14)
    game.faceoff(NEUTRAL, home=center, winner=Side.HOME, strength="5v5")
    game.faceoff(NEUTRAL, home=center, winner=Side.HOME, strength="5v4")
    data = game.build()

    assert _player(data, center).faceoffs.decided == 2
    assert _player(data, center, strength_state="5v5").faceoffs.decided == 1
    assert stats_engine.team_faceoff_stats(data, HOME).faceoffs.decided == 2
    team_5v5 = stats_engine.team_faceoff_stats(data, HOME, strength_state="5v5")
    assert team_5v5.faceoffs.decided == 1


def test_multi_game_faceoff_win_pct_sums_counts_before_dividing():
    first = GameBuilder(game_id=1)
    second = GameBuilder(game_id=2)
    center = first.player(HOME, 14, player_id=7)
    second.player(HOME, 14, player_id=7)
    first.faceoff(NEUTRAL, home=center, winner=Side.HOME)
    second.faceoff(NEUTRAL, home=center, winner=Side.HOME)
    for _ in range(2):
        second.faceoff(NEUTRAL, home=center, winner=Side.AWAY)
    games = [first.build(), second.build()]

    (combined,) = stats_engine.combined_player_faceoff_stats(games)
    team = stats_engine.combined_team_faceoff_stats(games, HOME)

    assert (combined.faceoffs.won, combined.faceoffs.decided) == (2, 4)
    assert combined.faceoffs.percentage == pytest.approx(0.5)
    assert team.faceoffs.percentage == pytest.approx(0.5)


def test_combined_team_faceoffs_skip_games_the_team_did_not_play():
    first = GameBuilder(game_id=1)
    other = GameBuilder(game_id=2, home=5, away=6)
    first.faceoff(NEUTRAL, winner=Side.HOME)
    other.faceoff(NEUTRAL, winner=Side.HOME)

    team = stats_engine.combined_team_faceoff_stats(
        [first.build(), other.build()], HOME
    )

    assert team.faceoffs.decided == 1


def _zoned_game():
    """Home attacks +x (its shot lands at +x); one draw in each zone for
    home, plus one never located."""
    game = GameBuilder()
    center = game.player(HOME, 14)
    rival = game.player(AWAY, 91)
    game.shot(HOME)
    game.faceoff(OFFENSIVE_FOR_HOME, home=center, away=rival, winner=Side.HOME)
    game.faceoff(-OFFENSIVE_FOR_HOME, home=center, away=rival, winner=Side.AWAY)
    game.faceoff(NEUTRAL, home=center, away=rival, winner=Side.HOME)
    game.faceoff(None, home=center, away=rival, winner=Side.AWAY)
    return game.build(), center, rival


def test_faceoffs_are_bucketed_by_zone_from_each_sides_attacking_direction():
    data, center, rival = _zoned_game()

    home = _player(data, center).faceoffs
    away = _player(data, rival).faceoffs

    assert home.offensive == FaceoffCounts(won=1, decided=1)
    assert home.defensive == FaceoffCounts(won=0, decided=1)
    assert away.offensive == FaceoffCounts(won=1, decided=1)
    assert away.defensive == FaceoffCounts(won=0, decided=1)
    assert home.neutral == FaceoffCounts(won=1, decided=1)
    assert home.undetermined == FaceoffCounts(won=0, decided=1)
    # Neutral and undetermined draws still count toward the overall FO%.
    assert (home.won, home.decided) == (2, 4)
    team = stats_engine.team_faceoff_stats(data, AWAY).faceoffs
    assert team.offensive == FaceoffCounts(won=1, decided=1)


def test_a_draw_in_a_period_with_unknown_direction_is_undetermined():
    game = GameBuilder()
    center = game.player(HOME, 14)
    game.faceoff(OFFENSIVE_FOR_HOME, home=center, winner=Side.HOME)

    faceoffs = _player(game.build(), center).faceoffs

    assert faceoffs.undetermined == FaceoffCounts(won=1, decided=1)
    assert faceoffs.offensive == FaceoffCounts()


def test_faceoff_stats_add_bucket_by_bucket():
    data, center, _rival = _zoned_game()
    once = _player(data, center).faceoffs

    twice = once + once

    assert twice.offensive == FaceoffCounts(won=2, decided=2)
    assert (twice.won, twice.decided) == (4, 8)
