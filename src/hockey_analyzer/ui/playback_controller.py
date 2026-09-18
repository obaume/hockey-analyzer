"""Playback transport logic, decoupled from Qt widgets.

Takes any object exposing the slice of `QMediaPlayer`'s API used here
(`position`/`setPosition`/`duration`/`playbackState`/`play`/`pause`/
`playbackRate`/`setPlaybackRate`), so a real `QMediaPlayer` and a plain
test double both work without the controller depending on Qt beyond the
`QMediaPlayer.PlaybackState` enum.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from PySide6.QtMultimedia import QMediaPlayer

DEFAULT_FRAME_RATE = 30.0
DEFAULT_SPEED_STEPS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


class MediaPlayerLike(Protocol):
    def position(self) -> int: ...
    def setPosition(self, ms: int) -> None: ...
    def duration(self) -> int: ...
    def playbackState(self) -> QMediaPlayer.PlaybackState: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def playbackRate(self) -> float: ...
    def setPlaybackRate(self, rate: float) -> None: ...


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


class PlaybackController:
    def __init__(
        self,
        player: MediaPlayerLike,
        *,
        frame_rate: float = DEFAULT_FRAME_RATE,
        speed_steps: Sequence[float] = DEFAULT_SPEED_STEPS,
    ) -> None:
        self._player = player
        self._frame_rate = frame_rate
        self._speed_steps = tuple(speed_steps)
        self._speed_index = self._speed_steps.index(1.0)

    @property
    def frame_rate(self) -> float:
        return self._frame_rate

    def set_frame_rate(self, frame_rate: float) -> None:
        if frame_rate > 0:
            self._frame_rate = frame_rate

    @property
    def speed(self) -> float:
        return self._speed_steps[self._speed_index]

    @property
    def speed_index(self) -> int:
        return self._speed_index

    def toggle_play_pause(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def jump(self, seconds: float) -> None:
        target = self._player.position() + round(seconds * 1000)
        self._player.setPosition(_clamp(target, 0, self._player.duration()))

    def seek(self, position_ms: int) -> None:
        self._player.setPosition(_clamp(position_ms, 0, self._player.duration()))

    def step_frame(self, direction: int) -> None:
        self._player.pause()
        frame_ms = round(1000 / self._frame_rate)
        target = self._player.position() + direction * frame_ms
        self._player.setPosition(_clamp(target, 0, self._player.duration()))

    def speed_up(self) -> None:
        self.set_speed_index(self._speed_index + 1)

    def speed_down(self) -> None:
        self.set_speed_index(self._speed_index - 1)

    def set_speed_index(self, index: int) -> None:
        self._speed_index = _clamp(index, 0, len(self._speed_steps) - 1)
        self._player.setPlaybackRate(self._speed_steps[self._speed_index])
