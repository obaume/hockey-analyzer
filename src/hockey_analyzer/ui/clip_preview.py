"""Clip preview (ticket 50): plays one candidate event's padding window
straight from the game's source footage, inside the clip export dialog,
before any Clip exists (see CONTEXT.md's Clip preview entry). Nothing is
encoded. The window itself is `clip_export.padding_window`'s answer, the
same one the export would cut, and playback never leaves it: the scrubber
spans only the window, and reaching its end pauses there.

The media player is injected; by default it's a real `QMediaPlayer`
rendering into a `VideoFrameView`, with audio.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QRect, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedLayout,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.clip_export import ClipSegment
from hockey_analyzer.ui.video_frame_view import VideoFrameView

SELECT_PROMPT = "Select an event to preview"
OUTSIDE_FOOTAGE = "This event is outside the footage — its clip would be empty"


class _SignalLike(Protocol):
    def connect(self, slot: object) -> object: ...


class PreviewPlayer(Protocol):
    """The slice of `QMediaPlayer` the preview drives."""

    positionChanged: _SignalLike
    playbackStateChanged: _SignalLike
    mediaStatusChanged: _SignalLike

    def setAudioOutput(self, output: QAudioOutput) -> None: ...
    def setVideoSink(self, sink: QVideoSink) -> None: ...
    def source(self) -> QUrl: ...
    def setSource(self, source: QUrl) -> None: ...
    def position(self) -> int: ...
    def setPosition(self, ms: int) -> None: ...
    def playbackState(self) -> QMediaPlayer.PlaybackState: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...


class _WindowScrubber(QSlider):
    """A slider over the padding window with a tick at the event's own
    video timestamp, so the lead-in and lead-out read at a glance."""

    user_seeked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.marker_ms: int | None = None
        self.setRange(0, 0)
        self.valueChanged.connect(self.user_seeked)

    def show_position(self, ms: int) -> None:
        """Follows playback without it reading as a user seek."""
        self.blockSignals(True)
        self.setValue(ms)
        self.blockSignals(False)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.marker_ms is None or self.maximum() <= self.minimum():
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )
        x = (
            QStyle.sliderPositionFromValue(
                self.minimum(),
                self.maximum(),
                self.marker_ms,
                groove.width() - handle.width(),
            )
            + groove.x()
            + handle.width() // 2
        )
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#d33"), 2))
        rect = QRect(x, self.rect().top() + 2, 0, self.rect().height() - 4)
        painter.drawLine(rect.topLeft(), rect.bottomLeft())


class ClipPreview(QWidget):
    def __init__(
        self, *, player: PreviewPlayer | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._player: PreviewPlayer = (
            player if player is not None else QMediaPlayer(self)
        )
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self.video_view = VideoFrameView()
        self._player.setVideoSink(self.video_view.video_sink)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._window: ClipSegment | None = None

        self.placeholder = QLabel(SELECT_PROMPT)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self._surface = QStackedLayout()
        self._surface.addWidget(self.placeholder)
        self._surface.addWidget(self.video_view)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_play_pause)
        self.scrubber = _WindowScrubber()
        self.scrubber.user_seeked.connect(self.seek)
        controls = QHBoxLayout()
        controls.addWidget(self.play_button)
        controls.addWidget(self.scrubber, stretch=1)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._surface, stretch=1)
        layout.addLayout(controls)
        self.setLayout(layout)
        self._show_placeholder(SELECT_PROMPT)

    def load(self, source_path: str, window: ClipSegment, event_ms: int) -> None:
        """Shows `window` of `source_path`, paused on its first frame. An
        empty window (the event lies past the end of the footage) shows
        a placeholder instead of loading anything."""
        if window.start_ms >= window.end_ms:
            self.clear(OUTSIDE_FOOTAGE)
            return
        self._window = window
        self.scrubber.setRange(window.start_ms, window.end_ms)
        self.scrubber.marker_ms = event_ms
        self.scrubber.update()
        source = QUrl.fromLocalFile(source_path)
        if self._player.source() != source:
            self._player.setSource(source)
        self._player.pause()
        self._player.setPosition(window.start_ms)
        self._surface.setCurrentWidget(self.video_view)
        self.play_button.setEnabled(True)
        self.scrubber.setEnabled(True)

    def clear(self, text: str = SELECT_PROMPT) -> None:
        """Stops playback and shows `text` in place of video."""
        if self._window is not None:
            self._player.stop()
        self._show_placeholder(text)

    def _show_placeholder(self, text: str) -> None:
        self._window = None
        self.placeholder.setText(text)
        self._surface.setCurrentWidget(self.placeholder)
        self.scrubber.marker_ms = None
        self.scrubber.setRange(0, 0)
        self.play_button.setEnabled(False)
        self.scrubber.setEnabled(False)

    def toggle_play_pause(self) -> None:
        window = self._window
        if window is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            return
        if self._player.position() >= window.end_ms:
            self._player.setPosition(window.start_ms)
        self._player.play()

    def seek(self, ms: int) -> None:
        """Moves within the window; a position outside it is clamped."""
        window = self._window
        if window is None:
            return
        self._player.setPosition(min(max(ms, window.start_ms), window.end_ms))

    def _on_position_changed(self, ms: int) -> None:
        window = self._window
        if window is None:
            return
        playing = (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        if playing and ms >= window.end_ms:
            self._player.pause()
            if ms > window.end_ms:
                self._player.setPosition(window.end_ms)
                return
        elif not window.start_ms <= ms <= window.end_ms:
            # A late report from the window loaded before this one; the
            # seek to this window's start is still on its way.
            return
        self.scrubber.show_position(ms)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("Pause" if playing else "Play")

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        # A seek issued while the footage was still opening can be lost;
        # land on the window's first frame once it's loaded.
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._window is not None:
            self._player.setPosition(self._window.start_ms)
