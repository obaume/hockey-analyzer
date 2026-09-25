"""Report charts (ticket 26): the shot map baked to a PNG at export time,
since a bundle carries no raw events to redraw it from (ADR-0003)."""

from __future__ import annotations

from PySide6.QtGui import QImage
from stats_fixtures import AWAY, HOME, GameBuilder

from hockey_analyzer.domain.enums import ShotOutcome
from hockey_analyzer.ui.report_charts import shot_map_chart, shot_sides


def test_shot_map_is_a_png_chart_of_the_games_located_shots(qapp):
    game = GameBuilder()
    game.shot(HOME)
    game.shot(AWAY)

    chart = shot_map_chart([game.build()])

    assert chart.name == "shot-map"
    image = QImage.fromData(chart.png, "PNG")
    assert image.isNull() is False
    assert image.width() > image.height() > 0


def test_no_shot_map_when_no_shot_has_a_location(qapp):
    game = GameBuilder()
    game.shot(HOME).shot_x = None

    assert shot_map_chart([game.build()]) is None


def test_shot_map_puts_the_chosen_team_attacking_right_in_every_game():
    first = GameBuilder(game_id=1)
    first.shot(HOME, x=50.0)
    first.shot(AWAY, x=-30.0)
    second = GameBuilder(game_id=2, home=AWAY, away=HOME)  # HOME visiting
    second.shot(HOME, x=-50.0)
    second.shot(AWAY, x=30.0)

    sides = shot_sides([first.build(), second.build()], right_team_id=HOME)

    assert [point.x for point in sides.right] == [50.0, 50.0]
    assert [point.x for point in sides.left] == [-30.0, -30.0]


def test_player_shot_map_follows_the_players_team_in_each_game():
    """A player who changed teams between the picked games: each game's
    attempts by whichever team the player was on then attack right."""
    first = GameBuilder(game_id=1)
    first.player(HOME, 14, player_id=7)
    first.shot(HOME, x=50.0)
    first.shot(AWAY, x=-30.0)
    second = GameBuilder(game_id=2)
    second.player(AWAY, 14, player_id=7)
    second.shot(HOME, x=30.0)
    second.shot(AWAY, x=-50.0)

    sides = shot_sides([first.build(), second.build()], player_id=7)

    assert [point.x for point in sides.right] == [50.0, 50.0]
    assert [point.x for point in sides.left] == [-30.0, -30.0]


def test_goals_are_marked_on_the_shot_map():
    game = GameBuilder()
    game.shot(HOME, ShotOutcome.GOAL)
    game.shot(HOME)

    sides = shot_sides([game.build()])

    assert [point.goal for point in sides.right] == [True, False]
