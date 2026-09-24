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
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import ShotType, UnitType
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import Game
from hockey_analyzer.domain.stats_engine import (
    ALL_SITUATIONS,
    EVEN_STRENGTH,
    NATURAL_STRENGTH,
    ForAgainst,
    PositionStats,
    SkaterStats,
    TeamStats,
    UnitStats,
)
from hockey_analyzer.domain.tagging_session import roster_entry_label

_ALL_SITUATIONS_LABEL = "All situations"
_UNIT_DEFAULT_LABEL = "Unit default"
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
_POSITION_COLUMNS = (
    "Team",
    "Position",
    "Skaters",
    "CF",
    "CA",
    "CF%",
    "FF",
    "FA",
    "FF%",
    "+/-",
    "CF/skater",
    "OZS",
    "DZS",
    "ZS%",
)
_UNIT_COLUMNS = (
    "Team",
    "Unit",
    "Players",
    "CF",
    "CA",
    "CF%",
    "FF",
    "FA",
    "FF%",
    "GF",
    "GA",
    "+/-",
    "TOI",
    "Note",
)
_UNIT_TYPE_LABELS = {
    UnitType.FORWARD_LINE: "Forward-Line",
    UnitType.DEFENSE_PAIR: "Defense-Pair",
    UnitType.POWER_PLAY: "Power-Play",
    UnitType.PENALTY_KILL: "Penalty-Kill",
}
_GOALIE_COLUMNS = ("Team", "SA", "GA", "SV%", "GAA", "HDSA", "HD SV%", "MIN")
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


def _minutes(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _unit_label(unit_type: UnitType, number: int) -> str:
    return f"{_UNIT_TYPE_LABELS[unit_type]} {number}"


def _game_label(game: Game) -> str:
    return game.date.isoformat() if game.date else f"Game #{game.id}"


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
    undetermined = stats.zone_starts.undetermined
    return f"{undetermined} zone start(s) undetermined" if undetermined else ""


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
    def __init__(
        self, games: Sequence[GameData], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "Game Stats" if len(games) == 1 else f"Stats -- {len(games)} games"
        )
        self.resize(900, 600)
        self._games = list(games)
        # Every team playing in any selected game, in first-seen order,
        # home side first; later games' names win (a team may be renamed).
        team_names: dict[int, str] = {}
        for data in self._games:
            game = data.game
            for team_id, team, fallback in (
                (game.home_team_id, game.home_team, "Home"),
                (game.away_team_id, game.away_team, "Away"),
            ):
                if team_id is not None:
                    team_names[team_id] = team.name if team is not None else fallback
        self._sides = list(team_names.items())
        self._team_names = team_names
        # A player's label as of the latest selected game they played in.
        self._labels = {
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
        self.unit_strength_combo.insertItem(0, _UNIT_DEFAULT_LABEL, NATURAL_STRENGTH)
        self.unit_strength_combo.setCurrentIndex(0)

        unresolved: dict[int, int] = {}
        for data in self._games:
            for team_id, count in stats_engine.unresolved_shift_changes(data).items():
                unresolved[team_id] = unresolved.get(team_id, 0) + count
        self.caveat_label = QLabel(
            "Individual stats are missing some ice time -- "
            + "; ".join(
                f"{name}: {unresolved[team_id]} shift change(s) with an unknown "
                "player or unset on/off"
                for team_id, name in self._sides
                if unresolved.get(team_id)
            )
        )
        self.caveat_label.setWordWrap(True)
        self.caveat_label.setHidden(not unresolved)

        self.coverage_label = QLabel(self._coverage_text())
        self.coverage_label.setWordWrap(True)
        self.coverage_label.setHidden(len(self._games) < 2)

        self.team_table = _read_only_table()
        self.skater_table = _read_only_table()
        self.skater_table.verticalHeader().setVisible(False)
        self.position_table = _read_only_table()
        self.position_table.verticalHeader().setVisible(False)
        self.unit_table = _read_only_table()
        self.unit_table.verticalHeader().setVisible(False)
        self.goalie_table = _read_only_table()
        self.shot_quality_table = _read_only_table()

        filters = QFormLayout()
        filters.addRow("Skater / team strength", self.skater_strength_combo)
        filters.addRow("Unit strength", self.unit_strength_combo)
        filters.addRow("Goalie strength", self.goalie_strength_combo)
        filter_row = QHBoxLayout()
        filter_row.addLayout(filters)
        filter_row.addStretch()

        tabs = QTabWidget()
        tabs.addTab(self.team_table, "Team")
        tabs.addTab(self.skater_table, "Skaters")
        tabs.addTab(self.position_table, "Positions")
        tabs.addTab(self.unit_table, "Units")
        tabs.addTab(self.goalie_table, "Goalies")
        tabs.addTab(self.shot_quality_table, "Shot quality")

        layout = QVBoxLayout(self)
        layout.addLayout(filter_row)
        layout.addWidget(self.caveat_label)
        layout.addWidget(self.coverage_label)
        layout.addWidget(tabs)

        self.skater_strength_combo.currentIndexChanged.connect(self._render_skaters)
        self.unit_strength_combo.currentIndexChanged.connect(self._render_units)
        self.goalie_strength_combo.currentIndexChanged.connect(self._render_goalies)
        self._render_skaters()
        self._render_units()
        self._render_goalies()

    def _coverage_text(self) -> str:
        games = {data.game.id: data.game for data in self._games}
        coverage = stats_engine.on_ice_coverage(self._games)
        parts = []
        for team_id, name in self._sides:
            team = coverage[team_id]
            played = len(team.included) + len(team.excluded)
            part = f"{name}: {len(team.included)} of {played} games"
            if team.excluded:
                excluded = ", ".join(_game_label(games[id_]) for id_ in team.excluded)
                part += f" (excluded: {excluded})"
            parts.append(part)
        return (
            "Individual and unit stats cover only games with trusted shift "
            "tracking -- " + "; ".join(parts)
        )

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
            stats_engine.combined_team_stats(
                self._games, team_id, strength_state=strength
            )
            for team_id, _name in self._sides
        ]
        side_names = [name for _team_id, name in self._sides]
        self._fill_by_side(self.team_table, side_names, team_stats, _team_rows)
        self._fill_by_side(
            self.shot_quality_table, side_names, team_stats, _shot_quality_rows
        )

        report = stats_engine.combined_skater_stats(
            self._games, strength_state=strength
        )
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
        _fill(
            self.position_table,
            _POSITION_COLUMNS,
            None,
            [
                self._position_cells(stats)
                for stats in stats_engine.position_rollup(report)
            ],
        )

    def _position_cells(self, stats: PositionStats) -> list[str]:
        zone_starts = stats.zone_starts
        return [
            self._team_names.get(stats.team_id, ""),
            stats.position.value if stats.position is not None else "Unset",
            str(stats.skaters),
            *_for_against_rows("C", stats.corsi).values(),
            *_for_against_rows("F", stats.fenwick).values(),
            _signed(stats.plus_minus),
            _number(stats.per_skater(stats.corsi.for_), 1),
            str(zone_starts.offensive),
            str(zone_starts.defensive),
            _percent(zone_starts.percentage),
        ]

    def _render_units(self) -> None:
        strength = self.unit_strength_combo.currentData()
        report = stats_engine.combined_unit_stats(self._games, strength_state=strength)
        rows = [self._unit_cells(stats) for stats in report.units]
        rows += [
            [
                self._team_names.get(excluded.team_id, ""),
                _unit_label(excluded.unit_type, excluded.unit_number),
                self._players_text(excluded.player_ids),
                *[_MISSING] * (len(_UNIT_COLUMNS) - 4),
                excluded.reason,
            ]
            for excluded in report.excluded
        ]
        _fill(self.unit_table, _UNIT_COLUMNS, None, rows)

    def _unit_cells(self, stats: UnitStats) -> list[str]:
        return [
            self._team_names.get(stats.team_id, ""),
            _unit_label(stats.unit_type, stats.unit_number),
            self._players_text(stats.player_ids),
            *_for_against_rows("C", stats.corsi).values(),
            *_for_against_rows("F", stats.fenwick).values(),
            str(stats.goals.for_),
            str(stats.goals.against),
            _signed(stats.plus_minus),
            _minutes(stats.time_together_ms),
            "",
        ]

    def _players_text(self, player_ids: frozenset[int]) -> str:
        return ", ".join(sorted(self._labels[player_id] for player_id in player_ids))

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
        goalies = stats_engine.combined_goalie_stats(
            self._games, strength_state=strength
        )
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
                ]
                for stats in goalies
            ],
        )
