"""ClipExporter selection/plan logic (ticket 23): the pure half of clip
export, from a filter over one game's events to an `ExportPlan` of
segments, order and filenames (see CONTEXT.md's Clip, Padding window and
Highlight reel entries). No GUI, video or file-I/O dependency: the actual
cutting/encoding is a `ClipEncoder` the caller supplies to `run_export`.

The flow is `select_clips` (filters -> a `ClipSelection` with every match
selected) -> hand-pick with `ClipSelection.select`/`deselect` ->
`plan_export` -> `run_export`.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from hockey_analyzer.domain.enums import EventType, ShotOutcome
from hockey_analyzer.domain.game_clock import GameClock, game_clocks
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import (
    Event,
    Faceoff,
    Game,
    Penalty,
    ShiftChange,
    ShotAttempt,
    Team,
)
from hockey_analyzer.domain.video_timestamp import format_video_timestamp


@dataclass(frozen=True)
class Padding:
    """The padding window: footage kept before and after each event's
    video timestamp, one value pair for the whole export."""

    before_ms: int
    after_ms: int

    def __post_init__(self) -> None:
        if self.before_ms < 0 or self.after_ms < 0:
            raise ValueError("padding can't be negative")


DEFAULT_PADDING = Padding(before_ms=5000, after_ms=3000)


class OutputShape(enum.StrEnum):
    """One file per selected event, or a single chronological hard-cut
    highlight reel -- chosen per export."""

    PER_CLIP = "per_clip"
    REEL = "reel"


@dataclass(frozen=True)
class ClipFilter:
    """Each field set narrows the match (AND); `None` means any. A
    `shot_outcome` only ever matches `shot_attempt` events. `player_id`
    matches the player in any role the event records (see
    `involved_players`)."""

    player_id: int | None = None
    event_type: EventType | None = None
    shot_outcome: ShotOutcome | None = None

    def matches(self, event: Event) -> bool:
        if self.event_type is not None and event.event_type != self.event_type:
            return False
        if self.shot_outcome is not None and not (
            isinstance(event, ShotAttempt) and event.shot_outcome == self.shot_outcome
        ):
            return False
        return self.player_id is None or self.player_id in involved_players(event)


def involved_players(event: Event) -> frozenset[int]:
    """Every known player an event names, whatever the role: shooter and
    assists, both faceoff participants, the penalized player, the shift
    participant."""
    if isinstance(event, ShotAttempt):
        ids = (event.shooter_id, event.assist1_id, event.assist2_id)
    elif isinstance(event, Faceoff):
        ids = (event.faceoff_participant_a_id, event.faceoff_participant_b_id)
    elif isinstance(event, Penalty):
        ids = (event.penalty_player_id,)
    elif isinstance(event, ShiftChange):
        ids = (event.shift_player_id,)
    else:
        ids = ()
    return frozenset(player_id for player_id in ids if player_id is not None)


@dataclass(frozen=True)
class ClipSelection:
    """A filter's matches (`candidates`, in video order) and which of them
    the user has hand-picked for export. Every candidate starts selected,
    unconfirmed vision events included. Immutable: `select`/`deselect`
    return a new selection."""

    clip_filter: ClipFilter
    candidates: tuple[Event, ...]
    excluded_ids: frozenset[int] = field(default=frozenset())

    @property
    def selected(self) -> tuple[Event, ...]:
        return tuple(e for e in self.candidates if e.id not in self.excluded_ids)

    def is_selected(self, event_id: int) -> bool:
        self._require_candidate(event_id)
        return event_id not in self.excluded_ids

    def select(self, event_id: int) -> ClipSelection:
        self._require_candidate(event_id)
        return replace(self, excluded_ids=self.excluded_ids - {event_id})

    def deselect(self, event_id: int) -> ClipSelection:
        self._require_candidate(event_id)
        return replace(self, excluded_ids=self.excluded_ids | {event_id})

    def _require_candidate(self, event_id: int) -> None:
        if all(event.id != event_id for event in self.candidates):
            raise KeyError(f"event {event_id} isn't a candidate of this selection")


def select_clips(data: GameData, clip_filter: ClipFilter) -> ClipSelection:
    candidates = sorted(
        (event for event in data.events if clip_filter.matches(event)),
        key=lambda event: (event.video_timestamp, event.id),
    )
    return ClipSelection(clip_filter, tuple(candidates))


@dataclass(frozen=True)
class ClipSegment:
    """One event's padding window, clamped to the footage."""

    event_id: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class PlannedOutput:
    """One file to write: its segments, hard-cut together in this order."""

    path: Path
    segments: tuple[ClipSegment, ...]


@dataclass(frozen=True)
class ExportPlan:
    source_path: str
    shape: OutputShape
    outputs: tuple[PlannedOutput, ...]

    @property
    def can_export(self) -> bool:
        """False when nothing is selected -- the export action is disabled
        rather than producing no files (or an empty reel)."""
        return bool(self.outputs)


def plan_export(
    data: GameData,
    selection: ClipSelection,
    *,
    shape: OutputShape,
    footage_duration_ms: int,
    output_dir: Path,
    padding: Padding = DEFAULT_PADDING,
) -> ExportPlan:
    """The selected events' clips as files to write under `output_dir`.
    A padding window running past either end of the footage is clamped to
    it. Raises `ValueError` if the game has no footage attached."""
    source_path = data.game.video_path
    if source_path is None:
        raise ValueError(f"game {data.game.id} has no footage attached")

    events = selection.selected
    segments = [
        ClipSegment(
            event.id,
            max(0, event.video_timestamp - padding.before_ms),
            min(footage_duration_ms, event.video_timestamp + padding.after_ms),
        )
        for event in events
    ]
    player = (
        _player_label(data, selection.clip_filter.player_id)
        if selection.clip_filter.player_id is not None
        else None
    )

    if not segments:
        outputs: tuple[PlannedOutput, ...] = ()
    elif shape is OutputShape.REEL:
        name = _reel_filename(data.game, selection.clip_filter, player)
        outputs = (PlannedOutput(output_dir / name, tuple(segments)),)
    else:
        clocks = game_clocks(data.events)
        names = _unique(
            _clip_filename(event, player, clocks.get(event.id)) for event in events
        )
        outputs = tuple(
            PlannedOutput(output_dir / name, (segment,))
            for name, segment in zip(names, segments, strict=True)
        )
    return ExportPlan(source_path, shape, outputs)


class ClipEncoder(Protocol):
    """Cuts `segments` out of `source_path` and writes them, hard-cut in
    order, to one H.264/AAC `.mp4` at `output_path` (ticket 24)."""

    def encode(
        self, source_path: str, segments: Sequence[ClipSegment], output_path: Path
    ) -> None: ...


def run_export(plan: ExportPlan, encoder: ClipEncoder) -> list[Path]:
    """Hands each planned output to `encoder`, in plan order; returns the
    paths written. Refuses a plan that `can_export` says is disabled."""
    if not plan.can_export:
        raise ValueError("nothing selected to export")
    for output in plan.outputs:
        encoder.encode(plan.source_path, list(output.segments), output.path)
    return [output.path for output in plan.outputs]


def _clip_filename(event: Event, player: str | None, clock: GameClock | None) -> str:
    parts = [event.event_type.value, player, _clock_label(event, clock)]
    return "_".join(part for part in parts if part) + ".mp4"


def _reel_filename(game: Game, clip_filter: ClipFilter, player: str | None) -> str:
    date = game.date.isoformat() if game.date else "undated"
    opponent = _opponent(game)
    opponent_label = _slug(opponent.name) if opponent is not None else "unknown"
    summary = [
        player,
        clip_filter.event_type.value if clip_filter.event_type else None,
        clip_filter.shot_outcome.value if clip_filter.shot_outcome else None,
    ]
    summary_label = "-".join(part for part in summary if part) or "all"
    return f"{date}_vs-{opponent_label}_{summary_label}_reel.mp4"


def _opponent(game: Game) -> Team | None:
    """The side that isn't the user's team; the away side when neither
    (or both) is flagged as the user's."""
    if game.away_team is not None and game.away_team.is_user_team:
        if not (game.home_team is not None and game.home_team.is_user_team):
            return game.home_team
    return game.away_team


def _player_label(data: GameData, player_id: int) -> str:
    """The player's name, else `no{jersey}` from this game's roster."""
    entry = next((e for e in data.roster if e.player_id == player_id), None)
    if entry is None:
        return f"player-{player_id}"
    if entry.player is not None and entry.player.full_name:
        return _slug(entry.player.full_name)
    return f"no{entry.jersey_number}"


def _clock_label(event: Event, clock: GameClock | None) -> str:
    """`P2-12m34s` for period 2, 12:34 remaining -- no colons, which
    aren't allowed in Windows filenames. An event before any
    `period_start` has no game clock, so its video timestamp stands in."""
    if clock is None:
        hours, minutes, seconds = format_video_timestamp(event.video_timestamp).split(
            ":"
        )
        return f"video-{hours}h{minutes}m{seconds}s"
    minutes, seconds = divmod(clock.remaining_ms // 1000, 60)
    return f"P{clock.period}-{minutes:02d}m{seconds:02d}s"


def _slug(text: str) -> str:
    """Lowercase, runs of anything but letters/digits collapsed to `-`
    (so `_` stays free as the filename's field separator)."""
    return re.sub(r"[\W_]+", "-", text.lower()).strip("-")


def _unique(names: Iterable[str]) -> list[str]:
    """Suffixes `_2`, `_3`... onto repeats (e.g. two shift changes during
    one stoppage share a game clock), so no clip overwrites another."""
    seen: dict[str, int] = {}
    unique = []
    for name in names:
        count = seen[name] = seen.get(name, 0) + 1
        stem = name.removesuffix(".mp4")
        unique.append(name if count == 1 else f"{stem}_{count}.mp4")
    return unique
