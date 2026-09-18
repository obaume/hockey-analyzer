"""The live-tagging UI (ticket 15): hotkey/click buttons that log an
event instantly without pausing playback, plus a single always-visible
event log where clicking a row opens its fields for inline edit or
delete -- no separate review mode (see CONTEXT.md's Event entry and
ticket 08's manual-tagging answer).

All domain logic -- CRUD, autosave, on-the-fly roster creation, unknown-
reference handling, strength-state defaulting, and display text -- lives
in `TaggingSession` (see its module docstring). This widget only wires
hotkeys/clicks to that service and renders/collects field values, per
ticket 15's "PySide6 widgets only handle video decode/hotkeys/rendering".
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.enums import EventType
from hockey_analyzer.domain.tagging_session import EVENT_TYPES_WITH_PLAYER_REFERENCE, TaggingSession, TeamSide
from hockey_analyzer.ui.keys import key_string, key_string_from_event
from hockey_analyzer.ui.shortcuts import ShortcutRegistry

TAGGING_SCOPE = "tagging"
# A text-entry field taking focus suspends the shortcuts scoped around it
# until it loses focus (ADR-0007) -- "h"/"a" live in their own scope so
# TAGGING_SCOPE's digit hotkeys can be suspended out from under them
# while the tagger is typing a jersey number that may itself contain
# 1-5 (see _JerseyEntry).
JERSEY_ENTRY_SCOPE = "jersey-entry"

_LOG_BUTTONS: tuple[tuple[EventType, str, Qt.Key], ...] = (
    (EventType.PERIOD_START, "Period Start", Qt.Key.Key_1),
    (EventType.PERIOD_END, "Period End", Qt.Key.Key_2),
    (EventType.STOPPAGE, "Stoppage", Qt.Key.Key_3),
    (EventType.PENALTY, "Penalty", Qt.Key.Key_4),
    (EventType.SHIFT_CHANGE, "Shift Change", Qt.Key.Key_5),
)

# Single source of truth for each in-scope type's display label -- table
# rows and log buttons both read from this rather than keeping a second,
# hand-synced mapping.
_TYPE_LABELS = {event_type: label for event_type, label, _ in _LOG_BUTTONS}

_TYPES_WITH_PERIOD_NUMBER = {EventType.PERIOD_START, EventType.PERIOD_END}


def _format_timestamp(video_timestamp_ms: int) -> str:
    total_seconds = video_timestamp_ms // 1000
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


class _JerseyEntry(QLineEdit):
    """A jersey-number field whose "h"/"a" keys resolve the team-scope
    hotkey (ticket 08) instead of typing the letter. Per ADR-0007, this
    goes through the shared `ShortcutRegistry` rather than ad hoc
    `keyPressEvent` wiring: gaining focus suspends `TAGGING_SCOPE` (so its
    digit hotkeys can't fire mid-jersey-number-entry, e.g. on "14") and
    resumes `JERSEY_ENTRY_SCOPE`, where "h"/"a" are registered; losing
    focus reverses that."""

    def __init__(self, shortcuts: ShortcutRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shortcuts = shortcuts

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self._shortcuts.suspend_scope(TAGGING_SCOPE)
        self._shortcuts.resume_scope(JERSEY_ENTRY_SCOPE)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._shortcuts.suspend_scope(JERSEY_ENTRY_SCOPE)
        self._shortcuts.resume_scope(TAGGING_SCOPE)
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._shortcuts.dispatch(key_string_from_event(event)):
            event.accept()
            return
        super().keyPressEvent(event)


class TaggingPanel(QWidget):
    def __init__(
        self,
        tagging_session: TaggingSession,
        *,
        current_position_ms: Callable[[], int],
        shortcuts: ShortcutRegistry | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = tagging_session
        self._current_position_ms = current_position_ms
        self._shortcuts = shortcuts if shortcuts is not None else ShortcutRegistry()
        self._shortcuts.enter_scope(TAGGING_SCOPE)
        self._selected_event_id: int | None = None
        self._row_event_ids: list[int] = []

        log_row = QHBoxLayout()
        self.log_buttons: dict[EventType, QPushButton] = {}
        for event_type, label, key in _LOG_BUTTONS:
            action = self._log_action(event_type)
            button = QPushButton(label)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(action)
            log_row.addWidget(button)
            self.log_buttons[event_type] = button
            self._shortcuts.register(key_string(key), TAGGING_SCOPE, action)

        # Registered up front (see _JerseyEntry) but suspended until the
        # jersey field actually has focus.
        self._shortcuts.enter_scope(JERSEY_ENTRY_SCOPE)
        self._shortcuts.register(key_string(Qt.Key.Key_H), JERSEY_ENTRY_SCOPE, self._side_action("home"))
        self._shortcuts.register(key_string(Qt.Key.Key_A), JERSEY_ENTRY_SCOPE, self._side_action("away"))
        self._shortcuts.suspend_scope(JERSEY_ENTRY_SCOPE)

        self.event_table = QTableWidget(0, 3)
        self.event_table.setHorizontalHeaderLabels(["Time", "Type", "Summary"])
        self.event_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.event_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.event_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.event_table.itemSelectionChanged.connect(self._on_selection_changed)

        self._build_edit_panel()

        layout = QVBoxLayout()
        layout.addLayout(log_row)
        layout.addWidget(self.event_table, stretch=1)
        layout.addWidget(self.edit_group)
        self.setLayout(layout)

        self.edit_group.setVisible(False)
        self.refresh()

    def _log_action(self, event_type: EventType) -> Callable[[], None]:
        return lambda: self._log(event_type)

    def _side_action(self, team_side: TeamSide) -> Callable[[], None]:
        return lambda: self._apply_side(team_side)

    # -- edit-panel construction ------------------------------------------

    def _build_edit_panel(self) -> None:
        self.edit_group = QGroupBox("Selected event")
        self.edit_form = QFormLayout()

        self.strength_state_field = QLineEdit()
        self.strength_state_field.editingFinished.connect(
            lambda: self._commit_field("strength_state", self.strength_state_field.text().strip() or None)
        )
        self.edit_form.addRow("Strength state", self.strength_state_field)

        # 0 reads as "not set" -- period_number is otherwise a plain
        # positive period count (nullable at the DB level; see models.py).
        self.period_number_field = QSpinBox()
        self.period_number_field.setRange(0, 9)
        self.period_number_field.setSpecialValueText("not set")
        self.period_number_field.editingFinished.connect(
            lambda: self._commit_field("period_number", self.period_number_field.value() or None)
        )
        self.edit_form.addRow("Period", self.period_number_field)

        self.jersey_field = _JerseyEntry(self._shortcuts)
        self.jersey_field.setPlaceholderText("jersey #")
        self.unknown_checkbox = QCheckBox("Unknown")
        self.home_button = QPushButton("Home (h)")
        self.home_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.home_button.clicked.connect(self._side_action("home"))
        self.away_button = QPushButton("Away (a)")
        self.away_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.away_button.clicked.connect(self._side_action("away"))

        # A single container widget so setRowVisible has one field widget
        # to target -- QFormLayout can't toggle visibility of individual
        # widgets nested inside a row's layout.
        self.player_reference_row = QWidget()
        player_reference_layout = QHBoxLayout(self.player_reference_row)
        player_reference_layout.setContentsMargins(0, 0, 0, 0)
        player_reference_layout.addWidget(self.jersey_field)
        player_reference_layout.addWidget(self.unknown_checkbox)
        player_reference_layout.addWidget(self.home_button)
        player_reference_layout.addWidget(self.away_button)
        self.edit_form.addRow("Player", self.player_reference_row)

        self.on_ice_checkbox = QCheckBox("On ice")
        self.on_ice_checkbox.toggled.connect(lambda checked: self._commit_field("shift_on_ice", checked))
        self.edit_form.addRow("", self.on_ice_checkbox)

        # 0.0 reads as "not set" -- there is no such thing as a real
        # zero-minute penalty.
        self.duration_field = QDoubleSpinBox()
        self.duration_field.setRange(0.0, 60.0)
        self.duration_field.setSingleStep(0.5)
        self.duration_field.setSpecialValueText("not set")
        self.duration_field.editingFinished.connect(
            lambda: self._commit_field("penalty_duration_minutes", self.duration_field.value() or None)
        )
        self.edit_form.addRow("Duration (min)", self.duration_field)

        self.infraction_field = QLineEdit()
        self.infraction_field.editingFinished.connect(
            lambda: self._commit_field("penalty_infraction", self.infraction_field.text().strip() or None)
        )
        self.edit_form.addRow("Infraction", self.infraction_field)

        self.delete_button = QPushButton("Delete")
        self.delete_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_button.clicked.connect(self._delete_selected)
        self.edit_form.addRow("", self.delete_button)

        self.edit_group.setLayout(self.edit_form)

    # -- instant capture ---------------------------------------------------

    def _log(self, event_type: EventType) -> None:
        self._session.log_event(event_type, self._current_position_ms())
        self.refresh()

    # -- event log -----------------------------------------------------

    def refresh(self) -> None:
        events = self._session.list_events()
        self.event_table.setRowCount(len(events))
        self._row_event_ids = [event.id for event in events]
        for row, event in enumerate(events):
            self.event_table.setItem(row, 0, QTableWidgetItem(_format_timestamp(event.video_timestamp)))
            self.event_table.setItem(row, 1, QTableWidgetItem(_TYPE_LABELS[EventType(event.event_type)]))
            self.event_table.setItem(row, 2, QTableWidgetItem(self._session.describe_event(event)))

        if self._selected_event_id in self._row_event_ids:
            self._select_row_for_event(self._selected_event_id)
            self._populate_edit_panel(self._selected_event_id)
        else:
            self._selected_event_id = None
            self.edit_group.setVisible(False)

    def _select_row_for_event(self, event_id: int) -> None:
        row = self._row_event_ids.index(event_id)
        self.event_table.blockSignals(True)
        self.event_table.selectRow(row)
        self.event_table.blockSignals(False)

    def _on_selection_changed(self) -> None:
        rows = self.event_table.selectionModel().selectedRows()
        if not rows:
            self._selected_event_id = None
            self.edit_group.setVisible(False)
            return
        self._selected_event_id = self._row_event_ids[rows[0].row()]
        self._populate_edit_panel(self._selected_event_id)

    # -- edit panel population/commit -----------------------------------

    def _populate_edit_panel(self, event_id: int) -> None:
        event = self._session.get_event(event_id)
        event_type = EventType(event.event_type)

        self.edit_group.setVisible(True)
        self.strength_state_field.setText(event.strength_state or "")

        has_period_number = event_type in _TYPES_WITH_PERIOD_NUMBER
        self.edit_form.setRowVisible(self.period_number_field, has_period_number)
        if has_period_number:
            self.period_number_field.setValue(event.period_number or 0)

        has_player_reference = event_type in EVENT_TYPES_WITH_PLAYER_REFERENCE
        self.edit_form.setRowVisible(self.player_reference_row, has_player_reference)
        if has_player_reference:
            # Always reset to a clean action-intent state rather than
            # mirroring the event's *current* unknown flag -- a fresh
            # stub defaults to unknown=True (see TaggingSession.log_event),
            # so pre-checking this from that value would silently block
            # the common "type jersey, press h/a" resolve flow. The
            # event log's Summary column already shows current status.
            self.jersey_field.clear()
            self.unknown_checkbox.setChecked(False)
            # Auto-focus so the tagger's next keystrokes land in the
            # field immediately, without an extra click -- required for
            # ticket 08's live-tag speed (jersey digits, then h/a).
            self.jersey_field.setFocus()

        is_shift_change = event_type is EventType.SHIFT_CHANGE
        self.edit_form.setRowVisible(self.on_ice_checkbox, is_shift_change)
        if is_shift_change:
            # blockSignals is per-widget, not recursive to children --
            # `toggled` (unlike the other fields' `editingFinished`) fires
            # on a programmatic setChecked() too, so without this,
            # populating the checkbox here would re-enter _commit_field
            # -> refresh() -> _populate_edit_panel mid-population.
            self.on_ice_checkbox.blockSignals(True)
            self.on_ice_checkbox.setChecked(bool(event.shift_on_ice))
            self.on_ice_checkbox.blockSignals(False)

        is_penalty = event_type is EventType.PENALTY
        self.edit_form.setRowVisible(self.duration_field, is_penalty)
        self.edit_form.setRowVisible(self.infraction_field, is_penalty)
        if is_penalty:
            self.duration_field.setValue(event.penalty_duration_minutes or 0.0)
            self.infraction_field.setText(event.penalty_infraction or "")

    def _commit_field(self, field: str, value: object) -> None:
        if self._selected_event_id is None:
            return
        self._session.update_event(self._selected_event_id, **{field: value})
        self.refresh()

    def _apply_side(self, team_side: TeamSide) -> None:
        if self._selected_event_id is None:
            return
        if self.unknown_checkbox.isChecked():
            self._session.set_player_reference(self._selected_event_id, team_side, unknown=True)
        else:
            text = self.jersey_field.text().strip()
            if not text.isdigit():
                return
            self._session.set_player_reference(self._selected_event_id, team_side, jersey_number=int(text))
        self.refresh()

    def _delete_selected(self) -> None:
        if self._selected_event_id is None:
            return
        self._session.delete_event(self._selected_event_id)
        self._selected_event_id = None
        self.refresh()
