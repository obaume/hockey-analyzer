"""Report export (ticket 26): turns the games open in the live stats view
into a report bundle file. The sender picks what a multi-game report is
about (a team or a player; a single game needs no subject), writes a
Markdown summary, previews the frozen report, and saves it.

The stats view's strength filters come in as they stood when "Export
Report…" was pressed, so each stat group is frozen at the filter the
sender was looking at (see CONTEXT.md's Report bundle entry). The shot-map
chart is baked here, from the live games, since the bundle can't carry
the events it's drawn from (ADR-0003).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.report_bundle import (
    BUNDLE_EXTENSION,
    Report,
    ReportKind,
    StrengthFilters,
    build_game_report,
    build_player_report,
    build_team_report,
    write_bundle,
)
from hockey_analyzer.domain.tagging_session import roster_entry_label
from hockey_analyzer.ui.report_charts import shot_map_chart
from hockey_analyzer.ui.report_view import ReportViewerDialog
from hockey_analyzer.ui.stat_tables import team_names

REPORT_FILE_FILTER = f"Hockey Analyzer report (*{BUNDLE_EXTENSION})"
# Characters Windows (the strictest target) refuses in a file name.
_UNSAFE_FILE_CHARS = str.maketrans({char: "-" for char in '<>:"/\\|?*'})


class _Subject(NamedTuple):
    """One "Report about" choice for a multi-game report."""

    kind: ReportKind  # TEAM or PLAYER
    id: int  # the team's or player's database id
    name: str
    team_id: int  # the team's own id, or the player's (latest) team's


class ReportExportDialog(QDialog):
    def __init__(
        self,
        games: Sequence[GameData],
        *,
        filters: StrengthFilters,
        choose_path: Callable[[str], str] | None = None,
        preview: Callable[[Report], object] | None = None,
        error_notice: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Report")
        self.resize(640, 480)
        self._games = list(games)
        self._filters = filters
        self._choose_path = choose_path or self._show_save_dialog
        self._preview = preview or self._show_preview
        self._error_notice = error_notice or self._show_error
        self.exported_path: Path | None = None

        # Every team, then every rostered player (as of the latest game
        # they played in), in first-seen order -- as the live stats view
        # lists them.
        self._team_names = team_names(self._games)
        players: dict[int, _Subject] = {}
        for data in self._games:
            for entry in data.roster:
                players[entry.player_id] = _Subject(
                    ReportKind.PLAYER,
                    entry.player_id,
                    roster_entry_label(entry),
                    entry.team_id,
                )
        self._subjects = [
            _Subject(ReportKind.TEAM, team_id, name, team_id)
            for team_id, name in self._team_names.items()
        ] + list(players.values())

        self.subject_combo = QComboBox()
        for subject in self._subjects:
            if subject.kind is ReportKind.TEAM:
                self.subject_combo.addItem(f"Team: {subject.name}")
            else:
                team = self._team_names.get(subject.team_id, "")
                self.subject_combo.addItem(f"Player: {subject.name} ({team})")
        form = QFormLayout()
        form.addRow("Report about", self.subject_combo)
        self.subject_combo.setHidden(len(self._games) < 2)
        form.labelForField(self.subject_combo).setHidden(len(self._games) < 2)

        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setPlaceholderText(
            "Written summary for whoever opens this report (Markdown: "
            "# heading, **bold**, - list)"
        )
        self._summary_preview = QTextBrowser()
        summary_tabs = QTabWidget()
        summary_tabs.addTab(self.summary_edit, "Write")
        summary_tabs.addTab(self._summary_preview, "Preview")
        summary_tabs.currentChanged.connect(
            lambda _index: self._summary_preview.setMarkdown(self._summary())
        )

        self.preview_button = QPushButton("Preview Report…")
        self.preview_button.clicked.connect(lambda: self._preview(self.report()))
        self.export_button = QPushButton("Export…")
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self._export)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.preview_button)
        buttons.addStretch(1)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Summary"))
        layout.addWidget(summary_tabs, stretch=1)
        layout.addLayout(buttons)

    def report(self) -> Report:
        """The report as it would be exported right now."""
        subject = self._subject()
        # The shot map attacks right for the report's own side: the team,
        # or the player's team.
        chart = shot_map_chart(
            self._games, right_team_id=None if subject is None else subject.team_id
        )
        common = {
            "summary": self._summary(),
            "filters": self._filters,
            "charts": [] if chart is None else [chart],
        }
        if subject is None:
            return build_game_report(self._games[0], **common)
        if subject.kind is ReportKind.TEAM:
            return build_team_report(self._games, subject.id, **common)
        return build_player_report(self._games, subject.id, **common)

    def _subject(self) -> _Subject | None:
        """None for a single game, whose report is about both sides."""
        if len(self._games) < 2:
            return None
        return self._subjects[self.subject_combo.currentIndex()]

    def _summary(self) -> str:
        return self.summary_edit.toPlainText()

    def _suggested_name(self) -> str:
        subject = self._subject()
        if subject is None:
            game = self._games[0].game
            home = self._team_names.get(game.home_team_id, "Home")
            away = self._team_names.get(game.away_team_id, "Away")
            name = f"{home} vs {away}"
            if game.date is not None:
                name = f"{game.date.isoformat()} {name}"
        else:
            name = f"{subject.name} -- {len(self._games)} games"
        return name.translate(_UNSAFE_FILE_CHARS) + BUNDLE_EXTENSION

    def _export(self) -> None:
        chosen = self._choose_path(self._suggested_name())
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix != BUNDLE_EXTENSION:
            path = path.with_name(path.name + BUNDLE_EXTENSION)
        try:
            write_bundle(path, self.report())
        except OSError as error:
            self._error_notice(f"Could not save the report to {path}:\n{error}")
            return
        self.exported_path = path
        self.accept()

    def _show_save_dialog(self, suggested_name: str) -> str:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export report",
            suggested_name,
            REPORT_FILE_FILTER,
        )
        return path

    def _show_preview(self, report: Report) -> None:
        ReportViewerDialog(report, parent=self).exec()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Export failed", message)
