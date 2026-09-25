"""Clip export (ticket 24): a GUI over `clip_export`'s filter -> hand-pick
-> plan -> run flow for the one game it was opened on (see CONTEXT.md's
Clip, Padding window and Highlight reel entries). Holds a `ClipSelection`
and re-plans on every change, so what the export button would write is
always exactly `plan_export`'s answer -- the dialog decides nothing about
segments or filenames itself. The encoder is injected; the app passes
`FfmpegClipEncoder`.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.clip_export import (
    DEFAULT_PADDING,
    ClipEncoder,
    ClipEncodingError,
    ClipFilter,
    ClipSegment,
    ClipSelection,
    ExportPlan,
    OutputShape,
    Padding,
    involved_players,
    plan_export,
    run_export,
    select_clips,
)
from hockey_analyzer.domain.enums import EventType, ShotOutcome
from hockey_analyzer.domain.game_clock import GameClock, game_clocks
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import Event, ShotAttempt
from hockey_analyzer.domain.tagging_session import roster_entry_label
from hockey_analyzer.domain.video_timestamp import format_video_timestamp

_E = TypeVar("_E", bound=enum.Enum)


def _event_type_label(event_type: EventType) -> str:
    return event_type.value.replace("_", " ").capitalize()


def _enum_or_none(enum_type: type[_E], combo: QComboBox) -> _E | None:
    """Qt hands a `StrEnum` item's data back as a plain `str`."""
    data = combo.currentData()
    return None if data is None else enum_type(data)


def _padding_spin(ms: int) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 60.0)
    spin.setDecimals(1)
    spin.setSingleStep(0.5)
    spin.setSuffix(" s")
    spin.setValue(ms / 1000)
    return spin


def _clock_label(event: Event, clock: GameClock | None) -> str:
    if clock is None:
        return f"Video {format_video_timestamp(event.video_timestamp)}"
    minutes, seconds = divmod(clock.remaining_ms // 1000, 60)
    return f"P{clock.period} {minutes}:{seconds:02d}"


class ClipExportDialog(QDialog):
    def __init__(
        self,
        data: GameData,
        *,
        footage_duration_ms: int,
        encoder: ClipEncoder,
        notice: Callable[[str, str], None] | None = None,
        choose_directory: Callable[[str], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._notice = notice if notice is not None else self._show_notice
        self._choose_directory = (
            choose_directory
            if choose_directory is not None
            else self._show_directory_dialog
        )
        self.exported_paths: list[Path] = []
        self.setWindowTitle("Export Clips")
        self._data = data
        self._footage_duration_ms = footage_duration_ms
        self._encoder = encoder
        self._selection: ClipSelection = select_clips(data, ClipFilter())
        self._clocks = game_clocks(data.events)
        self._player_labels = {
            entry.player_id: roster_entry_label(entry) for entry in data.roster
        }

        self.player_combo = QComboBox()
        self.player_combo.addItem("Any player", None)
        for entry in data.roster:
            self.player_combo.addItem(
                f"{self._player_labels[entry.player_id]} ({self._team_name(entry.team_id)})",
                entry.player_id,
            )
        self.event_type_combo = QComboBox()
        self.event_type_combo.addItem("Any event", None)
        for event_type in EventType:
            self.event_type_combo.addItem(_event_type_label(event_type), event_type)
        # An outcome only ever matches shot attempts (see `ClipFilter`).
        self.outcome_combo = QComboBox()
        self.outcome_combo.addItem("Any outcome", None)
        for outcome in ShotOutcome:
            self.outcome_combo.addItem(outcome.value.capitalize(), outcome)
        for combo in (self.player_combo, self.event_type_combo, self.outcome_combo):
            combo.currentIndexChanged.connect(self._refilter)

        filters = QFormLayout()
        filters.addRow("Player", self.player_combo)
        filters.addRow("Event", self.event_type_combo)
        filters.addRow("Shot outcome", self.outcome_combo)

        self.candidate_list = QListWidget()
        self.candidate_list.itemChanged.connect(self._on_candidate_toggled)
        self.summary_label = QLabel()

        self.per_clip_radio = QRadioButton("One file per clip")
        self.reel_radio = QRadioButton("Single highlight reel")
        self.per_clip_radio.setChecked(True)
        # Grouped so exactly one shape is ever checked.
        self._shape_group = QButtonGroup(self)
        self._shape_group.addButton(self.per_clip_radio)
        self._shape_group.addButton(self.reel_radio)
        shape_row = QHBoxLayout()
        shape_row.addWidget(self.per_clip_radio)
        shape_row.addWidget(self.reel_radio)
        shape_row.addStretch(1)

        self.padding_before_spin = _padding_spin(DEFAULT_PADDING.before_ms)
        self.padding_after_spin = _padding_spin(DEFAULT_PADDING.after_ms)
        padding_row = QHBoxLayout()
        padding_row.addWidget(QLabel("before"))
        padding_row.addWidget(self.padding_before_spin)
        padding_row.addWidget(QLabel("after"))
        padding_row.addWidget(self.padding_after_spin)
        padding_row.addStretch(1)

        video_path = data.game.video_path
        self.output_dir_edit = QLineEdit(
            str(Path(video_path).parent / "clips") if video_path else ""
        )
        self.output_dir_edit.textChanged.connect(self._refresh)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.output_dir_edit, stretch=1)
        folder_row.addWidget(self.browse_button)

        output = QFormLayout()
        output.addRow("Output", shape_row)
        output.addRow("Padding", padding_row)
        output.addRow("Folder", folder_row)

        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(self._export)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.export_button)
        buttons_row.addWidget(self.cancel_button)

        layout = QVBoxLayout()
        layout.addLayout(filters)
        layout.addWidget(self.candidate_list)
        layout.addWidget(self.summary_label)
        layout.addLayout(output)
        layout.addLayout(buttons_row)
        self.setLayout(layout)

        self._show_candidates()

    def _team_name(self, team_id: int) -> str:
        game = self._data.game
        team = game.home_team if team_id == game.home_team_id else game.away_team
        return team.name if team is not None else "?"

    def _refilter(self) -> None:
        """A new filter starts a fresh selection -- every new match
        checked -- as `select_clips` does."""
        self._selection = select_clips(
            self._data,
            ClipFilter(
                player_id=self.player_combo.currentData(),
                event_type=_enum_or_none(EventType, self.event_type_combo),
                shot_outcome=_enum_or_none(ShotOutcome, self.outcome_combo),
            ),
        )
        self._show_candidates()

    def _on_candidate_toggled(self, item: QListWidgetItem) -> None:
        event_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selection = self._selection.select(event_id)
        else:
            self._selection = self._selection.deselect(event_id)
        self._refresh()

    def _refresh(self) -> None:
        candidates = len(self._selection.candidates)
        selected = len(self._selection.selected)
        self.summary_label.setText(
            f"{selected} of {candidates} events selected."
            if candidates
            else "No events match this filter."
        )
        self.export_button.setEnabled(selected > 0 and bool(self._output_dir()))

    def _output_dir(self) -> str:
        return self.output_dir_edit.text().strip()

    def _plan(self) -> ExportPlan:
        return plan_export(
            self._data,
            self._selection,
            shape=(
                OutputShape.REEL
                if self.reel_radio.isChecked()
                else OutputShape.PER_CLIP
            ),
            footage_duration_ms=self._footage_duration_ms,
            output_dir=Path(self._output_dir()),
            padding=Padding(
                before_ms=round(self.padding_before_spin.value() * 1000),
                after_ms=round(self.padding_after_spin.value() * 1000),
            ),
        )

    def _export(self) -> None:
        plan = self._plan()
        progress = QProgressDialog("Exporting clips…", "", 0, len(plan.outputs), self)
        progress.setCancelButton(None)  # ffmpeg can't be stopped mid-file
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            paths = run_export(plan, _ProgressEncoder(self._encoder, progress))
        except (ClipEncodingError, OSError) as error:
            self._notice("Export failed", str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
            progress.close()
        self.exported_paths = paths
        noun = "file" if len(paths) == 1 else "files"
        self._notice(
            "Clips exported",
            f"Exported {len(paths)} {noun} to\n{self._output_dir()}",
        )
        self.accept()

    def _browse(self) -> None:
        chosen = self._choose_directory(self.output_dir_edit.text())
        if chosen:
            self.output_dir_edit.setText(chosen)

    def _show_notice(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def _show_directory_dialog(self, start: str) -> str:
        return QFileDialog.getExistingDirectory(self, "Export clips to", start)

    def _show_candidates(self) -> None:
        # Rebuilding the list isn't the user toggling anything.
        self.candidate_list.blockSignals(True)
        self.candidate_list.clear()
        for event in self._selection.candidates:
            item = QListWidgetItem(self._candidate_label(event))
            item.setData(Qt.ItemDataRole.UserRole, event.id)
            item.setCheckState(
                Qt.CheckState.Checked
                if self._selection.is_selected(event.id)
                else Qt.CheckState.Unchecked
            )
            self.candidate_list.addItem(item)
        self.candidate_list.blockSignals(False)
        self._refresh()

    def _candidate_label(self, event: Event) -> str:
        event_label = _event_type_label(event.event_type)
        if isinstance(event, ShotAttempt):
            event_label += f" ({event.shot_outcome.value})"
        players = sorted(
            self._player_labels[player_id]
            for player_id in involved_players(event)
            if player_id in self._player_labels
        )
        parts = [_clock_label(event, self._clocks.get(event.id)), event_label, *players]
        return " · ".join(parts)


class _ProgressEncoder:
    """Steps a progress dialog as each planned output starts encoding --
    each `encode` call blocks until ffmpeg finishes that file."""

    def __init__(self, encoder: ClipEncoder, progress: QProgressDialog) -> None:
        self._encoder = encoder
        self._progress = progress
        self._done = 0

    def encode(
        self, source_path: str, segments: Sequence[ClipSegment], output_path: Path
    ) -> None:
        self._progress.setLabelText(f"Writing {output_path.name}…")
        self._progress.setValue(self._done)
        QApplication.processEvents()
        self._encoder.encode(source_path, segments, output_path)
        self._done += 1
