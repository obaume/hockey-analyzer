"""The video playback shell (ticket 13): open a local file and drive
playback — no tagging or domain-model wiring here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.ui.keys import key_string, key_string_from_event
from hockey_analyzer.ui.playback_controller import DEFAULT_SPEED_STEPS, PlaybackController
from hockey_analyzer.ui.shortcuts import ShortcutRegistry
from hockey_analyzer.ui.video_frame_view import VideoFrameView

PLAYBACK_SCOPE = "playback"
JUMP_SECONDS = 5
VIDEO_FILE_FILTER = "Video files (*.mp4 *.mkv *.mov *.avi);;All files (*)"


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        player: QMediaPlayer | None = None,
        controller: PlaybackController | None = None,
        shortcuts: ShortcutRegistry | None = None,
        file_dialog: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hockey Analyzer")

        self._player = player if player is not None else QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)

        self.video_view = VideoFrameView(self)
        self._player.setVideoSink(self.video_view.video_sink)
        self._player.metaDataChanged.connect(self._on_meta_data_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)

        self._controller = (
            controller if controller is not None else PlaybackController(self._player)
        )
        self._file_dialog = (
            file_dialog if file_dialog is not None else self._show_open_file_dialog
        )

        self._shortcuts = shortcuts if shortcuts is not None else ShortcutRegistry()
        self._shortcuts.enter_scope(PLAYBACK_SCOPE)

        self.open_button = QPushButton("Open…")
        self.open_button.clicked.connect(self._open_video)

        self.play_pause_button = QPushButton("Play")
        self.play_pause_button.clicked.connect(self._toggle_play_pause)

        self.speed_combo = QComboBox()
        for rate in DEFAULT_SPEED_STEPS:
            self.speed_combo.addItem(f"{rate:g}x", rate)
        self.speed_combo.setCurrentIndex(DEFAULT_SPEED_STEPS.index(1.0))
        self.speed_combo.currentIndexChanged.connect(self._on_speed_combo_changed)

        controls = QHBoxLayout()
        controls.addWidget(self.open_button)
        controls.addWidget(self.play_pause_button)
        controls.addWidget(self.speed_combo)

        layout = QVBoxLayout()
        layout.addWidget(self.video_view, stretch=1)
        layout.addLayout(controls)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self._register_shortcuts()

    def _register_shortcuts(self) -> None:
        bindings: dict[str, Callable[[], None]] = {
            key_string(Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier): self._open_video,
            key_string(Qt.Key.Key_Space): self._toggle_play_pause,
            key_string(Qt.Key.Key_Right): lambda: self._controller.jump(JUMP_SECONDS),
            key_string(Qt.Key.Key_Left): lambda: self._controller.jump(-JUMP_SECONDS),
            key_string(Qt.Key.Key_Period): lambda: self._controller.step_frame(1),
            key_string(Qt.Key.Key_Comma): lambda: self._controller.step_frame(-1),
            key_string(Qt.Key.Key_BracketRight): self._speed_up,
            key_string(Qt.Key.Key_BracketLeft): self._speed_down,
        }
        for key, action in bindings.items():
            self._shortcuts.register(key, PLAYBACK_SCOPE, action)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._shortcuts.dispatch(key_string_from_event(event)):
            event.accept()
            return
        super().keyPressEvent(event)

    def open_video(self, path: Path) -> None:
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def _open_video(self) -> None:
        path = self._file_dialog()
        if path:
            self.open_video(Path(path))

    def _show_open_file_dialog(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self, "Open video", "", VIDEO_FILE_FILTER)
        return path

    def _toggle_play_pause(self) -> None:
        self._controller.toggle_play_pause()

    def _speed_up(self) -> None:
        self._controller.speed_up()
        self._sync_speed_combo()

    def _speed_down(self) -> None:
        self._controller.speed_down()
        self._sync_speed_combo()

    def _on_speed_combo_changed(self, index: int) -> None:
        self._controller.set_speed_index(index)

    def _sync_speed_combo(self) -> None:
        self.speed_combo.blockSignals(True)
        self.speed_combo.setCurrentIndex(self._controller.speed_index)
        self.speed_combo.blockSignals(False)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_pause_button.setText("Pause" if playing else "Play")

    def _on_meta_data_changed(self) -> None:
        frame_rate = self._player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
        if frame_rate:
            self._controller.set_frame_rate(float(frame_rate))
