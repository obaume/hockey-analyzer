from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

from hockey_analyzer.db import create_sqlite_engine, init_db, make_session_factory
from hockey_analyzer.ui.main_window import MainWindow

# Single-user local app with one ongoing dataset, not a "project file" the
# user picks per launch -- a fixed path in the platform's per-user app-data
# directory, auto-created on first run (see CONTEXT.md's Game entry).
DB_FILENAME = "hockey_analyzer.db"


def _default_db_path() -> Path:
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / DB_FILENAME


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Hockey Analyzer")

    engine = create_sqlite_engine(_default_db_path())
    init_db(engine)
    db_session = make_session_factory(engine)()

    window = MainWindow(db_session=db_session)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
