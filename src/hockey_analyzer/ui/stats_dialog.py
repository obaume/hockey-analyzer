"""Stats view (tickets 18, 19): renders `StatsEngine`'s output for one or
more loaded `GameData`s -- team, skater, position, line/unit, goalie, and
shot-quality tables -- and recomputes on a strength-state filter change.
Computes nothing itself; every number shown comes straight from
`stats_engine`, aggregated over the selected games sum-then-compute (a
single game is just a one-game selection).

Skater, goalie, and unit stats carry separate filters, since goalie stats
default to all situations, units to their own natural context, and
everything else to 5v5 (see CONTEXT.md's Goalie stats and Game unit
assignment entries). Team, shot-quality, and position tables follow the
skater filter. A stat that couldn't be computed says so in its row's Note
rather than disappearing, unresolved shift changes (which leave some
unknown player's ice time missing) get one caveat line per team, and a
multi-game selection lists, per team, which games its on-ice stats were
computed over.

The tables themselves are `StatTables`, shared with the report view, and
"Export Report…" (ticket 26) freezes what's on screen -- these games, at
the filters currently picked -- into a report bundle.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import Game
from hockey_analyzer.domain.report_bundle import StrengthFilters
from hockey_analyzer.domain.stats_engine import (
    ALL_SITUATIONS,
    EVEN_STRENGTH,
    NATURAL_STRENGTH,
)
from hockey_analyzer.domain.tagging_session import roster_entry_label
from hockey_analyzer.ui.report_export_dialog import ReportExportDialog
from hockey_analyzer.ui.stat_tables import (
    ALL_SITUATIONS_LABEL,
    UNIT_DEFAULT_LABEL,
    StatTables,
    coverage_text,
    team_names,
    unresolved_caveat_text,
)


def _game_label(game: Game) -> str:
    return game.date.isoformat() if game.date else f"Game #{game.id}"


class ExportDialogFactory(Protocol):
    """`ReportExportDialog`'s constructor shape, as `_export` calls it."""

    def __call__(
        self,
        games: Sequence[GameData],
        *,
        filters: StrengthFilters,
        parent: QWidget | None = None,
    ) -> ReportExportDialog: ...


class StatsDialog(QDialog):
    def __init__(
        self,
        games: Sequence[GameData],
        parent: QWidget | None = None,
        *,
        export_dialog_factory: ExportDialogFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self._export_dialog_factory = export_dialog_factory or ReportExportDialog
        self.setWindowTitle(
            "Game Stats" if len(games) == 1 else f"Stats -- {len(games)} games"
        )
        self.resize(900, 600)
        self._games = list(games)
        names = team_names(self._games)
        self._sides = list(names.items())
        # A player's label as of the latest selected game they played in.
        labels = {
            entry.player_id: roster_entry_label(entry)
            for data in self._games
            for entry in data.roster
        }

        strengths = sorted(
            {
                event.strength_state
                for data in self._games
                for event in data.events
                if event.strength_state and event.strength_state != EVEN_STRENGTH
            }
        )
        self.skater_strength_combo = self._strength_combo(strengths, EVEN_STRENGTH)
        self.goalie_strength_combo = self._strength_combo(strengths, ALL_SITUATIONS)
        self.unit_strength_combo = self._strength_combo(strengths, NATURAL_STRENGTH)
        self.unit_strength_combo.insertItem(0, UNIT_DEFAULT_LABEL, NATURAL_STRENGTH)
        self.unit_strength_combo.setCurrentIndex(0)

        unresolved: dict[int, int] = {}
        for data in self._games:
            for team_id, count in stats_engine.unresolved_shift_changes(data).items():
                unresolved[team_id] = unresolved.get(team_id, 0) + count
        self.caveat_label = QLabel(unresolved_caveat_text(unresolved, self._sides))
        self.caveat_label.setWordWrap(True)
        self.caveat_label.setHidden(not self.caveat_label.text())

        games_by_id = {data.game.id: data.game for data in self._games}
        self.coverage_label = QLabel(
            coverage_text(
                stats_engine.on_ice_coverage(self._games),
                self._sides,
                lambda game_id: _game_label(games_by_id[game_id]),
            )
        )
        self.coverage_label.setWordWrap(True)
        self.coverage_label.setHidden(len(self._games) < 2)

        self.tables = StatTables(
            team_name=lambda team_id: names.get(team_id, ""),
            player_label=lambda player_id: labels.get(player_id, f"Player {player_id}"),
        )
        self.team_table = self.tables.team_table
        self.skater_table = self.tables.skater_table
        self.position_table = self.tables.position_table
        self.unit_table = self.tables.unit_table
        self.goalie_table = self.tables.goalie_table
        self.shot_quality_table = self.tables.shot_quality_table

        filters = QFormLayout()
        filters.addRow("Skater / team strength", self.skater_strength_combo)
        filters.addRow("Unit strength", self.unit_strength_combo)
        filters.addRow("Goalie strength", self.goalie_strength_combo)
        # Ticket 26: freezes what's on screen -- these games, at these
        # filters -- into a report bundle.
        self.export_button = QPushButton("Export Report…")
        self.export_button.clicked.connect(self._export)
        filter_row = QHBoxLayout()
        filter_row.addLayout(filters)
        filter_row.addStretch()
        filter_row.addWidget(self.export_button)

        layout = QVBoxLayout(self)
        layout.addLayout(filter_row)
        layout.addWidget(self.caveat_label)
        layout.addWidget(self.coverage_label)
        layout.addWidget(self.tables.tabs)

        self.skater_strength_combo.currentIndexChanged.connect(self._render_skaters)
        self.unit_strength_combo.currentIndexChanged.connect(self._render_units)
        self.goalie_strength_combo.currentIndexChanged.connect(self._render_goalies)
        self._render_skaters()
        self._render_units()
        self._render_goalies()

    def _export(self) -> None:
        skaters = self.skater_strength_combo.currentData()
        filters = StrengthFilters(
            team=skaters,
            skaters=skaters,
            goalies=self.goalie_strength_combo.currentData(),
            units=self.unit_strength_combo.currentData(),
        )
        self._export_dialog_factory(self._games, filters=filters, parent=self).exec()

    def _strength_combo(
        self, other_strengths: Sequence[str], default: str | None
    ) -> QComboBox:
        combo = QComboBox()
        combo.addItem(EVEN_STRENGTH, EVEN_STRENGTH)
        combo.addItem(ALL_SITUATIONS_LABEL, ALL_SITUATIONS)
        for strength in other_strengths:
            combo.addItem(strength, strength)
        combo.setCurrentIndex(combo.findData(default))
        return combo

    def _render_skaters(self) -> None:
        strength = self.skater_strength_combo.currentData()
        self.tables.show_team_stats(
            [
                stats_engine.combined_team_stats(
                    self._games, team_id, strength_state=strength
                )
                for team_id, _name in self._sides
            ]
        )
        self.tables.show_skaters(
            stats_engine.combined_skater_stats(self._games, strength_state=strength)
        )

    def _render_units(self) -> None:
        self.tables.show_units(
            stats_engine.combined_unit_stats(
                self._games, strength_state=self.unit_strength_combo.currentData()
            )
        )

    def _render_goalies(self) -> None:
        self.tables.show_goalies(
            stats_engine.combined_goalie_stats(
                self._games, strength_state=self.goalie_strength_combo.currentData()
            )
        )
