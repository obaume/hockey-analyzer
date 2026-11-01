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
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from hockey_analyzer.domain.enums import (
    EventSource,
    EventType,
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
from hockey_analyzer.domain.tagging_session import OFFICIAL_REFERENCES

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
    # The goal or penalty found about, and its team's side (None for a
    # tagged one with no team set). None for Drift, which is about a
    # whole period.
    event_kind: OfficialEventKind | None
    side: Side | None
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
class _TaggedEvent:
    event: Event
    kind: OfficialEventKind
    side: Side | None
    period: int | None
    elapsed_ms: int | None


def sync_check(data: GameData) -> SyncCheck:
    if not data.official_events:
        return SyncCheck(has_official_events=False)
    tagged_events = _tagged_events(data)
    official = [
        entry
        for entry in data.official_events
        if entry.kind in (OfficialEventKind.GOAL, OfficialEventKind.PENALTY)
    ]
    pairs = _match(tagged_events, official)
    matched_tagged = {id(tagged) for tagged, _ in pairs}
    matched_official = {id(entry) for _, entry in pairs}

    findings = [
        Finding(
            FindingKind.MISSING,
            entry.kind,
            entry.side,
            entry.period_number,
            official=entry,
        )
        for entry in official
        if id(entry) not in matched_official
    ]
    findings += [
        Finding(
            FindingKind.EXTRA,
            tagged.kind,
            tagged.side,
            tagged.period,
            tagged_event_id=tagged.event.id,
            tagged_elapsed_ms=tagged.elapsed_ms,
        )
        for tagged in tagged_events
        if id(tagged) not in matched_tagged
    ]
    findings += _drift(pairs)
    for tagged, entry in sorted(pairs, key=lambda pair: _official_order(pair[1])):
        findings += _disagreements(data, tagged, entry)
    return SyncCheck(has_official_events=True, findings=tuple(findings))


def _is_counted(event: Event) -> bool:
    """An unconfirmed vision event is a suggestion, not a fact (CONTEXT.md's
    Event): only a manual or a confirmed event counts."""
    return event.source is not EventSource.VISION or event.confirmed


def _tagged_events(data: GameData) -> list[_TaggedEvent]:
    """The game's counted goals and penalties, in video order, each placed
    by the game clock derived from counted events only."""
    events = [event for event in data.events if _is_counted(event)]
    clocks = game_clocks(events)
    tagged_events = []
    for event in sorted(events, key=lambda event: (event.video_timestamp, event.id)):
        if isinstance(event, ShotAttempt) and event.shot_outcome is ShotOutcome.GOAL:
            kind, team_id = OfficialEventKind.GOAL, event.shot_team_id
        elif isinstance(event, Penalty):
            kind, team_id = OfficialEventKind.PENALTY, event.penalty_team_id
        else:
            continue
        clock = clocks.get(event.id)
        tagged_events.append(
            _TaggedEvent(
                event,
                kind,
                data.game.side_of(team_id),
                clock.period if clock is not None else None,
                PERIOD_LENGTH_MS - clock.remaining_ms if clock is not None else None,
            )
        )
    return tagged_events


def _official_order(entry: OfficialEvent) -> tuple[int, int, int]:
    return entry.period_number, entry.elapsed_seconds, entry.id or 0


def _match(
    tagged_events: Sequence[_TaggedEvent], official: Sequence[OfficialEvent]
) -> list[tuple[_TaggedEvent, OfficialEvent]]:
    """Every candidate pair within the tolerance, nearest first; a tie goes
    to the earlier official event, then the earlier tagged one. Each event
    is matched at most once."""
    candidates = []
    for t_index, tagged in enumerate(tagged_events):
        if tagged.period is None or tagged.side is None:
            continue
        for o_index, entry in enumerate(official):
            if (tagged.kind, tagged.period, tagged.side) != (
                entry.kind,
                entry.period_number,
                entry.side,
            ):
                continue
            gap = abs(tagged.elapsed_ms - entry.elapsed_seconds * 1000)
            if gap <= MATCH_TOLERANCE_MS:
                candidates.append((gap, _official_order(entry), o_index, t_index))
    pairs = []
    used_tagged, used_official = set(), set()
    for _, _, o_index, t_index in sorted(candidates):
        if t_index in used_tagged or o_index in used_official:
            continue
        used_tagged.add(t_index)
        used_official.add(o_index)
        pairs.append((tagged_events[t_index], official[o_index]))
    return pairs


def _drift(pairs: Sequence[tuple[_TaggedEvent, OfficialEvent]]) -> list[Finding]:
    """Per period: the least-squares trend of the matched pairs' clock gaps
    (tagged minus official) over official elapsed time, projected across
    the span the pairs cover. Reported at the matched tagged event where
    the gap jumps the most from the pair before -- the place to look for
    a missed stoppage."""
    by_period: dict[int, list[tuple[int, int, _TaggedEvent]]] = {}
    for tagged, entry in pairs:
        official_ms = entry.elapsed_seconds * 1000
        by_period.setdefault(entry.period_number, []).append(
            (official_ms, tagged.elapsed_ms - official_ms, tagged)
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
        tagged = points[jump][2]
        findings.append(
            Finding(
                FindingKind.DRIFT,
                None,
                None,
                period,
                tagged_event_id=tagged.event.id,
                tagged_elapsed_ms=tagged.elapsed_ms,
                drift_ms=drift,
            )
        )
    return findings


def _disagreements(
    data: GameData, tagged: _TaggedEvent, entry: OfficialEvent
) -> list[Finding]:
    def disagreement(field: str, *, fillable: bool = False) -> Finding:
        return Finding(
            FindingKind.DISAGREEMENT,
            entry.kind,
            entry.side,
            tagged.period,
            tagged_event_id=tagged.event.id,
            tagged_elapsed_ms=tagged.elapsed_ms,
            official=entry,
            field=field,
            fillable=fillable,
        )

    findings = []
    references = OFFICIAL_REFERENCES[EventType(tagged.event.event_type)]
    for field, reference in references.items():
        player_id = reference.player_id(tagged.event)
        official_player = reference.official(entry)
        if reference.is_unknown(tagged.event):
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
    if tagged.kind is OfficialEventKind.GOAL and _strength_contradicts(
        entry.strength_tag, tagged.event.strength_state, tagged.side
    ):
        findings.append(disagreement("strength"))
    return findings


def _same_player(data: GameData, player_id: int, official: OfficialPlayer) -> bool:
    """By licence number when the tagged Player has one (and the league
    names one); otherwise by the Player's jersey on this game's roster."""
    entry = data.roster_entry(player_id)
    player = entry.player if entry is not None else None
    if player is not None and player.league_id and official.league_id:
        return player.league_id == official.league_id
    return entry is not None and entry.jersey_number == official.jersey


# An official strength tag, by its prefix ("PP1", "SH2"...), as what it
# says about the scoring team's skaters against the other team's. Other
# tags ("GWG"...) say nothing about strength.
_TAG_AGREES = {
    "EQ": lambda own, other: own == other,
    "PP": lambda own, other: own > other,
    "SH": lambda own, other: own < other,
}
# Strength states read "{home}v{away}" (see TaggingSession).
_STRENGTH = re.compile(r"(\d+)v(\d+)")


def _strength_contradicts(
    tags: str | None, strength_state: str | None, side: Side | None
) -> bool:
    """Whether a goal's tagged strength state contradicts the official
    tags ("EQ", "PP1,GWG", "EQ,EN"...). A tag is coarser than skater
    counts, so "agrees" is a range: a 5v4 or a 5v3 both agree with a
    power play. An empty-net tag, wherever it is in the list, means the
    other team pulled its goalie for an extra skater on top of what the
    strength tag says ("PP1,EN" is 5v5 on ice), so that skater is taken
    off before the strength tag is checked. Tags or a state that can't
    be read contradict nothing."""
    agrees, empty_net = _tags_agree(tags)
    match = _STRENGTH.fullmatch(strength_state or "")
    if agrees is None or match is None or side is None:
        return False
    home, away = int(match[1]), int(match[2])
    own, other = (home, away) if side == Side.HOME else (away, home)
    return not agrees(own, other - 1 if empty_net else other)


def _tags_agree(
    tags: str | None,
) -> tuple[Callable[[int, int], bool] | None, bool]:
    """A goal's first strength tag's rule, and whether any tag says empty
    net. An empty-net tag alone, with no strength tag, gives no rule: the
    strength before the goalie was pulled is unknown."""
    prefixes = [tag.strip()[:2] for tag in (tags or "").split(",")]
    rule = next(
        (_TAG_AGREES[prefix] for prefix in prefixes if prefix in _TAG_AGREES), None
    )
    return rule, "EN" in prefixes
