"""Report view (ticket 26): renders a frozen `Report` -- the one just built
for export, or one opened from a bundle file -- with the same tables the
live stats view shows, plus the sender's summary, charts, and caveats."""

from __future__ import annotations

import dataclasses
import datetime

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
from stats_fixtures import AWAY, HOME, GameBuilder, named_game
from table_helpers import all_cells, column_headers, table_rows

from hockey_analyzer.domain.report_bundle import (
    Chart,
    StrengthFilters,
    build_game_report,
    build_player_report,
    build_team_report,
    read_bundle,
    write_bundle,
)
from hockey_analyzer.domain.stats_engine import ALL_SITUATIONS, NATURAL_STRENGTH
from hockey_analyzer.ui.report_view import ReportView
from hockey_analyzer.ui.stats_dialog import StatsDialog

_TABLES = (
    "team_table",
    "skater_table",
    "position_table",
    "unit_table",
    "goalie_table",
    "shot_quality_table",
)


def _view(qtbot, report):
    view = ReportView(report)
    qtbot.addWidget(view)
    return view


def test_game_report_shows_both_teams_stats(qtbot):
    view = _view(qtbot, build_game_report(named_game(), summary=""))

    assert column_headers(view.team_table) == ["Icebreakers", "Rivals"]
    assert table_rows(view.team_table)["CF"] == ["1", "1"]


def _select(combo, label):
    combo.setCurrentIndex(combo.findText(label))


def test_game_report_shows_exactly_what_the_live_stats_view_showed(qtbot):
    data = named_game()
    live = StatsDialog([data])
    qtbot.addWidget(live)
    _select(live.skater_strength_combo, "All situations")
    _select(live.goalie_strength_combo, "5v5")

    view = _view(
        qtbot,
        build_game_report(
            data,
            summary="",
            filters=StrengthFilters(
                team=ALL_SITUATIONS,
                skaters=ALL_SITUATIONS,
                goalies="5v5",
                units=NATURAL_STRENGTH,
            ),
        ),
    )

    for table in _TABLES:
        assert all_cells(getattr(view, table)) == all_cells(getattr(live, table)), table
    assert table_rows(view.team_table)["CF"] == ["2", "1"]


def test_a_report_opened_from_its_bundle_file_renders_identically(qtbot, tmp_path):
    report = build_game_report(named_game(), summary="Read me.")
    path = tmp_path / "game.hockeyreport"
    write_bundle(path, report)

    sent = _view(qtbot, report)
    received = _view(qtbot, read_bundle(path))

    for table in _TABLES:
        assert all_cells(getattr(received, table)) == all_cells(getattr(sent, table))
    assert received.filters_label.text() == sent.filters_label.text()
    assert received.summary.toMarkdown() == sent.summary.toMarkdown()


def test_each_stat_group_names_the_strength_filter_it_was_frozen_at(qtbot):
    view = _view(
        qtbot,
        build_game_report(
            named_game(),
            summary="",
            filters=StrengthFilters(
                team="5v5", skaters="5v5", goalies=ALL_SITUATIONS, units="5v4"
            ),
        ),
    )

    text = view.filters_label.text()
    assert "Team / skaters: 5v5" in text
    assert "Goalies: All situations" in text
    assert "Units: 5v4" in text


def test_stats_degraded_by_unknown_players_carry_a_caveat(qtbot):
    data = named_game()
    builder_game = GameBuilder(game_id=data.game.id)
    stub = builder_game.shift(HOME, None, True, unknown=True)
    stub.id = 999
    data.events.append(stub)

    view = _view(qtbot, build_game_report(data, summary=""))

    assert view.caveat_label.isHidden() is False
    assert "Icebreakers: 1 shift change(s) with an unknown player" in (
        view.caveat_label.text()
    )
    assert "Rivals" not in view.caveat_label.text()


def test_no_caveat_line_when_every_shift_change_is_resolved(qtbot):
    view = _view(qtbot, build_game_report(named_game(), summary=""))

    assert view.caveat_label.isHidden() is True


def test_coverage_names_the_games_excluded_for_incomplete_opponent_shifts(qtbot):
    unflagged = named_game(game_id=1)
    unflagged.game.date = datetime.date(2026, 1, 10)
    flagged = named_game(game_id=2, opponent_shifts_complete=True)
    flagged.game.date = datetime.date(2026, 1, 17)

    view = _view(qtbot, build_team_report([unflagged, flagged], AWAY, summary=""))

    assert view.coverage_label.isHidden() is False
    assert "Rivals: 1 of 2 games (excluded: 2026-01-10)" in view.coverage_label.text()


def test_a_single_game_report_still_says_when_a_team_was_excluded(qtbot):
    view = _view(qtbot, build_game_report(named_game(), summary=""))

    assert view.coverage_label.isHidden() is False
    assert "Rivals: 0 of 1 games (excluded: " in view.coverage_label.text()


def test_a_single_game_report_with_full_coverage_has_no_coverage_line(qtbot):
    view = _view(
        qtbot, build_game_report(named_game(opponent_shifts_complete=True), summary="")
    )

    assert view.coverage_label.isHidden() is True


def _tab(view, title):
    tabs = view.tables.tabs
    return next(i for i in range(tabs.count()) if tabs.tabText(i) == title)


def test_stat_groups_an_older_bundle_lacks_are_marked_not_included(qtbot):
    report = dataclasses.replace(
        build_game_report(named_game(), summary=""), skater_stats=None, unit_stats=None
    )

    view = _view(qtbot, report)

    tabs = view.tables.tabs
    for title in ("Skaters", "Positions", "Units"):
        assert tabs.isTabEnabled(_tab(view, title)) is False
        assert "not included" in tabs.tabToolTip(_tab(view, title))
    assert tabs.isTabEnabled(_tab(view, "Team")) is True
    assert table_rows(view.team_table)["CF"] == ["1", "1"]


def _png(width, height):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def test_charts_show_as_the_images_baked_at_export(qtbot):
    report = build_game_report(
        named_game(), summary="", charts=[Chart("shot-map", _png(40, 20))]
    )

    view = _view(qtbot, report)

    (chart,) = view.chart_labels
    assert chart.pixmap().size().toTuple() == (40, 20)
    assert view.tables.tabs.isTabEnabled(_tab(view, "Charts")) is True


def test_a_report_without_charts_has_no_charts_tab(qtbot):
    view = _view(qtbot, build_game_report(named_game(), summary=""))

    assert view.chart_labels == []
    assert "Charts" not in [
        view.tables.tabs.tabText(i) for i in range(view.tables.tabs.count())
    ]


def _two_games():
    first = named_game(game_id=1)
    first.game.date = datetime.date(2026, 1, 10)
    first.game.home_score, first.game.away_score = 3, 2
    second = named_game(game_id=2, opponent_shifts_complete=True)
    return [first, second]


def test_header_names_a_game_reports_matchup_and_its_game(qtbot):
    first, _second = _two_games()

    view = _view(qtbot, build_game_report(first, summary=""))

    assert view.title_label.text() == "Game report: Icebreakers 3–2 Rivals"
    assert "2026-01-10 · Icebreakers 3–2 Rivals" in view.games_label.text()


def test_header_names_a_team_reports_subject_and_lists_every_game(qtbot):
    view = _view(qtbot, build_team_report(_two_games(), AWAY, summary=""))

    assert view.title_label.text() == "Team report: Rivals -- 2 games"
    games = view.games_label.text()
    assert "2026-01-10 · Icebreakers 3–2 Rivals" in games
    assert "Game #2 · Icebreakers vs Rivals" in games


def test_team_report_shows_only_the_subject_teams_stats(qtbot):
    view = _view(qtbot, build_team_report(_two_games(), AWAY, summary=""))

    assert column_headers(view.team_table) == ["Rivals"]
    assert column_headers(view.shot_quality_table) == ["Rivals"]
    assert {
        view.skater_table.item(index, 1).text()
        for index in range(view.skater_table.rowCount())
    } == {"Rivals"}
    assert view.goalie_table.rowCount() == 0
    assert view.unit_table.rowCount() == 1


def test_player_report_shows_only_the_subject_player(qtbot):
    games = _two_games()
    kim = games[0].roster[0].player_id

    view = _view(qtbot, build_player_report(games, kim, summary=""))

    assert view.title_label.text() == "Player report: #14 Jordan Kim -- 2 games"
    assert column_headers(view.team_table) == ["Icebreakers"]
    assert view.skater_table.rowCount() == 1
    assert view.skater_table.item(0, 0).text() == "#14 Jordan Kim"
    assert view.unit_table.rowCount() == 1
    assert view.goalie_table.rowCount() == 0


def test_coverage_naming_a_game_missing_from_the_game_list_still_opens(qtbot):
    report = build_team_report(_two_games(), AWAY, summary="")
    damaged = dataclasses.replace(report, games=report.games[1:])

    view = _view(qtbot, damaged)

    assert "Rivals: 1 of 2 games (excluded: Game #1)" in view.coverage_label.text()


def test_player_report_without_the_players_rows_keeps_every_teams_caveats(qtbot):
    games = _two_games()
    kim = games[0].roster[0].player_id
    report = dataclasses.replace(
        build_player_report(games, kim, summary=""),
        skater_stats=None,
        goalie_stats=None,
    )

    view = _view(qtbot, report)

    assert "Rivals: 1 of 2 games" in view.coverage_label.text()
