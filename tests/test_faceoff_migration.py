"""Schema version 1: faceoff participant A/B (each with its own team) and
a winner team become a home participant, an away participant and a
winning side (see CONTEXT.md's Faceoff entry and docs/adr/0012)."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from hockey_analyzer.db import (
    LATEST_VERSION,
    create_sqlite_engine,
    init_db,
    make_session_factory,
)
from hockey_analyzer.domain.enums import Side
from hockey_analyzer.domain.models import Base, Faceoff, ShotAttempt

# The `events` table as schema version 0 left it.
_V0_EVENTS = """
CREATE TABLE events (
    id INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    event_type VARCHAR(12) NOT NULL,
    video_timestamp INTEGER NOT NULL,
    source VARCHAR(6) NOT NULL,
    confirmed BOOLEAN NOT NULL,
    strength_state VARCHAR,
    period_number INTEGER,
    faceoff_x FLOAT,
    faceoff_y FLOAT,
    faceoff_team_a_id INTEGER,
    faceoff_participant_a_id INTEGER,
    faceoff_participant_a_unknown BOOLEAN NOT NULL,
    faceoff_team_b_id INTEGER,
    faceoff_participant_b_id INTEGER,
    faceoff_participant_b_unknown BOOLEAN NOT NULL,
    faceoff_winner_team_id INTEGER,
    shot_x FLOAT,
    shot_y FLOAT,
    shot_team_id INTEGER,
    shot_outcome VARCHAR(7),
    shot_type VARCHAR(11),
    shot_rush BOOLEAN NOT NULL,
    shot_rebound BOOLEAN NOT NULL,
    shot_screened BOOLEAN NOT NULL,
    shot_one_timer BOOLEAN NOT NULL,
    shot_xg FLOAT,
    shooter_id INTEGER,
    shooter_unknown BOOLEAN NOT NULL,
    assist1_id INTEGER,
    assist1_unknown BOOLEAN NOT NULL,
    assist2_id INTEGER,
    assist2_unknown BOOLEAN NOT NULL,
    penalty_team_id INTEGER,
    penalty_player_id INTEGER,
    penalty_player_unknown BOOLEAN NOT NULL,
    penalty_duration_minutes FLOAT,
    penalty_infraction VARCHAR,
    shift_team_id INTEGER,
    shift_player_id INTEGER,
    shift_player_unknown BOOLEAN NOT NULL,
    shift_on_ice BOOLEAN,
    PRIMARY KEY (id),
    FOREIGN KEY(game_id) REFERENCES games (id),
    FOREIGN KEY(faceoff_team_a_id) REFERENCES teams (id),
    FOREIGN KEY(faceoff_participant_a_id) REFERENCES players (id),
    FOREIGN KEY(faceoff_team_b_id) REFERENCES teams (id),
    FOREIGN KEY(faceoff_participant_b_id) REFERENCES players (id),
    FOREIGN KEY(faceoff_winner_team_id) REFERENCES teams (id),
    CONSTRAINT ck_faceoff_participant_a_ref CHECK (event_type != 'faceoff' OR ((faceoff_participant_a_id IS NULL) AND (faceoff_participant_a_unknown = 1)) OR ((faceoff_participant_a_id IS NOT NULL) AND (faceoff_participant_a_unknown = 0))),
    CONSTRAINT ck_faceoff_participant_b_ref CHECK (event_type != 'faceoff' OR ((faceoff_participant_b_id IS NULL) AND (faceoff_participant_b_unknown = 1)) OR ((faceoff_participant_b_id IS NOT NULL) AND (faceoff_participant_b_unknown = 0))),
    FOREIGN KEY(shot_team_id) REFERENCES teams (id),
    FOREIGN KEY(shooter_id) REFERENCES players (id),
    FOREIGN KEY(assist1_id) REFERENCES players (id),
    FOREIGN KEY(assist2_id) REFERENCES players (id),
    CONSTRAINT ck_assist1_ref CHECK (NOT (assist1_id IS NOT NULL AND assist1_unknown = 1)),
    CONSTRAINT ck_assist2_ref CHECK (NOT (assist2_id IS NOT NULL AND assist2_unknown = 1)),
    CONSTRAINT ck_shot_xg_range CHECK (shot_xg IS NULL OR (shot_xg >= 0 AND shot_xg <= 1)),
    CONSTRAINT ck_shot_xg_only_for_shots_on_goal CHECK (shot_xg IS NULL OR shot_outcome IN ('goal', 'saved')),
    CONSTRAINT ck_shooter_ref CHECK (event_type != 'shot_attempt' OR ((shooter_id IS NULL) AND (shooter_unknown = 1)) OR ((shooter_id IS NOT NULL) AND (shooter_unknown = 0))),
    CONSTRAINT ck_shot_type_required CHECK (event_type != 'shot_attempt' OR shot_type IS NOT NULL),
    CONSTRAINT ck_shot_outcome_required CHECK (event_type != 'shot_attempt' OR shot_outcome IS NOT NULL),
    FOREIGN KEY(penalty_team_id) REFERENCES teams (id),
    FOREIGN KEY(penalty_player_id) REFERENCES players (id),
    CONSTRAINT ck_penalty_player_ref CHECK (event_type != 'penalty' OR ((penalty_player_id IS NULL) AND (penalty_player_unknown = 1)) OR ((penalty_player_id IS NOT NULL) AND (penalty_player_unknown = 0))),
    FOREIGN KEY(shift_team_id) REFERENCES teams (id),
    FOREIGN KEY(shift_player_id) REFERENCES players (id),
    CONSTRAINT ck_shift_player_ref CHECK (event_type != 'shift_change' OR ((shift_player_id IS NULL) AND (shift_player_unknown = 1)) OR ((shift_player_id IS NOT NULL) AND (shift_player_unknown = 0)))
)
"""

HOME, AWAY, OTHER = 1, 2, 3  # team ids
P1, P2 = 11, 12  # player ids
GAME = 1


@pytest.fixture
def v0_engine(tmp_path):
    """A version-0 database: every other table as today, `events` in its
    old shape, one game between HOME and AWAY (OTHER plays elsewhere)."""
    engine = create_sqlite_engine(tmp_path / "v0.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE events")
        conn.exec_driver_sql(_V0_EVENTS)
        conn.exec_driver_sql(
            "INSERT INTO teams (id, name, is_user_team) VALUES "
            f"({HOME}, 'Home', 1), ({AWAY}, 'Away', 0), ({OTHER}, 'Other', 0)"
        )
        conn.exec_driver_sql(
            f"INSERT INTO players (id, full_name) VALUES ({P1}, 'One'), ({P2}, 'Two')"
        )
        conn.exec_driver_sql(
            "INSERT INTO games (id, rink_type, opponent_shifts_complete, "
            f"home_team_id, away_team_id) VALUES ({GAME}, 'iihf', 0, {HOME}, {AWAY})"
        )
    return engine


def _insert_v0_faceoff(
    engine,
    *,
    team_a=None,
    player_a=None,
    team_b=None,
    player_b=None,
    winner=None,
) -> int:
    with engine.begin() as conn:
        result = conn.exec_driver_sql(
            "INSERT INTO events (game_id, event_type, video_timestamp, source, "
            "confirmed, faceoff_x, faceoff_y, faceoff_team_a_id, "
            "faceoff_participant_a_id, faceoff_participant_a_unknown, "
            "faceoff_team_b_id, faceoff_participant_b_id, "
            "faceoff_participant_b_unknown, faceoff_winner_team_id, shot_rush, "
            "shot_rebound, shot_screened, shot_one_timer, shooter_unknown, "
            "assist1_unknown, assist2_unknown, penalty_player_unknown, "
            "shift_player_unknown) "
            "VALUES (?, 'faceoff', 1000, 'manual', 0, 10.5, -3.0, ?, ?, ?, ?, ?, ?, "
            "?, 0, 0, 0, 0, 0, 0, 0, 0, 0)",
            (
                GAME,
                team_a,
                player_a,
                player_a is None,
                team_b,
                player_b,
                player_b is None,
                winner,
            ),
        )
        return result.lastrowid


def _migrate(engine) -> list[str]:
    return init_db(engine)


def _faceoff(engine, event_id) -> Faceoff:
    with make_session_factory(engine)() as session:
        return session.get(Faceoff, event_id)


def _participants(faceoff):
    """(home player, away player), each a player id or "unknown"."""
    return tuple(
        "unknown" if unknown else player_id
        for player_id, unknown in (
            (
                faceoff.faceoff_home_participant_id,
                faceoff.faceoff_home_participant_unknown,
            ),
            (
                faceoff.faceoff_away_participant_id,
                faceoff.faceoff_away_participant_unknown,
            ),
        )
    )


def test_it_is_the_first_shipped_migration():
    assert LATEST_VERSION == 1


def test_a_home_and_b_away_keep_their_sides(v0_engine):
    event_id = _insert_v0_faceoff(
        v0_engine, team_a=HOME, player_a=P1, team_b=AWAY, player_b=P2
    )

    messages = _migrate(v0_engine)

    assert _participants(_faceoff(v0_engine, event_id)) == (P1, P2)
    assert messages == []


def test_a_away_and_b_home_are_swapped(v0_engine):
    event_id = _insert_v0_faceoff(
        v0_engine, team_a=AWAY, player_a=P1, team_b=HOME, player_b=P2
    )

    _migrate(v0_engine)

    assert _participants(_faceoff(v0_engine, event_id)) == (P2, P1)


def test_only_a_team_set_keeps_its_side_and_b_takes_the_other(v0_engine):
    event_id = _insert_v0_faceoff(v0_engine, team_a=AWAY, player_a=P1, player_b=P2)

    _migrate(v0_engine)

    assert _participants(_faceoff(v0_engine, event_id)) == (P2, P1)


def test_only_b_team_set_keeps_its_side_and_a_takes_the_other(v0_engine):
    event_id = _insert_v0_faceoff(v0_engine, team_b=HOME, player_b=P2)

    _migrate(v0_engine)

    assert _participants(_faceoff(v0_engine, event_id)) == (P2, "unknown")


def test_a_team_from_neither_side_of_the_game_counts_as_not_set(v0_engine):
    event_id = _insert_v0_faceoff(
        v0_engine, team_a=OTHER, player_a=P1, team_b=HOME, player_b=P2
    )

    _migrate(v0_engine)

    assert _participants(_faceoff(v0_engine, event_id)) == (P2, P1)


def test_neither_team_set_makes_both_participants_unknown(v0_engine):
    event_id = _insert_v0_faceoff(v0_engine)

    _migrate(v0_engine)

    assert _participants(_faceoff(v0_engine, event_id)) == ("unknown", "unknown")


def test_both_on_the_same_team_resets_b_to_unknown_on_the_other_side(v0_engine):
    home_pair = _insert_v0_faceoff(
        v0_engine, team_a=HOME, player_a=P1, team_b=HOME, player_b=P2
    )
    away_pair = _insert_v0_faceoff(
        v0_engine, team_a=AWAY, player_a=P1, team_b=AWAY, player_b=P2
    )

    messages = _migrate(v0_engine)

    assert _participants(_faceoff(v0_engine, home_pair)) == (P1, "unknown")
    assert _participants(_faceoff(v0_engine, away_pair)) == ("unknown", P1)
    assert messages == [
        "2 faceoffs had both participants on the same team; "
        "the second participant was reset to unknown."
    ]


def test_the_same_team_message_reads_right_for_a_single_faceoff(v0_engine):
    _insert_v0_faceoff(v0_engine, team_a=HOME, player_a=P1, team_b=HOME, player_b=P2)

    messages = _migrate(v0_engine)

    assert messages == [
        "1 faceoff had both participants on the same team; "
        "the second participant was reset to unknown."
    ]


@pytest.mark.parametrize(
    ("winner_team", "winner"),
    [(HOME, Side.HOME), (AWAY, Side.AWAY), (OTHER, None), (None, None)],
)
def test_the_winner_team_maps_to_the_game_side_it_played(
    v0_engine, winner_team, winner
):
    event_id = _insert_v0_faceoff(
        v0_engine, team_a=HOME, player_a=P1, team_b=AWAY, winner=winner_team
    )

    _migrate(v0_engine)

    assert _faceoff(v0_engine, event_id).faceoff_winner == winner


def test_a_faceoff_in_a_game_with_no_sides_set_gets_unknown_participants(
    v0_engine,
):
    with v0_engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE games SET home_team_id = NULL, away_team_id = NULL"
        )
    event_id = _insert_v0_faceoff(
        v0_engine, team_a=HOME, player_a=P1, team_b=AWAY, player_b=P2, winner=HOME
    )

    _migrate(v0_engine)

    faceoff = _faceoff(v0_engine, event_id)
    assert _participants(faceoff) == ("unknown", "unknown")
    assert faceoff.faceoff_winner is None


def test_the_rest_of_every_event_is_carried_over(v0_engine):
    faceoff_id = _insert_v0_faceoff(
        v0_engine, team_a=HOME, player_a=P1, team_b=AWAY, player_b=P2
    )
    with v0_engine.begin() as conn:
        shot_id = conn.exec_driver_sql(
            "INSERT INTO events (game_id, event_type, video_timestamp, source, "
            "confirmed, strength_state, faceoff_participant_a_unknown, "
            "faceoff_participant_b_unknown, shot_x, shot_y, shot_team_id, "
            "shot_outcome, shot_type, shot_rush, shot_rebound, shot_screened, "
            "shot_one_timer, shot_xg, shooter_id, shooter_unknown, "
            "assist1_unknown, assist2_unknown, penalty_player_unknown, "
            "shift_player_unknown) VALUES (?, 'shot_attempt', 2000, 'vision', 1, "
            "'5v4', 0, 0, 50.0, 5.0, ?, 'goal', 'wrist', 1, 0, 1, 0, 0.25, ?, 0, "
            "0, 0, 0, 0)",
            (GAME, HOME, P1),
        ).lastrowid

    _migrate(v0_engine)

    faceoff = _faceoff(v0_engine, faceoff_id)
    assert (faceoff.video_timestamp, faceoff.faceoff_x, faceoff.faceoff_y) == (
        1000,
        10.5,
        -3.0,
    )
    with make_session_factory(v0_engine)() as session:
        shot = session.get(ShotAttempt, shot_id)
        assert shot.video_timestamp == 2000
        assert shot.confirmed is True
        assert shot.strength_state == "5v4"
        assert (shot.shot_team_id, shot.shooter_id, shot.shot_xg) == (HOME, P1, 0.25)
        assert (shot.shot_rush, shot.shot_screened) == (True, True)


def test_the_migrated_events_table_matches_a_freshly_created_one(v0_engine, tmp_path):
    _migrate(v0_engine)
    fresh = create_sqlite_engine(tmp_path / "fresh.db")
    init_db(fresh)

    def shape(engine):
        inspector = inspect(engine)
        return (
            [
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspector.get_columns("events")
            ],
            sorted(
                (fk["constrained_columns"], fk["referred_table"])
                for fk in inspector.get_foreign_keys("events")
            ),
            sorted(
                (check["name"], check["sqltext"])
                for check in inspector.get_check_constraints("events")
            ),
        )

    assert shape(v0_engine) == shape(fresh)
