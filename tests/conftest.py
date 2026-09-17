from __future__ import annotations

import pytest

from hockey_analyzer.db import create_sqlite_engine, init_db, make_session_factory


@pytest.fixture
def session():
    engine = create_sqlite_engine()
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        yield session
