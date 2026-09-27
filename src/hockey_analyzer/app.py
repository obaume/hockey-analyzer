from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication, QMessageBox

from hockey_analyzer.db import (
    MIGRATIONS,
    Migration,
    SchemaVersionError,
    create_sqlite_engine,
    init_db,
    make_session_factory,
)
from hockey_analyzer.ui.main_window import MainWindow

# Single-user local app with one ongoing dataset, not a "project file" the
# user picks per launch -- a fixed path in the platform's per-user app-data
# directory, auto-created on first run (see CONTEXT.md's Game entry).
DB_FILENAME = "hockey_analyzer.db"

# (title, text) -> shows a modal box; injectable so tests needn't block on one.
MessageBox = Callable[[str, str], None]


def _default_db_path() -> Path:
    data_dir = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / DB_FILENAME


def _information_box(title: str, text: str) -> None:
    QMessageBox.information(None, title, text)


def _critical_box(title: str, text: str) -> None:
    QMessageBox.critical(None, title, text)


def start(
    db_path: Path,
    *,
    migrations: Sequence[Migration] = MIGRATIONS,
    show_notice: MessageBox = _information_box,
    show_error: MessageBox = _critical_box,
) -> MainWindow | None:
    """Open (and if needed upgrade) the database and build the main window,
    or return None after telling the user why the database can't be used."""
    engine = create_sqlite_engine(db_path)
    try:
        messages = init_db(engine, migrations=migrations)
    except SchemaVersionError as error:
        engine.dispose()
        show_error("Can't open the database", str(error))
        return None
    if messages:
        show_notice("Database upgraded", "\n\n".join(messages))
    return MainWindow(db_session=make_session_factory(engine)())


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Hockey Analyzer")

    window = start(_default_db_path())
    if window is None:
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
