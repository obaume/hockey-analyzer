from __future__ import annotations

from stats_fixtures import AWAY, HOME, GameBuilder

from hockey_analyzer.domain.enums import Position
from hockey_analyzer.domain.on_ice import LONG_SHIFT_MS, on_ice_sets


def _jerseys(players):
    return [player.jersey_number for player in players]


def test_both_teams_have_a_set_even_before_any_shift_change():
    sets = on_ice_sets(GameBuilder().build(), 0)

    assert set(sets) == {HOME, AWAY}
    assert sets[HOME].skaters == () and sets[HOME].goalies == ()


def test_a_player_is_on_from_their_on_change_until_their_off_change():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shift(HOME, kim, True, at=1000)
    game.shift(HOME, kim, False, at=5000)
    data = game.build()

    assert _jerseys(on_ice_sets(data, 999)[HOME].skaters) == []
    assert _jerseys(on_ice_sets(data, 1000)[HOME].skaters) == [14]
    assert _jerseys(on_ice_sets(data, 4999)[HOME].skaters) == [14]
    assert _jerseys(on_ice_sets(data, 5000)[HOME].skaters) == []


def test_each_player_counts_toward_the_team_their_shift_change_names():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    rival = game.player(AWAY, 91)
    game.shift(HOME, kim, True)
    game.shift(AWAY, rival, True)

    sets = on_ice_sets(game.build(), 10_000)

    assert _jerseys(sets[HOME].skaters) == [14]
    assert _jerseys(sets[AWAY].skaters) == [91]


def test_the_goalie_is_kept_apart_from_the_skaters():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    goalie = game.player(HOME, 30, position=Position.GOALIE)
    game.shift(HOME, kim, True)
    game.shift(HOME, goalie, True)

    home = on_ice_sets(game.build(), 10_000)[HOME]

    assert _jerseys(home.skaters) == [14]
    assert _jerseys(home.goalies) == [30]


def test_skaters_read_left_wing_centre_right_wing_then_defence_then_unknown():
    game = GameBuilder()
    for jersey, position in [
        (4, Position.DEFENSE),
        (19, Position.RIGHT_WING),
        (2, Position.DEFENSE),
        (9, Position.CENTER),
        (77, None),
        (11, Position.LEFT_WING),
    ]:
        game.shift(HOME, game.player(HOME, jersey, position=position), True)

    assert _jerseys(on_ice_sets(game.build(), 10_000)[HOME].skaters) == [
        11,
        9,
        19,
        2,
        4,
        77,
    ]


def test_a_player_with_no_roster_entry_is_a_skater_with_no_jersey():
    game = GameBuilder()
    game.shift(HOME, 555, True)

    (player,) = on_ice_sets(game.build(), 10_000)[HOME].skaters

    assert (player.player_id, player.jersey_number, player.position) == (
        555,
        None,
        None,
    )


def test_shift_length_counts_live_play_only():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.period_start(1, at=0)
    game.shift(HOME, kim, True, at=0)
    game.faceoff(0.0, at=0)
    game.stoppage(at=30_000)
    game.faceoff(0.0, at=50_000)

    (player,) = on_ice_sets(game.build(), 60_000)[HOME].skaters

    assert player.shift_ms == 40_000


def test_coming_on_during_a_stoppage_starts_the_shift_at_the_faceoff():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.faceoff(0.0, at=0)
    game.stoppage(at=30_000)
    game.shift(HOME, kim, True, at=35_000)
    game.faceoff(0.0, at=40_000)

    (player,) = on_ice_sets(game.build(), 45_000)[HOME].skaters

    assert player.shift_ms == 5_000


def test_a_shift_past_two_minutes_is_long():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.faceoff(0.0, at=0)
    game.shift(HOME, kim, True, at=0)
    data = game.build()

    assert not on_ice_sets(data, LONG_SHIFT_MS)[HOME].skaters[0].long_shift
    assert on_ice_sets(data, LONG_SHIFT_MS + 1)[HOME].skaters[0].long_shift


def test_a_repeated_on_change_keeps_the_shifts_original_start():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.faceoff(0.0, at=0)
    game.shift(HOME, kim, True, at=1_000)
    game.shift(HOME, kim, True, at=5_000)

    (player,) = on_ice_sets(game.build(), 10_000)[HOME].skaters

    assert player.shift_ms == 9_000


def test_unresolved_shift_changes_are_counted_per_team_up_to_the_moment():
    game = GameBuilder()
    kim = game.player(HOME, 14)
    game.shift(HOME, None, True, at=1_000, unknown=True)
    game.shift(HOME, kim, None, at=2_000)
    game.shift(AWAY, None, True, at=3_000, unknown=True)
    game.shift(HOME, None, False, at=9_000, unknown=True)
    data = game.build()

    sets = on_ice_sets(data, 5_000)

    assert (
        sets[HOME].unresolved_shift_changes,
        sets[AWAY].unresolved_shift_changes,
    ) == (
        2,
        1,
    )
    assert sets[HOME].skaters == ()
