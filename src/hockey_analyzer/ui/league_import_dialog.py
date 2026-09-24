"""League-link game import (ticket 22): paste a league game link, review
everything the import would write on one screen, and confirm it as a
single batch -- never write-then-edit (ticket 11's import flow).

All matching and writing lives in `LeagueImportService` (ticket 21); this
widget only renders its `ImportProposal`, collects the user's answers to
the proposal's judgment calls -- uncertain team/player matches, which team
is the user's own, the rink type -- and hands them back as an
`ImportResolution`, the same widget/service split `game_setup_dialog.py`
follows.

After `exec()`, `game_id` is the game to activate (the one just imported,
or an already-imported one the user chose to open instead), and
`fall_back_to_manual` asks the caller for ticket 14's blank manual setup
when the league data couldn't be read.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.enums import RinkType, Side
from hockey_analyzer.domain.game_setup import SameTeamBothSidesError
from hockey_analyzer.league_import import (
    AlreadyImportedError,
    ImportProposal,
    ImportResolution,
    InvalidGameLinkError,
    LeagueImportService,
    ManualEntryFallbackError,
    PlayerMatchStatus,
    PlayerRosteredTwiceError,
    RosterRowProposal,
    TeamMatchStatus,
    TeamProposal,
)

_SIDES = (Side.HOME, Side.AWAY)
_SIDE_LABELS = {Side.HOME: "Home", Side.AWAY: "Away"}

# Combo item data. Plain ints rather than `object()` sentinels or None:
# PySide6's QVariant round-trip through a combo's userData keeps an int
# intact, and existing Team/Player ids are always positive.
_NEW = -1
_UNANSWERED = -2

_ROSTER_COLUMNS = ["#", "Name", "Position", "Match", "Player"]
_PLAYER_COLUMN = 4


class _Unanswered:
    def __repr__(self) -> str:
        return "UNANSWERED"


class LeagueImportDialog(QDialog):
    # Combo data meaning "create a new Team/Player" rather than link one.
    NEW = _NEW
    # `checked_user_team()` before the user has said which team is theirs.
    UNANSWERED = _Unanswered()

    def __init__(
        self, service: LeagueImportService, *, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Game from League Link")
        self._service = service
        self._proposal: ImportProposal | None = None
        self._existing_game_id: int | None = None
        self.game_id: int | None = None
        self.fall_back_to_manual = False

        self.link_field = QLineEdit()
        self.link_field.setPlaceholderText("Paste a league game link")
        self.fetch_button = QPushButton("Fetch")
        self.fetch_button.clicked.connect(self._fetch)
        link_row = QHBoxLayout()
        link_row.addWidget(self.link_field, stretch=1)
        link_row.addWidget(self.fetch_button)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.manual_entry_button = QPushButton("Enter Game Manually")
        self.manual_entry_button.clicked.connect(self._fall_back_to_manual)
        self.manual_entry_button.hide()
        self.open_existing_button = QPushButton("Open Existing Game")
        self.open_existing_button.clicked.connect(self._open_existing)
        self.open_existing_button.hide()
        notice_row = QHBoxLayout()
        notice_row.addWidget(self.error_label, stretch=1)
        notice_row.addWidget(self.manual_entry_button)
        notice_row.addWidget(self.open_existing_button)

        self.review_panel = QWidget()
        self._build_review_panel()
        self.review_panel.hide()

        self.confirm_button = QPushButton("Import Game")
        self.confirm_button.clicked.connect(self._confirm)
        self.confirm_button.setEnabled(False)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)

        layout = QVBoxLayout()
        layout.addLayout(link_row)
        layout.addLayout(notice_row)
        layout.addWidget(self.review_panel, stretch=1)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def _build_review_panel(self) -> None:
        self.game_summary_label = QLabel("")
        self.team_ids_note_label = QLabel("")
        self.team_ids_note_label.setWordWrap(True)

        # Set once at creation and immutable afterward (ADR-0008) -- the
        # league site doesn't say, so it's asked here like the manual path
        # asks it. Item data is the plain `.value` string, for the same
        # QVariant reason game_setup_dialog.py's rink_type_combo gives.
        self.rink_type_combo = QComboBox()
        for rink_type in RinkType:
            self.rink_type_combo.addItem(rink_type.value.upper(), rink_type.value)

        self._team_labels: dict[Side, QLabel] = {}
        self._team_status_labels: dict[Side, QLabel] = {}
        self._team_combos: dict[Side, QComboBox] = {}
        self._roster_tables: dict[Side, QTableWidget] = {}
        sides_row = QHBoxLayout()
        for side in _SIDES:
            team_label = QLabel("")
            status_label = QLabel("")
            status_label.setWordWrap(True)
            team_combo = QComboBox()
            team_combo.currentIndexChanged.connect(self._on_team_choice_changed)
            table = QTableWidget(0, len(_ROSTER_COLUMNS))
            table.setHorizontalHeaderLabels(_ROSTER_COLUMNS)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.verticalHeader().setVisible(False)
            self._team_labels[side] = team_label
            self._team_status_labels[side] = status_label
            self._team_combos[side] = team_combo
            self._roster_tables[side] = table

            side_layout = QVBoxLayout()
            side_layout.addWidget(team_label)
            side_layout.addWidget(status_label)
            side_layout.addWidget(team_combo)
            side_layout.addWidget(table, stretch=1)
            group = QGroupBox(_SIDE_LABELS[side])
            group.setLayout(side_layout)
            sides_row.addWidget(group)

        # Which team is the user's own: always asked, never inferred from
        # home/away (rink side is unrelated), and only ever pre-filled from
        # a team already flagged -- never for a new one (ticket 11).
        self._user_team_group = QButtonGroup(self)
        self._user_team_buttons: dict[Side | None, QRadioButton] = {}
        user_team_row = QHBoxLayout()
        for choice in (*_SIDES, None):
            button = QRadioButton("Neither" if choice is None else "")
            self._user_team_group.addButton(button)
            self._user_team_buttons[choice] = button
            user_team_row.addWidget(button)
        user_team_row.addStretch()
        self._user_team_group.buttonClicked.connect(self._on_user_team_clicked)

        form = QFormLayout()
        form.addRow("Game", self.game_summary_label)
        form.addRow("Rink type", self.rink_type_combo)
        form.addRow("Which team is yours?", user_team_row)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.team_ids_note_label)
        layout.addLayout(sides_row, stretch=1)
        self.review_panel.setLayout(layout)

    # -- accessors (for the review's widgets that exist per side/row) -------

    def team_label(self, side: Side) -> QLabel:
        return self._team_labels[side]

    def team_status_label(self, side: Side) -> QLabel:
        return self._team_status_labels[side]

    def team_combo(self, side: Side) -> QComboBox:
        return self._team_combos[side]

    def roster_table(self, side: Side) -> QTableWidget:
        return self._roster_tables[side]

    def user_team_button(self, choice: Side | None) -> QRadioButton:
        return self._user_team_buttons[choice]

    def player_combo(self, roster_index: int) -> QComboBox:
        """The player choice for `ImportProposal.roster[roster_index]`."""
        return self._player_combos[roster_index]

    def roster_index(self, full_name: str) -> int:
        return next(
            index
            for index, row in enumerate(self._proposal.roster)
            if row.full_name == full_name
        )

    def checked_user_team(self) -> Side | None | _Unanswered:
        for choice, button in self._user_team_buttons.items():
            if button.isChecked():
                return choice
        return self.UNANSWERED

    # -- fetching -------------------------------------------------------------

    def _fetch(self) -> None:
        self._clear()
        try:
            proposal = self._service.propose(self.link_field.text())
        except InvalidGameLinkError:
            self.error_label.setText("That isn't a league game link.")
            return
        except ManualEntryFallbackError as error:
            self.error_label.setText(
                f"Couldn't read this game's league data ({error}). "
                "You can enter it manually instead."
            )
            self.manual_entry_button.show()
            return
        if proposal.existing_game_id is not None:
            self._show_already_imported(proposal.existing_game_id)
            return
        self._show_review(proposal)

    def _clear(self) -> None:
        self._proposal = None
        self._existing_game_id = None
        self._player_combos: list[QComboBox] = []
        self._user_team_answered = False
        self.error_label.setText("")
        self.manual_entry_button.hide()
        self.open_existing_button.hide()
        self.review_panel.hide()
        self.confirm_button.setEnabled(False)

    def _show_already_imported(self, game_id: int) -> None:
        """A second import of the same league game is refused outright --
        the user is pointed at the existing game instead (ticket 11)."""
        self._existing_game_id = game_id
        self.error_label.setText(f"This game was already imported as game #{game_id}.")
        self.review_panel.hide()
        self.confirm_button.setEnabled(False)
        self.open_existing_button.show()

    # -- the review screen ------------------------------------------------------

    def _show_review(self, proposal: ImportProposal) -> None:
        self._proposal = proposal
        self.game_summary_label.setText(_game_summary(proposal))
        self.team_ids_note_label.setText(
            ""
            if proposal.team_league_ids_found
            else "The league's team IDs couldn't be read, so teams were "
            "matched by name only."
        )
        self.rink_type_combo.setCurrentIndex(0)
        known_players = self._service.known_players()
        for side in _SIDES:
            team = proposal.team(side)
            self._team_labels[side].setText(team.name)
            self._fill_team_combo(team)
            self._user_team_buttons[side].setText(team.name)
            self._fill_roster_table(side, proposal.roster_for(side), known_players)
        self._player_combos = [
            self._roster_tables[row.side].cellWidget(index, _PLAYER_COLUMN)
            for side in _SIDES
            for index, row in enumerate(proposal.roster_for(side))
        ]

        self._prefill_user_team()
        self.review_panel.show()
        self._update_team_status()
        self._update_confirm_enabled()

    def _fill_team_combo(self, team: TeamProposal) -> None:
        combo = self._team_combos[team.side]
        combo.blockSignals(True)
        combo.clear()
        if team.status == TeamMatchStatus.LINKED:
            combo.addItem("Existing team (same league team)", team.team_id)
            combo.setEnabled(False)
        else:
            combo.setEnabled(True)
            candidate_ids = {candidate.team_id for candidate in team.candidates}
            if team.status == TeamMatchStatus.NEEDS_CONFIRMATION:
                # Never silently merged: the user has to pick something.
                combo.addItem("Confirm or correct the match…", _UNANSWERED)
                for candidate in team.candidates:
                    combo.addItem(f"Same team as “{candidate.name}”", candidate.team_id)
            combo.addItem(f"Create new team “{team.name}”", _NEW)
            for other in self._service.linkable_teams(team.league_id):
                if other.team_id not in candidate_ids:
                    combo.addItem(f"Link to “{other.name}”", other.team_id)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _fill_roster_table(
        self, side: Side, rows: list[RosterRowProposal], known_players
    ) -> None:
        table = self._roster_tables[side]
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            status = _row_status(row)
            cells = [str(row.jersey_number), row.full_name, row.position.value, status]
            for column, text in enumerate(cells):
                table.setItem(index, column, QTableWidgetItem(text))
            table.setCellWidget(
                index, _PLAYER_COLUMN, _player_combo(row, known_players)
            )
        table.resizeColumnsToContents()

    def _on_team_choice_changed(self, _index: int) -> None:
        if self._proposal is None:
            return
        self._prefill_user_team()
        self._update_team_status()
        self._update_confirm_enabled()

    def _prefill_user_team(self) -> None:
        """Until the user answers themselves, pre-fill the answer from
        whichever currently-chosen existing team is already flagged as
        theirs -- a league_id link, or a name match they've confirmed --
        when exactly one is. Recomputed from scratch on every team
        change, so swapping a flagged team for a new one withdraws it."""
        if self._user_team_answered:
            return
        flagged = [side for side in _SIDES if self._chosen_team_is_flagged(side)]
        self._user_team_group.setExclusive(False)
        for button in self._user_team_buttons.values():
            button.setChecked(False)
        self._user_team_group.setExclusive(True)
        if len(flagged) == 1:
            self._user_team_buttons[flagged[0]].setChecked(True)

    def _chosen_team_is_flagged(self, side: Side) -> bool:
        team = self._proposal.team(side)
        if team.status == TeamMatchStatus.LINKED:
            return team.is_user_team
        choice = self._team_combos[side].currentData()
        return any(
            candidate.team_id == choice and candidate.is_user_team
            for candidate in team.candidates
        )

    def _update_team_status(self) -> None:
        for side in _SIDES:
            team = self._proposal.team(side)
            choice = self._team_combos[side].currentData()
            if team.status == TeamMatchStatus.LINKED:
                text = "Linked to the existing team with this league ID."
            elif choice == _UNANSWERED:
                text = "A team with this name already exists -- is it the same?"
            elif choice == _NEW:
                text = "New team will be created."
            else:
                text = f"Will be linked to “{self._team_combos[side].currentText()}”."
            self._team_status_labels[side].setText(text)

    def _on_user_team_clicked(self, _button: QAbstractButton) -> None:
        self._user_team_answered = True
        self._update_confirm_enabled()

    def _update_confirm_enabled(self) -> None:
        self.confirm_button.setEnabled(
            self._proposal is not None
            and self.checked_user_team() is not self.UNANSWERED
            and all(
                combo.currentData() != _UNANSWERED
                for combo in self._team_combos.values()
            )
        )

    # -- confirming ---------------------------------------------------------------

    def _confirm(self) -> None:
        if not self.confirm_button.isEnabled():
            return
        team_ids = {side: _chosen_id(self._team_combos[side]) for side in _SIDES}
        resolution = ImportResolution(
            home_team_id=team_ids[Side.HOME],
            away_team_id=team_ids[Side.AWAY],
            user_team=self.checked_user_team(),
            player_ids=tuple(_chosen_id(combo) for combo in self._player_combos),
            rink_type=RinkType(self.rink_type_combo.currentData()),
        )
        try:
            game = self._service.confirm(self._proposal, resolution)
        except AlreadyImportedError as error:
            self._show_already_imported(error.existing_game_id)
            return
        except (
            SameTeamBothSidesError,
            PlayerRosteredTwiceError,
            ValueError,
            KeyError,
        ) as error:
            self.error_label.setText(f"Couldn't import: {error}")
            return
        self.game_id = game.id
        self.accept()

    def _open_existing(self) -> None:
        self.game_id = self._existing_game_id
        self.accept()

    def _fall_back_to_manual(self) -> None:
        self.fall_back_to_manual = True
        self.accept()


def _game_summary(proposal: ImportProposal) -> str:
    parts = []
    if proposal.date is not None:
        parts.append(proposal.date.isoformat())
    if proposal.venue:
        parts.append(proposal.venue)
    periods = ", ".join(f"{p['home']}:{p['away']}" for p in proposal.period_scores)
    parts.append(
        f"{proposal.home.name} {proposal.home_score} : "
        f"{proposal.away_score} {proposal.away.name} ({periods})"
    )
    return " · ".join(parts)


def _row_status(row: RosterRowProposal) -> str:
    if row.status == PlayerMatchStatus.LINKED:
        return "Linked"
    if row.candidates:
        return "New — possible match"
    return "New"


def _player_combo(row: RosterRowProposal, known_players) -> QComboBox:
    """Defaults to the proposal's choice; offers the row's candidates
    first, then every other known player, searchable by typing -- the
    override ticket 11 asks for, since player matching is always the
    user's judgment call."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
    combo.completer().setCompletionMode(
        combo.completer().CompletionMode.PopupCompletion
    )
    combo.addItem("Create new player", _NEW)
    names = {player.player_id: player.full_name for player in known_players}
    listed = set()
    if row.player_id is not None:
        combo.addItem(
            names.get(row.player_id, f"Player {row.player_id}"), row.player_id
        )
        listed.add(row.player_id)
    for candidate in row.candidates:
        combo.addItem(f"{candidate.full_name} (possible match)", candidate.player_id)
        listed.add(candidate.player_id)
    if listed:
        combo.insertSeparator(combo.count())
    for player in known_players:
        if player.player_id not in listed:
            combo.addItem(player.full_name, player.player_id)
    combo.setCurrentIndex(combo.findData(row.player_id if row.player_id else _NEW))
    return combo


def _chosen_id(combo: QComboBox) -> int | None:
    data = combo.currentData()
    return None if data == _NEW else data
