"""StatsEngine (ticket 18): pure query functions turning one tagged game's
`GameData` into the stats CONTEXT.md defines -- Corsi/Fenwick, PDO, zone
starts, +/-, and goalie SV%/GAA/HD SV%. No GUI, database, or file-I/O
dependency: domain objects in, frozen result objects out.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, NamedTuple

from hockey_analyzer.domain import rink
from hockey_analyzer.domain.enums import Position, ShotOutcome, ShotType
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import (
    Event,
    Faceoff,
    GameRosterEntry,
    Penalty,
    PeriodEnd,
    PeriodStart,
    ShiftChange,
    ShotAttempt,
    Stoppage,
)

# The default strength-state filter for every stat except goalie stats
# (see CONTEXT.md's Corsi / Fenwick and Goalie stats entries). `None`
# anywhere a `strength_state` filter is accepted means all situations.
EVEN_STRENGTH = "5v5"
ALL_SITUATIONS = None

_ON_GOAL = frozenset({ShotOutcome.GOAL, ShotOutcome.SAVED})


@dataclass(frozen=True)
class ForAgainst:
    """The four derived quantities Corsi/Fenwick are reported as."""

    for_: int
    against: int

    @property
    def differential(self) -> int:
        return self.for_ - self.against

    @property
    def percentage(self) -> float | None:
        return _ratio(self.for_, self.for_ + self.against)


SHOT_CONTEXTS = ("rush", "rebound", "screened", "one_timer")


@dataclass(frozen=True)
class ShotQuality:
    """One team's shot attempts broken down by `ShotType`, by each shot
    context flag (independent, so they don't sum to `attempts`), and by
    danger zone. `high_danger_share` is over `located` attempts only --
    one never clicked on the rink can't be classified either way."""

    attempts: int
    by_type: dict[ShotType, int]
    by_context: dict[str, int]
    high_danger: int
    located: int

    @property
    def high_danger_share(self) -> float | None:
        return _ratio(self.high_danger, self.located)


@dataclass(frozen=True)
class TeamStats:
    team_id: int
    corsi: ForAgainst
    fenwick: ForAgainst
    # Goals / shots-on-goal, for and against: PDO's population (see
    # CONTEXT.md's PDO entry), narrower than Corsi/Fenwick's.
    goals: ForAgainst
    shots_on_goal: ForAgainst
    shot_quality: ShotQuality

    @property
    def shooting_percentage(self) -> float | None:
        return _ratio(self.goals.for_, self.shots_on_goal.for_)

    @property
    def save_percentage(self) -> float | None:
        goals_against = _ratio(self.goals.against, self.shots_on_goal.against)
        return None if goals_against is None else 1 - goals_against

    @property
    def pdo(self) -> float | None:
        """`(shooting% + save%) x 1000`; undefined until both sides have
        at least one shot on goal."""
        if self.shooting_percentage is None or self.save_percentage is None:
            return None
        return (self.shooting_percentage + self.save_percentage) * 1000


def team_stats(
    data: GameData, team_id: int, *, strength_state: str | None = EVEN_STRENGTH
) -> TeamStats:
    shots = _shots(data, strength_state)
    return TeamStats(
        team_id=team_id,
        corsi=_for_against(shots, team_id),
        fenwick=_for_against(_unblocked(shots), team_id),
        goals=_for_against(_goals(shots), team_id),
        shots_on_goal=_for_against(_on_goal(shots), team_id),
        shot_quality=_shot_quality(
            data, [shot for shot in shots if shot.shot_team_id == team_id]
        ),
    )


def _shot_quality(data: GameData, shots: list[ShotAttempt]) -> ShotQuality:
    by_type: dict[ShotType, int] = {}
    for shot in shots:
        by_type[shot.shot_type] = by_type.get(shot.shot_type, 0) + 1
    return ShotQuality(
        attempts=len(shots),
        by_type=by_type,
        by_context={
            context: sum(1 for shot in shots if getattr(shot, f"shot_{context}"))
            for context in SHOT_CONTEXTS
        },
        high_danger=sum(1 for shot in shots if _is_high_danger(data, shot)),
        located=sum(1 for shot in shots if _is_located(shot)),
    )


@dataclass(frozen=True)
class ZoneStarts:
    """Faceoff-anchored shift starts by zone (see CONTEXT.md's Zone start
    entry). On-the-fly and neutral-zone starts aren't counted anywhere;
    `undetermined` counts anchored starts whose zone couldn't be resolved
    (faceoff location unset, or no way to tell which end the team was
    attacking that period) -- reported, but outside the percentage."""

    offensive: int = 0
    defensive: int = 0
    undetermined: int = 0

    @property
    def percentage(self) -> float | None:
        return _ratio(self.offensive, self.offensive + self.defensive)


@dataclass(frozen=True)
class SkaterStats:
    """One rostered skater's individual on-ice stats for the game."""

    player_id: int
    team_id: int
    jersey_number: int
    corsi: ForAgainst
    fenwick: ForAgainst
    # On-ice goals for/against, at the active strength filter.
    goals: ForAgainst
    zone_starts: ZoneStarts

    @property
    def plus_minus(self) -> int:
        return self.goals.differential


@dataclass(frozen=True)
class ExcludedSkater:
    """A rostered skater whose on-ice stats weren't computed at all, and
    why -- reported rather than silently omitted."""

    player_id: int
    team_id: int
    jersey_number: int
    reason: str


@dataclass(frozen=True)
class SkaterReport:
    skaters: list[SkaterStats]
    excluded: list[ExcludedSkater]


OPPONENT_SHIFTS_INCOMPLETE = "opponent shifts not marked complete for this game"


def skater_stats(
    data: GameData, *, strength_state: str | None = EVEN_STRENGTH
) -> SkaterReport:
    """Every rostered non-goalie's on-ice stats, attributing each shot
    attempt to whoever their team's `shift_change` events put on the ice
    at that moment. The away team's skaters are excluded (and listed as
    such) unless `Game.opponent_shifts_complete` is set -- see ADR-0002."""
    shots = _shots(data, strength_state)
    on_ice = _on_ice_at_shots(data)
    zone_starts = _zone_starts(data, strength_state)
    skaters: list[SkaterStats] = []
    excluded: list[ExcludedSkater] = []
    for entry in data.roster:
        if _is_goalie(entry):
            continue
        if not _on_ice_eligible(data, entry.team_id):
            excluded.append(
                ExcludedSkater(
                    player_id=entry.player_id,
                    team_id=entry.team_id,
                    jersey_number=entry.jersey_number,
                    reason=OPPONENT_SHIFTS_INCOMPLETE,
                )
            )
            continue
        on_ice_shots = [shot for shot in shots if entry.player_id in on_ice[shot.id]]
        skaters.append(
            SkaterStats(
                player_id=entry.player_id,
                team_id=entry.team_id,
                jersey_number=entry.jersey_number,
                corsi=_for_against(on_ice_shots, entry.team_id),
                fenwick=_for_against(_unblocked(on_ice_shots), entry.team_id),
                goals=_for_against(_goals(on_ice_shots), entry.team_id),
                zone_starts=zone_starts.get(entry.player_id, ZoneStarts()),
            )
        )
    return SkaterReport(skaters=skaters, excluded=excluded)


@dataclass(frozen=True)
class GoalieStats:
    """One rostered goalie's stats for the game, over the shots on goal
    (`goal`/`saved`) the other team took while `shift_change` events had
    this goalie in net (see CONTEXT.md's Goalie stats entry)."""

    player_id: int
    team_id: int
    jersey_number: int
    shots_against: int
    goals_against: int
    high_danger_shots_against: int
    high_danger_goals_against: int
    # Derived game-clock time in net (live play only), not raw video time.
    time_in_net_ms: int

    @property
    def saves(self) -> int:
        return self.shots_against - self.goals_against

    @property
    def save_percentage(self) -> float | None:
        return _ratio(self.saves, self.shots_against)

    @property
    def high_danger_saves(self) -> int:
        return self.high_danger_shots_against - self.high_danger_goals_against

    @property
    def high_danger_save_percentage(self) -> float | None:
        return _ratio(self.high_danger_saves, self.high_danger_shots_against)

    @property
    def minutes_played(self) -> float:
        return self.time_in_net_ms / 60_000

    @property
    def goals_against_average(self) -> float | None:
        if not self.time_in_net_ms:
            return None
        return self.goals_against * 60 / self.minutes_played


def goalie_stats(
    data: GameData, *, strength_state: str | None = ALL_SITUATIONS
) -> list[GoalieStats]:
    """Every rostered goalie's SV%/GAA/HD SV%, defaulting to all
    situations -- unlike every other stat here -- since that's how these
    are conventionally reported. Not gated by `opponent_shifts_complete`
    (ticket 18 gates only on-ice skater stats)."""
    on_goal = _on_goal(_shots(data, strength_state))
    on_ice = _on_ice_at_shots(data)
    intervals = _on_ice_intervals(data)
    live = _live_segments(data)
    results = []
    for entry in data.roster:
        if not _is_goalie(entry):
            continue
        faced = [
            shot
            for shot in on_goal
            if shot.shot_team_id != entry.team_id and entry.player_id in on_ice[shot.id]
        ]
        high_danger = [shot for shot in faced if _is_high_danger(data, shot)]
        results.append(
            GoalieStats(
                player_id=entry.player_id,
                team_id=entry.team_id,
                jersey_number=entry.jersey_number,
                shots_against=len(faced),
                goals_against=len(_goals(faced)),
                high_danger_shots_against=len(high_danger),
                high_danger_goals_against=len(_goals(high_danger)),
                time_in_net_ms=_time_on_ice(
                    intervals.get(entry.player_id, []), live, strength_state
                ),
            )
        )
    return results


def _is_high_danger(data: GameData, shot: ShotAttempt) -> bool:
    """See CONTEXT.md's Danger zone entry. A shot whose location was never
    clicked can't be classified, so it isn't counted as high danger."""
    return _is_located(shot) and rink.high_danger(
        shot.shot_x, shot.shot_y, data.game.rink_type
    )


def _is_located(shot: ShotAttempt) -> bool:
    """Whether the tagger clicked this shot's rink location."""
    return shot.shot_x is not None and shot.shot_y is not None


Interval = tuple[int, int]


def _game_end(timeline: list[Event]) -> int:
    return timeline[-1].video_timestamp if timeline else 0


def _on_ice_intervals(data: GameData) -> dict[int, list[Interval]]:
    """Each known player's on-ice video intervals, from their own
    `shift_change` events; a shift still open at the end of the log closes
    at the game's last event."""
    timeline = _timeline(data)
    started: dict[int, int] = {}
    intervals: dict[int, list[Interval]] = {}
    for event in timeline:
        if not isinstance(event, ShiftChange) or event.shift_player_id is None:
            continue
        player_id = event.shift_player_id
        if event.shift_on_ice:
            started.setdefault(player_id, event.video_timestamp)
        elif event.shift_on_ice is not None and player_id in started:
            start = started.pop(player_id)
            intervals.setdefault(player_id, []).append((start, event.video_timestamp))
    end = _game_end(timeline)
    for player_id, start in started.items():
        intervals.setdefault(player_id, []).append((start, end))
    return intervals


class _LiveSegment(NamedTuple):
    start: int
    end: int
    strength_state: str | None


def _live_segments(data: GameData) -> list[_LiveSegment]:
    """The game clock's running stretches: from each faceoff to the next
    event that stops play (see `_stops_play`), so stoppage gaps and
    intermissions drop out -- CONTEXT.md's Game clock derivation. Each
    stretch is further cut wherever a logged event records a different
    strength state (e.g. a penalty expiring on the fly), so a filtered
    GAA divides by minutes at that strength only. `shift_change` events
    don't cut: theirs is only the tagging UI's mid-change headcount."""
    timeline = _timeline(data)
    segments: list[_LiveSegment] = []
    live_since: int | None = None
    strength: str | None = None
    for event in timeline:
        if isinstance(event, Faceoff) and live_since is None:
            live_since, strength = event.video_timestamp, event.strength_state
        elif live_since is None:
            continue
        elif _stops_play(event):
            segments.append(_LiveSegment(live_since, event.video_timestamp, strength))
            live_since = None
        elif (
            not isinstance(event, ShiftChange)
            and event.strength_state is not None
            and event.strength_state != strength
        ):
            segments.append(_LiveSegment(live_since, event.video_timestamp, strength))
            live_since, strength = event.video_timestamp, event.strength_state
    if live_since is not None:
        segments.append(_LiveSegment(live_since, _game_end(timeline), strength))
    return segments


def _time_on_ice(
    intervals: list[Interval],
    segments: list[_LiveSegment],
    strength_state: str | None,
) -> int:
    return sum(
        max(0, min(end, segment.end) - max(start, segment.start))
        for start, end in intervals
        for segment in segments
        if _matches(segment.strength_state, strength_state)
    )


def _on_ice_eligible(data: GameData, team_id: int) -> bool:
    """Home-team shift tracking is always assumed complete; the visiting
    team's only once the game says so (see CONTEXT.md's Opponent shifts
    complete entry)."""
    return team_id != data.game.away_team_id or bool(data.game.opponent_shifts_complete)


def unresolved_shift_changes(data: GameData) -> dict[int, int]:
    """Per team, how many `shift_change` events name an unknown player or
    leave on/off unset. A known player's on-ice set comes only from their
    own events, so their stats are unaffected; what's missing is that
    stretch of ice time for whoever the unresolved event really was -- a
    gap in the team's individual stats as a whole, reported here once per
    team rather than silently dropped (see CONTEXT.md's Unknown player
    reference entry). Team-wide stats never depend on it."""
    counts: dict[int, int] = {}
    for event in data.events:
        if isinstance(event, ShiftChange) and event.shift_team_id is not None:
            if event.shift_player_id is None or event.shift_on_ice is None:
                counts[event.shift_team_id] = counts.get(event.shift_team_id, 0) + 1
    return counts


def _is_goalie(entry: GameRosterEntry) -> bool:
    return entry.player is not None and entry.player.position is Position.GOALIE


def _timeline(data: GameData) -> list[Event]:
    """The game's events in log order: by video timestamp, tiebroken by
    insertion order -- the same order the event log shows, so a shift
    change logged at the same instant as a shot, but after it, doesn't
    apply to it."""
    return sorted(data.events, key=lambda event: (event.video_timestamp, event.id))


def _on_ice_at_shots(data: GameData) -> dict[int, frozenset[int]]:
    """For every shot attempt, by event id: the known players (either
    team) that `shift_change` events had on the ice at that moment."""
    on_ice: set[int] = set()
    snapshots: dict[int, frozenset[int]] = {}
    for event in _timeline(data):
        if isinstance(event, ShiftChange) and event.shift_player_id is not None:
            if event.shift_on_ice:
                on_ice.add(event.shift_player_id)
            elif event.shift_on_ice is not None:
                on_ice.discard(event.shift_player_id)
        elif isinstance(event, ShotAttempt):
            snapshots[event.id] = frozenset(on_ice)
    return snapshots


def _stops_play(event: Event) -> bool:
    """Whether play is dead right after `event`: a logged whistle, a
    period boundary, or an event that implies one (a goal, a penalty) --
    see CONTEXT.md's Game clock and Stoppage entries. Only a faceoff
    restarts it."""
    if isinstance(event, ShotAttempt):
        return event.shot_outcome is ShotOutcome.GOAL
    return isinstance(event, Stoppage | PeriodStart | PeriodEnd | Penalty)


def _with_periods(timeline: list[Event]) -> Iterator[tuple[int, Event]]:
    """Each event paired with which period it falls in, counted by the
    `period_start` events seen so far (0 before the first one) -- robust
    to a period_start whose number was never filled in."""
    period = 0
    for event in timeline:
        if isinstance(event, PeriodStart):
            period += 1
        yield period, event


Direction = Literal[1, -1]


def _attacking_directions(
    data: GameData, timeline: list[Event]
) -> dict[tuple[int, int], Direction]:
    """Which end (+1 = toward positive x) each team attacks in each
    period. Nothing stores this -- teams switch ends every period and the
    tagger clicks raw rink coordinates -- so it's derived from where each
    team's own shot attempts land that period (nearly all are taken in the
    offensive half), by majority. A team with no majority that period (no
    located shots, or a tie) attacks the end opposite the other team's;
    with no majority from either, it stays unknown. A shot on the center
    line (x == 0) points at neither end and doesn't vote."""
    votes: dict[tuple[int, int], int] = {}
    for period, event in _with_periods(timeline):
        if (
            isinstance(event, ShotAttempt)
            and event.shot_team_id is not None
            and _is_located(event)
            and event.shot_x != 0
        ):
            key = (event.shot_team_id, period)
            votes[key] = votes.get(key, 0) + (1 if event.shot_x > 0 else -1)

    sides = (data.game.home_team_id, data.game.away_team_id)
    periods = {period for _, period in votes}
    directions: dict[tuple[int, int], Direction] = {}
    for period in periods:
        for team_id, other_id in (sides, sides[::-1]):
            own = votes.get((team_id, period), 0)
            other = votes.get((other_id, period), 0)
            if own:
                directions[(team_id, period)] = 1 if own > 0 else -1
            elif other:
                directions[(team_id, period)] = -1 if other > 0 else 1
    return directions


def _zone_starts(data: GameData, strength_state: str | None) -> dict[int, ZoneStarts]:
    """Faceoff-anchored shift starts per player. A shift is anchored when
    it begins while play is dead (see `_stops_play`; also before the
    game's first faceoff) and the player is still on when the next faceoff
    restarts play -- or when it begins at the very instant of the faceoff
    that just restarted play, logged after it. Any other start is on the
    fly and isn't counted at all. The anchoring faceoff's own strength
    state is what the filter applies to."""
    timeline = _timeline(data)
    directions = _attacking_directions(data, timeline)
    tallies: dict[int, dict[str, int]] = {}

    def anchor(player_id: int, team_id: int, faceoff: Faceoff, period: int) -> None:
        if not _matches(faceoff.strength_state, strength_state):
            return
        direction = directions.get((team_id, period))
        if faceoff.faceoff_x is None or direction is None:
            zone = "undetermined"
        else:
            zone = rink.zone(faceoff.faceoff_x, direction, data.game.rink_type)
        tally = tallies.setdefault(player_id, {})
        tally[zone] = tally.get(zone, 0) + 1

    play_stopped = True
    last_faceoff: Faceoff | None = None
    on_ice: set[int] = set()
    pending: dict[int, int] = {}  # player id -> team id, awaiting a faceoff
    for period, event in _with_periods(timeline):
        if isinstance(event, ShiftChange):
            player_id, team_id = event.shift_player_id, event.shift_team_id
            if player_id is None or team_id is None or event.shift_on_ice is None:
                continue
            if not event.shift_on_ice:
                on_ice.discard(player_id)
                pending.pop(player_id, None)
            elif player_id not in on_ice:
                on_ice.add(player_id)
                if play_stopped:
                    pending[player_id] = team_id
                elif (
                    last_faceoff is not None
                    and last_faceoff.video_timestamp == event.video_timestamp
                ):
                    anchor(player_id, team_id, last_faceoff, period)
        elif isinstance(event, Faceoff):
            for player_id, team_id in pending.items():
                anchor(player_id, team_id, event, period)
            pending.clear()
            play_stopped = False
            last_faceoff = event
        elif _stops_play(event):
            play_stopped = True

    return {
        player_id: ZoneStarts(
            offensive=tally.get("offensive", 0),
            defensive=tally.get("defensive", 0),
            undetermined=tally.get("undetermined", 0),
        )
        for player_id, tally in tallies.items()
    }


def _shots(data: GameData, strength_state: str | None) -> list[ShotAttempt]:
    """Every attributed shot attempt at `strength_state` (all situations
    for `None`), in video order."""
    return [
        event
        for event in data.events
        if isinstance(event, ShotAttempt)
        and event.shot_team_id is not None
        and _matches(event.strength_state, strength_state)
    ]


def _matches(event_strength: str | None, strength_state: str | None) -> bool:
    return strength_state is None or event_strength == strength_state


def _unblocked(shots: list[ShotAttempt]) -> list[ShotAttempt]:
    return [shot for shot in shots if shot.shot_outcome is not ShotOutcome.BLOCKED]


def _on_goal(shots: list[ShotAttempt]) -> list[ShotAttempt]:
    return [shot for shot in shots if shot.shot_outcome in _ON_GOAL]


def _goals(shots: list[ShotAttempt]) -> list[ShotAttempt]:
    return [shot for shot in shots if shot.shot_outcome is ShotOutcome.GOAL]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _for_against(shots: list[ShotAttempt], team_id: int) -> ForAgainst:
    for_ = sum(1 for shot in shots if shot.shot_team_id == team_id)
    return ForAgainst(for_=for_, against=len(shots) - for_)
