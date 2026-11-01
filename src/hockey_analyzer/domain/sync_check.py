"""The Sync check (see CONTEXT.md's Sync check entry): a game's tagged goals
and penalties compared against its Official events, as a pure function
over `GameData` -- no session, no query.

A tagged event matches an official one of the same kind, period and team
when their elapsed times in the period -- the derived game clock on the
tagged side -- are within `MATCH_TOLERANCE_MS`. Matching is nearest
first and one to one. Whatever's left unmatched is Missing (official) or
Extra (tagged); matched pairs are checked for Disagreements, and each
period's pairs for Drift.

Shootout attempts aren't stored as Official events, and goalie changes are
skipped here: tagging has no goalie-change event to compare them with.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Sequence
from dataclasses import dataclass

from hockey_analyzer.domain.enums import (
    EventSource,
    OfficialEventKind,
    ShotOutcome,
    Side,
)
from hockey_analyzer.domain.game_clock import PERIOD_LENGTH_MS, game_clocks
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import (
    Event,
    OfficialEvent,
    OfficialPlayer,
    Penalty,
    ShotAttempt,
)

# How far apart a tagged and an official event may be, in elapsed time in
# the period, and still match. A starting value, to be tuned on real games.
MATCH_TOLERANCE_MS = 15_000
# How much a period's matched clock gaps must grow (or shrink) across it,
# by their trend, to be reported as Drift. A starting value too.
DRIFT_THRESHOLD_MS = 5_000


class FindingKind(enum.StrEnum):
    # In the order findings are listed.
    MISSING = "missing"
    EXTRA = "extra"
    DRIFT = "drift"
    DISAGREEMENT = "disagreement"


@dataclass(frozen=True)
class Finding:
    """One thing the sync check found. `tagged_event_id` is the tagged
    event to seek the footage to, when there is one."""

    kind: FindingKind
    # None only for an Extra event tagged before the first period start.
    period: int | None
    tagged_event_id: int | None = None
    tagged_elapsed_ms: int | None = None
    official: OfficialEvent | None = None
    # A Disagreement's field: one of the tagged event's player references
    # ("shooter", "assist1", "assist2" on a goal, "player" on a penalty --
    # TaggingSession's names) or "strength".
    field: str | None = None
    # A Disagreement whose tagged side is an Unknown player reference the
    # official player can fill (see `TaggingSession.fill_from_official`).
    fillable: bool = False
    # Drift: how far the period's clock gap moved across it, by its trend
    # (positive: the tagged clock ran ahead).
    drift_ms: int | None = None


@dataclass(frozen=True)
class SyncCheck:
    # False for a game with no Official events at all (not imported from a
    # league link, or not refreshed yet): no findings then isn't a clean
    # bill.
    has_official_events: bool
    findings: tuple[Finding, ...] = ()

    def of_kind(self, kind: FindingKind) -> list[Finding]:
        return [finding for finding in self.findings if finding.kind is kind]


@dataclass(frozen=True)
class _Tagged:
    event: Event
    kind: OfficialEventKind
    side: Side | None
    period: int | None
    elapsed_ms: int | None


def sync_check(data: GameData) -> SyncCheck:
    if not data.official_events:
        return SyncCheck(has_official_events=False)
    tagged = _tagged(data)
    official = [
        entry
        for entry in data.official_events
        if entry.kind in (OfficialEventKind.GOAL, OfficialEventKind.PENALTY)
    ]
    pairs = _match(tagged, official)
    matched_tagged = {id(t) for t, _ in pairs}
    matched_official = {id(o) for _, o in pairs}

    findings = [
        Finding(FindingKind.MISSING, entry.period_number, official=entry)
        for entry in official
        if id(entry) not in matched_official
    ]
    findings += [
        Finding(
            FindingKind.EXTRA,
            t.period,
            tagged_event_id=t.event.id,
            tagged_elapsed_ms=t.elapsed_ms,
        )
        for t in tagged
        if id(t) not in matched_tagged
    ]
    findings += _drift(pairs)
    for t, entry in sorted(pairs, key=lambda pair: _official_order(pair[1])):
        findings += _disagreements(data, t, entry)
    return SyncCheck(has_official_events=True, findings=tuple(findings))


def _counts(event: Event) -> bool:
    """An unconfirmed vision event is a suggestion, not a fact (CONTEXT.md's
    Event): only a manual or a confirmed event counts."""
    return event.source is not EventSource.VISION or event.confirmed


def _tagged(data: GameData) -> list[_Tagged]:
    """The game's counted goals and penalties, in video order, each placed
    by the game clock derived from counted events only."""
    events = [event for event in data.events if _counts(event)]
    clocks = game_clocks(events)
    sides = {data.game.home_team_id: Side.HOME, data.game.away_team_id: Side.AWAY}
    tagged = []
    for event in sorted(events, key=lambda event: (event.video_timestamp, event.id)):
        if isinstance(event, ShotAttempt) and event.shot_outcome is ShotOutcome.GOAL:
            kind, team_id = OfficialEventKind.GOAL, event.shot_team_id
        elif isinstance(event, Penalty):
            kind, team_id = OfficialEventKind.PENALTY, event.penalty_team_id
        else:
            continue
        clock = clocks.get(event.id)
        tagged.append(
            _Tagged(
                event,
                kind,
                sides.get(team_id) if team_id is not None else None,
                clock.period if clock is not None else None,
                PERIOD_LENGTH_MS - clock.remaining_ms if clock is not None else None,
            )
        )
    return tagged


def _official_order(entry: OfficialEvent) -> tuple[int, int, int]:
    return entry.period_number, entry.elapsed_seconds, entry.id or 0


def _match(
    tagged: Sequence[_Tagged], official: Sequence[OfficialEvent]
) -> list[tuple[_Tagged, OfficialEvent]]:
    """Every candidate pair within the tolerance, nearest first; a tie goes
    to the earlier official event, then the earlier tagged one. Each event
    is matched at most once."""
    candidates = []
    for t_index, t in enumerate(tagged):
        if t.period is None or t.side is None:
            continue
        for o_index, entry in enumerate(official):
            if (t.kind, t.period, t.side) != (
                entry.kind,
                entry.period_number,
                entry.side,
            ):
                continue
            gap = abs(t.elapsed_ms - entry.elapsed_seconds * 1000)
            if gap <= MATCH_TOLERANCE_MS:
                candidates.append((gap, _official_order(entry), o_index, t_index))
    pairs = []
    used_tagged, used_official = set(), set()
    for _, _, o_index, t_index in sorted(candidates):
        if t_index in used_tagged or o_index in used_official:
            continue
        used_tagged.add(t_index)
        used_official.add(o_index)
        pairs.append((tagged[t_index], official[o_index]))
    return pairs


def _drift(pairs: Sequence[tuple[_Tagged, OfficialEvent]]) -> list[Finding]:
    """Per period: the least-squares trend of the matched pairs' clock gaps
    (tagged minus official) over official elapsed time, projected across
    the span the pairs cover. Reported at the matched tagged event where
    the gap jumps the most from the pair before -- the place to look for
    a missed stoppage."""
    by_period: dict[int, list[tuple[int, int, _Tagged]]] = {}
    for t, entry in pairs:
        official_ms = entry.elapsed_seconds * 1000
        by_period.setdefault(entry.period_number, []).append(
            (official_ms, t.elapsed_ms - official_ms, t)
        )
    findings = []
    for period in sorted(by_period):
        points = sorted(
            by_period[period], key=lambda point: (point[0], point[2].event.id)
        )
        if len(points) < 2:
            continue
        xs = [x for x, _, _ in points]
        gaps = [gap for _, gap, _ in points]
        mean_x, mean_gap = sum(xs) / len(xs), sum(gaps) / len(gaps)
        spread = sum((x - mean_x) ** 2 for x in xs)
        if spread == 0:
            continue
        slope = (
            sum((x - mean_x) * (g - mean_gap) for x, g in zip(xs, gaps, strict=True))
            / spread
        )
        drift = round(slope * (xs[-1] - xs[0]))
        if abs(drift) < DRIFT_THRESHOLD_MS:
            continue
        # The first of the biggest jumps.
        jump = max(range(1, len(gaps)), key=lambda i: abs(gaps[i] - gaps[i - 1]))
        t = points[jump][2]
        findings.append(
            Finding(
                FindingKind.DRIFT,
                period,
                tagged_event_id=t.event.id,
                tagged_elapsed_ms=t.elapsed_ms,
                drift_ms=drift,
            )
        )
    return findings


# Each matched kind's player references: (TaggingSession's reference name,
# the event's id/unknown columns, the Official event's slot).
_PLAYER_FIELDS = {
    OfficialEventKind.GOAL: (
        ("shooter", "shooter", "player"),
        ("assist1", "assist1", "assist1"),
        ("assist2", "assist2", "assist2"),
    ),
    OfficialEventKind.PENALTY: (("player", "penalty_player", "player"),),
}


def _disagreements(data: GameData, t: _Tagged, entry: OfficialEvent) -> list[Finding]:
    def disagreement(field: str, *, fillable: bool = False) -> Finding:
        return Finding(
            FindingKind.DISAGREEMENT,
            t.period,
            tagged_event_id=t.event.id,
            tagged_elapsed_ms=t.elapsed_ms,
            official=entry,
            field=field,
            fillable=fillable,
        )

    findings = []
    for field, column, slot in _PLAYER_FIELDS[t.kind]:
        player_id = getattr(t.event, f"{column}_id")
        unknown = getattr(t.event, f"{column}_unknown")
        official_player = entry.official_player(slot)
        if unknown:
            # Fillable only from a player the league names by licence.
            fillable = official_player is not None and bool(official_player.league_id)
            findings.append(disagreement(field, fillable=fillable))
        elif player_id is None:
            if official_player is not None:
                findings.append(disagreement(field))
        elif official_player is None or not _same_player(
            data, player_id, official_player
        ):
            findings.append(disagreement(field))
    if t.kind is OfficialEventKind.GOAL and _strength_contradicts(
        entry.strength_tag, t.event.strength_state, t.side
    ):
        findings.append(disagreement("strength"))
    return findings


def _same_player(data: GameData, player_id: int, official: OfficialPlayer) -> bool:
    """By licence number when the tagged Player has one (and the league
    names one); otherwise by the Player's jersey on this game's roster."""
    entry = next((e for e in data.roster if e.player_id == player_id), None)
    player = entry.player if entry is not None else None
    if player is not None and player.league_id and official.league_id:
        return player.league_id == official.league_id
    return entry is not None and entry.jersey_number == official.jersey


# The official strength tag, by its prefix ("PP1", "SH2"...), as what it
# says about the scoring team's skaters against the other team's.
_TAG_AGREES = {
    "EQ": lambda own, other: own == other,
    "PP": lambda own, other: own > other,
    "SH": lambda own, other: own < other,
    # Into an empty net: the other team's goalie is out for an extra
    # skater.
    "EN": lambda own, other: own < other,
}
# Strength states read "{home}v{away}" (see TaggingSession).
_STRENGTH = re.compile(r"(\d+)v(\d+)")


def _strength_contradicts(
    tag: str | None, strength_state: str | None, side: Side | None
) -> bool:
    """Whether a goal's tagged strength state contradicts the official
    tag. The tag is coarser than skater counts, so "agrees" is a range:
    a 5v4 or a 5v3 both agree with a power play. A tag or a state that
    can't be read contradicts nothing."""
    agrees = _TAG_AGREES.get((tag or "")[:2])
    match = _STRENGTH.fullmatch(strength_state or "")
    if agrees is None or match is None or side is None:
        return False
    home, away = int(match[1]), int(match[2])
    own, other = (home, away) if side == Side.HOME else (away, home)
    return not agrees(own, other)
