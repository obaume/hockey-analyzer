"""Bulk line-change entry (ticket 17): pick a side, on/off, and either one
of that side's declared units or an ad-hoc set of jersey numbers. The
dialog only collects the choice -- `TaggingPanel` hands it to
`TaggingSession.log_unit_change`/`log_line_change`, which create the
individual `shift_change` events (see CONTEXT.md's Game unit assignment
entry: a line change is a tagging convenience, not a stored entity).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.tagging_session import TeamSide, Unit

# `unit_combo` item data for the ad-hoc choice. Declared units are stored
# as their index into `_units_by_side[side]` rather than the `Unit` itself
# -- a NamedTuple doesn't survive PySide6's QVariant round-trip intact
# (see rink_view.ShotAttemptCaptureDialog for the same concern with enums).
_AD_HOC = -1


class LineChangeDialog(QDialog):
    """`units` maps each side to its declared units, each paired with the
    label to show for it (`TaggingSession.describe_unit`). A side with no
    units offers only the ad-hoc jersey-number choice. After an accepted
    `exec()`, read `team_side`, `on_ice`, and either `unit` (a declared
    unit) or `jersey_numbers` (ad-hoc, `unit` is `None`)."""

    def __init__(
        self,
        units: Mapping[TeamSide, Sequence[tuple[str, Unit]]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Line Change")
        self._units_by_side = units
        self.team_side: TeamSide = "home"
        self.on_ice = True
        self.unit: Unit | None = None
        self.jersey_numbers: list[int] = []

        # Plain strings as item data -- see _AD_HOC's comment.
        self.side_combo = QComboBox()
        self.side_combo.addItem("Home", "home")
        self.side_combo.addItem("Away", "away")
        self.side_combo.currentIndexChanged.connect(self._refresh_unit_combo)

        self.direction_combo = QComboBox()
        self.direction_combo.addItem("On ice", True)
        self.direction_combo.addItem("Off ice", False)

        self.unit_combo = QComboBox()
        self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)

        self.jersey_field = QLineEdit()
        self.jersey_field.setPlaceholderText("e.g. 14 17 23")

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
        form.addRow("Jersey #s", self.jersey_field)
        form.addRow("", self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.ok_button)
        buttons.addWidget(self.cancel_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self._refresh_unit_combo()

    def _current_side(self) -> TeamSide:
        return self.side_combo.currentData()

    def _refresh_unit_combo(self) -> None:
        self.unit_combo.blockSignals(True)
        self.unit_combo.clear()
        for index, (label, _unit) in enumerate(
            self._units_by_side.get(self._current_side(), ())
        ):
            self.unit_combo.addItem(label, index)
        self.unit_combo.addItem("Ad hoc (jersey numbers)", _AD_HOC)
        self.unit_combo.setCurrentIndex(0)
        self.unit_combo.blockSignals(False)
        self._on_unit_changed()

    def _on_unit_changed(self) -> None:
        is_ad_hoc = self.unit_combo.currentData() == _AD_HOC
        self.jersey_field.setEnabled(is_ad_hoc)
        if is_ad_hoc:
            self.jersey_field.setFocus()

    def _accept_if_valid(self) -> None:
        self.error_label.setText("")
        side = self._current_side()
        choice = self.unit_combo.currentData()
        if choice == _AD_HOC:
            tokens = re.split(r"[\s,]+", self.jersey_field.text().strip())
            tokens = [token for token in tokens if token]
            if not tokens or not all(token.isdigit() for token in tokens):
                self.error_label.setText("Enter one or more jersey numbers.")
                return
            self.unit = None
            self.jersey_numbers = [int(token) for token in tokens]
        else:
            self.unit = self._units_by_side[side][choice][1]
            self.jersey_numbers = []
        self.team_side = side
        self.on_ice = bool(self.direction_combo.currentData())
        self.accept()
