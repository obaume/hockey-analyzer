from __future__ import annotations

import pytest
from table_helpers import column as _column
from table_helpers import row as _row
from table_helpers import table_rows as _table

from hockey_analyzer.domain.enums import (
    EventType,
    Position,
    ShotOutcome,
    ShotType,
    Side,
    UnitType,
)
from hockey_analyzer.domain.game_data import load_game_data
from hockey_analyzer.domain.report_bundle import StrengthFilters
from hockey_analyzer.domain.stats_engine import ALL_SITUATIONS
from hockey_analyzer.domain.tagging_session import TaggingSession
from hockey_analyzer.ui.stats_dialog import StatsDialog


@pytest.fixture
def tagged_game(session, game_setup_service):
    """Home "Icebreakers" (#14 Jordan Kim, a center on Forward-Line 1 and
    Power-Play 1; goalie #30) vs. away "Rivals" (#91, goalie #35). Kim is
    on for a 5v5 home goal and a 5v5 away save; the home team also scores
    a 5v4 power-play goal."""
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    return _tag_game(session, game_setup_service, home, away)


def _tag_game(session, game_setup_service, home, away, *, same_players_as=None):
    """Tag the fixture's game; `same_players_as` (an earlier `GameData`)
    rosters the same `Player`s again rather than creating new ones."""
    game = game_setup_service.create_game()
    game_setup_service.set_side_team(game.id, Side.HOME, home.id)
    game_setup_service.set_side_team(game.id, Side.AWAY, away.id)
    for entry in same_players_as.roster if same_players_as else ():
        game_setup_service.add_roster_entry(
            game_id=game.id,
            team_id=entry.team_id,
            jersey_number=entry.jersey_number,
            player_id=entry.player_id,
        )
    tagging = TaggingSession(
        session, game_id=game.id, home_team_id=home.id, away_team_id=away.id
    )
    kim = tagging.resolve_or_create_roster_entry(home.id, 14, full_name="Jordan Kim")
    for team, jersey in ((home, 30), (away, 35)):
        goalie = tagging.resolve_or_create_roster_entry(team.id, jersey)
        game_setup_service.set_player_position(goalie.player_id, Position.GOALIE)
    rival = tagging.resolve_or_create_roster_entry(away.id, 91)
    game_setup_service.set_player_position(kim.player_id, Position.CENTER)
    for team, entry, unit_type in (
        (home, kim, UnitType.FORWARD_LINE),
        (home, kim, UnitType.POWER_PLAY),
        (away, rival, UnitType.FORWARD_LINE),
    ):
        game_setup_service.assign_unit(
            game_id=game.id,
            team_id=team.id,
            player_id=entry.player_id,
            unit_type=unit_type,
            unit_number=1,
        )

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


def _dialog(qtbot, *games):
    dialog = StatsDialog(list(games))
    qtbot.addWidget(dialog)
    return dialog


def _skater_row(dialog, label):
    return _row(dialog.skater_table, "Player", label)


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


# -- ticket 19: position & unit rollups, multi-game aggregates ----------


def test_positions_tab_rolls_skaters_up_by_team_and_position(qtbot, tagged_game):
    data, _kim = tagged_game
    dialog = _dialog(qtbot, data)

    centers = _row(dialog.position_table, "Team", "Icebreakers", "C")
    assert (centers["Skaters"], centers["CF"], centers["CA"]) == ("1", "1", "1")
    assert centers["+/-"] == "+1"

    _select(dialog.skater_strength_combo, "All situations")

    assert _row(dialog.position_table, "Team", "Icebreakers", "C")["CF"] == "2"


def test_units_tab_shows_each_unit_at_its_natural_strength(qtbot, tagged_game):
    data, _kim = tagged_game
    dialog = _dialog(qtbot, data)

    assert dialog.unit_strength_combo.currentText() == "Unit default"
    line = _row(dialog.unit_table, "Team", "Icebreakers", "Forward-Line 1")
    power_play = _row(dialog.unit_table, "Team", "Icebreakers", "Power-Play 1")
    assert line["Players"] == "#14 Jordan Kim"
    assert (line["CF"], line["CA"]) == ("1", "1")  # 5v5 only
    assert (power_play["CF"], power_play["GF"]) == ("1", "1")  # the 5v4 goal
    rivals = _row(dialog.unit_table, "Team", "Rivals", "Forward-Line 1")
    assert rivals["CF"] == "—"
    assert "opponent shifts" in rivals["Note"]

    _select(dialog.unit_strength_combo, "5v5")

    power_play = _row(dialog.unit_table, "Team", "Icebreakers", "Power-Play 1")
    assert (power_play["CF"], power_play["CA"]) == ("1", "1")


def test_single_game_view_has_no_coverage_line(qtbot, tagged_game):
    data, _kim = tagged_game
    dialog = _dialog(qtbot, data)

    assert dialog.coverage_label.isHidden() is True


def test_multi_game_view_sums_games_and_reports_included_and_excluded_games(
    qtbot, session, game_setup_service, tagged_game
):
    unflagged, _kim = tagged_game
    home, away = unflagged.game.home_team, unflagged.game.away_team
    flagged_data, _ = _tag_game(
        session, game_setup_service, home, away, same_players_as=unflagged
    )
    flagged_game = flagged_data.game
    flagged_game.opponent_shifts_complete = True
    session.commit()
    flagged = load_game_data(session, flagged_game.id)

    dialog = _dialog(qtbot, unflagged, flagged)

    assert "2 games" in dialog.windowTitle()
    # Team stats are never narrowed: both games' 5v5 attempts, summed.
    assert _table(dialog.team_table)["CF"] == ["2", "2"]
    assert _skater_row(dialog, "#14 Jordan Kim")["CF"] == "2"
    # Rivals' on-ice stats come from the flagged game only.
    assert _skater_row(dialog, "#91")["CF"] == "1"
    assert _row(dialog.unit_table, "Team", "Rivals", "Forward-Line 1")["CF"] == "1"
    assert dialog.coverage_label.isHidden() is False
    coverage = dialog.coverage_label.text()
    assert f"Rivals: 1 of 2 games (excluded: Game #{unflagged.game.id})" in coverage
    assert "Icebreakers: 2 of 2 games" in coverage


# -- ticket 26: exporting what's on screen as a report bundle -----------


class _FakeExportDialog:
    def __init__(self, games, *, filters, parent=None):
        self.games = games
        self.filters = filters
        self.parent = parent
        self.executed = False

    def exec(self):
        self.executed = True


def test_export_hands_the_games_and_current_filters_to_the_report_export(
    qtbot, tagged_game
):
    data, _kim = tagged_game
    opened = []

    def factory(games, *, filters, parent=None):
        opened.append(_FakeExportDialog(games, filters=filters, parent=parent))
        return opened[-1]

    dialog = StatsDialog([data], export_dialog_factory=factory)
    qtbot.addWidget(dialog)
    _select(dialog.skater_strength_combo, "All situations")
    _select(dialog.unit_strength_combo, "5v4")
    _select(dialog.goalie_strength_combo, "5v5")

    dialog.export_button.click()

    (export,) = opened
    assert export.executed is True
    assert export.parent is dialog
    assert [game.game.id for game in export.games] == [data.game.id]
    assert export.filters == StrengthFilters(
        team=ALL_SITUATIONS, skaters=ALL_SITUATIONS, goalies="5v5", units="5v4"
    )
