from __future__ import annotations

from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from hockey_analyzer import app
from hockey_analyzer.ui.main_window import MainWindow


def test_main_shows_the_main_window_maximized_not_fullscreen(monkeypatch, tmp_path):
    # main() constructs its own QApplication and blocks in exec(); fake both,
    # and the window, so the test only observes how the window is shown.
    fake_app = Mock(spec=QApplication)
    fake_app.exec.return_value = 0
    monkeypatch.setattr(app, "QApplication", Mock(return_value=fake_app))
    window = Mock(spec=MainWindow)
    monkeypatch.setattr(app, "MainWindow", Mock(return_value=window))
    monkeypatch.setattr(app, "_default_db_path", lambda: tmp_path / app.DB_FILENAME)

    assert app.main() == 0

    window.showMaximized.assert_called_once_with()
    window.show.assert_not_called()
    window.showFullScreen.assert_not_called()
