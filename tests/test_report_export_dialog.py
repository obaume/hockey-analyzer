"""Report export (ticket 26): from the live stats view's games and filters,
the sender picks what the report is about, writes a Markdown summary, and
saves the bundle file -- read back here exactly as a recipient would."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog
from stats_fixtures import named_game

from hockey_analyzer.domain.report_bundle import (
    BUNDLE_EXTENSION,
    ReportKind,
    StrengthFilters,
    read_bundle,
)
from hockey_analyzer.domain.stats_engine import ALL_SITUATIONS, NATURAL_STRENGTH
from hockey_analyzer.ui.report_export_dialog import ReportExportDialog

_FILTERS = StrengthFilters(
    team=ALL_SITUATIONS,
    skaters=ALL_SITUATIONS,
    goalies="5v5",
    units=NATURAL_STRENGTH,
)


def _dialog(qtbot, games, *, path=None, previews=None, filters=_FILTERS):
    suggested = []

    def choose_path(suggestion):
        suggested.append(suggestion)
        return "" if path is None else str(path)

    dialog = ReportExportDialog(
        games,
        filters=filters,
        choose_path=choose_path,
        preview=None if previews is None else previews.append,
    )
    dialog.suggested_names = suggested
    qtbot.addWidget(dialog)
    return dialog


def test_single_game_export_writes_a_game_bundle_with_summary_and_filters(
    qtbot, tmp_path
):
    path = tmp_path / "out.hockeyreport"
    dialog = _dialog(qtbot, [named_game()], path=path)
    dialog.summary_edit.setPlainText("# Solid win\n\nKim was **everywhere**.")

    dialog.export_button.click()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.exported_path == path
    report = read_bundle(path)
    assert report.kind is ReportKind.GAME
    assert report.summary == "# Solid win\n\nKim was **everywhere**."
    assert report.skater_stats.strength_state == ALL_SITUATIONS
    assert report.goalie_stats.strength_state == "5v5"
    assert [chart.name for chart in report.charts] == ["shot-map"]


def test_single_game_export_needs_no_subject(qtbot, tmp_path):
    dialog = _dialog(qtbot, [named_game()], path=tmp_path / "out.hockeyreport")

    assert dialog.subject_combo.isHidden() is True


def test_suggested_file_name_describes_the_report(qtbot, tmp_path):
    dialog = _dialog(qtbot, [named_game()], path=tmp_path / "out.hockeyreport")

    dialog.export_button.click()

    (suggestion,) = dialog.suggested_names
    assert suggestion == f"Icebreakers vs Rivals{BUNDLE_EXTENSION}"


def test_cancelling_the_save_prompt_writes_nothing_and_stays_open(qtbot, tmp_path):
    dialog = _dialog(qtbot, [named_game()], path=None)

    dialog.export_button.click()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.exported_path is None
    assert list(tmp_path.iterdir()) == []


def _two_games():
    return [named_game(game_id=1), named_game(game_id=2)]


def test_multi_game_subject_lists_every_team_then_every_player(qtbot):
    dialog = _dialog(qtbot, _two_games())

    combo = dialog.subject_combo
    assert combo.isHidden() is False
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Team: Icebreakers",
        "Team: Rivals",
        "Player: #14 Jordan Kim (Icebreakers)",
        "Player: #30 (Icebreakers)",
        "Player: #91 (Rivals)",
    ]


def test_multi_game_export_writes_a_team_report_about_the_chosen_team(qtbot, tmp_path):
    path = tmp_path / "rivals.hockeyreport"
    dialog = _dialog(qtbot, _two_games(), path=path)
    dialog.subject_combo.setCurrentText("Team: Rivals")

    dialog.export_button.click()

    report = read_bundle(path)
    assert report.kind is ReportKind.TEAM
    assert report.teams[report.subject_team_id].name == "Rivals"
    assert len(report.games) == 2
    assert dialog.suggested_names == [f"Rivals -- 2 games{BUNDLE_EXTENSION}"]


def test_multi_game_export_writes_a_player_report_about_the_chosen_player(
    qtbot, tmp_path
):
    path = tmp_path / "kim.hockeyreport"
    dialog = _dialog(qtbot, _two_games(), path=path)
    dialog.subject_combo.setCurrentText("Player: #14 Jordan Kim (Icebreakers)")

    dialog.export_button.click()

    report = read_bundle(path)
    assert report.kind is ReportKind.PLAYER
    assert report.players[report.subject_player_id].full_name == "Jordan Kim"


def test_preview_shows_the_report_as_it_would_be_exported(qtbot):
    previews = []
    dialog = _dialog(qtbot, _two_games(), previews=previews)
    dialog.subject_combo.setCurrentText("Team: Rivals")
    dialog.summary_edit.setPlainText("Draft.")

    dialog.preview_button.click()

    (report,) = previews
    assert report.kind is ReportKind.TEAM
    assert report.subject_team_id is not None
    assert report.teams[report.subject_team_id].name == "Rivals"
    assert report.summary == "Draft."
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_a_failed_write_is_reported_and_the_dialog_stays_open(qtbot, tmp_path):
    errors = []
    dialog = ReportExportDialog(
        [named_game()],
        filters=_FILTERS,
        choose_path=lambda _suggestion: str(tmp_path / "missing" / "r.hockeyreport"),
        error_notice=errors.append,
    )
    qtbot.addWidget(dialog)

    dialog.export_button.click()

    (message,) = errors
    assert "missing" in message
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.exported_path is None
