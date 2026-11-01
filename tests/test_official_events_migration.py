"""Schema version 3: a game's Official events -- the league's own
play-by-play -- get a table of their own (see CONTEXT.md's Official event
and docs/adr/0012)."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from hockey_analyzer.db import (
    LATEST_VERSION,
    MIGRATIONS,
    create_sqlite_engine,
    init_db,
    make_session_factory,
)
from hockey_analyzer.domain.enums import OfficialEventKind, Side
from hockey_analyzer.domain.models import Base, Game, OfficialEvent
from hockey_analyzer.schema_migrations import official_events


@pytest.fixture
def v2_engine(tmp_path):
    """A version-2 database: every table as today but `official_events`,
    with one game in it."""
    engine = create_sqlite_engine(tmp_path / "v2.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE official_events")
        conn.exec_driver_sql("PRAGMA user_version = 2")
        conn.exec_driver_sql(
            "INSERT INTO games (id, league_id, rink_type, opponent_shifts_complete) "
            "VALUES (1, '20270009263101', 'iihf', 0)"
        )
    return engine


def test_it_is_the_third_shipped_migration():
    assert MIGRATIONS[2] is official_events
    assert LATEST_VERSION >= 3


def test_an_upgraded_game_has_no_official_events_until_refreshed(v2_engine):
    messages = init_db(v2_engine)

    assert messages == []
    with make_session_factory(v2_engine)() as session:
        assert session.query(OfficialEvent).count() == 0
        session.add(
            OfficialEvent(
                game_id=session.get(Game, 1).id,
                kind=OfficialEventKind.GOAL,
                period_number=1,
                elapsed_seconds=502,
                side=Side.HOME,
                player_league_id="300001",
            )
        )
        session.commit()


def test_the_migrated_table_matches_a_freshly_created_one(v2_engine, tmp_path):
    init_db(v2_engine)
    fresh = create_sqlite_engine(tmp_path / "fresh.db")
    init_db(fresh)

    def shape(engine):
        inspector = inspect(engine)
        return (
            [
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspector.get_columns("official_events")
            ],
            sorted(
                (index["name"], index["column_names"], index["unique"])
                for index in inspector.get_indexes("official_events")
            ),
            [
                (key["constrained_columns"], key["referred_table"])
                for key in inspector.get_foreign_keys("official_events")
            ],
        )

    assert shape(v2_engine) == shape(fresh)
