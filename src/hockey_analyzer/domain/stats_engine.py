"""StatsEngine (tickets 18, 19): pure query functions turning tagged games'
`GameData` into the stats CONTEXT.md defines -- Corsi/Fenwick, PDO, zone
starts, +/-, goalie SV%/GAA/HD SV% (ticket 18), plus per-position rollups,
line/unit stats, and `combined_*` sum-then-compute aggregates over a
hand-picked set of games (ticket 19). No GUI, database, or file-I/O
dependency: domain objects in, frozen result objects out.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Callable, Hashable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple, Protocol, Self, TypeVar

from hockey_analyzer.domain import rink
from hockey_analyzer.domain.enums import Position, ShotOutcome, ShotType, UnitType
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

    def __add__(self, other: ForAgainst) -> ForAgainst:
        return ForAgainst(self.for_ + other.for_, self.against + other.against)


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

    def __add__(self, other: ShotQuality) -> ShotQuality:
        return ShotQuality(
            attempts=self.attempts + other.attempts,
            by_type=_add_counts(self.by_type, other.by_type),
            by_context=_add_counts(self.by_context, other.by_context),
            high_danger=self.high_danger + other.high_danger,
            located=self.located + other.located,
        )


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

    def __add__(self, other: TeamStats) -> TeamStats:
        return TeamStats(
            team_id=self.team_id,
            corsi=self.corsi + other.corsi,
            fenwick=self.fenwick + other.fenwick,
            goals=self.goals + other.goals,
            shots_on_goal=self.shots_on_goal + other.shots_on_goal,
            shot_quality=self.shot_quality + other.shot_quality,
        )


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

    def __add__(self, other: ZoneStarts) -> ZoneStarts:
        return ZoneStarts(
            offensive=self.offensive + other.offensive,
            defensive=self.defensive + other.defensive,
            undetermined=self.undetermined + other.undetermined,
        )


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
    # `Player.position` (None when never set) -- for position rollups.
    position: Position | None = None

    @property
    def plus_minus(self) -> int:
        return self.goals.differential

    def __add__(self, other: SkaterStats) -> SkaterStats:
        """The same skater's stats over both games; jersey number and
        position as of `other`, the later one."""
        return SkaterStats(
            player_id=self.player_id,
            team_id=self.team_id,
            jersey_number=other.jersey_number,
            corsi=self.corsi + other.corsi,
            fenwick=self.fenwick + other.fenwick,
            goals=self.goals + other.goals,
            zone_starts=self.zone_starts + other.zone_starts,
            position=other.position,
        )


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
                position=entry.player.position if entry.player else None,
            )
        )
    return SkaterReport(skaters=skaters, excluded=excluded)


@dataclass(frozen=True)
class PositionStats:
    """One team's skaters at one `Position` (None: position never set),
    their individual stats pooled -- counts summed, then percentages
    computed from the sums, the same sum-then-compute rule multi-game
    aggregation follows. `per_skater` turns a pooled count into the
    position's per-skater average (e.g. average CF among defensemen)."""

    team_id: int
    position: Position | None
    skaters: int
    corsi: ForAgainst
    fenwick: ForAgainst
    goals: ForAgainst
    zone_starts: ZoneStarts

    @property
    def plus_minus(self) -> int:
        return self.goals.differential

    def per_skater(self, total: int) -> float | None:
        return _ratio(total, self.skaters)


def position_rollup(report: SkaterReport) -> list[PositionStats]:
    """Any `skater_stats` report -- one game's or several games' combined
    -- grouped by team and `Player.position` (see CONTEXT.md's Position
    entry), in first-seen order. Per team, since pooling both sides'
    defensemen would cancel one team's shots for against the other's.
    Excluded skaters have no stats to pool and stay out."""
    groups: dict[tuple[int, Position | None], list[SkaterStats]] = {}
    for stats in report.skaters:
        groups.setdefault((stats.team_id, stats.position), []).append(stats)
    return [
        PositionStats(
            team_id=team_id,
            position=position,
            skaters=len(members),
            corsi=sum((stats.corsi for stats in members), ForAgainst(0, 0)),
            fenwick=sum((stats.fenwick for stats in members), ForAgainst(0, 0)),
            goals=sum((stats.goals for stats in members), ForAgainst(0, 0)),
            zone_starts=sum((stats.zone_starts for stats in members), ZoneStarts()),
        )
        for (team_id, position), members in groups.items()
    ]


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

    def __add__(self, other: GoalieStats) -> GoalieStats:
        """The same goalie's stats over both games; jersey number as of
        `other`, the later one."""
        return GoalieStats(
            player_id=self.player_id,
            team_id=self.team_id,
            jersey_number=other.jersey_number,
            shots_against=self.shots_against + other.shots_against,
            goals_against=self.goals_against + other.goals_against,
            high_danger_shots_against=self.high_danger_shots_against
            + other.high_danger_shots_against,
            high_danger_goals_against=self.high_danger_goals_against
            + other.high_danger_goals_against,
            time_in_net_ms=self.time_in_net_ms + other.time_in_net_ms,
        )


def goalie_stats(
    data: GameData, *, strength_state: str | None = ALL_SITUATIONS
) -> list[GoalieStats]:
    """Every rostered goalie's SV%/GAA/HD SV%, defaulting to all
    situations -- unlike every other stat here -- since that's how these
    are conventionally reported. Not gated by `opponent_shifts_complete`,
    which gates only on-ice skater and line/unit stats."""
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
                    intervals.get(entry.player_id, []),
                    live,
                    lambda strength: _matches(strength, strength_state),
                ),
            )
        )
    return results


class UnitStrength(enum.Enum):
    """`unit_stats`' default strength filter: each unit at its natural
    context (see CONTEXT.md's Game unit assignment entry)."""

    NATURAL = "natural"


NATURAL_STRENGTH = UnitStrength.NATURAL

_STRENGTH_PATTERN = re.compile(r"(\d+)v(\d+)")


@dataclass(frozen=True)
class UnitStats:
    """One line/unit's stats: only what happened while *every* member was
    on the ice at once -- the intersection of their on-ice intervals, not
    the union or each member's own (see CONTEXT.md's Game unit assignment
    entry). `time_together_ms` is live game-clock time at the active
    strength filter, like goalie minutes. Over several games,
    `player_ids` is every player who held this unit slot in any of them."""

    team_id: int
    unit_type: UnitType
    unit_number: int
    player_ids: frozenset[int]
    corsi: ForAgainst
    fenwick: ForAgainst
    goals: ForAgainst
    time_together_ms: int

    @property
    def plus_minus(self) -> int:
        return self.goals.differential

    def __add__(self, other: UnitStats) -> UnitStats:
        return UnitStats(
            team_id=self.team_id,
            unit_type=self.unit_type,
            unit_number=self.unit_number,
            player_ids=self.player_ids | other.player_ids,
            corsi=self.corsi + other.corsi,
            fenwick=self.fenwick + other.fenwick,
            goals=self.goals + other.goals,
            time_together_ms=self.time_together_ms + other.time_together_ms,
        )


@dataclass(frozen=True)
class ExcludedUnit:
    team_id: int
    unit_type: UnitType
    unit_number: int
    player_ids: frozenset[int]
    reason: str

    def __add__(self, other: ExcludedUnit) -> ExcludedUnit:
        return ExcludedUnit(
            team_id=self.team_id,
            unit_type=self.unit_type,
            unit_number=self.unit_number,
            player_ids=self.player_ids | other.player_ids,
            reason=other.reason,
        )


@dataclass(frozen=True)
class UnitReport:
    units: list[UnitStats]
    excluded: list[ExcludedUnit]


UnitKey = tuple[int, UnitType, int]


def unit_stats(
    data: GameData,
    *,
    strength_state: str | None | UnitStrength = NATURAL_STRENGTH,
) -> UnitReport:
    """Every assigned unit's on-ice stats. By default each unit is filtered
    to its natural context -- forward lines and defense pairs at 5v5, power
    play units whenever their team has more skaters, penalty kill units
    whenever it has fewer -- or to one explicit `strength_state` for every
    unit. Units of a team whose shifts can't be trusted for on-ice
    attribution are excluded and listed, like `skater_stats`' skaters."""
    shots = _shots(data, ALL_SITUATIONS)
    on_ice = _on_ice_at_shots(data)
    intervals = _on_ice_intervals(data)
    live = _live_segments(data)
    units: list[UnitStats] = []
    excluded: list[ExcludedUnit] = []
    for (team_id, unit_type, number), members in _units(data).items():
        if not _on_ice_eligible(data, team_id):
            excluded.append(
                ExcludedUnit(
                    team_id=team_id,
                    unit_type=unit_type,
                    unit_number=number,
                    player_ids=members,
                    reason=OPPONENT_SHIFTS_INCOMPLETE,
                )
            )
            continue
        strength_filter = _unit_strength_filter(
            data, team_id, unit_type, strength_state
        )
        together_shots = [
            shot
            for shot in shots
            if strength_filter(shot.strength_state) and members <= on_ice[shot.id]
        ]
        together: list[Interval] | None = None
        for player_id in members:
            own = intervals.get(player_id, [])
            together = own if together is None else _intersect(together, own)
        units.append(
            UnitStats(
                team_id=team_id,
                unit_type=unit_type,
                unit_number=number,
                player_ids=members,
                corsi=_for_against(together_shots, team_id),
                fenwick=_for_against(_unblocked(together_shots), team_id),
                goals=_for_against(_goals(together_shots), team_id),
                time_together_ms=_time_on_ice(together or [], live, strength_filter),
            )
        )
    return UnitReport(units=units, excluded=excluded)


def _units(data: GameData) -> dict[UnitKey, frozenset[int]]:
    """Each assigned unit's members: home team's units first, then by unit
    type (in `UnitType` order) and number."""
    members: dict[UnitKey, set[int]] = {}
    for assignment in data.unit_assignments:
        key = (assignment.team_id, assignment.unit_type, assignment.unit_number)
        members.setdefault(key, set()).add(assignment.player_id)
    unit_types = list(UnitType)

    def order(key: UnitKey) -> tuple[bool, int, int]:
        team_id, unit_type, number = key
        return team_id != data.game.home_team_id, unit_types.index(unit_type), number

    return {key: frozenset(members[key]) for key in sorted(members, key=order)}


def _unit_strength_filter(
    data: GameData,
    team_id: int,
    unit_type: UnitType,
    strength_state: str | None | UnitStrength,
) -> StrengthFilter:
    if not isinstance(strength_state, UnitStrength):
        return lambda strength: _matches(strength, strength_state)
    if unit_type in (UnitType.FORWARD_LINE, UnitType.DEFENSE_PAIR):
        return lambda strength: strength == EVEN_STRENGTH
    is_home = team_id == data.game.home_team_id
    want_more = unit_type is UnitType.POWER_PLAY

    def natural(strength: str | None) -> bool:
        # Strength states read "{home}v{away}" (see TaggingSession).
        match = _STRENGTH_PATTERN.fullmatch(strength or "")
        if match is None:
            return False
        home, away = int(match[1]), int(match[2])
        own, other = (home, away) if is_home else (away, home)
        return own > other if want_more else own < other

    return natural


# -- multi-game aggregation (ticket 19) ---------------------------------
#
# Each `combined_*` function aggregates its single-game counterpart over a
# hand-picked set of games by summing counts first and computing every
# percentage from the sums (never averaging per-game percentages), and
# returns the same result type, so anything that renders or rolls up one
# game's results (e.g. `position_rollup`) takes several games' just as well.


@dataclass(frozen=True)
class GameCoverage:
    """Which selected games a team's on-ice stats (individual and
    line/unit) were computed over, by game id: a game where that team's
    shifts can't be trusted (see `_on_ice_eligible`) is excluded, and
    reported here rather than silently narrowing the aggregate (see
    ADR-0002). Team-wide and goalie stats are never narrowed."""

    included: tuple[int, ...]
    excluded: tuple[int, ...]


def on_ice_coverage(games: Sequence[GameData]) -> dict[int, GameCoverage]:
    """Per team playing in any of `games`, in first-seen order."""
    coverage: dict[int, tuple[list[int], list[int]]] = {}
    for data in games:
        for team_id in (data.game.home_team_id, data.game.away_team_id):
            if team_id is None:
                continue
            included, excluded = coverage.setdefault(team_id, ([], []))
            eligible = _on_ice_eligible(data, team_id)
            (included if eligible else excluded).append(data.game.id)
    return {
        team_id: GameCoverage(tuple(included), tuple(excluded))
        for team_id, (included, excluded) in coverage.items()
    }


def combined_team_stats(
    games: Sequence[GameData],
    team_id: int,
    *,
    strength_state: str | None = EVEN_STRENGTH,
) -> TeamStats:
    """`team_stats` summed over the selected games `team_id` played in."""
    total = TeamStats(
        team_id=team_id,
        corsi=ForAgainst(0, 0),
        fenwick=ForAgainst(0, 0),
        goals=ForAgainst(0, 0),
        shots_on_goal=ForAgainst(0, 0),
        shot_quality=ShotQuality(
            attempts=0,
            by_type={},
            by_context=dict.fromkeys(SHOT_CONTEXTS, 0),
            high_danger=0,
            located=0,
        ),
    )
    for data in games:
        if team_id in (data.game.home_team_id, data.game.away_team_id):
            total += team_stats(data, team_id, strength_state=strength_state)
    return total


def combined_skater_stats(
    games: Sequence[GameData], *, strength_state: str | None = EVEN_STRENGTH
) -> SkaterReport:
    """`skater_stats` summed per player and team over whichever selected
    games each was included in (see `on_ice_coverage`). A skater is listed
    as excluded only when no selected game included them."""
    skaters: dict[tuple[int, int], SkaterStats] = {}
    excluded: dict[tuple[int, int], ExcludedSkater] = {}
    for data in games:
        report = skater_stats(data, strength_state=strength_state)
        for stats in report.skaters:
            _accumulate(skaters, (stats.player_id, stats.team_id), stats)
        for skater in report.excluded:
            excluded[(skater.player_id, skater.team_id)] = skater
    return SkaterReport(
        skaters=list(skaters.values()),
        excluded=[skater for key, skater in excluded.items() if key not in skaters],
    )


def combined_goalie_stats(
    games: Sequence[GameData], *, strength_state: str | None = ALL_SITUATIONS
) -> list[GoalieStats]:
    """`goalie_stats` summed per goalie and team over the selected games."""
    goalies: dict[tuple[int, int], GoalieStats] = {}
    for data in games:
        for stats in goalie_stats(data, strength_state=strength_state):
            _accumulate(goalies, (stats.player_id, stats.team_id), stats)
    return list(goalies.values())


def combined_unit_stats(
    games: Sequence[GameData],
    *,
    strength_state: str | None | UnitStrength = NATURAL_STRENGTH,
) -> UnitReport:
    """`unit_stats` summed per unit slot -- (team, unit type, number), even
    if who filled it changed between games -- over whichever selected games
    its team was included in. A unit is listed as excluded only when no
    selected game included it."""
    units: dict[UnitKey, UnitStats] = {}
    excluded: dict[UnitKey, ExcludedUnit] = {}
    for data in games:
        report = unit_stats(data, strength_state=strength_state)
        for stats in report.units:
            _accumulate(units, _unit_key(stats), stats)
        for unit in report.excluded:
            _accumulate(excluded, _unit_key(unit), unit)
    return UnitReport(
        units=list(units.values()),
        excluded=[unit for key, unit in excluded.items() if key not in units],
    )


class _Summable(Protocol):
    def __add__(self, other: Self, /) -> Self: ...


_S = TypeVar("_S", bound=_Summable)
_K = TypeVar("_K", bound=Hashable)


def _accumulate(totals: dict[_K, _S], key: _K, value: _S) -> None:
    previous = totals.get(key)
    totals[key] = value if previous is None else previous + value


def _unit_key(unit: UnitStats | ExcludedUnit) -> UnitKey:
    return unit.team_id, unit.unit_type, unit.unit_number


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


StrengthFilter = Callable[[str | None], bool]


def _time_on_ice(
    intervals: list[Interval],
    segments: list[_LiveSegment],
    strength_filter: StrengthFilter,
) -> int:
    return sum(
        max(0, min(end, segment.end) - max(start, segment.start))
        for start, end in intervals
        for segment in segments
        if strength_filter(segment.strength_state)
    )


def _intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """The stretches covered by both interval lists at once."""
    return [
        (max(a_start, b_start), min(a_end, b_end))
        for a_start, a_end in a
        for b_start, b_end in b
        if max(a_start, b_start) < min(a_end, b_end)
    ]


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


def _add_counts(a: dict[_K, int], b: dict[_K, int]) -> dict[_K, int]:
    return {key: a.get(key, 0) + b.get(key, 0) for key in a | b}


def _for_against(shots: list[ShotAttempt], team_id: int) -> ForAgainst:
    for_ = sum(1 for shot in shots if shot.shot_team_id == team_id)
    return ForAgainst(for_=for_, against=len(shots) - for_)
