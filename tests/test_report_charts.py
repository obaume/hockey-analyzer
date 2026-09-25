"""Report charts (ticket 26): the shot map baked to a PNG at export time,
since a bundle carries no raw events to redraw it from (ADR-0003)."""

from __future__ import annotations

from PySide6.QtGui import QImage
from stats_fixtures import AWAY, HOME, GameBuilder

from hockey_analyzer.ui.report_charts import shot_map_chart


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
