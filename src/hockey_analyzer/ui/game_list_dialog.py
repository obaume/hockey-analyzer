""" "Select Game" (game-selection wiring ticket): picks among already-created
`Game`s so a resumed tagging pass doesn't have to go through
`GameSetupDialog` again. Read-only over `GameSetupService.list_games` --
creating a game is `GameSetupDialog`'s job, not this dialog's.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.game_setup import GameSetupService
from hockey_analyzer.domain.models import Game

_COLUMNS = ["Date", "Home", "Away", "Score"]


def _score_text(game: Game) -> str:
    if game.home_score is None or game.away_score is None:
        return ""
    return f"{game.home_score} – {game.away_score}"


class GameListDialog(QDialog):
    def __init__(
        self, service: GameSetupService, *, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Game")
        self.selected_game_id: int | None = None

        self._games = service.list_games()

        self.table = QTableWidget(len(self._games), len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemDoubleClicked.connect(self._accept_selection)
        for row, game in enumerate(self._games):
            self.table.setItem(
                row,
                0,
                QTableWidgetItem(game.date.isoformat() if game.date else "Unscheduled"),
            )
            self.table.setItem(
                row,
                1,
                QTableWidgetItem(game.home_team.name if game.home_team else "TBD"),
            )
            self.table.setItem(
                row,
                2,
                QTableWidgetItem(game.away_team.name if game.away_team else "TBD"),
            )
            self.table.setItem(row, 3, QTableWidgetItem(_score_text(game)))

        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(self._accept_selection)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.open_button)
        buttons_row.addWidget(self.cancel_button)

        layout = QVBoxLayout()
        layout.addWidget(self.table)
        layout.addLayout(buttons_row)
        self.setLayout(layout)

    def _accept_selection(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        self.selected_game_id = self._games[rows[0].row()].id
        self.accept()
