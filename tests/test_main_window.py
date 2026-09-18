from __future__ import annotations

from unittest.mock import Mock

from PySide6.QtCore import QUrl, Qt
from PySide6.QtMultimedia import QMediaPlayer

from hockey_analyzer.ui.main_window import MainWindow
from hockey_analyzer.ui.playback_controller import PlaybackController
from hockey_analyzer.ui.shortcuts import ShortcutRegistry


class _FakeSignal:
    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self, *args) -> None:
        for slot in self._slots:
            slot(*args)


class FakePlayer:
    def __init__(self) -> None:
        self.playbackStateChanged = _FakeSignal()
        self.metaDataChanged = _FakeSignal()
        self.durationChanged = _FakeSignal()
        self.positionChanged = _FakeSignal()
        self.source: QUrl | None = None
        self.play_called = False

    def setAudioOutput(self, output) -> None:
        pass

    def setVideoSink(self, sink) -> None:
        pass

    def setSource(self, url: QUrl) -> None:
        self.source = url

    def play(self) -> None:
        self.play_called = True

    def pause(self) -> None:
        pass

    def position(self) -> int:
        return 0

    def duration(self) -> int:
        return 0

    def playbackState(self) -> QMediaPlayer.PlaybackState:
        return QMediaPlayer.PlaybackState.StoppedState

    def playbackRate(self) -> float:
        return 1.0

    def setPlaybackRate(self, rate: float) -> None:
        pass

    def metaData(self):
        return {}


def _make_window(qtbot, *, controller=None, player=None, file_dialog=None):
    window = MainWindow(
        controller=controller,
        player=player if player is not None else FakePlayer(),
        shortcuts=ShortcutRegistry(),
        file_dialog=file_dialog,
    )
    qtbot.addWidget(window)
    return window


def test_space_dispatches_toggle_play_pause(qtbot):
    controller = Mock(spec=PlaybackController)
    window = _make_window(qtbot, controller=controller)

    qtbot.keyClick(window, Qt.Key.Key_Space)

    controller.toggle_play_pause.assert_called_once()


def test_right_arrow_jumps_forward_five_seconds(qtbot):
    controller = Mock(spec=PlaybackController)
    window = _make_window(qtbot, controller=controller)

    qtbot.keyClick(window, Qt.Key.Key_Right)

    controller.jump.assert_called_once_with(5)


def test_left_arrow_jumps_backward_five_seconds(qtbot):
    controller = Mock(spec=PlaybackController)
    window = _make_window(qtbot, controller=controller)

    qtbot.keyClick(window, Qt.Key.Key_Left)

    controller.jump.assert_called_once_with(-5)


def test_period_steps_one_frame_forward(qtbot):
    controller = Mock(spec=PlaybackController)
    window = _make_window(qtbot, controller=controller)

    qtbot.keyClick(window, Qt.Key.Key_Period)

    controller.step_frame.assert_called_once_with(1)


def test_comma_steps_one_frame_backward(qtbot):
    controller = Mock(spec=PlaybackController)
    window = _make_window(qtbot, controller=controller)

    qtbot.keyClick(window, Qt.Key.Key_Comma)

    controller.step_frame.assert_called_once_with(-1)


def test_bracket_right_speeds_up(qtbot):
    controller = Mock(spec=PlaybackController)
    controller.speed_index = 3
    window = _make_window(qtbot, controller=controller)

    qtbot.keyClick(window, Qt.Key.Key_BracketRight)

    controller.speed_up.assert_called_once()


def test_bracket_left_slows_down(qtbot):
    controller = Mock(spec=PlaybackController)
    controller.speed_index = 3
    window = _make_window(qtbot, controller=controller)

    qtbot.keyClick(window, Qt.Key.Key_BracketLeft)

    controller.speed_down.assert_called_once()


def test_play_pause_button_click_toggles(qtbot):
    controller = Mock(spec=PlaybackController)
    window = _make_window(qtbot, controller=controller)

    qtbot.mouseClick(window.play_pause_button, Qt.MouseButton.LeftButton)

    controller.toggle_play_pause.assert_called_once()


def test_ctrl_o_opens_a_file_via_the_injected_dialog(qtbot):
    player = FakePlayer()
    window = _make_window(qtbot, player=player, file_dialog=lambda: "C:/clips/game.mp4")

    qtbot.keyClick(window, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)

    assert player.source == QUrl.fromLocalFile("C:/clips/game.mp4")
    assert player.play_called is True


def test_a_cancelled_file_dialog_does_not_open_anything(qtbot):
    player = FakePlayer()
    window = _make_window(qtbot, player=player, file_dialog=lambda: "")

    qtbot.keyClick(window, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)

    assert player.source is None
    assert player.play_called is False


def test_open_button_click_opens_a_file(qtbot):
    player = FakePlayer()
    window = _make_window(qtbot, player=player, file_dialog=lambda: "C:/clips/game.mp4")

    qtbot.mouseClick(window.open_button, Qt.MouseButton.LeftButton)

    assert player.source == QUrl.fromLocalFile("C:/clips/game.mp4")


def test_playback_state_change_updates_button_label(qtbot):
    player = FakePlayer()
    window = _make_window(qtbot, player=player)

    player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    assert window.play_pause_button.text() == "Pause"

    player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PausedState)
    assert window.play_pause_button.text() == "Play"


def test_speed_combo_selection_sets_speed_index(qtbot):
    controller = Mock(spec=PlaybackController)
    controller.speed_index = 3
    window = _make_window(qtbot, controller=controller)

    window.speed_combo.setCurrentIndex(0)

    controller.set_speed_index.assert_called_with(0)


def test_buttons_and_combo_and_slider_opt_out_of_keyboard_focus(qtbot):
    # Regression: QAbstractButton/QComboBox/QSlider all intercept keys like
    # Space/arrows themselves when focused, before MainWindow.keyPressEvent
    # ever runs. If any of these can hold keyboard focus, a click on it
    # (e.g. Open) silently breaks the Space hotkey instead of routing
    # through the shortcut registry.
    window = _make_window(qtbot, controller=Mock(spec=PlaybackController))

    assert window.open_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert window.play_pause_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert window.speed_combo.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert window.position_slider.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_window_holds_keyboard_focus_after_construction(qtbot):
    window = _make_window(qtbot, controller=Mock(spec=PlaybackController))
    window.show()
    qtbot.waitExposed(window)

    assert window.focusWidget() is window


def test_space_still_toggles_play_pause_after_clicking_open_button(qtbot):
    controller = Mock(spec=PlaybackController)
    window = _make_window(qtbot, controller=controller, file_dialog=lambda: "")
    window.show()
    qtbot.waitExposed(window)

    qtbot.mouseClick(window.open_button, Qt.MouseButton.LeftButton)
    qtbot.keyClick(window, Qt.Key.Key_Space)

    controller.toggle_play_pause.assert_called_once()


def test_duration_change_sets_slider_range(qtbot):
    player = FakePlayer()
    window = _make_window(qtbot, player=player)

    player.durationChanged.emit(120_000)

    assert window.position_slider.maximum() == 120_000


def test_position_change_updates_slider_value(qtbot):
    player = FakePlayer()
    window = _make_window(qtbot, player=player)
    player.durationChanged.emit(120_000)

    player.positionChanged.emit(30_000)

    assert window.position_slider.value() == 30_000


def test_dragging_slider_seeks_the_controller(qtbot):
    controller = Mock(spec=PlaybackController)
    window = _make_window(qtbot, controller=controller)

    window.position_slider.sliderMoved.emit(45_000)

    controller.seek.assert_called_once_with(45_000)


def test_constructs_with_a_real_media_player(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.play_pause_button.text() == "Play"


def test_shortcuts_register_in_the_playback_scope_not_ad_hoc():
    registry = ShortcutRegistry()
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=registry,
    )

    # The registry itself dispatches "Space" — proof the binding went
    # through ShortcutRegistry rather than a QShortcut/keyPressEvent path
    # the registry doesn't know about.
    handled = registry.dispatch("Space")

    assert handled is True
    window.deleteLater()
