from __future__ import annotations

from hockey_analyzer.domain.enums import EventType, Position
from hockey_analyzer.domain.models import Player
from hockey_analyzer.ui.on_ice_dock import OnIceDock


def _shift(tagging_session, side, jersey, on, at, *, name=None):
    team_id = (
        tagging_session.home_team_id if side == "home" else tagging_session.away_team_id
    )
    entry = tagging_session.resolve_or_create_roster_entry(
        team_id, jersey, full_name=name
    )
    event = tagging_session.log_event(EventType.SHIFT_CHANGE, at)
    tagging_session.set_player_reference(event.id, side, jersey_number=jersey)
    tagging_session.update_event(event.id, shift_on_ice=on)
    return entry


def _make_dock(qtbot, tagging_session=None):
    dock = OnIceDock()
    qtbot.addWidget(dock)
    dock.set_session(tagging_session)
    return dock


def _rows(view):
    return [
        [view.table.item(row, column).text() for column in range(4)]
        for row in range(view.table.rowCount())
    ]


def test_without_a_session_both_tabs_are_empty(qtbot):
    dock = _make_dock(qtbot)

    assert [dock.tabs.tabText(i) for i in range(2)] == ["Home 0", "Away 0"]
    assert dock.team_views["home"].header.text() == "0 skaters, no goalie"


def test_shows_who_is_on_at_the_current_position(qtbot, tagging_session, session):
    _shift(tagging_session, "home", 14, True, 1_000, name="Jordan Kim")
    goalie = _shift(tagging_session, "home", 30, True, 1_000)
    session.get(Player, goalie.player_id).position = Position.GOALIE
    _shift(tagging_session, "home", 9, True, 1_000)
    _shift(tagging_session, "home", 9, False, 3_000)
    dock = _make_dock(qtbot, tagging_session)

    dock.set_position(2_000)
    assert dock.tabs.tabText(0) == "Home 2+G"
    assert dock.team_views["home"].header.text() == "2 skaters + G"
    assert [row[0] for row in _rows(dock.team_views["home"])] == ["#9", "#14"]
    assert dock.team_views["home"].goalie_label.text() == "G  #30"

    dock.set_position(5_000)
    assert _rows(dock.team_views["home"]) == [["#14", "Jordan Kim", "", "0:00"]]

    dock.set_position(500)
    assert dock.tabs.tabText(0) == "Home 0"


def test_reload_picks_up_newly_logged_shift_changes(qtbot, tagging_session):
    dock = _make_dock(qtbot, tagging_session)
    dock.set_position(5_000)

    _shift(tagging_session, "away", 91, True, 1_000)
    assert dock.tabs.tabText(1) == "Away 0"

    dock.reload()
    assert dock.tabs.tabText(1) == "Away 1"


def test_a_long_shift_is_highlighted(qtbot, tagging_session):
    tagging_session.log_event(EventType.FACEOFF, 0)
    _shift(tagging_session, "home", 14, True, 0)
    _shift(tagging_session, "home", 9, True, 100_000)
    dock = _make_dock(qtbot, tagging_session)

    dock.set_position(130_000)

    table = dock.team_views["home"].table
    shifts = {table.item(row, 0).text(): table.item(row, 0) for row in range(2)}
    assert table.item(1, 3).text() == "2:10"
    assert shifts["#14"].font().bold()
    assert not shifts["#9"].font().bold()


def test_unresolved_shift_changes_are_noted(qtbot, tagging_session):
    event = tagging_session.log_event(EventType.SHIFT_CHANGE, 1_000)
    tagging_session.set_player_reference(event.id, "home", unknown=True)
    tagging_session.update_event(event.id, shift_on_ice=True)
    dock = _make_dock(qtbot, tagging_session)
    dock.set_position(2_000)

    assert dock.team_views["home"].unresolved_label.text() == (
        "1 unresolved shift change"
    )
    assert dock.team_views["away"].unresolved_label.text() == ""


def test_flip_tab_alternates_between_the_two_sides(qtbot):
    dock = _make_dock(qtbot)

    dock.flip_tab()
    assert dock.tabs.currentIndex() == 1
    dock.flip_tab()
    assert dock.tabs.currentIndex() == 0
