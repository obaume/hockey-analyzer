"""`GameData`: everything `StatsEngine` needs about one tagged game, loaded
once up front so the engine itself stays a set of pure functions over
in-memory domain objects -- no session, no query, no file I/O inside it
(see ticket 18). `load_game_data` is the one place that reads it out of
the database."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from hockey_analyzer.domain.models import Event, Game, GameRosterEntry


@dataclass(frozen=True)
class GameData:
    """One game's snapshot: the `Game` row (sides, rink type,
    `opponent_shifts_complete`), its events in video order, and its roster
    entries with each entry's `Player` attached (for `Position`)."""

    game: Game
    events: Sequence[Event]
    roster: Sequence[GameRosterEntry]


def load_game_data(db_session: Session, game_id: int) -> GameData:
    game = db_session.get(Game, game_id)
    if game is None:
        raise KeyError(f"no game with id {game_id}")
    events = db_session.scalars(
        select(Event)
        .where(Event.game_id == game_id)
        .order_by(Event.video_timestamp, Event.id)
    )
    roster = db_session.scalars(
        select(GameRosterEntry)
        .where(GameRosterEntry.game_id == game_id)
        .options(selectinload(GameRosterEntry.player))
        .order_by(GameRosterEntry.team_id, GameRosterEntry.jersey_number)
    )
    return GameData(game=game, events=list(events), roster=list(roster))
