from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QDialog

from hockey_analyzer.domain.enums import (
    EventType,
    RinkType,
    ShotOutcome,
    ShotType,
    UnitType,
)
from hockey_analyzer.domain.game_setup import GameSetupService
from hockey_analyzer.ui.keys import key_string
from hockey_analyzer.ui.rink_view import RinkClickDialog
from hockey_analyzer.ui.shortcuts import ShortcutRegistry
from hockey_analyzer.ui.tagging_panel import TaggingPanel


class _FakeRinkClickDialog:
    """Stands in for `RinkClickDialog` in tests: a real one's `exec()`
    blocks on a modal event loop with nothing to click it, so every test
    that logs/relocates a faceoff or shot_attempt injects one of these
    (or `_FakeShotAttemptDialog`) instead, via the panel's dialog-factory
    seams. `accepted=False` simulates the tagger dismissing the dialog
    without clicking a location."""

    def __init__(
        self, x: float = 40.0, y: float = 5.0, *, accepted: bool = True
    ) -> None:
        self.x = x if accepted else None
        self.y = y if accepted else None
        self._accepted = accepted

    def exec(self) -> QDialog.DialogCode:
        return (
            QDialog.DialogCode.Accepted
            if self._accepted
            else QDialog.DialogCode.Rejected
        )


class _FakeShotAttemptDialog(_FakeRinkClickDialog):
    def __init__(
        self,
        x: float = 85.0,
        y: float = 0.0,
        *,
        accepted: bool = True,
        shot_outcome: ShotOutcome = ShotOutcome.GOAL,
        shot_type: ShotType = ShotType.WRIST,
    ) -> None:
        super().__init__(x, y, accepted=accepted)
        self.shot_outcome = shot_outcome
        self.shot_type = shot_type


class _FakeLineChangeDialog:
    """Stands in for `LineChangeDialog`, for the same modal-`exec()`
    reason as `_FakeRinkClickDialog`. Built via `_line_change_factory`,
    which records the `units`/`roster` the panel offered so tests can
    assert on them and pick a unit."""

    def __init__(
        self,
        units,
        roster=None,
        *,
        accepted=True,
        team_side="home",
        on_ice=True,
        unit_index=None,
        jersey_numbers=(),
    ):
        self.offered_units = units
        self.offered_roster = roster
        self._accepted = accepted
        self.team_side = team_side
        self.on_ice = on_ice
        self.unit = units[team_side][unit_index][1] if unit_index is not None else None
        self.jersey_numbers = list(jersey_numbers)

    def exec(self) -> QDialog.DialogCode:
        return (
            QDialog.DialogCode.Accepted
            if self._accepted
            else QDialog.DialogCode.Rejected
        )


def _line_change_factory(**choice):
    opened = []

    def factory(units, roster):
        dialog = _FakeLineChangeDialog(units, roster, **choice)
        opened.append(dialog)
        return dialog

    factory.opened = opened
    return factory


def _make_panel(
    qtbot,
    tagging_session,
    *,
    position_ms: int = 0,
    shortcuts=None,
    pause=None,
    rink_click_dialog_factory=None,
    shot_attempt_dialog_factory=None,
    line_change_dialog_factory=None,
):
    panel = TaggingPanel(
        tagging_session,
        current_position_ms=lambda: position_ms,
        shortcuts=shortcuts if shortcuts is not None else ShortcutRegistry(),
        pause=pause,
        rink_click_dialog_factory=rink_click_dialog_factory or _FakeRinkClickDialog,
        shot_attempt_dialog_factory=shot_attempt_dialog_factory
        or _FakeShotAttemptDialog,
        line_change_dialog_factory=line_change_dialog_factory
        or _line_change_factory(accepted=False),
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

    for button in panel.log_buttons.values():
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    logged_types = {event.event_type for event in tagging_session.list_events()}
    assert logged_types == set(panel.log_buttons.keys())


def test_hotkeys_1_through_5_log_each_event_type_via_the_shared_registry(
    qtbot, tagging_session
):
    registry = ShortcutRegistry()
    _panel = _make_panel(qtbot, tagging_session, shortcuts=registry)

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
    qtbot.mouseClick(
        panel.log_buttons[EventType.PERIOD_START], Qt.MouseButton.LeftButton
    )
    assert panel.edit_group.isVisible() is False

    _select_row(panel, 0)

    assert panel.edit_group.isVisible() is True


def test_editing_period_number_persists_through_the_session(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(
        panel.log_buttons[EventType.PERIOD_START], Qt.MouseButton.LeftButton
    )
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
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton
    )
    _select_row(panel, 0)

    panel.jersey_field.setText("14")
    qtbot.mouseClick(panel.home_button, Qt.MouseButton.LeftButton)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.shift_player_id is not None
    assert event.shift_player_unknown is False
    assert event.shift_team_id == tagging_session.home_team_id


def test_selecting_a_player_reference_row_auto_focuses_the_jersey_field(
    qtbot, tagging_session
):
    # Live-tag speed (ticket 08) needs the tagger's next keystrokes to
    # land in the jersey field immediately after selecting a row, with
    # no extra click required to reach it. focusWidget() (the widget that
    # would receive keyboard input) is checked rather than hasFocus(),
    # which additionally requires the top-level window to be OS-active --
    # true for a real user, but not guaranteed in every test environment.
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton
    )

    _select_row(panel, 0)

    assert panel.focusWidget() is panel.jersey_field


def test_jersey_field_h_key_resolves_against_home_team(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton
    )
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


def test_jersey_number_containing_1_through_5_does_not_trigger_event_type_hotkeys(
    qtbot, tagging_session
):
    # Regression (ADR-0007): TAGGING_SCOPE's digit hotkeys (1-5) must be
    # suspended while the jersey field has focus, since jersey numbers
    # routinely contain those same digits (e.g. "14").
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton
    )
    _select_row(panel, 0)
    _focus_jersey_field(panel)

    qtbot.keyClicks(panel.jersey_field, "14")

    assert panel.jersey_field.text() == "14"
    assert len(tagging_session.list_events()) == 1


def test_tagging_hotkeys_resume_once_the_jersey_field_loses_focus(
    qtbot, tagging_session
):
    # dispatch() doesn't just check bindability, it *invokes* the bound
    # action -- so this test makes exactly one side-effecting dispatch
    # call. (Dispatching "1" while still focused would log a PERIOD_START,
    # refresh(), and re-populate the still-selected row, which re-focuses
    # the jersey field and would silently undo the very state this test
    # is checking.)
    registry = ShortcutRegistry()
    panel = _make_panel(qtbot, tagging_session, shortcuts=registry)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton
    )
    _select_row(panel, 0)
    _focus_jersey_field(panel)  # suspends TAGGING_SCOPE

    _blur_jersey_field(panel)  # resumes TAGGING_SCOPE

    assert registry.dispatch(key_string(Qt.Key.Key_2)) is True
    assert len(tagging_session.list_events()) == 2


def test_jersey_entry_hotkeys_are_suspended_once_the_field_loses_focus(
    qtbot, tagging_session
):
    registry = ShortcutRegistry()
    panel = _make_panel(qtbot, tagging_session, shortcuts=registry)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton
    )
    _select_row(panel, 0)
    _focus_jersey_field(panel)

    _blur_jersey_field(panel)

    assert registry.dispatch(key_string(Qt.Key.Key_H)) is False


def test_jersey_entry_scope_is_suspended_until_the_field_has_focus(
    qtbot, tagging_session
):
    registry = ShortcutRegistry()
    _make_panel(qtbot, tagging_session, shortcuts=registry)

    assert registry.dispatch(key_string(Qt.Key.Key_H)) is False


def test_unknown_checkbox_starts_unchecked_even_though_a_fresh_stub_is_unknown(
    qtbot, tagging_session
):
    # Regression: a freshly-logged event defaults its player reference to
    # explicit unknown (see TaggingSession.log_event), so the checkbox
    # must NOT mirror that on selection -- otherwise typing a jersey
    # number and pressing Home/Away would silently be ignored.
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton
    )
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


def test_delete_button_removes_the_event_and_hides_the_edit_panel(
    qtbot, tagging_session
):
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
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton
    )
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
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHIFT_CHANGE], Qt.MouseButton.LeftButton
    )
    event_id = tagging_session.list_events()[0].id
    tagging_session.update_event(event_id, shift_on_ice=True)

    calls = []
    original_update_event = tagging_session.update_event
    tagging_session.update_event = lambda *args, **kwargs: (
        calls.append((args, kwargs)) or original_update_event(*args, **kwargs)
    )

    _select_row(panel, 0)

    assert calls == []
    assert panel.on_ice_checkbox.isChecked() is True


def test_event_log_stays_ordered_by_video_timestamp_after_edits(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session, position_ms=500)
    qtbot.mouseClick(panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton)

    panel._current_position_ms = lambda: 100
    qtbot.mouseClick(
        panel.log_buttons[EventType.PERIOD_START], Qt.MouseButton.LeftButton
    )

    assert panel.event_table.item(0, 1).text() == "Period Start"
    assert panel.event_table.item(1, 1).text() == "Stoppage"


# -- location-bearing capture: faceoff / shot_attempt (ticket 16) ---------


def test_faceoff_button_pauses_playback_then_logs_the_clicked_location(
    qtbot, tagging_session
):
    pause_calls = []
    panel = _make_panel(
        qtbot,
        tagging_session,
        position_ms=4200,
        pause=lambda: pause_calls.append(True),
        rink_click_dialog_factory=lambda: _FakeRinkClickDialog(x=40.0, y=-5.0),
    )

    qtbot.mouseClick(panel.log_buttons[EventType.FACEOFF], Qt.MouseButton.LeftButton)

    assert pause_calls == [True]
    events = tagging_session.list_events()
    assert len(events) == 1
    assert events[0].event_type is EventType.FACEOFF
    assert events[0].video_timestamp == 4200
    assert events[0].faceoff_x == 40.0
    assert events[0].faceoff_y == -5.0


def test_shot_attempt_button_pauses_playback_and_captures_location_plus_outcome(
    qtbot, tagging_session
):
    pause_calls = []
    panel = _make_panel(
        qtbot,
        tagging_session,
        pause=lambda: pause_calls.append(True),
        shot_attempt_dialog_factory=lambda: _FakeShotAttemptDialog(
            x=85.0, y=0.0, shot_outcome=ShotOutcome.GOAL, shot_type=ShotType.WRIST
        ),
    )

    qtbot.mouseClick(
        panel.log_buttons[EventType.SHOT_ATTEMPT], Qt.MouseButton.LeftButton
    )

    assert pause_calls == [True]
    events = tagging_session.list_events()
    assert len(events) == 1
    assert events[0].shot_x == 85.0
    assert events[0].shot_outcome is ShotOutcome.GOAL
    assert events[0].shot_type is ShotType.WRIST


def test_logging_other_event_types_does_not_pause_playback(qtbot, tagging_session):
    pause_calls = []
    panel = _make_panel(qtbot, tagging_session, pause=lambda: pause_calls.append(True))

    for event_type in (
        EventType.PERIOD_START,
        EventType.PERIOD_END,
        EventType.STOPPAGE,
        EventType.PENALTY,
        EventType.SHIFT_CHANGE,
    ):
        qtbot.mouseClick(panel.log_buttons[event_type], Qt.MouseButton.LeftButton)

    assert pause_calls == []


def test_faceoff_pauses_even_with_no_pause_callable_injected(qtbot, tagging_session):
    # pause=None (the default) must not raise -- MainWindow only wires a
    # real one up once a Game exists to tag against.
    panel = _make_panel(qtbot, tagging_session)

    qtbot.mouseClick(panel.log_buttons[EventType.FACEOFF], Qt.MouseButton.LeftButton)

    assert len(tagging_session.list_events()) == 1


def test_dismissing_the_rink_dialog_logs_no_event(qtbot, tagging_session):
    panel = _make_panel(
        qtbot,
        tagging_session,
        rink_click_dialog_factory=lambda: _FakeRinkClickDialog(accepted=False),
    )

    qtbot.mouseClick(panel.log_buttons[EventType.FACEOFF], Qt.MouseButton.LeftButton)

    assert tagging_session.list_events() == []


def test_dismissing_the_shot_attempt_dialog_logs_no_event(qtbot, tagging_session):
    panel = _make_panel(
        qtbot,
        tagging_session,
        shot_attempt_dialog_factory=lambda: _FakeShotAttemptDialog(accepted=False),
    )

    qtbot.mouseClick(
        panel.log_buttons[EventType.SHOT_ATTEMPT], Qt.MouseButton.LeftButton
    )

    assert tagging_session.list_events() == []


def test_faceoff_and_shot_attempt_appear_in_the_same_event_log(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)

    qtbot.mouseClick(panel.log_buttons[EventType.FACEOFF], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHOT_ATTEMPT], Qt.MouseButton.LeftButton
    )

    assert panel.event_table.rowCount() == 2
    labels = {panel.event_table.item(row, 1).text() for row in range(2)}
    assert labels == {"Faceoff", "Shot Attempt"}


def test_default_rink_click_dialog_factory_uses_the_sessions_rink_type(
    qtbot, tagging_session, session, game_and_teams
):
    # Every other test injects a fake dialog factory (a real one's exec()
    # blocks with nothing to click); this test exercises the panel's own
    # default factory instead, without ever calling exec(), to confirm it
    # threads the tagged game's rink_type through to RinkClickDialog.
    game, _, _ = game_and_teams
    game.rink_type = RinkType.NHL
    session.commit()
    panel = TaggingPanel(
        tagging_session, current_position_ms=lambda: 0, shortcuts=ShortcutRegistry()
    )
    qtbot.addWidget(panel)

    dialog = panel._rink_click_dialog_factory()
    qtbot.addWidget(dialog)

    assert isinstance(dialog, RinkClickDialog)
    assert (
        dialog.rink._ax.get_xlim() != RinkClickDialog(RinkType.IIHF).rink._ax.get_xlim()
    )


# -- location-bearing inline edit: reference combo, outcome/type, context --


def test_faceoff_reference_combo_lets_both_participants_be_resolved_independently(
    qtbot, tagging_session
):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.FACEOFF], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    assert panel.edit_form.isRowVisible(panel.reference_combo) is True
    assert panel.reference_combo.currentData() == "participant_a"

    panel.jersey_field.setText("14")
    qtbot.mouseClick(panel.home_button, Qt.MouseButton.LeftButton)

    index = panel.reference_combo.findData("participant_b")
    panel.reference_combo.setCurrentIndex(index)
    panel.jersey_field.setText("9")
    qtbot.mouseClick(panel.away_button, Qt.MouseButton.LeftButton)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.faceoff_participant_a_unknown is False
    assert event.faceoff_team_a_id == tagging_session.home_team_id
    assert event.faceoff_participant_b_unknown is False
    assert event.faceoff_team_b_id == tagging_session.away_team_id


def test_shot_attempt_reference_combo_defaults_to_shooter(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHOT_ATTEMPT], Qt.MouseButton.LeftButton
    )
    _select_row(panel, 0)

    assert panel.reference_combo.currentData() == "shooter"

    panel.jersey_field.setText("9")
    qtbot.mouseClick(panel.home_button, Qt.MouseButton.LeftButton)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.shooter_unknown is False
    assert event.shot_team_id == tagging_session.home_team_id


def test_shot_attempt_reference_combo_can_set_an_assist(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHOT_ATTEMPT], Qt.MouseButton.LeftButton
    )
    _select_row(panel, 0)

    index = panel.reference_combo.findData("assist1")
    panel.reference_combo.setCurrentIndex(index)
    panel.jersey_field.setText("14")
    qtbot.mouseClick(panel.home_button, Qt.MouseButton.LeftButton)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.assist1_unknown is False
    assert event.assist1_id is not None


def test_penalty_hides_the_reference_combo_since_it_has_only_one_reference(
    qtbot, tagging_session
):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.PENALTY], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    assert panel.edit_form.isRowVisible(panel.reference_combo) is False


def test_shot_attempt_outcome_and_type_are_editable_inline(qtbot, tagging_session):
    panel = _make_panel(
        qtbot,
        tagging_session,
        shot_attempt_dialog_factory=lambda: _FakeShotAttemptDialog(
            shot_outcome=ShotOutcome.SAVED, shot_type=ShotType.SLAP
        ),
    )
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHOT_ATTEMPT], Qt.MouseButton.LeftButton
    )
    _select_row(panel, 0)

    outcome_index = panel.outcome_combo.findData(ShotOutcome.GOAL.value)
    panel.outcome_combo.setCurrentIndex(outcome_index)
    type_index = panel.shot_type_combo.findData(ShotType.WRIST.value)
    panel.shot_type_combo.setCurrentIndex(type_index)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.shot_outcome is ShotOutcome.GOAL
    assert event.shot_type is ShotType.WRIST


def test_shot_context_checkboxes_are_editable_inline(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(
        panel.log_buttons[EventType.SHOT_ATTEMPT], Qt.MouseButton.LeftButton
    )
    _select_row(panel, 0)

    panel.shot_context_checkboxes["shot_rush"].setChecked(True)
    panel.shot_context_checkboxes["shot_screened"].setChecked(True)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.shot_rush is True
    assert event.shot_screened is True
    assert event.shot_rebound is False
    assert event.shot_one_timer is False


def test_relocate_button_updates_the_stored_location(qtbot, tagging_session):
    panel = _make_panel(
        qtbot,
        tagging_session,
        rink_click_dialog_factory=lambda: _FakeRinkClickDialog(x=1.0, y=1.0),
    )
    qtbot.mouseClick(panel.log_buttons[EventType.FACEOFF], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    panel._rink_click_dialog_factory = lambda: _FakeRinkClickDialog(x=-60.0, y=20.0)
    qtbot.mouseClick(panel.relocate_button, Qt.MouseButton.LeftButton)

    event_id = tagging_session.list_events()[0].id
    event = tagging_session.get_event(event_id)
    assert event.faceoff_x == -60.0
    assert event.faceoff_y == 20.0


def test_location_row_hidden_for_event_types_without_a_location(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    assert panel.edit_form.isRowVisible(panel.location_row) is False


def test_deleting_a_faceoff_removes_it_from_the_log(qtbot, tagging_session):
    panel = _make_panel(qtbot, tagging_session)
    qtbot.mouseClick(panel.log_buttons[EventType.FACEOFF], Qt.MouseButton.LeftButton)
    _select_row(panel, 0)

    qtbot.mouseClick(panel.delete_button, Qt.MouseButton.LeftButton)

    assert tagging_session.list_events() == []
    assert panel.event_table.rowCount() == 0


# -- release_shortcuts: lets a replacement panel reuse the same registry ---


def test_release_shortcuts_frees_every_key_this_panel_registered(
    qtbot, tagging_session
):
    registry = ShortcutRegistry()
    panel = _make_panel(qtbot, tagging_session, shortcuts=registry)

    panel.release_shortcuts()

    # None of this panel's hotkeys still dispatch through the registry.
    assert registry.dispatch(key_string(Qt.Key.Key_1)) is False
    assert registry.dispatch(key_string(Qt.Key.Key_H)) is False


def test_a_second_panel_can_reuse_the_registry_after_release_shortcuts(
    qtbot, tagging_session
):
    # Regression: without release_shortcuts, a second TaggingPanel sharing
    # the same registry (e.g. MainWindow switching to a different Game)
    # would raise ShortcutConflictError on construction -- Qt's own
    # deleteLater() doesn't free these bindings synchronously.
    registry = ShortcutRegistry()
    first = _make_panel(qtbot, tagging_session, shortcuts=registry)
    first.release_shortcuts()

    _second = _make_panel(qtbot, tagging_session, shortcuts=registry)

    assert registry.dispatch(key_string(Qt.Key.Key_1)) is True
    assert len(tagging_session.list_events()) == 1


# -- bulk line change (ticket 17) -------------------------------------------


def _declare_unit(session, tagging_session, jerseys, unit_type, unit_number):
    service = GameSetupService(session)
    for jersey in jerseys:
        entry = tagging_session.resolve_or_create_roster_entry(
            tagging_session.home_team_id, jersey
        )
        service.assign_unit(
            game_id=tagging_session.game_id,
            team_id=tagging_session.home_team_id,
            player_id=entry.player_id,
            unit_type=unit_type,
            unit_number=unit_number,
        )


def test_line_change_button_bulk_logs_a_declared_unit(qtbot, tagging_session, session):
    _declare_unit(session, tagging_session, (14, 17, 23), UnitType.FORWARD_LINE, 1)
    factory = _line_change_factory(unit_index=0, on_ice=True)
    panel = _make_panel(
        qtbot, tagging_session, position_ms=5000, line_change_dialog_factory=factory
    )

    qtbot.mouseClick(panel.line_change_button, Qt.MouseButton.LeftButton)

    ((offered_label, _unit),) = factory.opened[0].offered_units["home"]
    assert offered_label == "Forward line 1 (#14, #17, #23)"
    assert factory.opened[0].offered_units["away"] == []
    events = tagging_session.list_events()
    assert len(events) == 3
    assert {event.video_timestamp for event in events} == {5000}
    assert all(event.shift_on_ice is True for event in events)
    assert panel.event_table.rowCount() == 3


def test_line_change_ad_hoc_logs_the_typed_jerseys(qtbot, tagging_session):
    factory = _line_change_factory(
        team_side="away", on_ice=False, jersey_numbers=[7, 12]
    )
    panel = _make_panel(qtbot, tagging_session, line_change_dialog_factory=factory)

    qtbot.mouseClick(panel.line_change_button, Qt.MouseButton.LeftButton)

    events = tagging_session.list_events()
    assert len(events) == 2
    assert all(event.shift_team_id == tagging_session.away_team_id for event in events)
    assert all(event.shift_on_ice is False for event in events)


def test_line_change_timestamp_is_captured_before_the_dialog_opens(
    tagging_session, qtbot
):
    # The tagger may take a few seconds to pick the unit while footage
    # keeps playing -- the change belongs at the instant the key was hit.
    position = {"ms": 1000}

    def factory(units, roster):
        position["ms"] = 9000
        return _FakeLineChangeDialog(units, jersey_numbers=[14])

    panel = TaggingPanel(
        tagging_session,
        current_position_ms=lambda: position["ms"],
        shortcuts=ShortcutRegistry(),
        line_change_dialog_factory=factory,
    )
    qtbot.addWidget(panel)

    qtbot.mouseClick(panel.line_change_button, Qt.MouseButton.LeftButton)

    assert [event.video_timestamp for event in tagging_session.list_events()] == [1000]


def test_cancelling_the_line_change_dialog_logs_nothing(qtbot, tagging_session):
    panel = _make_panel(
        qtbot,
        tagging_session,
        line_change_dialog_factory=_line_change_factory(accepted=False),
    )

    qtbot.mouseClick(panel.line_change_button, Qt.MouseButton.LeftButton)

    assert tagging_session.list_events() == []


def test_hotkey_8_opens_the_line_change_dialog(qtbot, tagging_session):
    registry = ShortcutRegistry()
    factory = _line_change_factory(jersey_numbers=[14])
    _panel = _make_panel(
        qtbot, tagging_session, shortcuts=registry, line_change_dialog_factory=factory
    )

    assert registry.dispatch(key_string(Qt.Key.Key_8)) is True

    assert len(tagging_session.list_events()) == 1


def test_bulk_logged_rows_are_individually_selectable_and_deletable(
    qtbot, tagging_session
):
    factory = _line_change_factory(jersey_numbers=[14, 17])
    panel = _make_panel(qtbot, tagging_session, line_change_dialog_factory=factory)
    qtbot.mouseClick(panel.line_change_button, Qt.MouseButton.LeftButton)

    _select_row(panel, 0)
    qtbot.mouseClick(panel.delete_button, Qt.MouseButton.LeftButton)

    assert panel.event_table.rowCount() == 1


def test_line_change_offers_each_sides_roster_for_ad_hoc_picking(
    qtbot, tagging_session
):
    tagging_session.resolve_or_create_roster_entry(
        tagging_session.away_team_id, 12, full_name="Sam Diaz"
    )
    factory = _line_change_factory(accepted=False)
    panel = _make_panel(qtbot, tagging_session, line_change_dialog_factory=factory)

    qtbot.mouseClick(panel.line_change_button, Qt.MouseButton.LeftButton)

    assert factory.opened[0].offered_roster == {
        "home": [],
        "away": [("#12 Sam Diaz", 12)],
    }
