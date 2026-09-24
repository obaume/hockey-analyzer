"""Keeps `Game.updated_at` tracking "most recently worked on", not just
"most recently edited at the GameSetupService level" -- so the "Select
Game" picker (see CONTEXT.md's Game entry) can surface the game a tagger
was just actively logging events/roster entries against, which is the
overwhelmingly dominant activity once a game exists.

This is domain *behavior* (a cross-cutting business rule about what counts
as "activity" on a Game), not schema, so it's kept out of models.py --
matching this codebase's existing split of "domain logic lives in a
service" (see game_setup.py's module docstring). It's registered as a
session-level hook rather than touched manually at each
`TaggingSession`/`GameSetupService` call site specifically so no write
path can forget to do it -- db.py imports this module for that
registration side effect (see its own comment).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session

from hockey_analyzer.domain.models import Event, Game, GameRosterEntry

_last_touch: datetime | None = None


def _next_touch_timestamp() -> datetime:
    """A wall-clock timestamp, nudged forward by a microsecond whenever it
    would otherwise tie or go backwards relative to the previous call.
    Plain `datetime.now(timezone.utc)` calls made in quick succession
    (e.g. two `Game`s created back-to-back, well within a test or a fast
    tagging burst) can land on the same value at some platforms' clock
    resolution -- which would make `GameSetupService.list_games`'
    ordering fall back on arbitrary tie-breaking instead of "whichever
    actually happened more recently"."""
    global _last_touch
    now = datetime.now(UTC)
    if _last_touch is not None and now <= _last_touch:
        now = _last_touch + timedelta(microseconds=1)
    _last_touch = now
    return now


@event.listens_for(Session, "before_flush")
def _touch_game_updated_at(session: Session, flush_context, instances) -> None:
    now = _next_touch_timestamp()
    touched_game_ids: set[int] = set()
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, Game):
            obj.updated_at = now
        elif isinstance(obj, (Event, GameRosterEntry)):
            touched_game_ids.add(obj.game_id)
    for game_id in touched_game_ids:
        game = session.get(Game, game_id)
        if game is not None:
            game.updated_at = now
