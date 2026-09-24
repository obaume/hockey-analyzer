from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from hockey_analyzer.domain.enums import UnitType
from hockey_analyzer.ui.units_dialog import UnitsDialog


@pytest.fixture
def game(game_setup_service):
    """A game with a small roster on each side: home #4, #14 (named),
    #17; away #91."""
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    for jersey, name in ((14, "Jordan Kim"), (4, None), (17, None)):
        game_setup_service.add_roster_entry(
            game_id=game.id, team_id=home.id, jersey_number=jersey, full_name=name
        )
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=away.id, jersey_number=91
    )
    return game, home, away


def _make_dialog(qtbot, game_setup_service, game):
    game_row, home, away = game
    dialog = UnitsDialog(
        game_setup_service,
        game_id=game_row.id,
        home_team_id=home.id,
        away_team_id=away.id,
    )
    qtbot.addWidget(dialog)
    return dialog


def _labels(dialog):
    return [
        dialog.members_list.item(row).text()
        for row in range(dialog.members_list.count())
    ]


def _row_for_jersey(dialog, jersey):
    return next(
        row
        for row in range(dialog.members_list.count())
        if dialog.members_list.item(row).text().startswith(f"#{jersey}")
    )


def _check(dialog, jersey, checked=True):
    dialog.members_list.item(_row_for_jersey(dialog, jersey)).setCheckState(
        Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    )


def _select_unit(dialog, unit_type, unit_number):
    dialog.unit_type_combo.setCurrentIndex(
        dialog.unit_type_combo.findData(unit_type.value)
    )
    dialog.unit_number_field.setValue(unit_number)


def _player_id(game_setup_service, game, team, jersey):
    game_row = game[0]
    return next(
        entry.player_id
        for entry in game_setup_service.list_roster(game_row.id, team.id)
        if entry.jersey_number == jersey
    )


def test_lists_the_home_roster_by_jersey_number_unchecked(
    qtbot, game_setup_service, game
):
    dialog = _make_dialog(qtbot, game_setup_service, game)

    assert _labels(dialog) == ["#4", "#14 Jordan Kim", "#17"]
    assert all(
        dialog.members_list.item(row).checkState() == Qt.CheckState.Unchecked
        for row in range(dialog.members_list.count())
    )


def test_switching_side_lists_that_sides_roster(qtbot, game_setup_service, game):
    dialog = _make_dialog(qtbot, game_setup_service, game)

    dialog.side_combo.setCurrentIndex(dialog.side_combo.findData("away"))

    assert _labels(dialog) == ["#91"]


def test_ticking_players_puts_them_on_the_selected_unit(
    qtbot, game_setup_service, game
):
    game_row, home, _away = game
    dialog = _make_dialog(qtbot, game_setup_service, game)
    _select_unit(dialog, UnitType.FORWARD_LINE, 2)

    _check(dialog, 14)
    _check(dialog, 17)

    numbers = game_setup_service.unit_numbers(
        game_id=game_row.id, team_id=home.id, unit_type=UnitType.FORWARD_LINE
    )
    assert numbers == {
        _player_id(game_setup_service, game, home, jersey): 2 for jersey in (14, 17)
    }


def test_unticking_a_player_takes_them_off_the_unit(qtbot, game_setup_service, game):
    game_row, home, _away = game
    dialog = _make_dialog(qtbot, game_setup_service, game)
    _check(dialog, 14)

    _check(dialog, 14, checked=False)

    assert (
        game_setup_service.player_unit_assignments(
            game_row.id, _player_id(game_setup_service, game, home, 14)
        )
        == {}
    )


def test_changing_unit_shows_that_units_current_members(
    qtbot, game_setup_service, game
):
    dialog = _make_dialog(qtbot, game_setup_service, game)
    _select_unit(dialog, UnitType.POWER_PLAY, 1)
    _check(dialog, 4)

    _select_unit(dialog, UnitType.FORWARD_LINE, 1)
    assert dialog.members_list.item(_row_for_jersey(dialog, 4)).checkState() == (
        Qt.CheckState.Unchecked
    )

    _select_unit(dialog, UnitType.POWER_PLAY, 1)
    assert dialog.members_list.item(_row_for_jersey(dialog, 4)).checkState() == (
        Qt.CheckState.Checked
    )


def test_a_player_can_be_on_a_line_and_the_power_play(qtbot, game_setup_service, game):
    game_row, home, _away = game
    dialog = _make_dialog(qtbot, game_setup_service, game)
    _select_unit(dialog, UnitType.FORWARD_LINE, 1)
    _check(dialog, 14)
    _select_unit(dialog, UnitType.POWER_PLAY, 1)
    _check(dialog, 14)

    assert game_setup_service.player_unit_assignments(
        game_row.id, _player_id(game_setup_service, game, home, 14)
    ) == {UnitType.FORWARD_LINE: 1, UnitType.POWER_PLAY: 1}


def test_a_player_on_another_number_of_this_type_is_annotated_and_moved(
    qtbot, game_setup_service, game
):
    # At most one unit per type: ticking a line-2 player onto line 1
    # moves them, and the list says where they are before you do.
    game_row, home, _away = game
    dialog = _make_dialog(qtbot, game_setup_service, game)
    _select_unit(dialog, UnitType.FORWARD_LINE, 2)
    _check(dialog, 17)

    _select_unit(dialog, UnitType.FORWARD_LINE, 1)
    assert dialog.members_list.item(_row_for_jersey(dialog, 17)).text() == (
        "#17 (Forward line 2)"
    )
    _check(dialog, 17)

    assert game_setup_service.player_unit_assignments(
        game_row.id, _player_id(game_setup_service, game, home, 17)
    ) == {UnitType.FORWARD_LINE: 1}
    assert dialog.members_list.item(_row_for_jersey(dialog, 17)).text() == "#17"


def test_unticking_after_a_move_does_not_touch_the_other_unit(
    qtbot, game_setup_service, game
):
    game_row, home, _away = game
    dialog = _make_dialog(qtbot, game_setup_service, game)
    _select_unit(dialog, UnitType.FORWARD_LINE, 2)
    _check(dialog, 17)
    _select_unit(dialog, UnitType.FORWARD_LINE, 1)

    # 17 is on line 2, not line 1 -- unchecked here already; toggling a
    # different player must leave 17's line-2 spot alone.
    _check(dialog, 4)
    _check(dialog, 4, checked=False)

    assert game_setup_service.player_unit_assignments(
        game_row.id, _player_id(game_setup_service, game, home, 17)
    ) == {UnitType.FORWARD_LINE: 2}


def test_members_header_names_the_side_and_unit(qtbot, game_setup_service, game):
    dialog = _make_dialog(qtbot, game_setup_service, game)
    assert dialog.members_label.text() == "Members of Home - Forward line 1:"

    dialog.side_combo.setCurrentIndex(dialog.side_combo.findData("away"))
    _select_unit(dialog, UnitType.PENALTY_KILL, 2)

    assert dialog.members_label.text() == "Members of Away - Penalty kill 2:"
