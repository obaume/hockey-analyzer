"""The main application window: video playback (ticket 13) plus, once a
`db_session` is supplied, the Game menu that creates/selects a `Game` and
wires up the `TaggingSession`-backed tagging panel for it. Video playback
and game selection are deliberately independent actions (File menu vs.
Game menu) -- opening footage never requires a game, and picking a game
never requires footage to already be open (see CONTEXT.md's Game entry).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QKeyEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session

from hockey_analyzer.domain.game_data import GameData, load_game_data
from hockey_analyzer.domain.game_setup import GameSetupService
from hockey_analyzer.domain.tagging_session import TaggingSession
from hockey_analyzer.domain.video_timestamp import (
    format_video_timestamp,
    parse_video_timestamp,
)
from hockey_analyzer.league_import import (
    LeagueImportService,
    LeagueSource,
    SihfHttpSource,
)
from hockey_analyzer.ui.game_list_dialog import GameListDialog
from hockey_analyzer.ui.game_setup_dialog import GameSetupDialog
from hockey_analyzer.ui.keys import key_string, key_string_from_event
from hockey_analyzer.ui.league_import_dialog import LeagueImportDialog
from hockey_analyzer.ui.playback_controller import (
    DEFAULT_SPEED_STEPS,
    PlaybackController,
)
from hockey_analyzer.ui.shortcuts import ShortcutRegistry
from hockey_analyzer.ui.stats_dialog import StatsDialog
from hockey_analyzer.ui.tagging_panel import TaggingPanel
from hockey_analyzer.ui.units_dialog import UnitsDialog
from hockey_analyzer.ui.video_frame_view import VideoFrameView

PLAYBACK_SCOPE = "playback"


class UnitsDialogFactory(Protocol):
    """`UnitsDialog`'s constructor shape -- spelled out (rather than
    `Callable[..., UnitsDialog]`) so a test fake is checked against the
    same keyword arguments `_edit_units` passes."""

    def __call__(
        self,
        service: GameSetupService,
        *,
        game_id: int,
        home_team_id: int,
        away_team_id: int,
        parent: QWidget | None = None,
    ) -> UnitsDialog: ...


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
        game_setup_dialog_factory: Callable[[GameSetupService], GameSetupDialog]
        | None = None,
        game_list_dialog_factory: Callable[[GameSetupService], GameListDialog]
        | None = None,
        league_import_dialog_factory: Callable[
            [LeagueImportService], LeagueImportDialog
        ]
        | None = None,
        league_source: LeagueSource | None = None,
        units_dialog_factory: UnitsDialogFactory | None = None,
        stats_dialog_factory: Callable[[GameData, QWidget], StatsDialog] | None = None,
        video_missing_notice: Callable[[str], None] | None = None,
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
        self._game_setup_service = (
            GameSetupService(db_session) if db_session is not None else None
        )
        self._game_setup_dialog_factory = game_setup_dialog_factory or GameSetupDialog
        self._game_list_dialog_factory = game_list_dialog_factory or GameListDialog
        self._league_import_dialog_factory = (
            league_import_dialog_factory or LeagueImportDialog
        )
        self._league_source = league_source or SihfHttpSource()
        self._units_dialog_factory = units_dialog_factory or UnitsDialog
        self._stats_dialog_factory = stats_dialog_factory or StatsDialog
        self._video_missing_notice = (
            video_missing_notice
            if video_missing_notice is not None
            else self._show_video_missing_notice
        )
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

        self.time = QLineEdit()
        self.time.setFixedWidth(80)
        self.time.editingFinished.connect(self._on_time_changed)
        self.duration = QLabel()
        self.duration.setFixedWidth(80)

        controls = QHBoxLayout()
        controls.addStretch()
        controls.addWidget(self.time)
        controls.addWidget(self.duration)
        controls.addWidget(self.play_pause_button)
        controls.addWidget(self.speed_combo)
        controls.addStretch()

        playback_layout = QVBoxLayout()
        playback_layout.addWidget(self.video_view, stretch=1)
        playback_layout.addWidget(self.position_slider)
        playback_layout.addLayout(controls)

        self.tagging_panel: TaggingPanel | None = None
        self._root_layout = QVBoxLayout()
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
        self.import_game_action = game_menu.addAction("Import from League Link…")
        self.import_game_action.triggered.connect(self._import_game)
        self.select_game_action = game_menu.addAction("Select Game…")
        self.select_game_action.triggered.connect(self._select_game)
        # Units (ticket 17) belong to one game and one team per side, so
        # this only enables once the active game has both -- see
        # `_activate_game`.
        self.units_action = game_menu.addAction("Units…")
        self.units_action.triggered.connect(self._edit_units)
        self.units_action.setEnabled(False)
        # Stats (ticket 18) are for/against one side vs. the other, so
        # they need both sides too -- same enabling rule as Units.
        self.stats_action = game_menu.addAction("Stats…")
        self.stats_action.triggered.connect(self._show_stats)
        self.stats_action.setEnabled(False)
        # No db_session means there's nowhere to persist a Game -- keep
        # this window in its ticket-13 video-only mode rather than
        # offering actions that would have nothing to write to.
        if self._game_setup_service is None:
            self.new_game_action.setEnabled(False)
            self.import_game_action.setEnabled(False)
            self.select_game_action.setEnabled(False)

    def _install_tagging_panel(self, tagging_session: TaggingSession) -> None:
        if self.tagging_panel is not None:
            self.tagging_panel.release_shortcuts()
            self._root_layout.removeWidget(self.tagging_panel)
            self.tagging_panel.deleteLater()
        self.tagging_panel = TaggingPanel(
            tagging_session,
            current_position_ms=self._player.position,
            shortcuts=self._shortcuts,
            pause=self._controller.pause,
        )
        self._root_layout.addWidget(self.tagging_panel, stretch=1)

    def _activate_game(self, game_id: int | None) -> None:
        """Single path for both New Game and Select Game -- always
        re-fetches home/away/video_path from the Game itself rather than
        trusting values threaded in by the caller, so there's one source
        of truth instead of each call site re-deriving and re-passing the
        same three fields."""
        self._active_game_id = game_id
        if game_id is None:
            self.units_action.setEnabled(False)
            self.stats_action.setEnabled(False)
            return
        game = self._game_setup_service.get_game(game_id)
        has_both_sides = game.home_team_id is not None and game.away_team_id is not None
        self.units_action.setEnabled(has_both_sides)
        self.stats_action.setEnabled(has_both_sides)
        if has_both_sides:
            tagging_session = TaggingSession(
                self._db_session,
                game_id=game.id,
                home_team_id=game.home_team_id,
                away_team_id=game.away_team_id,
            )
            self._install_tagging_panel(tagging_session)
        # Tagging needs both sides' teams (see TaggingSession); a game can
        # still be "active" for video-attachment purposes before roster
        # setup is finished -- see `_open_video`.
        if game.video_path:
            self._open_stored_video(game.video_path)

    def _new_game(self) -> None:
        if self._game_setup_service is None:
            return
        dialog = self._game_setup_dialog_factory(self._game_setup_service)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._activate_game(dialog.game_id)

    def _import_game(self) -> None:
        """Ticket 22: the league-link path to a new Game. Lands on the same
        activation as New Game -- the imported game, or the existing one
        a duplicate import pointed the user at -- and falls back to New
        Game's blank manual setup when the league data couldn't be read,
        rather than blocking game creation."""
        if self._db_session is None:
            return
        dialog = self._league_import_dialog_factory(
            LeagueImportService(self._db_session, self._league_source)
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.fall_back_to_manual:
            self._new_game()
            return
        self._activate_game(dialog.game_id)

    def _edit_units(self) -> None:
        if self._game_setup_service is None or self._active_game_id is None:
            return
        game = self._game_setup_service.get_game(self._active_game_id)
        if game.home_team_id is None or game.away_team_id is None:
            return
        dialog = self._units_dialog_factory(
            self._game_setup_service,
            game_id=game.id,
            home_team_id=game.home_team_id,
            away_team_id=game.away_team_id,
            parent=self,
        )
        dialog.exec()

    def _show_stats(self) -> None:
        if self._db_session is None or self._active_game_id is None:
            return
        data = load_game_data(self._db_session, self._active_game_id)
        self._stats_dialog_factory(data, self).exec()

    def _select_game(self) -> None:
        if self._game_setup_service is None:
            return
        dialog = self._game_list_dialog_factory(self._game_setup_service)
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or dialog.selected_game_id is None
        ):
            return
        self._activate_game(dialog.selected_game_id)

    def _open_stored_video(self, path: str) -> None:
        if not Path(path).exists():
            # No silent failure and no crash on a moved/deleted file --
            # tell the user, and leave `_active_game_id` set so "Open
            # Video..." relinks (and persists) a replacement for this
            # same game rather than a second, duplicate relink flow here.
            self._video_missing_notice(path)
            return
        self.open_video(Path(path))

    def _register_shortcuts(self) -> None:
        bindings: dict[str, Callable[[], None]] = {
            key_string(
                Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier
            ): self._open_video,
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
            if (
                self._active_game_id is not None
                and self._game_setup_service is not None
            ):
                self._game_setup_service.set_video_path(self._active_game_id, path)

    def _show_open_file_dialog(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self, "Open video", "", VIDEO_FILE_FILTER)
        return path

    def _show_video_missing_notice(self, path: str) -> None:
        QMessageBox.warning(
            self,
            "Video not found",
            f"This game's video file could not be found:\n{path}\n\nUse File → Open Video… to relink it.",
        )

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
        self.duration.setText(format_video_timestamp(duration))

    def _on_position_changed(self, position: int) -> None:
        self.time.setText(format_video_timestamp(position))
        if not self.position_slider.isSliderDown():
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position)
            self.position_slider.blockSignals(False)

    def _on_slider_moved(self, position: int) -> None:
        self._controller.seek(position)

    def _on_time_changed(self) -> None:
        ms = parse_video_timestamp(self.time.text())
        if ms:
            self._controller.seek(ms)
