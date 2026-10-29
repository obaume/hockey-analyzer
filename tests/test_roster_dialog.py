from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from hockey_analyzer.domain.enums import ShotOutcome, ShotType, UnitType
from hockey_analyzer.domain.models import ShotAttempt
from hockey_analyzer.ui.roster_dialog import RosterDialog


@pytest.fixture
def game(game_setup_service):
    """Home has a mistyped, nameless #41 and #14 Smith; away has #91."""
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.set_side_team(game.id, "home", home.id)
    game_setup_service.set_side_team(game.id, "away", away.id)
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=41
    )
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=14, full_name="Smith"
    )
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=away.id, jersey_number=91
    )
    return game, home, away


class _Confirm:
    """Stands in for the yes/no box: records each question, answers
    `answer`."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.questions: list[str] = []

    def __call__(self, question: str) -> bool:
        self.questions.append(question)
        return self.answer


def _make_dialog(qtbot, service, game, *, confirm=None):
    game_row, home, away = game
    dialog = RosterDialog(
        service,
        game_id=game_row.id,
        home_team_id=home.id,
        away_team_id=away.id,
        confirm=confirm or _Confirm(True),
    )
    qtbot.addWidget(dialog)
    return dialog


def _numbers(panel):
    return [
        panel.roster_table.item(row, 0).text()
        for row in range(panel.roster_table.rowCount())
    ]


def _select(panel, jersey_number):
    panel.roster_table.selectRow(_numbers(panel).index(str(jersey_number)))


def _renumber(qtbot, panel, from_number, to_number):
    _select(panel, from_number)
    panel.edit_jersey_field.setText(str(to_number))
    qtbot.mouseClick(panel.renumber_button, Qt.MouseButton.LeftButton)


def _remove(qtbot, panel, jersey_number):
    _select(panel, jersey_number)
    qtbot.mouseClick(panel.remove_button, Qt.MouseButton.LeftButton)


def _shot(session, game_row, *, at, shooter, assist1=None):
    session.add(
        ShotAttempt(
            game_id=game_row.id,
            video_timestamp=at,
            shooter_id=shooter,
            assist1_id=assist1,
            shot_type=ShotType.WRIST,
            shot_outcome=ShotOutcome.SAVED,
        )
    )
    session.commit()


def _player_id(service, game, team, jersey_number):
    return service.find_roster_entry(game.id, team.id, jersey_number).player_id


# -- opening on an existing game ----------------------------------------------


def test_each_side_shows_that_teams_roster(qtbot, game_setup_service, game):
    dialog = _make_dialog(qtbot, game_setup_service, game)

    assert _numbers(dialog.home_panel) == ["14", "41"]
    assert _numbers(dialog.away_panel) == ["91"]


def test_the_team_row_is_read_only(qtbot, game_setup_service, game):
    _game, home, _away = game
    dialog = _make_dialog(qtbot, game_setup_service, game)
    panel = dialog.home_panel

    assert panel.team_combo.currentData() == home.id
    for widget in (
        panel.team_combo,
        panel.new_team_name_field,
        panel.is_user_team_checkbox,
        panel.create_team_button,
    ):
        assert widget.isEnabled() is False


def test_entries_can_still_be_added(qtbot, game_setup_service, game):
    dialog = _make_dialog(qtbot, game_setup_service, game)
    panel = dialog.away_panel

    panel.jersey_field.setText("7")
    qtbot.mouseClick(panel.add_player_button, Qt.MouseButton.LeftButton)

    assert _numbers(panel) == ["7", "91"]


def test_selecting_an_entry_shows_its_jersey_number(qtbot, game_setup_service, game):
    dialog = _make_dialog(qtbot, game_setup_service, game)

    _select(dialog.home_panel, 41)

    assert dialog.home_panel.edit_jersey_field.text() == "41"


# -- renumber -----------------------------------------------------------------


def test_renumbering_onto_a_free_number_applies_at_once(
    qtbot, game_setup_service, game
):
    confirm = _Confirm(True)
    dialog = _make_dialog(qtbot, game_setup_service, game, confirm=confirm)

    _renumber(qtbot, dialog.home_panel, 41, 15)

    assert _numbers(dialog.home_panel) == ["14", "15"]
    assert confirm.questions == []


def test_renumbering_onto_a_taken_number_asks_to_merge(
    qtbot, game_setup_service, session, game
):
    game_row, home, _away = game
    stray_id = _player_id(game_setup_service, game_row, home, 41)
    smith_id = _player_id(game_setup_service, game_row, home, 14)
    for at in (1000, 2000, 3000):
        _shot(session, game_row, at=at, shooter=stray_id)
    confirm = _Confirm(True)
    dialog = _make_dialog(qtbot, game_setup_service, game, confirm=confirm)

    _renumber(qtbot, dialog.home_panel, 41, 14)

    assert confirm.questions == [
        "#14 is already Smith. Move #41's 3 events to Smith and remove #41? "
        "This can't be undone."
    ]
    assert _numbers(dialog.home_panel) == ["14"]
    assert game_setup_service.referencing_events(game_row.id, smith_id) != []


def test_the_merge_question_names_a_nameless_target_by_number(
    qtbot, game_setup_service, session, game
):
    game_row, home, _away = game
    game_setup_service.renumber_roster_entry(
        game_setup_service.find_roster_entry(game_row.id, home.id, 14).id, 9
    )
    game_setup_service.add_roster_entry(
        game_id=game_row.id, team_id=home.id, jersey_number=14
    )
    _shot(
        session,
        game_row,
        at=1000,
        shooter=_player_id(game_setup_service, game_row, home, 41),
    )
    confirm = _Confirm(False)
    dialog = _make_dialog(qtbot, game_setup_service, game, confirm=confirm)

    _renumber(qtbot, dialog.home_panel, 41, 14)

    assert confirm.questions == [
        "#14 is already on the roster. Move #41's 1 event to #14 and remove #41? "
        "This can't be undone."
    ]


def test_declining_the_merge_changes_nothing(qtbot, game_setup_service, game):
    dialog = _make_dialog(qtbot, game_setup_service, game, confirm=_Confirm(False))

    _renumber(qtbot, dialog.home_panel, 41, 14)

    assert _numbers(dialog.home_panel) == ["14", "41"]


def test_a_merge_refusal_is_shown_without_asking(
    qtbot, game_setup_service, session, game
):
    game_row, home, _away = game
    _shot(
        session,
        game_row,
        at=754_000,
        shooter=_player_id(game_setup_service, game_row, home, 41),
        assist1=_player_id(game_setup_service, game_row, home, 14),
    )
    confirm = _Confirm(True)
    dialog = _make_dialog(qtbot, game_setup_service, game, confirm=confirm)

    _renumber(qtbot, dialog.home_panel, 41, 14)

    assert confirm.questions == []
    assert dialog.home_panel.edit_error_label.text() == (
        "#41 and #14 Smith are both on 1 event — change it first: "
        "shot attempt @ 0:12:34"
    )
    assert _numbers(dialog.home_panel) == ["14", "41"]


def test_a_non_numeric_jersey_number_is_rejected(qtbot, game_setup_service, game):
    dialog = _make_dialog(qtbot, game_setup_service, game)

    _renumber(qtbot, dialog.home_panel, 41, "x")

    assert dialog.home_panel.edit_error_label.text() == "Enter a jersey number."
    assert _numbers(dialog.home_panel) == ["14", "41"]


# -- remove -------------------------------------------------------------------


def test_removing_an_unreferenced_entry_asks_then_removes_it(
    qtbot, game_setup_service, game
):
    _game_row, _home, _away = game
    confirm = _Confirm(True)
    dialog = _make_dialog(qtbot, game_setup_service, game, confirm=confirm)

    _remove(qtbot, dialog.home_panel, 41)

    assert confirm.questions == ["Remove #41 from this game's roster?"]
    assert _numbers(dialog.home_panel) == ["14"]
    assert dialog.home_panel.edit_group.isHidden() is True


def test_declining_the_removal_keeps_the_entry(qtbot, game_setup_service, game):
    dialog = _make_dialog(qtbot, game_setup_service, game, confirm=_Confirm(False))

    _remove(qtbot, dialog.home_panel, 41)

    assert _numbers(dialog.home_panel) == ["14", "41"]


def test_removing_a_referenced_entry_shows_the_refusal(
    qtbot, game_setup_service, session, game
):
    game_row, home, _away = game
    _shot(
        session,
        game_row,
        at=754_000,
        shooter=_player_id(game_setup_service, game_row, home, 41),
    )
    confirm = _Confirm(True)
    dialog = _make_dialog(qtbot, game_setup_service, game, confirm=confirm)

    _remove(qtbot, dialog.home_panel, 41)

    assert confirm.questions == []
    assert dialog.home_panel.edit_error_label.text() == (
        "#41 is used by 1 event — change it first: shot attempt @ 0:12:34"
    )
    assert _numbers(dialog.home_panel) == ["14", "41"]


def test_a_removed_player_leaves_the_player_picker(qtbot, game_setup_service, game):
    game_row, home, _away = game
    stray_id = _player_id(game_setup_service, game_row, home, 41)
    dialog = _make_dialog(qtbot, game_setup_service, game)
    panel = dialog.home_panel

    _remove(qtbot, panel, 41)

    picker_ids = [
        panel.player_combo.itemData(i) for i in range(panel.player_combo.count())
    ]
    assert stray_id not in picker_ids


def test_unit_edits_still_save_from_the_selected_entry(qtbot, game_setup_service, game):
    game_row, home, _away = game
    dialog = _make_dialog(qtbot, game_setup_service, game)
    panel = dialog.home_panel

    _select(panel, 14)
    panel.edit_unit_fields[UnitType.FORWARD_LINE].setValue(2)
    qtbot.mouseClick(panel.edit_save_button, Qt.MouseButton.LeftButton)

    smith_id = _player_id(game_setup_service, game_row, home, 14)
    assert game_setup_service.player_unit_assignments(game_row.id, smith_id) == {
        UnitType.FORWARD_LINE: 2
    }
