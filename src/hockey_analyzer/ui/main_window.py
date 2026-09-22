"""The main application window: video playback (ticket 13) plus, once a
`db_session` is supplied, the Game menu that creates/selects a `Game` and
wires up the `TaggingSession`-backed tagging panel for it. Video playback
and game selection are deliberately independent actions (File menu vs.
Game menu) -- opening footage never requires a game, and picking a game
never requires footage to already be open (see CONTEXT.md's Game entry).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session

from hockey_analyzer.domain.game_setup import GameSetupService
from hockey_analyzer.domain.tagging_session import TaggingSession
from hockey_analyzer.ui.game_list_dialog import GameListDialog
from hockey_analyzer.ui.game_setup_dialog import GameSetupDialog
from hockey_analyzer.ui.keys import key_string, key_string_from_event
from hockey_analyzer.ui.playback_controller import DEFAULT_SPEED_STEPS, PlaybackController
from hockey_analyzer.ui.shortcuts import ShortcutRegistry
from hockey_analyzer.ui.tagging_panel import TaggingPanel
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
        db_session: Session | None = None,
        tagging_session: TaggingSession | None = None,
        game_setup_dialog_factory: Callable[[GameSetupService], GameSetupDialog] | None = None,
        game_list_dialog_factory: Callable[[GameSetupService], GameListDialog] | None = None,
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
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(self._on_position_changed)

        self._controller = (
            controller if controller is not None else PlaybackController(self._player)
        )
        self._file_dialog = (
            file_dialog if file_dialog is not None else self._show_open_file_dialog
        )

        self._shortcuts = shortcuts if shortcuts is not None else ShortcutRegistry()
        self._shortcuts.enter_scope(PLAYBACK_SCOPE)

        self._db_session = db_session
        self._game_setup_service = GameSetupService(db_session) if db_session is not None else None
        self._game_setup_dialog_factory = game_setup_dialog_factory or GameSetupDialog
        self._game_list_dialog_factory = game_list_dialog_factory or GameListDialog
        # The Game currently being tagged/played in this window, if any --
        # set by New Game/Select Game, and used to attach a video path to
        # the right Game when "Open Video…" is used afterward (see
        # `_open_video`). Independent of whether a full TaggingSession
        # could be built for it yet (home/away may still be unset).
        self._active_game_id: int | None = None

        self.play_pause_button = QPushButton("Play")
        self.play_pause_button.clicked.connect(self._toggle_play_pause)
        self.play_pause_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.speed_combo = QComboBox()
        for rate in DEFAULT_SPEED_STEPS:
            self.speed_combo.addItem(f"{rate:g}x", rate)
        self.speed_combo.setCurrentIndex(DEFAULT_SPEED_STEPS.index(1.0))
        self.speed_combo.currentIndexChanged.connect(self._on_speed_combo_changed)
        self.speed_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self._on_slider_moved)
        self.position_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        controls = QHBoxLayout()
        controls.addWidget(self.play_pause_button)
        controls.addWidget(self.speed_combo)

        playback_layout = QVBoxLayout()
        playback_layout.addWidget(self.video_view, stretch=1)
        playback_layout.addWidget(self.position_slider)
        playback_layout.addLayout(controls)

        self.tagging_panel: TaggingPanel | None = None
        self._root_layout = QHBoxLayout()
        self._root_layout.addLayout(playback_layout, stretch=2)
        if tagging_session is not None:
            self._install_tagging_panel(tagging_session)

        container = QWidget()
        container.setLayout(self._root_layout)
        self.setCentralWidget(container)

        self._build_menus()

        # Buttons/combo/slider opt out of keyboard focus above so a click
        # never leaves one of them holding focus and intercepting a
        # hotkey (e.g. QAbstractButton's own Space handling) before it
        # reaches the registry dispatch in keyPressEvent below.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

        self._register_shortcuts()

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_video_action = file_menu.addAction("Open Video…")
        self.open_video_action.triggered.connect(self._open_video)

        game_menu = self.menuBar().addMenu("&Game")
        self.new_game_action = game_menu.addAction("New Game…")
        self.new_game_action.triggered.connect(self._new_game)
        self.select_game_action = game_menu.addAction("Select Game…")
        self.select_game_action.triggered.connect(self._select_game)
        # No db_session means there's nowhere to persist a Game -- keep
        # this window in its ticket-13 video-only mode rather than
        # offering actions that would have nothing to write to.
        if self._game_setup_service is None:
            self.new_game_action.setEnabled(False)
            self.select_game_action.setEnabled(False)

    def _install_tagging_panel(self, tagging_session: TaggingSession) -> None:
        if self.tagging_panel is not None:
            self._root_layout.removeWidget(self.tagging_panel)
            self.tagging_panel.deleteLater()
        self.tagging_panel = TaggingPanel(
            tagging_session,
            current_position_ms=self._player.position,
            shortcuts=self._shortcuts,
            pause=self._controller.pause,
        )
        self._root_layout.addWidget(self.tagging_panel, stretch=1)

    def _activate_game(self, game_id: int | None, home_team_id: int | None, away_team_id: int | None) -> None:
        self._active_game_id = game_id
        if game_id is None or home_team_id is None or away_team_id is None:
            # Tagging needs both sides' teams (see TaggingSession); a game
            # can still be "active" for video-attachment purposes before
            # roster setup is finished -- see `_open_video`.
            return
        tagging_session = TaggingSession(
            self._db_session, game_id=game_id, home_team_id=home_team_id, away_team_id=away_team_id
        )
        self._install_tagging_panel(tagging_session)

    def _new_game(self) -> None:
        if self._game_setup_service is None:
            return
        dialog = self._game_setup_dialog_factory(self._game_setup_service)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._activate_game(dialog.game_id, dialog.home_team_id, dialog.away_team_id)

    def _select_game(self) -> None:
        if self._game_setup_service is None:
            return
        dialog = self._game_list_dialog_factory(self._game_setup_service)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_game_id is None:
            return
        game = self._game_setup_service.get_game(dialog.selected_game_id)
        self._activate_game(game.id, game.home_team_id, game.away_team_id)
        if game.video_path:
            self.open_video(Path(game.video_path))

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
            if self._active_game_id is not None and self._game_setup_service is not None:
                self._game_setup_service.set_video_path(self._active_game_id, path)

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

    def _on_duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, duration)

    def _on_position_changed(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position)
            self.position_slider.blockSignals(False)

    def _on_slider_moved(self, position: int) -> None:
        self._controller.seek(position)
