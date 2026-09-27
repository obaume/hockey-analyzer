from __future__ import annotations

from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from hockey_analyzer import app
from hockey_analyzer.app import start
from hockey_analyzer.db import create_sqlite_engine
from hockey_analyzer.domain.models import Base
from hockey_analyzer.ui.main_window import MainWindow


class _RecordingBoxes:
    """Stands in for the startup QMessageBoxes, which would block a
    headless test on their modal loop."""

    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []
        self.errors: list[tuple[str, str]] = []

    def notice(self, title: str, text: str) -> None:
        self.notices.append((title, text))

    def error(self, title: str, text: str) -> None:
        self.errors.append((title, text))


def _start(db_path, boxes, migrations):
    return start(
        db_path,
        migrations=migrations,
        show_notice=boxes.notice,
        show_error=boxes.error,
    )


def _v0_database(path) -> None:
    engine = create_sqlite_engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()


def test_migration_messages_are_shown_once_together_at_startup(qtbot, tmp_path):
    db_path = tmp_path / "hockey.db"
    _v0_database(db_path)
    boxes = _RecordingBoxes()

    window = _start(
        db_path,
        boxes,
        [lambda conn: ["First change."], lambda conn: None, lambda conn: ["Second."]],
    )
    qtbot.addWidget(window)

    assert isinstance(window, MainWindow)
    assert len(boxes.notices) == 1
    _, text = boxes.notices[0]
    assert "First change." in text
    assert "Second." in text
    assert boxes.errors == []


def test_no_box_when_no_migration_has_anything_to_say(qtbot, tmp_path):
    boxes = _RecordingBoxes()

    window = _start(tmp_path / "hockey.db", boxes, [lambda conn: ["unused"]])
    qtbot.addWidget(window)

    # A fresh database skips migrations, so nothing to report.
    assert boxes.notices == []
    assert boxes.errors == []


def test_a_database_that_cant_be_opened_shows_an_error_instead(qtbot, tmp_path):
    db_path = tmp_path / "hockey.db"
    _v0_database(db_path)
    boxes = _RecordingBoxes()

    def fails(conn):
        raise RuntimeError("boom")

    window = _start(db_path, boxes, [lambda conn: ["Committed change."], fails])

    assert window is None
    assert len(boxes.errors) == 1
    _, text = boxes.errors[0]
    assert "version 2" in text
    # The first migration stuck, so its message is shown despite the failure.
    assert [text for _, text in boxes.notices] == ["Committed change."]


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
