from __future__ import annotations

from hockey_analyzer.db import create_sqlite_engine, init_db, make_session_factory
from hockey_analyzer.domain.models import Team


def test_round_trip_against_a_temp_file_database(tmp_path):
    db_path = tmp_path / "hockey.sqlite"
    engine = create_sqlite_engine(db_path)
    init_db(engine)
    factory = make_session_factory(engine)

    with factory() as session:
        session.add(Team(name="Icebreakers", is_user_team=True))
        session.commit()

    # Reopen against the same file to confirm the data actually persisted
    # to disk, not just within one connection's lifetime.
    reopened_engine = create_sqlite_engine(db_path)
    reopened_factory = make_session_factory(reopened_engine)
    with reopened_factory() as session:
        teams = session.query(Team).all()
        assert len(teams) == 1
        assert teams[0].name == "Icebreakers"
