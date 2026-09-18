"""Manual game & roster setup (ticket 14): the fully manual path to create
a `Game` and populate its rosters, with no league import involved -- both
a standalone user-facing capability and the source of the
`game_id`/`home_team_id`/`away_team_id` a `TaggingSession` (ticket 15)
needs to exist before a tagging pass can start.

All domain logic -- CRUD for games/teams/players/roster entries, duplicate-
jersey rejection -- lives in `GameSetupService` (see its module docstring).
This widget only wires clicks to that service and renders/collects field
values, matching the split `tagging_panel.py` already sets.

`TeamRosterPanel` is used for both sides of the game unchanged -- there is
no separate "opponent" widget or code path (see CONTEXT.md's Game roster
entry entry).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.enums import Position
from hockey_analyzer.domain.game_setup import DuplicateJerseyNumberError, GameSetupService

# Sentinel `player_combo` item data meaning "create a brand-new Player from
# the full_name/position fields" rather than rostering an existing one.
_NEW_PLAYER = -1


class TeamRosterPanel(QWidget):
    """One side of the game: pick or create a `Team`, then build its
    roster for this game. Constructed once per side with an arbitrary
    `side_label` used only for the group box title -- nothing here reads
    or special-cases which side it is."""

    def __init__(self, service: GameSetupService, side_label: str, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._game_id: int | None = None
        self.team_id: int | None = None

        self.team_combo = QComboBox()
        self.team_combo.currentIndexChanged.connect(self._on_team_selected)

        self.new_team_name_field = QLineEdit()
        self.new_team_name_field.setPlaceholderText("New team name")
        self.is_user_team_checkbox = QCheckBox("This is my team")
        self.create_team_button = QPushButton("Create Team")
        self.create_team_button.clicked.connect(self._create_team)

        team_row = QHBoxLayout()
        team_row.addWidget(self.team_combo)
        team_row.addWidget(self.new_team_name_field)
        team_row.addWidget(self.is_user_team_checkbox)
        team_row.addWidget(self.create_team_button)

        self.roster_table = QTableWidget(0, 3)
        self.roster_table.setHorizontalHeaderLabels(["#", "Name", "Position"])
        self.roster_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.roster_table.itemSelectionChanged.connect(self._on_roster_selection_changed)

        self.jersey_field = QLineEdit()
        self.jersey_field.setPlaceholderText("Jersey #")

        self.player_combo = QComboBox()
        self.player_combo.currentIndexChanged.connect(self._on_player_choice_changed)

        self.full_name_field = QLineEdit()
        self.full_name_field.setPlaceholderText("Full name (optional)")

        self.position_combo = _build_position_combo()

        self.add_player_button = QPushButton("Add to roster")
        self.add_player_button.clicked.connect(self._add_roster_entry)

        self.error_label = QLabel("")

        add_form = QFormLayout()
        add_form.addRow("Jersey #", self.jersey_field)
        add_form.addRow("Player", self.player_combo)
        add_form.addRow("Full name", self.full_name_field)
        add_form.addRow("Position", self.position_combo)
        add_form.addRow("", self.add_player_button)
        add_form.addRow("", self.error_label)

        self.edit_full_name_field = QLineEdit()
        self.edit_position_combo = _build_position_combo()
        self.edit_save_button = QPushButton("Save")
        self.edit_save_button.clicked.connect(self._save_selected_player)

        edit_form = QFormLayout()
        edit_form.addRow("Full name", self.edit_full_name_field)
        edit_form.addRow("Position", self.edit_position_combo)
        edit_form.addRow("", self.edit_save_button)
        self.edit_group = QGroupBox("Selected player")
        self.edit_group.setLayout(edit_form)
        self.edit_group.setVisible(False)

        layout = QVBoxLayout()
        group = QGroupBox(side_label)
        group_layout = QVBoxLayout()
        group_layout.addLayout(team_row)
        group_layout.addWidget(self.roster_table)
        group_layout.addLayout(add_form)
        group_layout.addWidget(self.edit_group)
        group.setLayout(group_layout)
        layout.addWidget(group)
        self.setLayout(layout)

        self._refresh_team_combo()
        self._refresh_player_combo()

    def set_game_id(self, game_id: int) -> None:
        self._game_id = game_id
        self._refresh_roster_table()

    # -- team selection/creation ------------------------------------------

    def _refresh_team_combo(self) -> None:
        self.team_combo.blockSignals(True)
        self.team_combo.clear()
        self.team_combo.addItem("Select team…", None)
        for team in self._service.list_teams():
            self.team_combo.addItem(team.name, team.id)
        self.team_combo.blockSignals(False)

    def _create_team(self) -> None:
        name = self.new_team_name_field.text().strip()
        if not name:
            return
        team = self._service.create_team(name, is_user_team=self.is_user_team_checkbox.isChecked())
        self.new_team_name_field.clear()
        self.is_user_team_checkbox.setChecked(False)
        self._refresh_team_combo()
        self.team_combo.setCurrentIndex(self.team_combo.findData(team.id))

    def _on_team_selected(self, index: int) -> None:
        self.team_id = self.team_combo.itemData(index)
        self._refresh_roster_table()

    # -- adding to the roster ---------------------------------------------

    def _refresh_player_combo(self) -> None:
        self.player_combo.blockSignals(True)
        self.player_combo.clear()
        self.player_combo.addItem("New player…", _NEW_PLAYER)
        for player in self._service.list_players():
            self.player_combo.addItem(player.full_name or f"Player {player.id}", player.id)
        self.player_combo.blockSignals(False)
        self._on_player_choice_changed(self.player_combo.currentIndex())

    def _on_player_choice_changed(self, index: int) -> None:
        is_new = self.player_combo.itemData(index) == _NEW_PLAYER
        self.full_name_field.setEnabled(is_new)
        self.position_combo.setEnabled(is_new)

    def _add_roster_entry(self) -> None:
        self.error_label.setText("")
        if self._game_id is None or self.team_id is None:
            self.error_label.setText("Select a team first.")
            return

        jersey_text = self.jersey_field.text().strip()
        if not jersey_text.isdigit():
            self.error_label.setText("Enter a jersey number.")
            return
        jersey_number = int(jersey_text)

        player_choice = self.player_combo.currentData()
        try:
            if player_choice == _NEW_PLAYER:
                self._service.add_roster_entry(
                    game_id=self._game_id,
                    team_id=self.team_id,
                    jersey_number=jersey_number,
                    full_name=self.full_name_field.text().strip() or None,
                    position=self.position_combo.currentData(),
                )
            else:
                self._service.add_roster_entry(
                    game_id=self._game_id,
                    team_id=self.team_id,
                    jersey_number=jersey_number,
                    player_id=player_choice,
                )
        except DuplicateJerseyNumberError as error:
            self.error_label.setText(str(error))
            return

        self.jersey_field.clear()
        self.full_name_field.clear()
        self.position_combo.setCurrentIndex(0)
        self._refresh_player_combo()
        self._refresh_roster_table()

    # -- roster display + backfill ----------------------------------------

    def _refresh_roster_table(self) -> None:
        self._roster_player_ids: list[int] = []
        if self._game_id is None or self.team_id is None:
            self.roster_table.setRowCount(0)
            return

        entries = self._service.list_roster(self._game_id, self.team_id)
        self.roster_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self._roster_player_ids.append(entry.player_id)
            player = entry.player
            self.roster_table.setItem(row, 0, QTableWidgetItem(str(entry.jersey_number)))
            self.roster_table.setItem(row, 1, QTableWidgetItem(player.full_name or ""))
            self.roster_table.setItem(row, 2, QTableWidgetItem(player.position.value if player.position else ""))

    def _on_roster_selection_changed(self) -> None:
        rows = self.roster_table.selectionModel().selectedRows()
        if not rows:
            self.edit_group.setVisible(False)
            return

        player_id = self._roster_player_ids[rows[0].row()]
        player = self._service.get_player(player_id)
        self.edit_group.setVisible(True)
        self.edit_full_name_field.setText(player.full_name or "")
        self.edit_position_combo.setCurrentIndex(
            self.edit_position_combo.findData(player.position) if player.position is not None else 0
        )

    def _save_selected_player(self) -> None:
        rows = self.roster_table.selectionModel().selectedRows()
        if not rows:
            return
        player_id = self._roster_player_ids[rows[0].row()]
        self._service.set_player_full_name(player_id, self.edit_full_name_field.text().strip() or None)
        self._service.set_player_position(player_id, self.edit_position_combo.currentData())
        self._refresh_roster_table()
        self._refresh_player_combo()


def _build_position_combo() -> QComboBox:
    combo = QComboBox()
    combo.addItem("", None)
    for position in Position:
        combo.addItem(position.value, position)
    return combo


class GameSetupDialog(QDialog):
    def __init__(self, service: GameSetupService, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Game & Roster Setup")
        self._service = service
        self.game_id: int | None = None

        self.new_game_button = QPushButton("New Game")
        self.new_game_button.clicked.connect(self._create_game)
        self.game_status_label = QLabel("No game yet")

        top_row = QHBoxLayout()
        top_row.addWidget(self.new_game_button)
        top_row.addWidget(self.game_status_label)

        self.home_panel = TeamRosterPanel(service, "Home")
        self.away_panel = TeamRosterPanel(service, "Away")
        self.home_panel.setEnabled(False)
        self.away_panel.setEnabled(False)

        rosters_row = QHBoxLayout()
        rosters_row.addWidget(self.home_panel)
        rosters_row.addWidget(self.away_panel)

        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.accept)

        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addLayout(rosters_row)
        layout.addWidget(self.done_button)
        self.setLayout(layout)

    def _create_game(self) -> None:
        game = self._service.create_game()
        self.game_id = game.id
        self.game_status_label.setText(f"Game #{game.id}")
        self.home_panel.set_game_id(game.id)
        self.away_panel.set_game_id(game.id)
        self.home_panel.setEnabled(True)
        self.away_panel.setEnabled(True)

    @property
    def home_team_id(self) -> int | None:
        return self.home_panel.team_id

    @property
    def away_team_id(self) -> int | None:
        return self.away_panel.team_id
