"""Report view (ticket 26): renders one frozen `Report` -- the one about to
be exported, or one opened from a bundle file -- through the same
`StatTables` the live stats view uses, so a recipient sees the numbers
exactly as the sender did at export time.

Computes nothing from raw events (a report has none): every number comes
from the report's own frozen stat groups, each labelled with the strength
filter it was computed at, since an opened report can't be re-filtered.
A team or player report shows only its subject's rows (and caveats); a
game report shows both sides, as the live single-game view does.
"""

from __future__ import annotations

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.report_bundle import (
    Filtered,
    GameRef,
    Report,
    ReportKind,
)
from hockey_analyzer.domain.stats_engine import (
    ALL_SITUATIONS,
    SkaterReport,
    UnitReport,
    UnitStrength,
)
from hockey_analyzer.ui.stat_tables import (
    StatTables,
    coverage_text,
    unresolved_caveat_text,
)

_KIND_TITLES = {
    ReportKind.GAME: "Game report",
    ReportKind.TEAM: "Team report",
    ReportKind.PLAYER: "Player report",
}


def _strength_label(strength: str | None | UnitStrength) -> str:
    """Worded as the live stats view's filter combos word it."""
    if isinstance(strength, UnitStrength):
        return "Unit default"
    if strength == ALL_SITUATIONS:
        return "All situations"
    return strength


def _filters_text(report: Report) -> str:
    groups: list[tuple[str, Filtered | None]] = (
        [("Team / skaters", report.team_stats)]
        if report.team_stats is not None
        and report.skater_stats is not None
        and report.team_stats.strength_state == report.skater_stats.strength_state
        else [("Team", report.team_stats), ("Skaters", report.skater_stats)]
    )
    groups += [("Units", report.unit_stats), ("Goalies", report.goalie_stats)]
    return "Strength: " + " · ".join(
        f"{name}: {_strength_label(group.strength_state)}"
        for name, group in groups
        if group is not None
    )


class ReportView(QWidget):
    def __init__(self, report: Report, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._report = report
        self._player_id = report.subject_player_id
        self._team_ids = self._subject_team_ids()
        sides = [
            (team_id, team.name)
            for team_id, team in report.teams.items()
            if team_id in self._team_ids
        ]

        self.tables = StatTables(
            team_name=self._team_name, player_label=self._player_label
        )
        self.team_table = self.tables.team_table
        self.skater_table = self.tables.skater_table
        self.position_table = self.tables.position_table
        self.unit_table = self.tables.unit_table
        self.goalie_table = self.tables.goalie_table
        self.shot_quality_table = self.tables.shot_quality_table

        self.title_label = QLabel(self._title())
        font = self.title_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.title_label.setFont(font)
        self.games_label = QLabel(
            "Games: " + "; ".join(self._game_line(game) for game in report.games)
        )
        self.games_label.setWordWrap(True)

        self.filters_label = QLabel(_filters_text(report))
        self.filters_label.setWordWrap(True)

        self.caveat_label = QLabel(
            unresolved_caveat_text(report.unresolved_shift_changes, sides)
        )
        self.caveat_label.setWordWrap(True)
        self.caveat_label.setHidden(not self.caveat_label.text())

        # A one-game report only needs the line when a team was left out;
        # over several games, which ones counted is worth saying either way.
        coverage = {
            team_id: cov
            for team_id, cov in report.on_ice_coverage.items()
            if team_id in self._team_ids
        }
        excluded = any(cov.excluded for cov in coverage.values())
        self.coverage_label = QLabel(coverage_text(coverage, sides, self._game_label))
        self.coverage_label.setWordWrap(True)
        self.coverage_label.setHidden(
            not coverage or (len(report.games) < 2 and not excluded)
        )

        self.summary = QTextBrowser()
        self.summary.setMarkdown(report.summary)
        self.summary.setHidden(not report.summary.strip())

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.games_label)
        layout.addWidget(self.summary)
        layout.addWidget(self.filters_label)
        layout.addWidget(self.caveat_label)
        layout.addWidget(self.coverage_label)
        layout.addWidget(self.tables.tabs, stretch=1)

        self._show_stats()
        self.chart_labels: list[QLabel] = []
        if report.charts:
            self.tables.tabs.addTab(self._charts_page(), "Charts")

    def _charts_page(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        for chart in self._report.charts:
            pixmap = QPixmap()
            pixmap.loadFromData(chart.png, "PNG")
            label = QLabel()
            label.setPixmap(pixmap)
            label.setToolTip(chart.name)
            self.chart_labels.append(label)
            column.addWidget(label)
        column.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        return scroll

    def _mark_not_included(self, *titles: str) -> None:
        """An older bundle's missing stat group: say so rather than show an
        empty table that reads like "no stats"."""
        tabs = self.tables.tabs
        for index in range(tabs.count()):
            if tabs.tabText(index) in titles:
                tabs.setTabEnabled(index, False)
                tabs.setTabToolTip(
                    index, "These stats are not included in this report."
                )

    def _subject_team_ids(self) -> set[int]:
        """The teams whose rows this report shows: both sides of a game
        report, a team report's subject, or every team a player report's
        subject played for."""
        report = self._report
        if report.subject_team_id is not None:
            return {report.subject_team_id}
        if self._player_id is None:
            return set(report.teams)
        rows = []
        if report.skater_stats is not None:
            rows += report.skater_stats.stats.skaters
            rows += report.skater_stats.stats.excluded
        if report.goalie_stats is not None:
            rows += report.goalie_stats.stats
        return {row.team_id for row in rows if row.player_id == self._player_id}

    def _is_shown(self, team_id: int, player_id: int | None = None) -> bool:
        if team_id not in self._team_ids:
            return False
        return self._player_id is None or player_id in (None, self._player_id)

    def _show_stats(self) -> None:
        report = self._report
        if report.team_stats is None:
            self._mark_not_included("Team", "Shot quality")
        if report.skater_stats is None:
            self._mark_not_included("Skaters", "Positions")
        if report.unit_stats is None:
            self._mark_not_included("Units")
        if report.goalie_stats is None:
            self._mark_not_included("Goalies")
        if report.team_stats is not None:
            self.tables.show_team_stats(
                [s for s in report.team_stats.stats if self._is_shown(s.team_id)]
            )
        if report.skater_stats is not None:
            skaters = report.skater_stats.stats
            self.tables.show_skaters(
                SkaterReport(
                    skaters=[
                        s
                        for s in skaters.skaters
                        if self._is_shown(s.team_id, s.player_id)
                    ],
                    excluded=[
                        s
                        for s in skaters.excluded
                        if self._is_shown(s.team_id, s.player_id)
                    ],
                )
            )
        if report.unit_stats is not None:
            units = report.unit_stats.stats
            self.tables.show_units(
                UnitReport(
                    units=[u for u in units.units if self._shows_unit(u)],
                    excluded=[u for u in units.excluded if self._shows_unit(u)],
                )
            )
        if report.goalie_stats is not None:
            self.tables.show_goalies(
                [
                    g
                    for g in report.goalie_stats.stats
                    if self._is_shown(g.team_id, g.player_id)
                ]
            )

    def _shows_unit(self, unit) -> bool:
        if unit.team_id not in self._team_ids:
            return False
        return self._player_id is None or self._player_id in unit.player_ids

    def _title(self) -> str:
        report = self._report
        title = _KIND_TITLES[report.kind]
        if report.kind is ReportKind.GAME and report.games:
            game = report.games[0]
            return f"{title}: {self._matchup(game)}"
        if report.subject_team_id is not None:
            subject = self._team_name(report.subject_team_id)
        elif report.subject_player_id is not None:
            subject = self._player_label(report.subject_player_id)
        else:
            subject = ""
        return f"{title}: {subject} -- {len(report.games)} games"

    def _matchup(self, game: GameRef) -> str:
        home = self._side_name(game.home_team_id, "Home")
        away = self._side_name(game.away_team_id, "Away")
        if game.home_score is None or game.away_score is None:
            return f"{home} vs {away}"
        return f"{home} {game.home_score}–{game.away_score} {away}"

    def _game_line(self, game: GameRef) -> str:
        return f"{self._game_label(game.game_id)} · {self._matchup(game)}"

    def _side_name(self, team_id: int | None, fallback: str) -> str:
        return fallback if team_id is None else self._team_name(team_id)

    def _team_name(self, team_id: int) -> str:
        team = self._report.teams.get(team_id)
        return "" if team is None else team.name

    def _game_label(self, game_id: int) -> str:
        game = next(game for game in self._report.games if game.game_id == game_id)
        return game.date.isoformat() if game.date else f"Game {game_id}"

    def _player_label(self, player_id: int) -> str:
        """As the live view labels a rostered player ("#14 Jordan Kim")."""
        player = self._report.players.get(player_id)
        if player is None or player.jersey_number is None:
            return f"Player {player_id}"
        if player.full_name:
            return f"#{player.jersey_number} {player.full_name}"
        return f"#{player.jersey_number}"


class ReportViewerDialog(QDialog):
    """A `ReportView` in its own window: the export preview, or a bundle
    opened from a file (`title` then names the file)."""

    def __init__(
        self, report: Report, *, title: str = "Report", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 700)
        self.view = ReportView(report)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        layout.addWidget(buttons)
