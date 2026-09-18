"""Video display surface fed directly by a `QVideoSink`.

`QMediaPlayer` decodes in-process and pushes each `QVideoFrame` here rather
than handing off to a separate renderer process, keeping frame-stepping and
scrubbing responsive (see ticket 13).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPaintEvent
from PySide6.QtMultimedia import QVideoFrame, QVideoSink
from PySide6.QtWidgets import QWidget


class VideoFrameView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_frame_changed)
        self._image: QImage | None = None

    @property
    def video_sink(self) -> QVideoSink:
        return self._sink

    def _on_frame_changed(self, frame: QVideoFrame) -> None:
        image = frame.toImage()
        self._image = image if not image.isNull() else None
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        if self._image is not None:
            painter.drawImage(self.rect(), self._image)
        else:
            painter.fillRect(self.rect(), Qt.GlobalColor.black)
