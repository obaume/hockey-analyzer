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
    StrengthFilters,
    build_game_report,
    build_player_report,
    build_team_report,
    write_bundle,
)
from hockey_analyzer.domain.tagging_session import roster_entry_label
from hockey_analyzer.ui.report_charts import shot_map_chart
from hockey_analyzer.ui.report_view import ReportViewerDialog

_TEAM = "team"
_PLAYER = "player"
# Characters Windows (the strictest target) refuses in a file name.
_UNSAFE_FILE_CHARS = str.maketrans({char: "-" for char in '<>:"/\\|?*'})


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

        # Every team, then every rostered player, in first-seen order --
        # as the live stats view lists them. Data is (kind, database id).
        self._team_names: dict[int, str] = {}
        for data in self._games:
            game = data.game
            for team_id, team, fallback in (
                (game.home_team_id, game.home_team, "Home"),
                (game.away_team_id, game.away_team, "Away"),
            ):
                if team_id is not None:
                    self._team_names[team_id] = (
                        team.name if team is not None else fallback
                    )
        players: dict[int, str] = {}
        for data in self._games:
            for entry in data.roster:
                team = self._team_names.get(entry.team_id, "")
                players[entry.player_id] = f"{roster_entry_label(entry)} ({team})"

        self.subject_combo = QComboBox()
        for team_id, name in self._team_names.items():
            self.subject_combo.addItem(f"Team: {name}", (_TEAM, team_id))
        for player_id, label in players.items():
            self.subject_combo.addItem(f"Player: {label}", (_PLAYER, player_id))
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
        kind, subject_id = self._subject()
        right_team_id = subject_id if kind == _TEAM else None
        chart = shot_map_chart(self._games, right_team_id=right_team_id)
        common = {
            "summary": self._summary(),
            "filters": self._filters,
            "charts": [] if chart is None else [chart],
        }
        if kind == _TEAM:
            return build_team_report(self._games, subject_id, **common)
        if kind == _PLAYER:
            return build_player_report(self._games, subject_id, **common)
        return build_game_report(self._games[0], **common)

    def _subject(self) -> tuple[str | None, int | None]:
        if len(self._games) < 2:
            return None, None
        kind, subject_id = self.subject_combo.currentData()
        return kind, subject_id

    def _summary(self) -> str:
        return self.summary_edit.toPlainText()

    def _suggested_name(self) -> str:
        kind, subject_id = self._subject()
        if kind is None:
            game = self._games[0].game
            home = self._team_names.get(game.home_team_id, "Home")
            away = self._team_names.get(game.away_team_id, "Away")
            name = f"{home} vs {away}"
            if game.date is not None:
                name = f"{game.date.isoformat()} {name}"
        else:
            subject = self.subject_combo.currentText().split(": ", 1)[1]
            name = f"{subject} -- {len(self._games)} games"
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
            f"Hockey Analyzer report (*{BUNDLE_EXTENSION})",
        )
        return path

    def _show_preview(self, report: Report) -> None:
        ReportViewerDialog(report, parent=self).exec()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Export failed", message)
