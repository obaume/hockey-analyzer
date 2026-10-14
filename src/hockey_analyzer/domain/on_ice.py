"""On-ice sets (see CONTEXT.md's On-ice set entry): who each team has on
the ice at a given video timestamp, derived from its `shift_change`
events and never stored. Read by the tagging strength-state default (its
skater counts) and by the on-ice dock (the players themselves)."""

from __future__ import annotations

from dataclasses import dataclass

from hockey_analyzer.domain.enums import Position
from hockey_analyzer.domain.game_clock import live_intervals
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import GameRosterEntry, ShiftChange

# Well past a normal 40-60 s shift: a player still on after this much
# live play most likely has a missed OFF tag.
LONG_SHIFT_MS = 2 * 60 * 1000

# Skaters read the way a line is called: left wing, centre, right wing,
# then the defence; a skater with no recorded position comes last.
_POSITION_ORDER = {
    Position.LEFT_WING: 0,
    Position.CENTER: 1,
    Position.RIGHT_WING: 2,
    Position.DEFENSE: 3,
}


@dataclass(frozen=True)
class OnIcePlayer:
    """`jersey_number`/`full_name`/`position` are `None` for a player with
    no roster entry in this game. `shift_ms` is the live play (game-clock
    time, stoppages excluded) since their current shift began."""

    player_id: int
    jersey_number: int | None
    full_name: str | None
    position: Position | None
    shift_ms: int

    @property
    def long_shift(self) -> bool:
        return self.shift_ms > LONG_SHIFT_MS


@dataclass(frozen=True)
class OnIceSet:
    """`goalies` is normally one or none; more than one is a missed OFF
    tag, kept visible rather than hidden. `unresolved_shift_changes`
    counts the team's shift changes so far with an unknown player or no
    on/off -- ones that can't place anyone, reported rather than silently
    dropped (see CONTEXT.md's Unknown player reference entry)."""

    skaters: tuple[OnIcePlayer, ...] = ()
    goalies: tuple[OnIcePlayer, ...] = ()
    unresolved_shift_changes: int = 0


def on_ice_sets(data: GameData, at_ms: int) -> dict[int, OnIceSet]:
    """Each side's on-ice set at `at_ms`, by team id, counting every shift
    change at or before it in log order (video timestamp, then id) -- the
    same order StatsEngine attributes on-ice stats in. Both of the game's
    sides always get one, even if empty, as does any other team a shift
    change names."""
    shift_starts: dict[int, dict[int, int]] = {
        team_id: {}
        for team_id in (data.game.home_team_id, data.game.away_team_id)
        if team_id is not None
    }
    unresolved = dict.fromkeys(shift_starts, 0)
    timeline = sorted(data.events, key=lambda event: (event.video_timestamp, event.id))
    for event in timeline:
        if event.video_timestamp > at_ms:
            break
        if not isinstance(event, ShiftChange) or event.shift_team_id is None:
            continue
        team_id = event.shift_team_id
        starts = shift_starts.setdefault(team_id, {})
        player_id = event.shift_player_id
        if player_id is None or event.shift_on_ice is None:
            unresolved[team_id] = unresolved.get(team_id, 0) + 1
        elif event.shift_on_ice:
            starts.setdefault(player_id, event.video_timestamp)
        else:
            starts.pop(player_id, None)

    live = live_intervals(timeline, at_ms)
    entries = {entry.player_id: entry for entry in data.roster}
    sets: dict[int, OnIceSet] = {}
    for team_id, starts in shift_starts.items():
        players = [
            _on_ice_player(player_id, entries.get(player_id), start, live)
            for player_id, start in starts.items()
        ]
        sets[team_id] = OnIceSet(
            skaters=tuple(
                sorted(
                    (p for p in players if p.position is not Position.GOALIE),
                    key=_skater_order,
                )
            ),
            goalies=tuple(
                sorted(
                    (p for p in players if p.position is Position.GOALIE),
                    key=_jersey_order,
                )
            ),
            unresolved_shift_changes=unresolved.get(team_id, 0),
        )
    return sets


def _on_ice_player(
    player_id: int,
    entry: GameRosterEntry | None,
    shift_start: int,
    live: list[tuple[int, int]],
) -> OnIcePlayer:
    player = entry.player if entry is not None else None
    return OnIcePlayer(
        player_id=player_id,
        jersey_number=entry.jersey_number if entry is not None else None,
        full_name=player.full_name if player is not None else None,
        position=player.position if player is not None else None,
        shift_ms=sum(
            max(0, end - max(start, shift_start))
            for start, end in live
            if end > shift_start
        ),
    )


def _jersey_order(player: OnIcePlayer) -> tuple[bool, int]:
    return (player.jersey_number is None, player.jersey_number or 0)


def _skater_order(player: OnIcePlayer) -> tuple[int, bool, int]:
    return (
        _POSITION_ORDER.get(player.position, len(_POSITION_ORDER)),
        *_jersey_order(player),
    )
