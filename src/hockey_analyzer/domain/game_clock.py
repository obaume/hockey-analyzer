"""Game clock derivation (see CONTEXT.md's Game clock entry): the period
number and time remaining a scoreboard would show at each event, derived
from the event timeline and never stored. Only live play -- from a
faceoff to the next event that stops play -- runs the clock down, so
stoppage gaps and intermissions drop out.

No period length is stored anywhere, so every period is assumed to be
`PERIOD_LENGTH_MS` long (regulation under both IIHF and NHL rules).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hockey_analyzer.domain.enums import ShotOutcome
from hockey_analyzer.domain.models import (
    Event,
    Faceoff,
    Penalty,
    PeriodEnd,
    PeriodStart,
    ShotAttempt,
    Stoppage,
)

PERIOD_LENGTH_MS = 20 * 60 * 1000


@dataclass(frozen=True)
class GameClock:
    period: int
    remaining_ms: int


def stops_play(event: Event) -> bool:
    """Whether play is dead right after `event`: a logged whistle, a
    period boundary, or an event that implies one (a goal, a penalty) --
    see CONTEXT.md's Game clock and Stoppage entries. Only a faceoff
    restarts it."""
    if isinstance(event, ShotAttempt):
        return event.shot_outcome is ShotOutcome.GOAL
    return isinstance(event, Stoppage | PeriodStart | PeriodEnd | Penalty)


def game_clocks(events: Sequence[Event]) -> dict[int, GameClock]:
    """The game clock at each event, by event id, read in log order (video
    timestamp, then id). An event is stamped with the clock as it stood
    when it happened, before its own effect (a goal's clock is the moment
    it went in). Events before the first `period_start` have no game
    clock and are left out. Periods are numbered by counting `period_start`
    events, not by `period_number` (which may be unset or mistyped) -- the
    same count StatsEngine's per-period stats use."""
    clocks: dict[int, GameClock] = {}
    period = 0
    elapsed = 0
    live_since: int | None = None
    for event in sorted(events, key=lambda event: (event.video_timestamp, event.id)):
        now = event.video_timestamp
        if isinstance(event, PeriodStart):
            period += 1
            elapsed, live_since = 0, None
        if period:
            running = elapsed + (now - live_since if live_since is not None else 0)
            clocks[event.id] = GameClock(period, max(0, PERIOD_LENGTH_MS - running))
        if live_since is not None and stops_play(event):
            elapsed += now - live_since
            live_since = None
        elif live_since is None and isinstance(event, Faceoff):
            live_since = now
    return clocks
