from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent

from hockey_analyzer.domain.enums import EventType
from hockey_analyzer.domain.tagging_session import TaggingSession
from hockey_analyzer.ui.keys import key_string
from hockey_analyzer.ui.shortcuts import ShortcutRegistry
from hockey_analyzer.ui.tagging_panel import TaggingPanel


def _make_panel(qtbot, tagging_session, *, position_ms: int = 0, shortcuts=None):
    panel = TaggingPanel(
        tagging_session,
        current_position_ms=lambda: position_ms,
        shortcuts=shortcuts if shortcuts is not None else ShortcutRegistry(),
    )
    qtbot.addWidget(panel)
    # Shown so isVisible() (used to assert the edit panel appears/hides)
    # reflects our own setVisible() calls rather than always reading
    # False for a never-shown top-level widget.
    panel.show()
    qtbot.waitExposed(panel)
    return panel


def _select_row(panel, row: int) -> None:
    panel.event_table.selectRow(row)


def _focus_jersey_field(panel) -> None:
    # In a real, active application, _populate_edit_panel's own
    # setFocus() call (the auto-focus behavior) synchronously fires
    # focusInEvent. This test process has no real window manager, so
    # setFocus() alone only records the pending focus target -- fire the
    # event directly so tests exercise the same suspend/resume behavior
    # a real user's keystrokes would trigger.
    panel.jersey_field.setFocus()
    panel.jersey_field.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))


def _blur_jersey_field(panel) -> None:
    panel.event_table.setFocus()
    panel.jersey_field.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))


# -- instant capture: buttons + hotkeys ------------------------------------


def test_stoppage_button_logs_an_event_at_the_current_position(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session, position_ms=4200)

    qtbot.mouseClick(panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton)

    events = tagging_session.list_events()
    assert len(events) == 1
    assert events[0].video_timestamp == 4200
    assert events[0].event_type is EventType.STOPPAGE


def test_each_event_type_has_a_working_log_button(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)

    for event_type, button in panel.log_buttons.items():
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    logged_types = {event.event_type for event in tagging_session.list_events()}
    assert logged_types == set(panel.log_buttons.keys())


def test_hotkeys_1_through_5_log_each_event_type_via_the_shared_registry(qtbot, tagging_session):
    registry = ShortcutRegistry()
    panel = _make_panel(qtbot, tagging_session, shortcuts=registry)

    for key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4, Qt.Key.Key_5):
        assert registry.dispatch(key_string(key)) is True

    assert len(tagging_session.list_events()) == 5


def test_logging_an_event_never_touches_a_player_or_controller(qtbot, tagging_session):
    # Regression guard for "instant capture without pausing playback":
    # the panel's only interface to playback position is the injected
    # current_position_ms callable, so there is nothing here that could
    # call pause().
    panel = _make_panel(qtbot, tagging_session)
    assert not hasattr(panel, "player")
    assert not hasattr(panel, "controller")


def test_log_buttons_opt_out_of_keyboard_focus(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    for button in panel.log_buttons.values():
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus


# -- always-visible event log + inline edit ---------------------------------


def test_logging_an_event_adds_a_row_to_the_table(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session, position_ms=1000)

    qtbot.mouseClick(panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton)

    assert panel.event_table.rowCount() == 1
    assert panel.event_table.item(0, 1).text() == "Stoppage"


def test_selecting_a_row_reveals_the_edit_panel(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.PERIOD_START], Qt.MouseButton.LeftButton)
    assert panel.edit_group.isVisible() is False

    _select_row(panel, 0)

    assert panel.edit_group.isVisible() is True


def test_editing_period_number_persists_through_the_session(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.PERIOD_START], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    panel.period_number_field.setValue(2)
    panel.period_number_field.editingFinished.emit()

    event_id = tagging_session.list_events()[0].id
    assert tagging_session.get_event(event_id).period_number == 2


def test_editing_strength_state_overrides_it(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    panel.strength_state_field.setText("4v4")
    panel.strength_state_field.editingFinished.emit()

    event_id = tagging_session.list_events()[0].id
    assert tagging_session.get_event(event_id).strength_state == "4v4"


def test_jersey_home_button_resolves_and_creates_roster_entry(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    panel.jersey_field.setText("14")
    qtbot.mouseClick(panel.home_button, Qt.MouseButton.LeftButton)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.shift_player_id is not None
    assert event.shift_player_unknown is False
    assert event.shift_team_id == tagging_session.home_team_id


def test_selecting_a_player_reference_row_auto_focuses_the_jersey_field(qtbot, tagging_session):
    # Live-tag speed (ticket 08) needs the tagger's next keystrokes to
    # land in the jersey field immediately after selecting a row, with
    # no extra click required to reach it. focusWidget() (the widget that
    # would receive keyboard input) is checked rather than hasFocus(),
    # which additionally requires the top-level window to be OS-active --
    # true for a real user, but not guaranteed in every test environment.
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton)

    _select_row(panel, 0)

    assert panel.focusWidget() is panel.jersey_field


def test_jersey_field_h_key_resolves_against_home_team(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)
    _focus_jersey_field(panel)

    qtbot.keyClicks(panel.jersey_field, "9")
    qtbot.keyClick(panel.jersey_field, Qt.Key.Key_H)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.shift_team_id == tagging_session.home_team_id
    assert event.shift_player_unknown is False


def test_jersey_field_a_key_resolves_against_away_team(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.PENALTY], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)
    _focus_jersey_field(panel)

    qtbot.keyClicks(panel.jersey_field, "9")
    qtbot.keyClick(panel.jersey_field, Qt.Key.Key_A)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.penalty_team_id == tagging_session.away_team_id


def test_jersey_number_containing_1_through_5_does_not_trigger_event_type_hotkeys(qtbot, tagging_session):
    # Regression (ADR-0007): TAGGING_SCOPE's digit hotkeys (1-5) must be
    # suspended while the jersey field has focus, since jersey numbers
    # routinely contain those same digits (e.g. "14").
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)
    _focus_jersey_field(panel)

    qtbot.keyClicks(panel.jersey_field, "14")

    assert panel.jersey_field.text() == "14"
    assert len(tagging_session.list_events()) == 1


def test_tagging_hotkeys_resume_once_the_jersey_field_loses_focus(qtbot, tagging_session):
    # dispatch() doesn't just check bindability, it *invokes* the bound
    # action -- so this test makes exactly one side-effecting dispatch
    # call. (Dispatching "1" while still focused would log a PERIOD_START,
    # refresh(), and re-populate the still-selected row, which re-focuses
    # the jersey field and would silently undo the very state this test
    # is checking.)
    registry = ShortcutRegistry()
    panel = _make_panel(qtbot, tagging_session, shortcuts=registry)
    qtbot.mouseClick(panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)
    _focus_jersey_field(panel)  # suspends TAGGING_SCOPE

    _blur_jersey_field(panel)  # resumes TAGGING_SCOPE

    assert registry.dispatch(key_string(Qt.Key.Key_2)) is True
    assert len(tagging_session.list_events()) == 2


def test_jersey_entry_hotkeys_are_suspended_once_the_field_loses_focus(qtbot, tagging_session):
    registry = ShortcutRegistry()
    panel = _make_panel(qtbot, tagging_session, shortcuts=registry)
    qtbot.mouseClick(panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)
    _focus_jersey_field(panel)

    _blur_jersey_field(panel)

    assert registry.dispatch(key_string(Qt.Key.Key_H)) is False


def test_jersey_entry_scope_is_suspended_until_the_field_has_focus(qtbot, tagging_session):
    registry = ShortcutRegistry()
    _make_panel(qtbot, tagging_session, shortcuts=registry)

    assert registry.dispatch(key_string(Qt.Key.Key_H)) is False


def test_unknown_checkbox_starts_unchecked_even_though_a_fresh_stub_is_unknown(qtbot, tagging_session):
    # Regression: a freshly-logged event defaults its player reference to
    # explicit unknown (see TaggingSession.log_event), so the checkbox
    # must NOT mirror that on selection -- otherwise typing a jersey
    # number and pressing Home/Away would silently be ignored.
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    assert panel.unknown_checkbox.isChecked() is False


def test_unknown_checkbox_marks_the_player_reference_unknown(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.PENALTY], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    panel.unknown_checkbox.setChecked(True)
    qtbot.mouseClick(panel.home_button, Qt.MouseButton.LeftButton)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.penalty_player_unknown is True
    assert event.penalty_player_id is None


def test_delete_button_removes_the_event_and_hides_the_edit_panel(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    qtbot.mouseClick(panel.delete_button, Qt.MouseButton.LeftButton)

    assert tagging_session.list_events() == []
    assert panel.event_table.rowCount() == 0
    assert panel.edit_group.isVisible() is False


def test_field_visibility_matches_event_type(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    assert panel.edit_form.isRowVisible(panel.period_number_field) is False
    assert panel.edit_form.isRowVisible(panel.player_reference_row) is False
    assert panel.edit_form.isRowVisible(panel.on_ice_checkbox) is False
    assert panel.edit_form.isRowVisible(panel.duration_field) is False


def test_field_visibility_for_shift_change(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    assert panel.edit_form.isRowVisible(panel.player_reference_row) is True
    assert panel.edit_form.isRowVisible(panel.on_ice_checkbox) is True
    assert panel.edit_form.isRowVisible(panel.duration_field) is False


def test_selecting_a_row_does_not_write_any_field_by_itself(qtbot, tagging_session):
    # Regression: populating on_ice_checkbox's displayed state via
    # setChecked() must not re-enter _commit_field -> refresh() as a side
    # effect of merely selecting a row (QCheckBox.toggled, unlike the
    # other fields' editingFinished, fires on a programmatic set too).
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton)
    event_id = tagging_session.list_events()[0].id
    tagging_session.update_event(event_id, shift_on_ice=True)

    calls = []
    original_update_event = tagging_session.update_event
    tagging_session.update_event = lambda *args, **kwargs: calls.append((args, kwargs)) or original_update_event(
        *args, **kwargs
    )

    _select_row(panel, 0)

    assert calls == []
    assert panel.on_ice_checkbox.isChecked() is True


def test_event_log_stays_ordered_by_video_timestamp_after_edits(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session, position_ms=500)
    qtbot.mouseClick(panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton)

    panel._current_position_ms = lambda: 100
    qtbot.mouseClick(panel.log_buttons[EventType.PERIOD_START], Qt.MouseButton.LeftButton)

    assert panel.event_table.item(0, 1).text() == "Period Start"
    assert panel.event_table.item(1, 1).text() == "Stoppage"
