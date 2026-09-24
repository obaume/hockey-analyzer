from __future__ import annotations

import pytest

from hockey_analyzer.domain.enums import (
    EventType,
    Position,
    ShotOutcome,
    ShotType,
    Side,
)
from hockey_analyzer.domain.game_data import load_game_data
from hockey_analyzer.domain.tagging_session import TaggingSession
from hockey_analyzer.ui.stats_dialog import StatsDialog


@pytest.fixture
def tagged_game(session, game_setup_service):
    """Home "Icebreakers" (#14 Jordan Kim, goalie #30) vs. away "Rivals"
    (#91, goalie #35). Kim is on for a 5v5 home goal and a 5v5 away
    save; the home team also scores a 5v4 power-play goal."""
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.set_side_team(game.id, Side.HOME, home.id)
    game_setup_service.set_side_team(game.id, Side.AWAY, away.id)
    tagging = TaggingSession(
        session, game_id=game.id, home_team_id=home.id, away_team_id=away.id
    )
    kim = tagging.resolve_or_create_roster_entry(home.id, 14, full_name="Jordan Kim")
    for team, jersey in ((home, 30), (away, 35)):
        goalie = tagging.resolve_or_create_roster_entry(team.id, jersey)
        game_setup_service.set_player_position(goalie.player_id, Position.GOALIE)
    tagging.resolve_or_create_roster_entry(away.id, 91)

    tagging.log_ad_hoc_line_change("home", [14, 30], 1000, on_ice=True)
    tagging.log_ad_hoc_line_change("away", [91, 35], 1000, on_ice=True)
    for at, side, outcome, strength in (
        (2000, "home", ShotOutcome.GOAL, "5v5"),
        (3000, "away", ShotOutcome.SAVED, "5v5"),
        (4000, "home", ShotOutcome.GOAL, "5v4"),
    ):
        shot = tagging.log_event(
            EventType.SHOT_ATTEMPT,
            at,
            strength_state=strength,
            shot_outcome=outcome,
            shot_type=ShotType.WRIST,
        )
        tagging.update_event(
            shot.id, shot_x=80.0 if side == "home" else -40.0, shot_y=0.0
        )
        tagging.set_player_reference(shot.id, side, reference="shooter", unknown=True)
    return load_game_data(session, game.id), kim


def _dialog(qtbot, data):
    dialog = StatsDialog(data)
    qtbot.addWidget(dialog)
    return dialog


def _table(table):
    """A QTableWidget as {row header: [cell texts]}."""
    return {
        table.verticalHeaderItem(row).text(): [
            table.item(row, column).text() for column in range(table.columnCount())
        ]
        for row in range(table.rowCount())
    }


def _column(table, header):
    return next(
        column
        for column in range(table.columnCount())
        if table.horizontalHeaderItem(column).text() == header
    )


def _skater_row(dialog, label):
    table = dialog.skater_table
    row = next(
        row
        for row in range(table.rowCount())
        if table.item(row, _column(table, "Player")).text() == label
    )
    return {
        table.horizontalHeaderItem(column).text(): table.item(row, column).text()
        for column in range(table.columnCount())
    }


def _select(combo, label):
    combo.setCurrentIndex(combo.findText(label))


def test_team_table_shows_both_sides_at_5v5_by_default(qtbot, tagged_game):
    data, _kim = tagged_game
    dialog = _dialog(qtbot, data)

    headers = [
        dialog.team_table.horizontalHeaderItem(column).text()
        for column in range(dialog.team_table.columnCount())
    ]
    assert headers == ["Icebreakers", "Rivals"]
    rows = _table(dialog.team_table)
    assert rows["CF"] == ["1", "1"]
    assert rows["CF%"] == ["50.0%", "50.0%"]
    # 5v5: Icebreakers scored on their only shot on goal and saved Rivals'.
    assert rows["PDO"] == ["2000", "0"]


def test_changing_the_skater_filter_recomputes_team_and_skater_stats(
    qtbot, tagged_game
):
    data, _kim = tagged_game
    dialog = _dialog(qtbot, data)

    _select(dialog.skater_strength_combo, "All situations")

    assert _table(dialog.team_table)["CF"] == ["2", "1"]
    assert _skater_row(dialog, "#14 Jordan Kim")["+/-"] == "+2"


def test_skater_table_lists_home_stats_and_excluded_opponents(qtbot, tagged_game):
    data, _kim = tagged_game
    dialog = _dialog(qtbot, data)

    kim = _skater_row(dialog, "#14 Jordan Kim")
    assert kim["Team"] == "Icebreakers"
    assert (kim["CF"], kim["CA"], kim["+/-"]) == ("1", "1", "+1")
    opponent = _skater_row(dialog, "#91")
    assert opponent["CF"] == "—"
    assert "opponent shifts" in opponent["Note"]


def test_goalie_table_defaults_to_all_situations(qtbot, tagged_game):
    data, _kim = tagged_game
    dialog = _dialog(qtbot, data)

    assert dialog.goalie_strength_combo.currentText() == "All situations"
    goalies = _table(dialog.goalie_table)
    # Home goalie #30 faced one saved shot; away #35 two goals.
    assert goalies["#30"][_column(dialog.goalie_table, "SV%")] == "1.000"
    assert goalies["#35"][_column(dialog.goalie_table, "SV%")] == ".000"
    assert goalies["#35"][_column(dialog.goalie_table, "GA")] == "2"

    _select(dialog.goalie_strength_combo, "5v5")

    assert _table(dialog.goalie_table)["#35"][_column(dialog.goalie_table, "GA")] == "1"


def test_caveat_line_is_hidden_when_every_shift_change_is_resolved(qtbot, tagged_game):
    data, _kim = tagged_game
    dialog = _dialog(qtbot, data)

    assert dialog.caveat_label.isHidden() is True


def test_unresolved_shift_changes_are_reported_once_per_team(
    qtbot, session, tagged_game
):
    data, _kim = tagged_game
    game = data.game
    tagging = TaggingSession(
        session,
        game_id=game.id,
        home_team_id=game.home_team_id,
        away_team_id=game.away_team_id,
    )
    stub = tagging.log_event(EventType.SHIFT_CHANGE, 5000)
    tagging.set_player_reference(stub.id, "home", unknown=True)

    dialog = _dialog(qtbot, load_game_data(session, game.id))

    assert dialog.caveat_label.isHidden() is False
    assert "Icebreakers: 1 shift change" in dialog.caveat_label.text()
    assert "Rivals" not in dialog.caveat_label.text()


def test_shot_quality_table_shows_type_counts_and_high_danger_share(qtbot, tagged_game):
    data, _kim = tagged_game
    dialog = _dialog(qtbot, data)

    rows = _table(dialog.shot_quality_table)
    assert rows["Wrist"] == ["1", "1"]
    assert rows["High-danger share"] == ["100.0%", "0.0%"]
