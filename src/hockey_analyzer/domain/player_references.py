"""Which of an event's fields name a `Player` (see CONTEXT.md's Unknown
player reference entry for the full list): the shooter and both assists,
both faceoff participants, the penalized player, the shift participant.
Stated once here so everything that asks "which players does this event
name" -- clip export's player filter, roster cleanup (#89) -- reads the
same list. Only the `*_id` columns: the companion `*_unknown` flags name
no one.
"""

from __future__ import annotations

from hockey_analyzer.domain.models import (
    Event,
    Faceoff,
    Penalty,
    ShiftChange,
    ShotAttempt,
)

PLAYER_REFERENCE_COLUMNS: dict[type[Event], tuple[str, ...]] = {
    ShotAttempt: ("shooter_id", "assist1_id", "assist2_id"),
    Faceoff: ("faceoff_home_participant_id", "faceoff_away_participant_id"),
    Penalty: ("penalty_player_id",),
    ShiftChange: ("shift_player_id",),
}


def involved_players(event: Event) -> frozenset[int]:
    """Every known player an event names, whatever the role."""
    columns = PLAYER_REFERENCE_COLUMNS.get(type(event), ())
    return frozenset(
        player_id
        for player_id in (getattr(event, column) for column in columns)
        if player_id is not None
    )


def replace_player(event: Event, old_player_id: int, new_player_id: int) -> None:
    """Point every field of `event` that names `old_player_id` at
    `new_player_id` instead."""
    for column in PLAYER_REFERENCE_COLUMNS.get(type(event), ()):
        if getattr(event, column) == old_player_id:
            setattr(event, column, new_player_id)
