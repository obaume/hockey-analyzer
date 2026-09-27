"""The shipped schema migrations, in the order `db.MIGRATIONS` runs them
(see docs/adr/0012). Each is frozen once shipped: it spells out the DDL it
needs instead of reading it from the models, which keep moving."""

from __future__ import annotations

from sqlalchemy import Connection

# The `events` table as of schema version 1.
_EVENTS_V1 = """
CREATE TABLE events_v1 (
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
    faceoff_home_participant_id INTEGER,
    faceoff_home_participant_unknown BOOLEAN NOT NULL,
    faceoff_away_participant_id INTEGER,
    faceoff_away_participant_unknown BOOLEAN NOT NULL,
    faceoff_winner VARCHAR(4),
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
    FOREIGN KEY(faceoff_home_participant_id) REFERENCES players (id),
    FOREIGN KEY(faceoff_away_participant_id) REFERENCES players (id),
    CONSTRAINT ck_faceoff_home_participant_ref CHECK (event_type != 'faceoff' OR ((faceoff_home_participant_id IS NULL) AND (faceoff_home_participant_unknown = 1)) OR ((faceoff_home_participant_id IS NOT NULL) AND (faceoff_home_participant_unknown = 0))),
    CONSTRAINT ck_faceoff_away_participant_ref CHECK (event_type != 'faceoff' OR ((faceoff_away_participant_id IS NULL) AND (faceoff_away_participant_unknown = 1)) OR ((faceoff_away_participant_id IS NOT NULL) AND (faceoff_away_participant_unknown = 0))),
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

# Every `events` column version 0 and version 1 share, copied as-is.
_EVENTS_V1_CARRIED_COLUMNS = (
    "id, game_id, event_type, video_timestamp, source, confirmed, "
    "strength_state, period_number, faceoff_x, faceoff_y, shot_x, shot_y, "
    "shot_team_id, shot_outcome, shot_type, shot_rush, shot_rebound, "
    "shot_screened, shot_one_timer, shot_xg, shooter_id, shooter_unknown, "
    "assist1_id, assist1_unknown, assist2_id, assist2_unknown, "
    "penalty_team_id, penalty_player_id, penalty_player_unknown, "
    "penalty_duration_minutes, penalty_infraction, shift_team_id, "
    "shift_player_id, shift_player_unknown, shift_on_ice"
)

_OTHER_SIDE = {"home": "away", "away": "home"}
_UNKNOWN = (None, True)  # a participant as (player id, unknown flag)


def faceoff_home_away(conn: Connection) -> list[str]:
    """Version 1: a faceoff's participant A/B, each with a team of its own,
    becomes one home and one away participant, and its winner team a
    winning side (see CONTEXT.md's Faceoff entry). A participant keeps the
    side its team plays in the game; the other one takes the opposite side.
    With neither team on a side of the game, both become unknown; with both
    on the same side, B -- which can't be on that team too -- does."""
    rows = conn.exec_driver_sql(
        "SELECT e.id, e.faceoff_team_a_id, e.faceoff_participant_a_id, "
        "e.faceoff_participant_a_unknown, e.faceoff_team_b_id, "
        "e.faceoff_participant_b_id, e.faceoff_participant_b_unknown, "
        "e.faceoff_winner_team_id, g.home_team_id, g.away_team_id "
        "FROM events e JOIN games g ON g.id = e.game_id "
        "WHERE e.event_type = 'faceoff'"
    ).fetchall()

    converted = []
    same_team = 0
    for (
        event_id,
        team_a,
        player_a,
        unknown_a,
        team_b,
        player_b,
        unknown_b,
        winner_team,
        home_team,
        away_team,
    ) in rows:
        sides = (home_team, away_team)
        a, b = (player_a, bool(unknown_a)), (player_b, bool(unknown_b))
        side_a, side_b = _side_of(team_a, *sides), _side_of(team_b, *sides)
        if side_a is None and side_b is None:
            slots = {"home": _UNKNOWN, "away": _UNKNOWN}
        elif side_a == side_b:
            slots = {side_a: a, _OTHER_SIDE[side_a]: _UNKNOWN}
            same_team += 1
        elif side_a is not None:
            slots = {side_a: a, _OTHER_SIDE[side_a]: b}
        else:
            slots = {side_b: b, _OTHER_SIDE[side_b]: a}
        converted.append(
            (event_id, *slots["home"], *slots["away"], _side_of(winner_team, *sides))
        )

    # SQLite can't drop a column a constraint names, so the table is
    # rebuilt: new shape, rows copied across, then swapped in.
    conn.exec_driver_sql(_EVENTS_V1)
    conn.exec_driver_sql(
        "CREATE TEMP TABLE faceoff_v1 (id INTEGER PRIMARY KEY, home_id INTEGER, "
        "home_unknown BOOLEAN, away_id INTEGER, away_unknown BOOLEAN, "
        "winner VARCHAR(4))"
    )
    if converted:
        conn.exec_driver_sql(
            "INSERT INTO faceoff_v1 VALUES (?, ?, ?, ?, ?, ?)", converted
        )
    carried = ", ".join(
        f"e.{column.strip()}" for column in _EVENTS_V1_CARRIED_COLUMNS.split(",")
    )
    conn.exec_driver_sql(
        f"INSERT INTO events_v1 ({_EVENTS_V1_CARRIED_COLUMNS}, "
        "faceoff_home_participant_id, faceoff_home_participant_unknown, "
        "faceoff_away_participant_id, faceoff_away_participant_unknown, "
        f"faceoff_winner) SELECT {carried}, f.home_id, "
        "COALESCE(f.home_unknown, 0), f.away_id, COALESCE(f.away_unknown, 0), "
        "f.winner FROM events e LEFT JOIN faceoff_v1 f ON f.id = e.id"
    )
    conn.exec_driver_sql("DROP TABLE faceoff_v1")
    conn.exec_driver_sql("DROP TABLE events")
    conn.exec_driver_sql("ALTER TABLE events_v1 RENAME TO events")

    if not same_team:
        return []
    faceoffs = "faceoff" if same_team == 1 else "faceoffs"
    return [
        f"{same_team} {faceoffs} had both participants on the same team; "
        "the second participant was reset to unknown."
    ]


def _side_of(
    team_id: int | None, home_team: int | None, away_team: int | None
) -> str | None:
    """The side `team_id` plays in its game, or None -- for no team, or a
    team on neither side (the game's sides were changed since)."""
    if team_id is None:
        return None
    if team_id == home_team:
        return "home"
    if team_id == away_team:
        return "away"
    return None
