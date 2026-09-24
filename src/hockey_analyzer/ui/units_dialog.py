"""Registering a game's units (ticket 17): pick a side and a unit (e.g.
Forward line 1), then tick its members off that side's roster. Every tick
commits immediately through `GameSetupService`, matching the autosave
treatment the rest of setup/tagging already has -- there is no separate
save step, only Close.

A player holds at most one unit per unit type (see CONTEXT.md's Game unit
assignment entry), so a player already on another number of the selected
type is annotated with it, and ticking them here moves them.
"""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.enums import Side, UnitType
from hockey_analyzer.domain.game_setup import GameSetupService
from hockey_analyzer.domain.tagging_session import (
    UNIT_TYPE_LABELS,
    roster_entry_label,
    unit_label,
)

# Each row's plain roster label (no "(Forward line 2)" annotation) -- what
# the row reverts to once a toggle leaves the player on this number or on
# none of this type.
_BASE_LABEL_ROLE = Qt.ItemDataRole.UserRole + 1


class UnitsDialog(QDialog):
    def __init__(
        self,
        service: GameSetupService,
        *,
        game_id: int,
        home_team_id: int,
        away_team_id: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Units")
        self._service = service
        self._game_id = game_id
        self._team_ids = {Side.HOME: home_team_id, Side.AWAY: away_team_id}

        # Plain `.value` strings as combo item data, not the enum members
        # -- see rink_view.ShotAttemptCaptureDialog for why.
        self.side_combo = QComboBox()
        for side in Side:
            self.side_combo.addItem(side.value.capitalize(), side.value)
        self.side_combo.currentIndexChanged.connect(self._refresh_members)

        self.unit_type_combo = QComboBox()
        for unit_type in UnitType:
            self.unit_type_combo.addItem(UNIT_TYPE_LABELS[unit_type], unit_type.value)
        self.unit_type_combo.currentIndexChanged.connect(self._refresh_members)

        self.unit_number_field = QSpinBox()
        self.unit_number_field.setRange(1, 9)
        self.unit_number_field.valueChanged.connect(self._refresh_members)

        self.members_label = QLabel()
        self.members_list = QListWidget()
        self.members_list.itemChanged.connect(self._on_member_toggled)

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)

        selector = QHBoxLayout()
        selector.addWidget(QLabel("Side"))
        selector.addWidget(self.side_combo)
        selector.addWidget(QLabel("Unit"))
        selector.addWidget(self.unit_type_combo)
        selector.addWidget(self.unit_number_field)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.addLayout(selector)
        layout.addWidget(self.members_label)
        layout.addWidget(self.members_list)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self._refresh_members()

    def _team_id(self) -> int:
        return self._team_ids[Side(self.side_combo.currentData())]

    def _unit_type(self) -> UnitType:
        return UnitType(self.unit_type_combo.currentData())

    def _refresh_members(self) -> None:
        unit_type = self._unit_type()
        unit_number = self.unit_number_field.value()
        self.members_label.setText(
            f"Members of {self.side_combo.currentText()} - "
            f"{unit_label(unit_type, unit_number)}:"
        )
        numbers = self._service.unit_numbers(
            game_id=self._game_id, team_id=self._team_id(), unit_type=unit_type
        )

        # Blocked: populating check states would otherwise fire
        # itemChanged and write every row straight back.
        blocker = QSignalBlocker(self.members_list)
        self.members_list.clear()
        for entry in self._service.list_roster(self._game_id, self._team_id()):
            base_label = roster_entry_label(entry)
            label = base_label
            current = numbers.get(entry.player_id)
            if current is not None and current != unit_number:
                label = f"{base_label} ({unit_label(unit_type, current)})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry.player_id)
            item.setData(_BASE_LABEL_ROLE, base_label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if current == unit_number
                else Qt.CheckState.Unchecked
            )
            self.members_list.addItem(item)
        blocker.unblock()

    def _on_member_toggled(self, item: QListWidgetItem) -> None:
        player_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._service.assign_unit(
                game_id=self._game_id,
                team_id=self._team_id(),
                player_id=player_id,
                unit_type=self._unit_type(),
                unit_number=self.unit_number_field.value(),
            )
        else:
            self._service.unassign_unit(
                game_id=self._game_id,
                player_id=player_id,
                unit_type=self._unit_type(),
            )
        # Either way the player is now on this number or on none of this
        # type, so any "(Forward line 2)" annotation is stale. Only this
        # row is updated -- rebuilding the list here would delete `item`
        # from inside its own itemChanged signal.
        blocker = QSignalBlocker(self.members_list)
        item.setText(item.data(_BASE_LABEL_ROLE))
        blocker.unblock()
