from __future__ import annotations

from unittest.mock import Mock

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QDialog

from hockey_analyzer.domain.enums import EventType, Side
from hockey_analyzer.domain.models import Event
from hockey_analyzer.league_import import LeagueImportService
from hockey_analyzer.ui.main_window import MainWindow
from hockey_analyzer.ui.playback_controller import PlaybackController
from hockey_analyzer.ui.shortcuts import ShortcutRegistry


class _FakeGameSetupDialog:
    """Stands in for GameSetupDialog.exec()'s modal event loop, which a
    headless test can't drive by clicking through -- see MainWindow's
    game_setup_dialog_factory injection seam. Only `game_id` is needed:
    MainWindow re-fetches home/away/video_path itself (see
    MainWindow._activate_game), matching what the real dialog's own
    home/away already persisted via GameSetupService as the user picked
    each side's team."""

    def __init__(self, *, game_id=None, accepted=True) -> None:
        self.game_id = game_id
        self._accepted = accepted

    def exec(self) -> QDialog.DialogCode:
        return (
            QDialog.DialogCode.Accepted
            if self._accepted
            else QDialog.DialogCode.Rejected
        )


class _FakeGameListDialog:
    """Stands in for GameListDialog.exec() -- see _FakeGameSetupDialog."""

    def __init__(self, *, selected_game_id=None, accepted=True) -> None:
        self.selected_game_id = selected_game_id
        self._accepted = accepted

    def exec(self) -> QDialog.DialogCode:
        return (
            QDialog.DialogCode.Accepted
            if self._accepted
            else QDialog.DialogCode.Rejected
        )


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


def test_open_video_menu_action_opens_a_file(qtbot):
    player = FakePlayer()
    window = _make_window(qtbot, player=player, file_dialog=lambda: "C:/clips/game.mp4")

    window.open_video_action.trigger()

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
    # silently breaks the Space hotkey instead of routing through the
    # shortcut registry.
    window = _make_window(qtbot, controller=Mock(spec=PlaybackController))

    assert window.play_pause_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert window.speed_combo.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert window.position_slider.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_window_holds_keyboard_focus_after_construction(qtbot):
    window = _make_window(qtbot, controller=Mock(spec=PlaybackController))
    window.show()
    qtbot.waitExposed(window)

    assert window.focusWidget() is window


def test_space_still_toggles_play_pause_after_opening_video(qtbot):
    controller = Mock(spec=PlaybackController)
    window = _make_window(qtbot, controller=controller, file_dialog=lambda: "")
    window.show()
    qtbot.waitExposed(window)

    window.open_video_action.trigger()
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


def test_no_tagging_session_means_no_tagging_panel(qtbot):
    window = _make_window(qtbot, controller=Mock(spec=PlaybackController))
    assert window.tagging_panel is None


def test_tagging_session_wires_a_tagging_panel_bound_to_player_position(
    qtbot, tagging_session
):
    player = FakePlayer()
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=player,
        shortcuts=ShortcutRegistry(),
        tagging_session=tagging_session,
    )
    qtbot.addWidget(window)

    assert window.tagging_panel is not None
    qtbot.mouseClick(
        window.tagging_panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton
    )

    assert len(tagging_session.list_events()) == 1


def test_tagging_panel_shares_the_windows_shortcut_registry(qtbot, tagging_session):
    registry = ShortcutRegistry()
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=registry,
        tagging_session=tagging_session,
    )
    qtbot.addWidget(window)

    # Space (playback scope) and 1 (tagging scope) must both dispatch
    # through the same registry MainWindow.keyPressEvent uses.
    qtbot.keyClick(window, Qt.Key.Key_1)

    assert len(tagging_session.list_events()) == 1


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


# -- Game menu: disabled with no db_session, no way to persist a Game ------


def test_game_menu_actions_disabled_without_db_session(qtbot):
    window = _make_window(qtbot)
    assert window.new_game_action.isEnabled() is False
    assert window.select_game_action.isEnabled() is False


def test_game_menu_actions_enabled_with_db_session(qtbot, session):
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
    )
    qtbot.addWidget(window)
    assert window.new_game_action.isEnabled() is True
    assert window.select_game_action.isEnabled() is True


# -- New Game: wires a tagging panel once both sides' teams are known ------


def test_new_game_action_installs_a_tagging_panel_once_home_and_away_are_set(
    qtbot, session, game_setup_service
):
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.set_side_team(game.id, Side.HOME, home.id)
    game_setup_service.set_side_team(game.id, Side.AWAY, away.id)
    fake_dialog = _FakeGameSetupDialog(game_id=game.id)
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_setup_dialog_factory=lambda service: fake_dialog,
    )
    qtbot.addWidget(window)
    assert window.tagging_panel is None

    window.new_game_action.trigger()

    assert window.tagging_panel is not None


def test_new_game_action_with_incomplete_rosters_leaves_no_tagging_panel(
    qtbot, session, game_setup_service
):
    game = game_setup_service.create_game()
    fake_dialog = _FakeGameSetupDialog(game_id=game.id)
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_setup_dialog_factory=lambda service: fake_dialog,
    )
    qtbot.addWidget(window)

    window.new_game_action.trigger()

    assert window.tagging_panel is None


def test_cancelling_new_game_dialog_does_nothing(qtbot, session):
    fake_dialog = _FakeGameSetupDialog(accepted=False)
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_setup_dialog_factory=lambda service: fake_dialog,
    )
    qtbot.addWidget(window)

    window.new_game_action.trigger()

    assert window.tagging_panel is None


# -- Select Game: activates the game and auto-loads its stored video -------


def test_select_game_action_auto_loads_the_stored_video(
    qtbot, session, game_setup_service, tmp_path
):
    video_path = tmp_path / "game.mp4"
    video_path.write_bytes(b"")
    game = game_setup_service.create_game()
    game_setup_service.set_video_path(game.id, str(video_path))
    player = FakePlayer()
    fake_dialog = _FakeGameListDialog(selected_game_id=game.id)
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=player,
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_list_dialog_factory=lambda service: fake_dialog,
    )
    qtbot.addWidget(window)

    window.select_game_action.trigger()

    assert player.source == QUrl.fromLocalFile(str(video_path))


def test_select_game_action_with_no_stored_video_does_not_touch_the_player(
    qtbot, session, game_setup_service
):
    game = game_setup_service.create_game()
    player = FakePlayer()
    fake_dialog = _FakeGameListDialog(selected_game_id=game.id)
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=player,
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_list_dialog_factory=lambda service: fake_dialog,
    )
    qtbot.addWidget(window)

    window.select_game_action.trigger()

    assert player.source is None


# -- Open Video: persists the path onto whichever game is active -----------


def test_opening_video_while_a_game_is_active_persists_the_path(
    qtbot, session, game_setup_service
):
    game = game_setup_service.create_game()
    fake_dialog = _FakeGameListDialog(selected_game_id=game.id)
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_list_dialog_factory=lambda service: fake_dialog,
        file_dialog=lambda: "C:/clips/new.mp4",
    )
    qtbot.addWidget(window)
    window.select_game_action.trigger()

    window.open_video_action.trigger()

    assert game_setup_service.get_game(game.id).video_path == "C:/clips/new.mp4"


def test_opening_video_with_no_active_game_does_not_touch_any_game(
    qtbot, session, game_setup_service
):
    game = game_setup_service.create_game()
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        file_dialog=lambda: "C:/clips/new.mp4",
    )
    qtbot.addWidget(window)

    window.open_video_action.trigger()

    assert game_setup_service.get_game(game.id).video_path is None


# -- Select Game: a stored-but-missing video file relinks instead of crashing --


def test_selecting_a_game_with_a_missing_video_file_shows_a_notice_instead_of_loading_it(
    qtbot, session, game_setup_service
):
    game = game_setup_service.create_game()
    game_setup_service.set_video_path(game.id, "C:/does/not/exist.mp4")
    player = FakePlayer()
    fake_dialog = _FakeGameListDialog(selected_game_id=game.id)
    notices = []
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=player,
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_list_dialog_factory=lambda service: fake_dialog,
        video_missing_notice=notices.append,
    )
    qtbot.addWidget(window)

    window.select_game_action.trigger()

    assert notices == ["C:/does/not/exist.mp4"]
    assert player.source is None


def test_relinking_after_a_missing_video_notice_persists_the_new_path(
    qtbot, session, game_setup_service
):
    # The notice doesn't open a second file-picker itself -- Open Video...
    # (already wired to persist onto whichever game is active) is the one
    # relink path, so a missing file doesn't leave the game un-relinkable.
    game = game_setup_service.create_game()
    game_setup_service.set_video_path(game.id, "C:/does/not/exist.mp4")
    fake_dialog = _FakeGameListDialog(selected_game_id=game.id)
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_list_dialog_factory=lambda service: fake_dialog,
        video_missing_notice=lambda path: None,
        file_dialog=lambda: "C:/clips/relinked.mp4",
    )
    qtbot.addWidget(window)
    window.select_game_action.trigger()

    window.open_video_action.trigger()

    assert game_setup_service.get_game(game.id).video_path == "C:/clips/relinked.mp4"


# -- Select Game: works while a different game is already active in the window --


def test_selecting_a_different_game_while_one_is_already_active_swaps_the_tagging_panel(
    qtbot, session, game_setup_service
):
    game_a = game_setup_service.create_game()
    home_a = game_setup_service.create_team("A Home")
    away_a = game_setup_service.create_team("A Away")
    game_setup_service.set_side_team(game_a.id, Side.HOME, home_a.id)
    game_setup_service.set_side_team(game_a.id, Side.AWAY, away_a.id)

    game_b = game_setup_service.create_game()
    home_b = game_setup_service.create_team("B Home")
    away_b = game_setup_service.create_team("B Away")
    game_setup_service.set_side_team(game_b.id, Side.HOME, home_b.id)
    game_setup_service.set_side_team(game_b.id, Side.AWAY, away_b.id)

    fake_dialog = _FakeGameListDialog(selected_game_id=game_a.id)
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_list_dialog_factory=lambda service: fake_dialog,
    )
    qtbot.addWidget(window)
    window.select_game_action.trigger()
    first_panel = window.tagging_panel
    assert first_panel is not None

    fake_dialog.selected_game_id = game_b.id
    window.select_game_action.trigger()
    assert window.tagging_panel is not None
    assert window.tagging_panel is not first_panel

    # Prove the new panel is actually wired to game_b, not still game_a,
    # by logging through it and checking which game the event landed on.
    qtbot.mouseClick(
        window.tagging_panel.log_buttons[EventType.STOPPAGE], Qt.MouseButton.LeftButton
    )

    assert session.query(Event).filter_by(game_id=game_b.id).count() == 1
    assert session.query(Event).filter_by(game_id=game_a.id).count() == 0


# -- Game > Units: register the active game's units (ticket 17) -------------


class _FakeUnitsDialog:
    """Stands in for UnitsDialog.exec() -- see _FakeGameSetupDialog.
    Records what the window opened it for."""

    def __init__(
        self, service, *, game_id, home_team_id, away_team_id, parent=None
    ) -> None:
        self.opened_for = {
            "game_id": game_id,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
        }
        self.parent = parent
        self.executed = False

    def exec(self) -> QDialog.DialogCode:
        self.executed = True
        return QDialog.DialogCode.Accepted


def _window_with_units_dialog(qtbot, session, opened, *, setup_dialog=None):
    def factory(service, **kwargs):
        dialog = _FakeUnitsDialog(service, **kwargs)
        opened.append(dialog)
        return dialog

    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_setup_dialog_factory=lambda service: setup_dialog,
        units_dialog_factory=factory,
    )
    qtbot.addWidget(window)
    return window


def _game_with_sides(game_setup_service, *, both_sides=True):
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.set_side_team(game.id, Side.HOME, home.id)
    if both_sides:
        game_setup_service.set_side_team(game.id, Side.AWAY, away.id)
    return game, home, away


def test_units_action_is_disabled_with_no_active_game(qtbot, session):
    window = _window_with_units_dialog(qtbot, session, [])

    assert window.units_action.isEnabled() is False


def test_units_action_opens_the_units_dialog_for_the_active_game(
    qtbot, session, game_setup_service
):
    game, home, away = _game_with_sides(game_setup_service)
    opened = []
    window = _window_with_units_dialog(
        qtbot, session, opened, setup_dialog=_FakeGameSetupDialog(game_id=game.id)
    )
    window.new_game_action.trigger()
    assert window.units_action.isEnabled() is True

    window.units_action.trigger()

    assert opened[0].executed is True
    assert opened[0].parent is window
    assert opened[0].opened_for == {
        "game_id": game.id,
        "home_team_id": home.id,
        "away_team_id": away.id,
    }


def test_units_action_stays_disabled_until_both_sides_are_set(
    qtbot, session, game_setup_service
):
    game, _home, _away = _game_with_sides(game_setup_service, both_sides=False)
    window = _window_with_units_dialog(
        qtbot, session, [], setup_dialog=_FakeGameSetupDialog(game_id=game.id)
    )

    window.new_game_action.trigger()

    assert window.units_action.isEnabled() is False


def test_units_action_is_disabled_again_when_no_game_ends_up_active(
    qtbot, session, game_setup_service
):
    # New Game accepted without ever creating a game leaves nothing
    # active -- Units... must not keep the previous game's enabled state.
    game, _home, _away = _game_with_sides(game_setup_service)
    setup_dialogs = [
        _FakeGameSetupDialog(game_id=game.id),
        _FakeGameSetupDialog(game_id=None),
    ]
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_setup_dialog_factory=lambda service: setup_dialogs.pop(0),
    )
    qtbot.addWidget(window)
    window.new_game_action.trigger()
    assert window.units_action.isEnabled() is True

    window.new_game_action.trigger()

    assert window.units_action.isEnabled() is False


class _FakeStatsDialog:
    """Stands in for StatsDialog.exec() -- records the GameData list it
    was opened with."""

    def __init__(self, games, parent=None) -> None:
        self.games = games
        self.parent = parent
        self.executed = False

    def exec(self) -> QDialog.DialogCode:
        self.executed = True
        return QDialog.DialogCode.Accepted


def _window_with_stats_dialog(
    qtbot, session, opened, *, setup_dialog=None, game_picker=None
):
    def factory(games, parent=None):
        dialog = _FakeStatsDialog(games, parent)
        opened.append(dialog)
        return dialog

    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        game_setup_dialog_factory=lambda service: setup_dialog,
        stats_dialog_factory=factory,
        stats_game_picker_factory=lambda service: game_picker,
    )
    qtbot.addWidget(window)
    return window


def test_stats_action_is_disabled_with_no_active_game(qtbot, session):
    window = _window_with_stats_dialog(qtbot, session, [])

    assert window.stats_action.isEnabled() is False


def test_stats_action_opens_the_stats_view_for_the_active_game(
    qtbot, session, game_setup_service
):
    game, home, _away = _game_with_sides(game_setup_service)
    opened = []
    window = _window_with_stats_dialog(
        qtbot, session, opened, setup_dialog=_FakeGameSetupDialog(game_id=game.id)
    )
    window.new_game_action.trigger()
    assert window.stats_action.isEnabled() is True

    window.stats_action.trigger()

    assert opened[0].executed is True
    assert opened[0].parent is window
    (data,) = opened[0].games
    assert data.game.id == game.id
    assert data.game.home_team_id == home.id


def test_stats_action_stays_disabled_until_both_sides_are_set(
    qtbot, session, game_setup_service
):
    game, _home, _away = _game_with_sides(game_setup_service, both_sides=False)
    window = _window_with_stats_dialog(
        qtbot, session, [], setup_dialog=_FakeGameSetupDialog(game_id=game.id)
    )

    window.new_game_action.trigger()

    assert window.stats_action.isEnabled() is False


# -- Import from League Link: the same activation path as New Game ---------


class _FakeLeagueImportDialog:
    """Stands in for LeagueImportDialog.exec() -- see _FakeGameSetupDialog."""

    def __init__(self, *, game_id=None, fall_back_to_manual=False, accepted=True):
        self.game_id = game_id
        self.fall_back_to_manual = fall_back_to_manual
        self._accepted = accepted

    def exec(self) -> QDialog.DialogCode:
        return (
            QDialog.DialogCode.Accepted
            if self._accepted
            else QDialog.DialogCode.Rejected
        )


def _make_import_window(qtbot, session, import_dialog, **kwargs):
    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        league_import_dialog_factory=lambda service: import_dialog,
        **kwargs,
    )
    qtbot.addWidget(window)
    return window


def test_import_action_is_disabled_without_db_session(qtbot):
    window = _make_window(qtbot)

    assert window.import_game_action.isEnabled() is False


def test_import_action_activates_the_imported_game(qtbot, session, game_setup_service):
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.set_side_team(game.id, Side.HOME, home.id)
    game_setup_service.set_side_team(game.id, Side.AWAY, away.id)
    window = _make_import_window(
        qtbot, session, _FakeLeagueImportDialog(game_id=game.id)
    )

    window.import_game_action.trigger()

    assert window.tagging_panel is not None
    assert window.stats_action.isEnabled()


def test_import_action_hands_the_dialog_a_league_import_service(qtbot, session):
    received = []

    def factory(service):
        received.append(service)
        return _FakeLeagueImportDialog(accepted=False)

    window = MainWindow(
        controller=Mock(spec=PlaybackController),
        player=FakePlayer(),
        shortcuts=ShortcutRegistry(),
        db_session=session,
        league_import_dialog_factory=factory,
    )
    qtbot.addWidget(window)

    window.import_game_action.trigger()

    assert isinstance(received[0], LeagueImportService)


def test_unreadable_league_data_falls_back_to_manual_setup(
    qtbot, session, game_setup_service
):
    game = game_setup_service.create_game()
    opened = []

    def manual_factory(service):
        opened.append(service)
        return _FakeGameSetupDialog(game_id=game.id)

    window = _make_import_window(
        qtbot,
        session,
        _FakeLeagueImportDialog(fall_back_to_manual=True),
        game_setup_dialog_factory=manual_factory,
    )

    window.import_game_action.trigger()

    assert len(opened) == 1
    assert window.units_action.isEnabled() is False


def test_cancelling_the_import_dialog_does_nothing(qtbot, session):
    window = _make_import_window(
        qtbot, session, _FakeLeagueImportDialog(accepted=False)
    )

    window.import_game_action.trigger()

    assert window.tagging_panel is None
class _FakeGamePicker(_FakeGameListDialog):
    def __init__(self, *, selected_game_ids=(), accepted=True) -> None:
        super().__init__(accepted=accepted)
        self.selected_game_ids = list(selected_game_ids)


def test_multi_game_stats_opens_the_stats_view_over_every_picked_game(
    qtbot, session, game_setup_service
):
    first, _home, _away = _game_with_sides(game_setup_service)
    second, _home, _away = _game_with_sides(game_setup_service)
    opened = []
    window = _window_with_stats_dialog(
        qtbot,
        session,
        opened,
        game_picker=_FakeGamePicker(selected_game_ids=[first.id, second.id]),
    )
    assert window.multi_game_stats_action.isEnabled() is True

    window.multi_game_stats_action.trigger()

    assert opened[0].executed is True
    assert opened[0].parent is window
    assert [data.game.id for data in opened[0].games] == [first.id, second.id]


def test_multi_game_stats_opens_nothing_when_the_picker_is_cancelled(
    qtbot, session, game_setup_service
):
    game, _home, _away = _game_with_sides(game_setup_service)
    opened = []
    window = _window_with_stats_dialog(
        qtbot,
        session,
        opened,
        game_picker=_FakeGamePicker(selected_game_ids=[game.id], accepted=False),
    )

    window.multi_game_stats_action.trigger()

    assert opened == []
