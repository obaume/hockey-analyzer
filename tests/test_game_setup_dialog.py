from __future__ import annotations

from PySide6.QtCore import Qt

from hockey_analyzer.domain.enums import Position
from hockey_analyzer.ui.game_setup_dialog import GameSetupDialog, TeamRosterPanel


def _make_dialog(qtbot, service):
    dialog = GameSetupDialog(service)
    qtbot.addWidget(dialog)
    return dialog


def _create_game(qtbot, dialog):
    qtbot.mouseClick(dialog.new_game_button, Qt.MouseButton.LeftButton)


def _create_team(qtbot, panel, name):
    panel.new_team_name_field.setText(name)
    qtbot.mouseClick(panel.create_team_button, Qt.MouseButton.LeftButton)


def _select_team(panel, team_id):
    panel.team_combo.setCurrentIndex(panel.team_combo.findData(team_id))


def _add_new_player(qtbot, panel, *, jersey_number, full_name="", position=None):
    panel.player_combo.setCurrentIndex(0)  # "New player..." sentinel
    panel.jersey_field.setText(str(jersey_number))
    panel.full_name_field.setText(full_name)
    if position is not None:
        panel.position_combo.setCurrentIndex(panel.position_combo.findData(position))
    else:
        panel.position_combo.setCurrentIndex(0)
    qtbot.mouseClick(panel.add_player_button, Qt.MouseButton.LeftButton)


# -- creating a game: no pre-existing data required ------------------------


def test_roster_panels_start_disabled_with_no_game(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)

    assert dialog.home_panel.isEnabled() is False
    assert dialog.away_panel.isEnabled() is False


def test_new_game_button_creates_a_game_with_no_other_input(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)

    _create_game(qtbot, dialog)

    assert dialog.game_id is not None
    assert dialog.home_panel.isEnabled() is True
    assert dialog.away_panel.isEnabled() is True


# -- teams: create new or select existing, symmetric for either side -------


def test_creating_a_team_selects_it_in_the_combo(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)

    _create_team(qtbot, dialog.home_panel, "Icebreakers")

    assert dialog.home_panel.team_combo.currentText() == "Icebreakers"
    assert dialog.home_panel.team_id is not None


def test_an_existing_team_can_be_selected_without_creating_a_new_one(qtbot, game_setup_service):
    existing = game_setup_service.create_team("Rivals")
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)

    _select_team(dialog.away_panel, existing.id)

    assert dialog.away_panel.team_id == existing.id


def test_home_and_away_panels_are_the_same_widget_class(qtbot, game_setup_service):
    # No special-cased "opponent" panel/logic -- both sides use one class.
    dialog = _make_dialog(qtbot, game_setup_service)
    assert isinstance(dialog.home_panel, TeamRosterPanel)
    assert isinstance(dialog.away_panel, TeamRosterPanel)
    assert type(dialog.home_panel) is type(dialog.away_panel)


# -- roster entries: brand-new player on the spot ---------------------------


def test_adding_a_brand_new_player_creates_a_roster_entry(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.home_panel, "Icebreakers")

    _add_new_player(qtbot, dialog.home_panel, jersey_number=14, full_name="Jordan Kim")

    roster = game_setup_service.list_roster(dialog.game_id, dialog.home_panel.team_id)
    assert len(roster) == 1
    assert roster[0].jersey_number == 14
    assert roster[0].player.full_name == "Jordan Kim"


def test_roster_table_shows_the_added_player(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.home_panel, "Icebreakers")

    _add_new_player(qtbot, dialog.home_panel, jersey_number=14, full_name="Jordan Kim")

    panel = dialog.home_panel
    assert panel.roster_table.rowCount() == 1
    assert panel.roster_table.item(0, 0).text() == "14"
    assert panel.roster_table.item(0, 1).text() == "Jordan Kim"


def test_full_name_is_skippable_at_creation(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.home_panel, "Icebreakers")

    _add_new_player(qtbot, dialog.home_panel, jersey_number=14, full_name="")

    roster = game_setup_service.list_roster(dialog.game_id, dialog.home_panel.team_id)
    assert roster[0].player.full_name is None


def test_position_can_be_set_at_creation(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.home_panel, "Icebreakers")

    _add_new_player(qtbot, dialog.home_panel, jersey_number=31, full_name="Sam Lee", position=Position.GOALIE)

    roster = game_setup_service.list_roster(dialog.game_id, dialog.home_panel.team_id)
    assert roster[0].player.position is Position.GOALIE


def test_roster_entry_works_identically_on_the_away_side(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.away_panel, "Rivals")

    _add_new_player(qtbot, dialog.away_panel, jersey_number=14, full_name="Away Player")

    roster = game_setup_service.list_roster(dialog.game_id, dialog.away_panel.team_id)
    assert roster[0].player.full_name == "Away Player"


def test_duplicate_jersey_number_shows_an_error_and_does_not_add_a_row(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.home_panel, "Icebreakers")
    _add_new_player(qtbot, dialog.home_panel, jersey_number=14, full_name="Jordan Kim")

    _add_new_player(qtbot, dialog.home_panel, jersey_number=14, full_name="Casey Nguyen")

    panel = dialog.home_panel
    assert panel.error_label.text() != ""
    assert panel.roster_table.rowCount() == 1


def test_same_jersey_number_allowed_on_the_other_side(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.home_panel, "Icebreakers")
    _create_team(qtbot, dialog.away_panel, "Rivals")
    _add_new_player(qtbot, dialog.home_panel, jersey_number=14, full_name="Home Player")

    _add_new_player(qtbot, dialog.away_panel, jersey_number=14, full_name="Away Player")

    assert dialog.away_panel.error_label.text() == ""
    assert dialog.away_panel.roster_table.rowCount() == 1


# -- roster entries: pick an existing player instead ------------------------


def test_an_existing_player_can_be_added_to_the_roster(qtbot, game_setup_service):
    existing = game_setup_service.create_player(full_name="Jordan Kim")
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.home_panel, "Icebreakers")
    panel = dialog.home_panel
    panel._refresh_player_combo()

    panel.player_combo.setCurrentIndex(panel.player_combo.findData(existing.id))
    panel.jersey_field.setText("14")
    qtbot.mouseClick(panel.add_player_button, Qt.MouseButton.LeftButton)

    roster = game_setup_service.list_roster(dialog.game_id, panel.team_id)
    assert roster[0].player_id == existing.id


# -- backfilling full_name / setting position after creation ---------------


def test_full_name_is_backfillable_after_creation(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.home_panel, "Icebreakers")
    panel = dialog.home_panel
    _add_new_player(qtbot, panel, jersey_number=14, full_name="")

    panel.roster_table.selectRow(0)
    panel.edit_full_name_field.setText("Jordan Kim")
    qtbot.mouseClick(panel.edit_save_button, Qt.MouseButton.LeftButton)

    roster = game_setup_service.list_roster(dialog.game_id, panel.team_id)
    assert roster[0].player.full_name == "Jordan Kim"


def test_position_can_be_set_after_creation(qtbot, game_setup_service):
    dialog = _make_dialog(qtbot, game_setup_service)
    _create_game(qtbot, dialog)
    _create_team(qtbot, dialog.home_panel, "Icebreakers")
    panel = dialog.home_panel
    _add_new_player(qtbot, panel, jersey_number=14, full_name="Jordan Kim")

    panel.roster_table.selectRow(0)
    panel.edit_position_combo.setCurrentIndex(panel.edit_position_combo.findData(Position.DEFENSE))
    qtbot.mouseClick(panel.edit_save_button, Qt.MouseButton.LeftButton)

    roster = game_setup_service.list_roster(dialog.game_id, panel.team_id)
    assert roster[0].player.position is Position.DEFENSE
