"""Correcting the active game's rosters after the fact (#89): the same
`TeamRosterPanel` New Game uses, one per side, with each side's team
fixed. Adding entries and editing name/position/units work as in New
Game; renumbering, merging and removing are what this dialog is for --
chiefly cleaning up an entry created on the fly from a mistyped jersey
number (see CONTEXT.md's Game roster entry entry).

Every change commits on its own confirmation through `GameSetupService`,
matching Units… -- there is no separate save step, only Close.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.enums import Side
from hockey_analyzer.domain.game_setup import GameSetupService
from hockey_analyzer.ui.game_setup_dialog import Confirm, TeamRosterPanel


class RosterDialog(QDialog):
    def __init__(
        self,
        service: GameSetupService,
        *,
        game_id: int,
        home_team_id: int,
        away_team_id: int,
        confirm: Confirm | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Roster")

        self.home_panel = TeamRosterPanel(service, "Home", Side.HOME, confirm=confirm)
        self.away_panel = TeamRosterPanel(service, "Away", Side.AWAY, confirm=confirm)
        self.home_panel.lock_team(game_id, home_team_id)
        self.away_panel.lock_team(game_id, away_team_id)

        rosters_row = QHBoxLayout()
        rosters_row.addWidget(self.home_panel)
        rosters_row.addWidget(self.away_panel)

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)

        layout = QVBoxLayout()
        layout.addLayout(rosters_row)
        layout.addWidget(self.close_button)
        self.setLayout(layout)
