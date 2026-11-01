"""Schema version 2: a Player's SIHF licence number becomes their league
identity (`Player.league_id`, ADR-0014), and team league IDs still in the
scraped site's `{league}-{div}-{teamNum}` format are cleared, so those
teams are name-match candidates again (see docs/adr/0012)."""

from __future__ import annotations

import pytest
from sihf_fixtures import GAME_LINK, FakeLeagueSource
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from hockey_analyzer.db import (
    LATEST_VERSION,
    MIGRATIONS,
    create_sqlite_engine,
    init_db,
    make_session_factory,
)
from hockey_analyzer.domain.enums import Side
from hockey_analyzer.domain.models import Base, Player, Team
from hockey_analyzer.league_import import (
    ImportResolution,
    LeagueImportService,
    TeamMatchStatus,
)
from hockey_analyzer.schema_migrations import player_licence_numbers

# The `players` table as schema version 1 left it.
_V1_PLAYERS = """
CREATE TABLE players (
    id INTEGER NOT NULL,
    full_name VARCHAR,
    handedness VARCHAR(5),
    position VARCHAR(2),
    PRIMARY KEY (id)
)
"""

OLD_FORMAT, NUMERIC, UNLINKED = 1, 2, 3  # team ids


@pytest.fixture
def v1_engine(tmp_path):
    """A version-1 database: every other table as today, `players` without
    `league_id`, no `official_events` (version 3's), one team per kind of
    team league_id."""
    engine = create_sqlite_engine(tmp_path / "v1.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE official_events")
        conn.exec_driver_sql("DROP TABLE players")
        conn.exec_driver_sql(_V1_PLAYERS)
        conn.exec_driver_sql("PRAGMA user_version = 1")
        conn.exec_driver_sql(
            "INSERT INTO teams (id, name, league_id, is_user_team) VALUES "
            f"({OLD_FORMAT}, 'HC Monthey', '10-4-104254', 1), "
            f"({NUMERIC}, 'Numeric', '103010', 0), "
            f"({UNLINKED}, 'Unlinked', NULL, 0)"
        )
        conn.exec_driver_sql("INSERT INTO players (id, full_name) VALUES (1, 'One')")
    return engine


def _team_league_ids(engine):
    with engine.connect() as conn:
        return dict(conn.exec_driver_sql("SELECT id, league_id FROM teams").all())


def test_it_is_the_second_shipped_migration():
    assert MIGRATIONS[1] is player_licence_numbers
    assert LATEST_VERSION >= 2


def test_old_format_team_ids_are_cleared_and_numeric_ones_kept(v1_engine):
    init_db(v1_engine)

    assert _team_league_ids(v1_engine) == {
        OLD_FORMAT: None,
        NUMERIC: "103010",
        UNLINKED: None,
    }


def test_the_user_is_told_which_teams_lost_their_old_league_id(v1_engine):
    messages = init_db(v1_engine)

    assert messages == [
        "1 team had a league ID in the old format; it was cleared and will be "
        "offered as a name match the next time it's imported."
    ]


def test_players_get_a_unique_league_id(v1_engine):
    init_db(v1_engine)

    with make_session_factory(v1_engine)() as session:
        assert session.get(Player, 1).league_id is None
        session.get(Player, 1).league_id = "300001"
        session.commit()
        session.add(Player(full_name="Two", league_id="300001"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_the_migrated_players_table_matches_a_freshly_created_one(v1_engine, tmp_path):
    init_db(v1_engine)
    fresh = create_sqlite_engine(tmp_path / "fresh.db")
    init_db(fresh)

    def shape(engine):
        inspector = inspect(engine)
        return (
            [
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspector.get_columns("players")
            ],
            sorted(
                (index["name"], index["column_names"], index["unique"])
                for index in inspector.get_indexes("players")
            ),
        )

    assert shape(v1_engine) == shape(fresh)


def test_a_cleared_team_is_a_name_candidate_and_learns_the_new_id(v1_engine):
    init_db(v1_engine)
    with make_session_factory(v1_engine)() as session:
        service = LeagueImportService(session, FakeLeagueSource())
        proposal = service.propose(GAME_LINK)
        away = proposal.team(Side.AWAY)
        assert away.status == TeamMatchStatus.NEEDS_CONFIRMATION
        assert [c.team_id for c in away.candidates] == [OLD_FORMAT]
        assert OLD_FORMAT in {t.team_id for t in service.linkable_teams("104254")}

        resolution = ImportResolution.as_proposed(proposal)
        service.confirm(
            proposal,
            ImportResolution(
                home_team_id=resolution.home_team_id,
                away_team_id=OLD_FORMAT,
                user_team=Side.AWAY,
                player_ids=resolution.player_ids,
            ),
        )

        assert session.get(Team, OLD_FORMAT).league_id == "104254"
