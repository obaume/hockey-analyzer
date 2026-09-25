"""The stat tables shared by the live stats view (tickets 18, 19) and the
report view (ticket 26): team, skater, position, line/unit, goalie, and
shot-quality tabs, filled from `stats_engine` result types. Both views
render through this one module, so a report shows its numbers exactly as
the live stats view it was exported from did.

Formats only -- computes nothing beyond `stats_engine`'s own derived
properties and `position_rollup`. Which team/player a stat belongs to is
resolved through the caller's `team_name`/`player_label` lookups, since
the live view keys stats by database id and a report by bundle-local key.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QTabWidget, QWidget

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import ShotType, UnitType
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.stats_engine import (
    ForAgainst,
    GameCoverage,
    GoalieStats,
    PositionStats,
    SkaterReport,
    SkaterStats,
    TeamStats,
    UnitReport,
    UnitStats,
)

MISSING = "—"
# How a strength filter reads wherever one is picked or shown.
ALL_SITUATIONS_LABEL = "All situations"
UNIT_DEFAULT_LABEL = "Unit default"


def team_names(games: Sequence[GameData]) -> dict[int, str]:
    """Every team playing in any of `games`, in first-seen order, home
    side first; a later game's name wins (a team may be renamed)."""
    names: dict[int, str] = {}
    for data in games:
        game = data.game
        for team_id, team, fallback in (
            (game.home_team_id, game.home_team, "Home"),
            (game.away_team_id, game.away_team, "Away"),
        ):
            if team_id is not None:
                names[team_id] = team.name if team is not None else fallback
    return names


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
    "CA/skater",
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
    "MIN",
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
    return MISSING if value is None else f"{value * 100:.1f}%"


def _save_percentage(value: float | None) -> str:
    """Hockey's usual ".917" form (1.000 for a perfect game)."""
    if value is None:
        return MISSING
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0") else text


def _number(value: float | None, digits: int = 0) -> str:
    return MISSING if value is None else f"{value:.{digits}f}"


def _signed(value: int) -> str:
    return f"{value:+d}" if value else "0"


def _unit_label(unit_type: UnitType, number: int) -> str:
    return f"{_UNIT_TYPE_LABELS[unit_type]} {number}"


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


def unresolved_caveat_text(
    unresolved: Mapping[int, int], sides: Sequence[tuple[int, str]]
) -> str:
    """The one caveat line for shift changes with an unknown player (whose
    ice time is missing from individual stats), one clause per team in
    `sides` order; empty when there are none."""
    parts = [
        f"{name}: {unresolved[team_id]} shift change(s) with an unknown "
        "player or unset on/off"
        for team_id, name in sides
        if unresolved.get(team_id)
    ]
    if not parts:
        return ""
    return "Individual stats are missing some ice time -- " + "; ".join(parts)


def coverage_text(
    coverage: Mapping[int, GameCoverage],
    sides: Sequence[tuple[int, str]],
    game_label: Callable[[int], str],
) -> str:
    """Which games each team's on-ice stats were computed over, and which
    were excluded for incomplete opponent shift tracking."""
    parts = []
    for team_id, name in sides:
        team = coverage.get(team_id)
        if team is None:
            continue
        played = len(team.included) + len(team.excluded)
        part = f"{name}: {len(team.included)} of {played} games"
        if team.excluded:
            part += (
                f" (excluded: {', '.join(game_label(id_) for id_ in team.excluded)})"
            )
        parts.append(part)
    return (
        "Individual and unit stats cover only games with trusted shift "
        "tracking -- " + "; ".join(parts)
    )


class StatTables:
    """The six stat tables, laid out as `tabs`. Each `show_*` refills one
    group; a group left unshown stays an empty table."""

    def __init__(
        self,
        *,
        team_name: Callable[[int], str],
        player_label: Callable[[int], str],
        parent: QWidget | None = None,
    ) -> None:
        self._team_name = team_name
        self._player_label = player_label
        self.team_table = _read_only_table()
        self.skater_table = _read_only_table()
        self.skater_table.verticalHeader().setVisible(False)
        self.position_table = _read_only_table()
        self.position_table.verticalHeader().setVisible(False)
        self.unit_table = _read_only_table()
        self.unit_table.verticalHeader().setVisible(False)
        self.goalie_table = _read_only_table()
        self.shot_quality_table = _read_only_table()

        self.tabs = QTabWidget(parent)
        self.tabs.addTab(self.team_table, "Team")
        self.tabs.addTab(self.skater_table, "Skaters")
        self.tabs.addTab(self.position_table, "Positions")
        self.tabs.addTab(self.unit_table, "Units")
        self.tabs.addTab(self.goalie_table, "Goalies")
        self.tabs.addTab(self.shot_quality_table, "Shot quality")

    def show_team_stats(self, team_stats: Sequence[TeamStats]) -> None:
        """Team and shot-quality tables, one column per team."""
        names = [self._team_name(stats.team_id) for stats in team_stats]
        self._fill_by_side(self.team_table, names, team_stats, _team_rows)
        self._fill_by_side(
            self.shot_quality_table, names, team_stats, _shot_quality_rows
        )

    def show_skaters(self, report: SkaterReport) -> None:
        """Skater table, and the position rollup over those skaters."""
        rows = [self._skater_cells(stats) for stats in report.skaters]
        rows += [
            [
                self._player_label(excluded.player_id),
                self._team_name(excluded.team_id),
                *[MISSING] * (len(_SKATER_COLUMNS) - 3),
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

    def show_units(self, report: UnitReport) -> None:
        rows = [self._unit_cells(stats) for stats in report.units]
        rows += [
            [
                self._team_name(excluded.team_id),
                _unit_label(excluded.unit_type, excluded.unit_number),
                self._players_text(excluded.player_ids),
                *[MISSING] * (len(_UNIT_COLUMNS) - 4),
                excluded.reason,
            ]
            for excluded in report.excluded
        ]
        _fill(self.unit_table, _UNIT_COLUMNS, None, rows)

    def show_goalies(self, goalies: Sequence[GoalieStats]) -> None:
        _fill(
            self.goalie_table,
            _GOALIE_COLUMNS,
            [self._player_label(stats.player_id) for stats in goalies],
            [
                [
                    self._team_name(stats.team_id),
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

    def _fill_by_side(
        self,
        table: QTableWidget,
        side_names: list[str],
        team_stats: Sequence[TeamStats],
        rows_for: Callable[[TeamStats], dict[str, str]],
    ) -> None:
        per_side = [rows_for(stats) for stats in team_stats]
        row_headers = list(per_side[0]) if per_side else []
        _fill(
            table,
            side_names,
            row_headers,
            [[side[header] for side in per_side] for header in row_headers],
        )

    def _skater_cells(self, stats: SkaterStats) -> list[str]:
        zone_starts = stats.zone_starts
        return [
            self._player_label(stats.player_id),
            self._team_name(stats.team_id),
            *_for_against_rows("C", stats.corsi).values(),
            *_for_against_rows("F", stats.fenwick).values(),
            _signed(stats.plus_minus),
            str(zone_starts.offensive),
            str(zone_starts.defensive),
            _percent(zone_starts.percentage),
            _skater_note(stats),
        ]

    def _position_cells(self, stats: PositionStats) -> list[str]:
        zone_starts = stats.zone_starts
        return [
            self._team_name(stats.team_id),
            stats.position.value if stats.position is not None else "Unset",
            str(stats.skaters),
            *_for_against_rows("C", stats.corsi).values(),
            *_for_against_rows("F", stats.fenwick).values(),
            _signed(stats.plus_minus),
            _number(stats.per_skater(stats.corsi.for_), 1),
            _number(stats.per_skater(stats.corsi.against), 1),
            str(zone_starts.offensive),
            str(zone_starts.defensive),
            _percent(zone_starts.percentage),
        ]

    def _unit_cells(self, stats: UnitStats) -> list[str]:
        return [
            self._team_name(stats.team_id),
            _unit_label(stats.unit_type, stats.unit_number),
            self._players_text(stats.player_ids),
            *_for_against_rows("C", stats.corsi).values(),
            *_for_against_rows("F", stats.fenwick).values(),
            str(stats.goals.for_),
            str(stats.goals.against),
            _signed(stats.plus_minus),
            _number(stats.time_together_ms / 60_000, 1),
            "",
        ]

    def _players_text(self, player_ids: frozenset[int]) -> str:
        return ", ".join(
            sorted(self._player_label(player_id) for player_id in player_ids)
        )
