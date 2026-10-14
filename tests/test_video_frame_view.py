from __future__ import annotations

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoFrame

from hockey_analyzer.ui.video_frame_view import VideoFrameView, letterbox_rect


def test_wide_frame_in_tall_area_is_letterboxed_and_centred():
    assert letterbox_rect(QSize(1920, 1080), QRect(0, 0, 400, 400)) == QRect(
        0, 87, 400, 225
    )


def test_narrow_frame_in_wide_area_is_pillarboxed_and_centred():
    assert letterbox_rect(QSize(640, 480), QRect(0, 0, 800, 300)) == QRect(
        200, 0, 400, 300
    )


def test_frame_matching_the_area_fills_it():
    assert letterbox_rect(QSize(1920, 1080), QRect(0, 0, 640, 360)) == QRect(
        0, 0, 640, 360
    )


def test_frame_is_drawn_at_its_own_aspect_ratio_with_black_bars(qtbot):
    view = VideoFrameView()
    qtbot.addWidget(view)
    view.resize(400, 400)
    image = QImage(1920, 1080, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))

    view.video_sink.setVideoFrame(QVideoFrame(image))
    rendered = view.grab().toImage()

    def pixel(x: int, y: int) -> QColor:
        ratio = rendered.devicePixelRatio()
        return rendered.pixelColor(int(x * ratio), int(y * ratio))

    assert pixel(200, 10) == QColor("black")
    assert pixel(200, 200) == QColor("red")
    assert pixel(200, 390) == QColor("black")
