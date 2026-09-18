from __future__ import annotations

from PySide6.QtMultimedia import QMediaPlayer

from hockey_analyzer.ui.playback_controller import DEFAULT_SPEED_STEPS, PlaybackController


class FakePlayer:
    """Duck-typed double matching the slice of QMediaPlayer's API the
    controller uses, so controller logic can be unit tested without a real
    media backend.
    """

    def __init__(
        self,
        *,
        position: int = 0,
        duration: int = 100_000,
        state: QMediaPlayer.PlaybackState = QMediaPlayer.PlaybackState.StoppedState,
    ) -> None:
        self._position = position
        self._duration = duration
        self._state = state
        self._rate = 1.0

    def position(self) -> int:
        return self._position

    def duration(self) -> int:
        return self._duration

    def setPosition(self, ms: int) -> None:
        self._position = ms

    def playbackState(self) -> QMediaPlayer.PlaybackState:
        return self._state

    def play(self) -> None:
        self._state = QMediaPlayer.PlaybackState.PlayingState

    def pause(self) -> None:
        self._state = QMediaPlayer.PlaybackState.PausedState

    def playbackRate(self) -> float:
        return self._rate

    def setPlaybackRate(self, rate: float) -> None:
        self._rate = rate


def test_toggle_play_pause_plays_when_stopped():
    player = FakePlayer(state=QMediaPlayer.PlaybackState.StoppedState)
    controller = PlaybackController(player)

    controller.toggle_play_pause()

    assert player.playbackState() == QMediaPlayer.PlaybackState.PlayingState


def test_toggle_play_pause_pauses_when_playing():
    player = FakePlayer(state=QMediaPlayer.PlaybackState.PlayingState)
    controller = PlaybackController(player)

    controller.toggle_play_pause()

    assert player.playbackState() == QMediaPlayer.PlaybackState.PausedState


def test_jump_forward_advances_position_by_seconds():
    player = FakePlayer(position=10_000, duration=100_000)
    controller = PlaybackController(player)

    controller.jump(5)

    assert player.position() == 15_000


def test_jump_backward_moves_position_back_by_seconds():
    player = FakePlayer(position=10_000, duration=100_000)
    controller = PlaybackController(player)

    controller.jump(-5)

    assert player.position() == 5_000


def test_jump_forward_clamps_to_duration():
    player = FakePlayer(position=98_000, duration=100_000)
    controller = PlaybackController(player)

    controller.jump(5)

    assert player.position() == 100_000


def test_jump_backward_clamps_to_zero():
    player = FakePlayer(position=2_000, duration=100_000)
    controller = PlaybackController(player)

    controller.jump(-5)

    assert player.position() == 0


def test_seek_moves_to_the_given_absolute_position():
    player = FakePlayer(position=10_000, duration=100_000)
    controller = PlaybackController(player)

    controller.seek(42_000)

    assert player.position() == 42_000


def test_seek_clamps_to_duration():
    player = FakePlayer(position=10_000, duration=100_000)
    controller = PlaybackController(player)

    controller.seek(500_000)

    assert player.position() == 100_000


def test_seek_clamps_to_zero():
    player = FakePlayer(position=10_000, duration=100_000)
    controller = PlaybackController(player)

    controller.seek(-500)

    assert player.position() == 0


def test_step_frame_forward_advances_by_one_frame_duration():
    player = FakePlayer(position=1_000, duration=100_000)
    controller = PlaybackController(player, frame_rate=25.0)

    controller.step_frame(1)

    assert player.position() == 1_040  # 1000ms / 25fps = 40ms


def test_step_frame_backward_moves_back_by_one_frame_duration():
    player = FakePlayer(position=1_000, duration=100_000)
    controller = PlaybackController(player, frame_rate=25.0)

    controller.step_frame(-1)

    assert player.position() == 960


def test_step_frame_pauses_playback():
    player = FakePlayer(position=1_000, duration=100_000, state=QMediaPlayer.PlaybackState.PlayingState)
    controller = PlaybackController(player)

    controller.step_frame(1)

    assert player.playbackState() == QMediaPlayer.PlaybackState.PausedState


def test_step_frame_clamps_to_duration():
    player = FakePlayer(position=99_990, duration=100_000)
    controller = PlaybackController(player, frame_rate=25.0)

    controller.step_frame(1)

    assert player.position() == 100_000


def test_set_frame_rate_changes_step_size():
    player = FakePlayer(position=1_000, duration=100_000)
    controller = PlaybackController(player, frame_rate=25.0)

    controller.set_frame_rate(50.0)
    controller.step_frame(1)

    assert player.position() == 1_020  # 1000ms / 50fps = 20ms


def test_set_frame_rate_ignores_non_positive_values():
    player = FakePlayer(position=1_000, duration=100_000)
    controller = PlaybackController(player, frame_rate=25.0)

    controller.set_frame_rate(0.0)
    controller.step_frame(1)

    assert player.position() == 1_040  # unchanged 25fps step


def test_default_speed_is_1x():
    player = FakePlayer()
    controller = PlaybackController(player)

    assert controller.speed == 1.0


def test_speed_up_selects_the_next_faster_step():
    player = FakePlayer()
    controller = PlaybackController(player)

    controller.speed_up()

    assert controller.speed == DEFAULT_SPEED_STEPS[DEFAULT_SPEED_STEPS.index(1.0) + 1]
    assert player.playbackRate() == controller.speed


def test_speed_down_selects_the_next_slower_step():
    player = FakePlayer()
    controller = PlaybackController(player)

    controller.speed_down()

    assert controller.speed == DEFAULT_SPEED_STEPS[DEFAULT_SPEED_STEPS.index(1.0) - 1]
    assert player.playbackRate() == controller.speed


def test_speed_up_clamps_at_fastest_step():
    player = FakePlayer()
    controller = PlaybackController(player)

    for _ in range(len(DEFAULT_SPEED_STEPS) + 2):
        controller.speed_up()

    assert controller.speed == DEFAULT_SPEED_STEPS[-1]


def test_speed_down_clamps_at_slowest_step():
    player = FakePlayer()
    controller = PlaybackController(player)

    for _ in range(len(DEFAULT_SPEED_STEPS) + 2):
        controller.speed_down()

    assert controller.speed == DEFAULT_SPEED_STEPS[0]


def test_set_speed_index_applies_the_chosen_step():
    player = FakePlayer()
    controller = PlaybackController(player)

    controller.set_speed_index(0)

    assert controller.speed == DEFAULT_SPEED_STEPS[0]
    assert player.playbackRate() == DEFAULT_SPEED_STEPS[0]
