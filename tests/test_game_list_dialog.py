from __future__ import annotations

import datetime

from hockey_analyzer.ui.game_list_dialog import GameListDialog


def _make_dialog(qtbot, service):
    dialog = GameListDialog(service)
    qtbot.addWidget(dialog)
    return dialog


def test_empty_games_list_shows_no_rows(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    assert dialog.table.rowCount() == 0


def test_lists_games_most_recently_updated_first(qtbot, game_setup_service):
    older = game_setup_service.create_game()
    newer = game_setup_service.create_game()

    dialog = _make_dialog(qtbot, game_setup_service)

    assert [g.id for g in dialog._games] == [newer.id, older.id]
    assert dialog.table.rowCount() == 2


def test_row_shows_date_home_away_and_score(qtbot, game_setup_service, session):
    game = game_setup_service.create_game()
    game.date = datetime.date(2026, 1, 15)
    game.home_score = 4
    game.away_score = 2
    session.commit()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.set_side_team(game.id, "home", home.id)
    game_setup_service.set_side_team(game.id, "away", away.id)

    dialog = _make_dialog(qtbot, game_setup_service)

    assert dialog.table.item(0, 0).text() == "2026-01-15"
    assert dialog.table.item(0, 1).text() == "Icebreakers"
    assert dialog.table.item(0, 2).text() == "Rivals"
    assert dialog.table.item(0, 3).text() == "4 – 2"


def test_row_shows_placeholders_for_unset_fields(qtbot, game_setup_service):
    game_setup_service.create_game()

    dialog = _make_dialog(qtbot, game_setup_service)

    assert dialog.table.item(0, 0).text() == "Unscheduled"
    assert dialog.table.item(0, 1).text() == "TBD"
    assert dialog.table.item(0, 2).text() == "TBD"
    assert dialog.table.item(0, 3).text() == ""


def test_open_button_with_no_selection_does_nothing(qtbot, game_setup_service):
    game_setup_service.create_game()
    dialog = _make_dialog(qtbot, game_setup_service)

    dialog.open_button.click()

    assert dialog.selected_game_id is None
    assert dialog.result() == 0  # not accepted or rejected yet


def test_selecting_a_row_and_opening_sets_selected_game_id(qtbot, game_setup_service):
    game = game_setup_service.create_game()
    dialog = _make_dialog(qtbot, game_setup_service)
    dialog.table.selectRow(0)

    dialog.open_button.click()

    assert dialog.selected_game_id == game.id
    assert dialog.result() == dialog.DialogCode.Accepted


def test_cancel_button_rejects_without_a_selection(qtbot, game_setup_service):
    game_setup_service.create_game()
    dialog = _make_dialog(qtbot, game_setup_service)
    dialog.table.selectRow(0)

    dialog.cancel_button.click()

    assert dialog.selected_game_id is None
    assert dialog.result() == dialog.DialogCode.Rejected
