"""Bulk line-change entry (ticket 17): pick a side, on/off, and either one
of that side's declared units or an ad-hoc multi-select of its players.
The dialog only collects the choice -- `TaggingPanel` hands it to
`TaggingSession.log_unit_change`/`log_ad_hoc_line_change`, which create
the individual `shift_change` events (see CONTEXT.md's Game unit
assignment entry: a line change is a tagging convenience, not a stored
entity).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.enums import Side
from hockey_analyzer.domain.tagging_session import TeamSide, Unit

# `unit_combo` item data for the ad-hoc choice. Declared units are stored
# as their index into `_units_by_side[side]` rather than the `Unit` itself
# -- a NamedTuple doesn't survive PySide6's QVariant round-trip intact
# (see rink_view.ShotAttemptCaptureDialog for the same concern with enums).
_AD_HOC = -1


class LineChangeDialog(QDialog):
    """`units` maps each side to its declared units, and `roster` each
    side to its rostered jersey numbers, each paired with the label to
    show for it (`TaggingSession.describe_unit`/`roster_entry_label`).
    A side with no units offers only the ad-hoc choice: tick players off
    its roster, and/or type the numbers of players not rostered yet.
    After an accepted `exec()`, read `team_side`, `on_ice`, and either
    `unit` (a declared unit) or `jersey_numbers` (ad hoc, `unit` is
    `None`)."""

    def __init__(
        self,
        units: Mapping[TeamSide, Sequence[tuple[str, Unit]]],
        roster: Mapping[TeamSide, Sequence[tuple[str, int]]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Line Change")
        self._units_by_side = units
        self._roster_by_side = roster
        self.team_side: TeamSide = Side.HOME
        self.on_ice = True
        self.unit: Unit | None = None
        self.jersey_numbers: list[int] = []

        # Each side's plain `.value` string as item data, not the Side
        # member -- see _AD_HOC's comment.
        self.side_combo = QComboBox()
        for side in Side:
            self.side_combo.addItem(side.value.capitalize(), side.value)
        self.side_combo.currentIndexChanged.connect(self._on_side_changed)

        self.direction_combo = QComboBox()
        self.direction_combo.addItem("On ice", True)
        self.direction_combo.addItem("Off ice", False)

        self.unit_combo = QComboBox()
        self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)

        self.roster_list = QListWidget()

        self.jersey_field = QLineEdit()
        self.jersey_field.setPlaceholderText("not rostered yet, e.g. 14 17")

        self.error_label = QLabel("")

        self.ok_button = QPushButton("OK")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self._accept_if_valid)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("Side", self.side_combo)
        form.addRow("Direction", self.direction_combo)
        form.addRow("Unit", self.unit_combo)
        form.addRow("Players", self.roster_list)
        form.addRow("Other #s", self.jersey_field)
        form.addRow("", self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.ok_button)
        buttons.addWidget(self.cancel_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self._on_side_changed()

    def _current_side(self) -> Side:
        return Side(self.side_combo.currentData())

    def _on_side_changed(self) -> None:
        side = self._current_side()

        self.roster_list.clear()
        for label, jersey_number in self._roster_by_side.get(side, ()):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, jersey_number)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.roster_list.addItem(item)
        self.jersey_field.clear()

        self.unit_combo.blockSignals(True)
        self.unit_combo.clear()
        for index, (label, _unit) in enumerate(self._units_by_side.get(side, ())):
            self.unit_combo.addItem(label, index)
        self.unit_combo.addItem("Ad hoc (pick players)", _AD_HOC)
        self.unit_combo.setCurrentIndex(0)
        self.unit_combo.blockSignals(False)
        self._on_unit_changed()

    def _on_unit_changed(self) -> None:
        is_ad_hoc = self.unit_combo.currentData() == _AD_HOC
        self.roster_list.setEnabled(is_ad_hoc)
        self.jersey_field.setEnabled(is_ad_hoc)
        if is_ad_hoc:
            self.roster_list.setFocus()

    def _accept_if_valid(self) -> None:
        self.error_label.setText("")
        side = self._current_side()
        choice = self.unit_combo.currentData()
        if choice == _AD_HOC:
            tokens = re.split(r"[\s,]+", self.jersey_field.text().strip())
            tokens = [token for token in tokens if token]
            if not all(token.isdigit() for token in tokens):
                self.error_label.setText("Other #s must be jersey numbers.")
                return
            picked = [
                self.roster_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(self.roster_list.count())
                if self.roster_list.item(row).checkState() == Qt.CheckState.Checked
            ]
            jersey_numbers = picked + [int(token) for token in tokens]
            if not jersey_numbers:
                self.error_label.setText("Pick or type at least one player.")
                return
            self.unit = None
            self.jersey_numbers = jersey_numbers
        else:
            self.unit = self._units_by_side[side][choice][1]
            self.jersey_numbers = []
        self.team_side = side
        self.on_ice = bool(self.direction_combo.currentData())
        self.accept()
