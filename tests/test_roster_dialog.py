from __future__ import annotations

from PySide6.QtCore import Qt

from hockey_analyzer.domain.enums import ShotOutcome, ShotType, Side, UnitType
from hockey_analyzer.domain.models import ShotAttempt
from hockey_analyzer.ui.roster_dialog import RosterDialog


class _Confirm:
    """Answers every confirmation with `answer`, recording each question."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.questions: list[str] = []

    def __call__(self, question: str) -> bool:
        self.questions.append(question)
        return self.answer


def _game_with_sides(service):
    game = service.create_game()
    home = service.create_team("Icebreakers")
    away = service.create_team("Rivals")
    service.set_side_team(game.id, Side.HOME, home.id)
    service.set_side_team(game.id, Side.AWAY, away.id)
    return game, home, away


def _make_dialog(qtbot, service, game, home, away, *, confirm=None):
    dialog = RosterDialog(
        service,
        game_id=game.id,
        home_team_id=home.id,
        away_team_id=away.id,
        confirm=confirm or _Confirm(True),
    )
    qtbot.addWidget(dialog)
    return dialog


def _shot(session, game_id, *, at, shooter_id, assist1_id=None):
    shot = ShotAttempt(
        game_id=game_id,
        video_timestamp=at,
        shot_type=ShotType.WRIST,
        shot_outcome=ShotOutcome.SAVED,
        shooter_id=shooter_id,
        assist1_id=assist1_id,
    )
    session.add(shot)
    session.commit()
    return shot


def _select_jersey(panel, jersey_number):
    for row in range(panel.roster_table.rowCount()):
        if panel.roster_table.item(row, 0).text() == str(jersey_number):
            panel.roster_table.selectRow(row)
            return
    raise AssertionError(f"#{jersey_number} not on the roster")


def _renumber(qtbot, panel, jersey_number):
    panel.edit_jersey_field.setText(str(jersey_number))
    qtbot.mouseClick(panel.renumber_button, Qt.MouseButton.LeftButton)


def _jerseys(service, game, team):
    return [entry.jersey_number for entry in service.list_roster(game.id, team.id)]


def test_each_side_shows_its_team_read_only(qtbot, game_setup_service):
    game, home, away = _game_with_sides(game_setup_service)

    dialog = _make_dialog(qtbot, game_setup_service, game, home, away)

    assert dialog.home_panel.team_combo.currentData() == home.id
    assert dialog.away_panel.team_combo.currentData() == away.id
    for panel in (dialog.home_panel, dialog.away_panel):
        assert not panel.team_combo.isEnabled()
        assert not panel.new_team_name_field.isEnabled()
        assert not panel.is_user_team_checkbox.isEnabled()
        assert not panel.create_team_button.isEnabled()


def test_the_existing_roster_is_listed_and_can_be_added_to(qtbot, game_setup_service):
    game, home, away = _game_with_sides(game_setup_service)
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=14
    )
    dialog = _make_dialog(qtbot, game_setup_service, game, home, away)
    panel = dialog.home_panel
    assert panel.roster_table.rowCount() == 1

    panel.jersey_field.setText("9")
    qtbot.mouseClick(panel.add_player_button, Qt.MouseButton.LeftButton)

    assert _jerseys(game_setup_service, game, home) == [9, 14]


def test_renumbering_onto_a_free_number_changes_the_entry(qtbot, game_setup_service):
    game, home, away = _game_with_sides(game_setup_service)
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=41
    )
    dialog = _make_dialog(qtbot, game_setup_service, game, home, away)
    panel = dialog.home_panel
    _select_jersey(panel, 41)
    assert panel.edit_jersey_field.text() == "41"

    _renumber(qtbot, panel, 14)

    assert _jerseys(game_setup_service, game, home) == [14]
    assert panel.roster_table.item(0, 0).text() == "14"


def test_renumbering_onto_a_taken_number_offers_a_merge(
    qtbot, session, game_setup_service
):
    game, home, away = _game_with_sides(game_setup_service)
    target = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=14, full_name="Smith"
    )
    stray = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=41
    )
    shots = [
        _shot(session, game.id, at=at, shooter_id=stray.player_id)
        for at in (1000, 2000, 3000)
    ]
    confirm = _Confirm(True)
    dialog = _make_dialog(qtbot, game_setup_service, game, home, away, confirm=confirm)
    panel = dialog.home_panel
    _select_jersey(panel, 41)

    _renumber(qtbot, panel, 14)

    assert confirm.questions == [
        "#14 is already Smith. Move #41's 3 events to Smith and remove #41? "
        "This can't be undone."
    ]
    assert _jerseys(game_setup_service, game, home) == [14]
    assert [shot.shooter_id for shot in shots] == [target.player_id] * 3


def test_a_merge_onto_a_nameless_player_names_them_by_number(qtbot, game_setup_service):
    game, home, away = _game_with_sides(game_setup_service)
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=14
    )
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=41
    )
    confirm = _Confirm(False)
    dialog = _make_dialog(qtbot, game_setup_service, game, home, away, confirm=confirm)
    panel = dialog.home_panel
    _select_jersey(panel, 41)

    _renumber(qtbot, panel, 14)

    assert confirm.questions == [
        "#14 is already on the roster. Move #41's 0 events to #14 and remove #41? "
        "This can't be undone."
    ]


def test_cancelling_a_merge_changes_nothing(qtbot, game_setup_service):
    game, home, away = _game_with_sides(game_setup_service)
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=14
    )
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=41
    )
    dialog = _make_dialog(
        qtbot, game_setup_service, game, home, away, confirm=_Confirm(False)
    )
    panel = dialog.home_panel
    _select_jersey(panel, 41)

    _renumber(qtbot, panel, 14)

    assert _jerseys(game_setup_service, game, home) == [14, 41]


def test_a_merge_refused_over_a_shared_event_lists_it(
    qtbot, session, game_setup_service
):
    game, home, away = _game_with_sides(game_setup_service)
    target = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=14
    )
    stray = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=41
    )
    _shot(
        session,
        game.id,
        at=754_000,
        shooter_id=stray.player_id,
        assist1_id=target.player_id,
    )
    confirm = _Confirm(True)
    dialog = _make_dialog(qtbot, game_setup_service, game, home, away, confirm=confirm)
    panel = dialog.home_panel
    _select_jersey(panel, 41)

    _renumber(qtbot, panel, 14)

    # Refused up front: never asked to confirm a merge that can't happen.
    assert confirm.questions == []
    assert panel.edit_error_label.text() == (
        "#41 and #14 are both in 1 event — change it first: shot attempt @ 0:12:34"
    )
    assert _jerseys(game_setup_service, game, home) == [14, 41]


def test_removing_an_unused_entry_drops_it_and_its_units(qtbot, game_setup_service):
    game, home, away = _game_with_sides(game_setup_service)
    entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=41
    )
    game_setup_service.assign_unit(
        game_id=game.id,
        team_id=home.id,
        player_id=entry.player_id,
        unit_type=UnitType.FORWARD_LINE,
        unit_number=1,
    )
    player_id = entry.player_id
    confirm = _Confirm(True)
    dialog = _make_dialog(qtbot, game_setup_service, game, home, away, confirm=confirm)
    panel = dialog.home_panel
    _select_jersey(panel, 41)

    qtbot.mouseClick(panel.remove_button, Qt.MouseButton.LeftButton)

    assert confirm.questions == ["Remove #41 from this game's roster?"]
    assert _jerseys(game_setup_service, game, home) == []
    assert game_setup_service.player_unit_assignments(game.id, player_id) == {}
    assert panel.roster_table.rowCount() == 0
    assert not panel.edit_group.isVisibleTo(panel)


def test_a_removal_refused_over_referencing_events_lists_them(
    qtbot, session, game_setup_service
):
    game, home, away = _game_with_sides(game_setup_service)
    entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=41
    )
    _shot(session, game.id, at=754_000, shooter_id=entry.player_id)
    _shot(session, game.id, at=3_725_000, shooter_id=entry.player_id)
    dialog = _make_dialog(qtbot, game_setup_service, game, home, away)
    panel = dialog.home_panel
    _select_jersey(panel, 41)

    qtbot.mouseClick(panel.remove_button, Qt.MouseButton.LeftButton)

    assert panel.edit_error_label.text() == (
        "#41 is used by 2 events — change them first: "
        "shot attempt @ 0:12:34, shot attempt @ 1:02:05"
    )
    assert _jerseys(game_setup_service, game, home) == [41]


def test_a_removed_player_leaves_the_player_picker(qtbot, game_setup_service):
    game, home, away = _game_with_sides(game_setup_service)
    entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=41, full_name="Typo"
    )
    dialog = _make_dialog(qtbot, game_setup_service, game, home, away)
    panel = dialog.home_panel
    _select_jersey(panel, 41)

    qtbot.mouseClick(panel.remove_button, Qt.MouseButton.LeftButton)

    picker = [panel.player_combo.itemData(i) for i in range(panel.player_combo.count())]
    assert entry.player_id not in picker
