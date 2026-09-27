from __future__ import annotations

import pytest
from sqlalchemy import inspect

from hockey_analyzer.db import (
    LATEST_VERSION,
    MIGRATIONS,
    DatabaseTooNewError,
    MigrationFailedError,
    create_sqlite_engine,
    init_db,
    make_session_factory,
)
from hockey_analyzer.domain.models import Base, Team


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


def _v0_database(path):
    """A database as an app from before versioned migrations left it:
    tables present, `user_version` never set (so it reads 0)."""
    engine = create_sqlite_engine(path)
    Base.metadata.create_all(engine)
    return engine


def _user_version(engine) -> int:
    with engine.connect() as conn:
        return conn.exec_driver_sql("PRAGMA user_version").scalar_one()


def _team_columns(engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("teams")}


def _add_team_column(name):
    def migration(conn):
        conn.exec_driver_sql(f"ALTER TABLE teams ADD COLUMN {name} TEXT")

    return migration


def test_a_fresh_database_is_stamped_at_the_latest_version(tmp_path):
    engine = create_sqlite_engine(tmp_path / "hockey.sqlite")
    migrations = [_add_team_column("nickname"), _add_team_column("colour")]

    messages = init_db(engine, migrations=migrations)

    assert _user_version(engine) == 2
    assert messages == []
    # Fresh tables come from create_all, not from replaying migrations.
    assert "nickname" not in _team_columns(engine)
    assert not list(tmp_path.glob("*.bak-v*"))


def test_the_shipped_migrations_stamp_a_fresh_database_at_latest_version():
    engine = create_sqlite_engine()

    init_db(engine)

    assert _user_version(engine) == LATEST_VERSION == len(MIGRATIONS)


def test_a_v0_database_is_backed_up_then_upgraded_and_stamped(tmp_path):
    engine = _v0_database(tmp_path / "hockey.sqlite")
    with make_session_factory(engine)() as session:
        session.add(Team(name="Icebreakers"))
        session.commit()

    def add_nickname(conn):
        conn.exec_driver_sql("ALTER TABLE teams ADD COLUMN nickname TEXT")
        return ["Teams now have nicknames."]

    messages = init_db(engine, migrations=[add_nickname])

    assert _user_version(engine) == 1
    assert "nickname" in _team_columns(engine)
    assert messages == ["Teams now have nicknames."]

    backup = create_sqlite_engine(tmp_path / "hockey.sqlite.bak-v0")
    assert _user_version(backup) == 0
    assert "nickname" not in _team_columns(backup)
    with make_session_factory(backup)() as session:
        assert [team.name for team in session.query(Team)] == ["Icebreakers"]


def test_only_the_migrations_past_the_current_version_run(tmp_path):
    engine = _v0_database(tmp_path / "hockey.sqlite")
    init_db(engine, migrations=[_add_team_column("nickname")])

    init_db(
        engine,
        migrations=[_add_team_column("nickname"), _add_team_column("colour")],
    )

    assert _user_version(engine) == 2
    assert {"nickname", "colour"} <= _team_columns(engine)
    assert (tmp_path / "hockey.sqlite.bak-v1").exists()


def test_an_up_to_date_database_is_left_alone(tmp_path):
    engine = create_sqlite_engine(tmp_path / "hockey.sqlite")
    init_db(engine, migrations=[_add_team_column("nickname")])

    messages = init_db(engine, migrations=[_add_team_column("nickname")])

    assert messages == []
    assert _user_version(engine) == 1
    assert not list(tmp_path.glob("*.bak-v*"))


def test_a_failing_migration_leaves_the_database_at_its_previous_version(tmp_path):
    engine = _v0_database(tmp_path / "hockey.sqlite")

    def half_done_then_fails(conn):
        conn.exec_driver_sql("ALTER TABLE teams ADD COLUMN colour TEXT")
        raise RuntimeError("boom")

    def add_nickname(conn):
        conn.exec_driver_sql("ALTER TABLE teams ADD COLUMN nickname TEXT")
        return ["Teams now have nicknames."]

    with pytest.raises(MigrationFailedError, match="version 2") as excinfo:
        init_db(engine, migrations=[add_nickname, half_done_then_fails])

    assert isinstance(excinfo.value.__cause__, RuntimeError)
    # The first migration is committed and won't run again, so its message
    # must still reach the user.
    assert excinfo.value.messages == ["Teams now have nicknames."]
    assert _user_version(engine) == 1
    columns = _team_columns(engine)
    assert "nickname" in columns
    assert "colour" not in columns


def test_a_database_from_a_newer_app_is_refused(tmp_path):
    engine = _v0_database(tmp_path / "hockey.sqlite")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version = 3")

    with pytest.raises(DatabaseTooNewError, match="version 3"):
        init_db(engine, migrations=[_add_team_column("nickname")])

    assert _user_version(engine) == 3
    assert "nickname" not in _team_columns(engine)
    assert not list(tmp_path.glob("*.bak-v*"))
