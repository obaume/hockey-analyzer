"""Single-game stats view (ticket 18): renders `StatsEngine`'s output for
one loaded `GameData` -- team, skater, goalie, and shot-quality tables --
and recomputes on a strength-state filter change. Computes nothing
itself; every number shown comes straight from `stats_engine`.

Skater and goalie stats carry separate filters, since goalie stats
default to all situations while everything else defaults to 5v5 (see
CONTEXT.md's Goalie stats entry). Team and shot-quality tables follow the
skater filter. A stat that couldn't be computed, or was computed from
incomplete data, says so in its row's Note rather than disappearing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import ShotType
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.stats_engine import (
    ALL_SITUATIONS,
    EVEN_STRENGTH,
    ForAgainst,
    GoalieStats,
    SkaterStats,
    TeamStats,
)
from hockey_analyzer.domain.tagging_session import roster_entry_label

_ALL_SITUATIONS_LABEL = "All situations"
_MISSING = "—"

_SKATER_COLUMNS = (
    "Player",
    "Team",
    "CF",
    "CA",
    "CF%",
    "FF",
    "FA",
    "FF%",
    "+/-",
    "OZS",
    "DZS",
    "ZS%",
    "Note",
)
_GOALIE_COLUMNS = ("Team", "SA", "GA", "SV%", "GAA", "HDSA", "HD SV%", "MIN", "Note")
_CONTEXT_LABELS = {
    "rush": "Rush",
    "rebound": "Rebound",
    "screened": "Screened",
    "one_timer": "One-timer",
}


def _percent(value: float | None) -> str:
    return _MISSING if value is None else f"{value * 100:.1f}%"


def _save_percentage(value: float | None) -> str:
    """Hockey's usual ".917" form (1.000 for a perfect game)."""
    if value is None:
        return _MISSING
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0") else text


def _number(value: float | None, digits: int = 0) -> str:
    return _MISSING if value is None else f"{value:.{digits}f}"


def _signed(value: int) -> str:
    return f"{value:+d}" if value else "0"


def _team_rows(stats: TeamStats) -> dict[str, str]:
    return {
        **_for_against_rows("C", stats.corsi),
        **_for_against_rows("F", stats.fenwick),
        "GF": str(stats.goals.for_),
        "GA": str(stats.goals.against),
        "SH%": _percent(stats.shooting_percentage),
        "SV%": _save_percentage(stats.save_percentage),
        "PDO": _number(stats.pdo),
    }


def _for_against_rows(prefix: str, stats: ForAgainst) -> dict[str, str]:
    return {
        f"{prefix}F": str(stats.for_),
        f"{prefix}A": str(stats.against),
        f"{prefix}F%": _percent(stats.percentage),
    }


def _shot_quality_rows(stats: TeamStats) -> dict[str, str]:
    quality = stats.shot_quality
    return {
        "Attempts": str(quality.attempts),
        **{
            shot_type.value.capitalize(): str(quality.by_type.get(shot_type, 0))
            for shot_type in ShotType
        },
        **{
            label: str(quality.by_context[context])
            for context, label in _CONTEXT_LABELS.items()
        },
        "High-danger": str(quality.high_danger),
        "High-danger share": _percent(quality.high_danger_share),
    }


def _skater_note(stats: SkaterStats) -> str:
    notes = []
    if stats.incomplete:
        notes.append(
            f"incomplete: {stats.unresolved_shift_changes} unresolved shift change(s)"
        )
    if stats.zone_starts.undetermined:
        notes.append(f"{stats.zone_starts.undetermined} zone start(s) undetermined")
    return "; ".join(notes)


def _goalie_note(stats: GoalieStats) -> str:
    if not stats.incomplete:
        return ""
    return f"incomplete: {stats.unresolved_shift_changes} unresolved shift change(s)"


def _fill(
    table: QTableWidget,
    column_headers: Sequence[str],
    row_headers: Sequence[str] | None,
    rows: Sequence[Sequence[str]],
) -> None:
    table.clear()
    table.setColumnCount(len(column_headers))
    table.setRowCount(len(rows))
    table.setHorizontalHeaderLabels(list(column_headers))
    if row_headers is not None:
        table.setVerticalHeaderLabels(list(row_headers))
    for row, cells in enumerate(rows):
        for column, text in enumerate(cells):
            table.setItem(row, column, QTableWidgetItem(text))
    table.resizeColumnsToContents()


def _read_only_table() -> QTableWidget:
    table = QTableWidget()
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    return table


class StatsDialog(QDialog):
    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Game Stats")
        self.resize(900, 600)
        self._data = data
        game = data.game
        self._sides = [
            (team_id, team.name if team is not None else fallback)
            for team_id, team, fallback in (
                (game.home_team_id, game.home_team, "Home"),
                (game.away_team_id, game.away_team, "Away"),
            )
        ]
        self._team_names = dict(self._sides)
        self._labels = {
            entry.player_id: roster_entry_label(entry) for entry in data.roster
        }

        strengths = sorted(
            {
                event.strength_state
                for event in data.events
                if event.strength_state and event.strength_state != EVEN_STRENGTH
            }
        )
        self.skater_strength_combo = self._strength_combo(strengths, EVEN_STRENGTH)
        self.goalie_strength_combo = self._strength_combo(strengths, ALL_SITUATIONS)

        self.team_table = _read_only_table()
        self.skater_table = _read_only_table()
        self.skater_table.verticalHeader().setVisible(False)
        self.goalie_table = _read_only_table()
        self.shot_quality_table = _read_only_table()

        filters = QFormLayout()
        filters.addRow("Skater / team strength", self.skater_strength_combo)
        filters.addRow("Goalie strength", self.goalie_strength_combo)
        filter_row = QHBoxLayout()
        filter_row.addLayout(filters)
        filter_row.addStretch()

        tabs = QTabWidget()
        tabs.addTab(self.team_table, "Team")
        tabs.addTab(self.skater_table, "Skaters")
        tabs.addTab(self.goalie_table, "Goalies")
        tabs.addTab(self.shot_quality_table, "Shot quality")

        layout = QVBoxLayout(self)
        layout.addLayout(filter_row)
        layout.addWidget(tabs)

        self.skater_strength_combo.currentIndexChanged.connect(self._render_skaters)
        self.goalie_strength_combo.currentIndexChanged.connect(self._render_goalies)
        self._render_skaters()
        self._render_goalies()

    def _strength_combo(
        self, other_strengths: Sequence[str], default: str | None
    ) -> QComboBox:
        combo = QComboBox()
        combo.addItem(EVEN_STRENGTH, EVEN_STRENGTH)
        combo.addItem(_ALL_SITUATIONS_LABEL, ALL_SITUATIONS)
        for strength in other_strengths:
            combo.addItem(strength, strength)
        combo.setCurrentIndex(combo.findData(default))
        return combo

    def _render_skaters(self) -> None:
        strength = self.skater_strength_combo.currentData()
        team_stats = [
            stats_engine.team_stats(self._data, team_id, strength_state=strength)
            for team_id, _name in self._sides
        ]
        side_names = [name for _team_id, name in self._sides]
        self._fill_by_side(self.team_table, side_names, team_stats, _team_rows)
        self._fill_by_side(
            self.shot_quality_table, side_names, team_stats, _shot_quality_rows
        )

        report = stats_engine.skater_stats(self._data, strength_state=strength)
        rows = [self._skater_cells(stats) for stats in report.skaters]
        rows += [
            [
                self._labels[excluded.player_id],
                self._team_names.get(excluded.team_id, ""),
                *[_MISSING] * (len(_SKATER_COLUMNS) - 3),
                excluded.reason,
            ]
            for excluded in report.excluded
        ]
        _fill(self.skater_table, _SKATER_COLUMNS, None, rows)

    def _fill_by_side(
        self,
        table: QTableWidget,
        side_names: list[str],
        team_stats: list[TeamStats],
        rows_for: Callable[[TeamStats], dict[str, str]],
    ) -> None:
        per_side = [rows_for(stats) for stats in team_stats]
        row_headers = list(per_side[0])
        _fill(
            table,
            side_names,
            row_headers,
            [[side[header] for side in per_side] for header in row_headers],
        )

    def _skater_cells(self, stats: SkaterStats) -> list[str]:
        zone_starts = stats.zone_starts
        return [
            self._labels[stats.player_id],
            self._team_names.get(stats.team_id, ""),
            *_for_against_rows("C", stats.corsi).values(),
            *_for_against_rows("F", stats.fenwick).values(),
            _signed(stats.plus_minus),
            str(zone_starts.offensive),
            str(zone_starts.defensive),
            _percent(zone_starts.percentage),
            _skater_note(stats),
        ]

    def _render_goalies(self) -> None:
        strength = self.goalie_strength_combo.currentData()
        goalies = stats_engine.goalie_stats(self._data, strength_state=strength)
        _fill(
            self.goalie_table,
            _GOALIE_COLUMNS,
            [self._labels[stats.player_id] for stats in goalies],
            [
                [
                    self._team_names.get(stats.team_id, ""),
                    str(stats.shots_against),
                    str(stats.goals_against),
                    _save_percentage(stats.save_percentage),
                    _number(stats.goals_against_average, 2),
                    str(stats.high_danger_shots_against),
                    _save_percentage(stats.high_danger_save_percentage),
                    _number(stats.minutes_played, 1),
                    _goalie_note(stats),
                ]
                for stats in goalies
            ],
        )
