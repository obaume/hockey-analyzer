"""Report bundle (ticket 25): the file format a game, team, or player report
is handed to another install in (see CONTEXT.md's Report bundle entries).

Two steps, both free of any GUI dependency:

- `build_game_report` / `build_team_report` / `build_player_report` run
  `StatsEngine` over tagged games and freeze the result into a `Report`:
  the same stat result types the live stats view renders, but with every
  sender-side database id swapped for a bundle-local key into the report's
  own `teams`/`players`/`games` tables -- a recipient's install has its own
  database, where those ids mean nothing (see ADR-0004). Teams carry
  `league_id` when known plus their name; players only `full_name` plus
  jersey number. Nothing matches either against the recipient's records.
- `write_bundle` / `read_bundle` (the writer/reader pair) move a `Report`
  to and from a zip archive holding `manifest.json` (format marker and
  integer schema version), `report.json` (stats, identities, game list,
  caveats, and the sender's Markdown summary), and `assets/<name>.png`
  (charts baked at export time -- see ADR-0003; never inlined into the
  JSON).

A bundle carries only computed numbers, never raw events or shifts, so an
opened one can't be re-filtered: each stat group keeps the strength-state
filter it was computed at. RAPM and xG-dependent stats aren't written at
all (not even as nulls) until those models exist, since StatsEngine
computes neither.
"""

from __future__ import annotations

import enum
import json
import os
import re
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from datetime import date as date_
from pathlib import Path
from typing import Any, Generic, TypeVar

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import Position, ShotType, UnitType
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.stats_engine import (
    ALL_SITUATIONS,
    EVEN_STRENGTH,
    NATURAL_STRENGTH,
    ExcludedSkater,
    ExcludedUnit,
    ForAgainst,
    GameCoverage,
    GoalieStats,
    ShotQuality,
    SkaterReport,
    SkaterStats,
    TeamStats,
    UnitReport,
    UnitStats,
    UnitStrength,
    ZoneStarts,
)

BUNDLE_EXTENSION = ".hockeyreport"
# Bumped on any breaking change to the bundle's fields. An app refuses a
# bundle newer than this and best-effort opens an older one.
SCHEMA_VERSION = 1

_FORMAT = "hockey-analyzer-report-bundle"
_MANIFEST = "manifest.json"
_REPORT = "report.json"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHART_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


class ReportBundleError(Exception):
    """A file that can't be opened as a report bundle."""


class InvalidBundleError(ReportBundleError):
    """Not a zip, no recognizable manifest, or a damaged/missing member."""


class BundleTooNewError(ReportBundleError):
    def __init__(self, schema_version: int) -> None:
        self.schema_version = schema_version
        super().__init__(
            "This report was made by a newer version of Hockey Analyzer "
            f"(bundle schema version {schema_version}; this app reads up to "
            f"version {SCHEMA_VERSION}). Update the app to open it."
        )


class ReportKind(enum.StrEnum):
    GAME = "game"
    TEAM = "team"
    PLAYER = "player"


class AggregationMode(enum.StrEnum):
    """How a multi-game report combined its games. Ticket 10 also allows a
    per-game-average mode; it joins here once StatsEngine can compute it."""

    SUM_THEN_COMPUTE = "sum-then-compute"


@dataclass(frozen=True)
class TeamRef:
    """A team as a recipient's install can recognize it: by `league_id`
    when the sender's team has one, else by name (see ADR-0004)."""

    name: str
    league_id: str | None = None

    @property
    def identity(self) -> tuple[str, str]:
        if self.league_id is not None:
            return "league_id", self.league_id
        return "name", self.name


@dataclass(frozen=True)
class PlayerRef:
    """A player as `full_name` + jersey number -- no cross-install id
    exists (see ADR-0004). Jersey number as of the latest game in the
    report; None only for a unit member missing from every roster."""

    full_name: str | None
    jersey_number: int | None


@dataclass(frozen=True)
class GameRef:
    """One game the report drew from, keyed by `game_id` (bundle-local)."""

    game_id: int
    date: date_ | None
    home_team_id: int | None
    away_team_id: int | None
    home_score: int | None
    away_score: int | None

    def opponent_of(self, team_id: int) -> int | None:
        if team_id == self.home_team_id:
            return self.away_team_id
        if team_id == self.away_team_id:
            return self.home_team_id
        return None


@dataclass(frozen=True)
class Chart:
    """A chart baked to PNG at export time; stored as `assets/<name>.png`."""

    name: str
    png: bytes

    def __post_init__(self) -> None:
        if not _CHART_NAME.fullmatch(self.name):
            raise ValueError(f"invalid chart name {self.name!r}")
        if not self.png.startswith(_PNG_SIGNATURE):
            raise ValueError(f"chart {self.name!r} is not a PNG image")


_T = TypeVar("_T")


@dataclass(frozen=True)
class Filtered(Generic[_T]):
    """One stat group together with the strength-state filter it was
    computed at (None: all situations; `NATURAL_STRENGTH`: each unit at
    its natural context)."""

    strength_state: str | None | UnitStrength
    stats: _T


@dataclass(frozen=True)
class StrengthFilters:
    """Each stat group's filter, as set independently in the live stats
    view; defaults match StatsEngine's."""

    team: str | None = EVEN_STRENGTH
    skaters: str | None = EVEN_STRENGTH
    goalies: str | None = ALL_SITUATIONS
    units: str | None | UnitStrength = NATURAL_STRENGTH


DEFAULT_FILTERS = StrengthFilters()


@dataclass(frozen=True)
class Report:
    """A frozen report snapshot. Every `team_id`/`player_id`/`game_id`
    inside (stats included) is a key into `teams`/`players`/`games`, never
    a database id. A stat group is None when absent -- not applicable, or
    a bundle from an older schema that didn't have it.

    Caveats ride along rather than being dropped: skaters and units whose
    on-ice stats couldn't be computed are listed as excluded with a reason,
    `on_ice_coverage` names the games each team's on-ice stats did and
    didn't cover, and `unresolved_shift_changes` counts, per team, the
    shift changes with an unknown player whose ice time is missing from
    that team's individual stats."""

    kind: ReportKind
    summary: str
    teams: Mapping[int, TeamRef]
    players: Mapping[int, PlayerRef]
    games: tuple[GameRef, ...]
    exported_at: datetime | None = None
    subject_team_id: int | None = None
    subject_player_id: int | None = None
    aggregation_mode: AggregationMode | None = None
    team_stats: Filtered[tuple[TeamStats, ...]] | None = None
    skater_stats: Filtered[SkaterReport] | None = None
    goalie_stats: Filtered[tuple[GoalieStats, ...]] | None = None
    unit_stats: Filtered[UnitReport] | None = None
    on_ice_coverage: Mapping[int, GameCoverage] = field(default_factory=dict)
    unresolved_shift_changes: Mapping[int, int] = field(default_factory=dict)
    charts: tuple[Chart, ...] = ()


# -- building a report ---------------------------------------------------


def build_game_report(
    data: GameData,
    *,
    summary: str,
    filters: StrengthFilters = DEFAULT_FILTERS,
    charts: Sequence[Chart] = (),
    exported_at: datetime | None = None,
) -> Report:
    return _build(
        ReportKind.GAME,
        [data],
        summary=summary,
        filters=filters,
        charts=charts,
        exported_at=exported_at,
    )


def build_team_report(
    games: Sequence[GameData],
    team_id: int,
    *,
    summary: str,
    filters: StrengthFilters = DEFAULT_FILTERS,
    charts: Sequence[Chart] = (),
    exported_at: datetime | None = None,
    aggregation_mode: AggregationMode = AggregationMode.SUM_THEN_COMPUTE,
) -> Report:
    """The full stat set over the hand-picked `games`, about `team_id`."""
    if not any(
        team_id in (data.game.home_team_id, data.game.away_team_id) for data in games
    ):
        raise ValueError(f"team {team_id} plays in none of the selected games")
    return _build(
        ReportKind.TEAM,
        games,
        summary=summary,
        filters=filters,
        charts=charts,
        exported_at=exported_at,
        aggregation_mode=aggregation_mode,
        subject_team_id=team_id,
    )


def build_player_report(
    games: Sequence[GameData],
    player_id: int,
    *,
    summary: str,
    filters: StrengthFilters = DEFAULT_FILTERS,
    charts: Sequence[Chart] = (),
    exported_at: datetime | None = None,
    aggregation_mode: AggregationMode = AggregationMode.SUM_THEN_COMPUTE,
) -> Report:
    """The full stat set over the hand-picked `games`, about `player_id`."""
    if not any(entry.player_id == player_id for data in games for entry in data.roster):
        raise ValueError(f"player {player_id} is on no selected game's roster")
    return _build(
        ReportKind.PLAYER,
        games,
        summary=summary,
        filters=filters,
        charts=charts,
        exported_at=exported_at,
        aggregation_mode=aggregation_mode,
        subject_player_id=player_id,
    )


def _build(
    kind: ReportKind,
    games: Sequence[GameData],
    *,
    summary: str,
    filters: StrengthFilters,
    charts: Sequence[Chart],
    exported_at: datetime | None,
    aggregation_mode: AggregationMode | None = None,
    subject_team_id: int | None = None,
    subject_player_id: int | None = None,
) -> Report:
    if not games:
        raise ValueError("a report needs at least one game")
    teams = _team_refs(games)
    players = _player_refs(games)
    keys = _Keys(
        teams={team_id: key for key, team_id in enumerate(teams, start=1)},
        players={player_id: key for key, player_id in enumerate(players, start=1)},
        games={data.game.id: key for key, data in enumerate(games, start=1)},
    )

    unresolved: dict[int, int] = {}
    for data in games:
        for team_id, count in stats_engine.unresolved_shift_changes(data).items():
            unresolved[team_id] = unresolved.get(team_id, 0) + count

    units = None
    if any(data.unit_assignments for data in games):
        units = Filtered(
            filters.units,
            keys.unit_report(
                stats_engine.combined_unit_stats(games, strength_state=filters.units)
            ),
        )

    return Report(
        kind=kind,
        summary=summary,
        exported_at=exported_at or datetime.now(UTC),
        teams={keys.teams[team_id]: ref for team_id, ref in teams.items()},
        players={keys.players[player_id]: ref for player_id, ref in players.items()},
        games=tuple(keys.game(data) for data in games),
        subject_team_id=None
        if subject_team_id is None
        else keys.teams[subject_team_id],
        subject_player_id=(
            None if subject_player_id is None else keys.players[subject_player_id]
        ),
        aggregation_mode=aggregation_mode,
        team_stats=Filtered(
            filters.team,
            tuple(
                keys.team_stats(
                    stats_engine.combined_team_stats(
                        games, team_id, strength_state=filters.team
                    )
                )
                for team_id in teams
            ),
        ),
        skater_stats=Filtered(
            filters.skaters,
            keys.skater_report(
                stats_engine.combined_skater_stats(
                    games, strength_state=filters.skaters
                )
            ),
        ),
        goalie_stats=Filtered(
            filters.goalies,
            tuple(
                keys.player_stats(stats)
                for stats in stats_engine.combined_goalie_stats(
                    games, strength_state=filters.goalies
                )
            ),
        ),
        unit_stats=units,
        on_ice_coverage={
            keys.teams[team_id]: GameCoverage(
                included=tuple(keys.games[game_id] for game_id in coverage.included),
                excluded=tuple(keys.games[game_id] for game_id in coverage.excluded),
            )
            for team_id, coverage in stats_engine.on_ice_coverage(games).items()
        },
        unresolved_shift_changes={
            keys.teams[team_id]: count for team_id, count in unresolved.items()
        },
        charts=tuple(charts),
    )


def _team_refs(games: Sequence[GameData]) -> dict[int, TeamRef]:
    """Every team playing in any of `games`, first-seen order, home side
    first; a later game's name wins (a team may be renamed) -- as the live
    stats view lists them."""
    refs: dict[int, TeamRef] = {}
    for data in games:
        game = data.game
        for team_id, team, fallback in (
            (game.home_team_id, game.home_team, "Home"),
            (game.away_team_id, game.away_team, "Away"),
        ):
            if team_id is not None:
                refs[team_id] = (
                    TeamRef(fallback)
                    if team is None
                    else TeamRef(team.name, team.league_id)
                )
    return refs


def _player_refs(games: Sequence[GameData]) -> dict[int, PlayerRef]:
    """Every rostered player as of the latest game they played in, then
    any unit member no roster lists."""
    refs: dict[int, PlayerRef] = {}
    for data in games:
        for entry in data.roster:
            name = entry.player.full_name if entry.player is not None else None
            refs[entry.player_id] = PlayerRef(name, entry.jersey_number)
    for data in games:
        for assignment in data.unit_assignments:
            refs.setdefault(assignment.player_id, PlayerRef(None, None))
    return refs


@dataclass(frozen=True)
class _Keys:
    """Database id -> bundle-local key, per table; rewrites StatsEngine
    results to reference keys instead of ids."""

    teams: Mapping[int, int]
    players: Mapping[int, int]
    games: Mapping[int, int]

    def game(self, data: GameData) -> GameRef:
        game = data.game
        return GameRef(
            game_id=self.games[game.id],
            date=game.date,
            home_team_id=self._team(game.home_team_id),
            away_team_id=self._team(game.away_team_id),
            home_score=game.home_score,
            away_score=game.away_score,
        )

    def _team(self, team_id: int | None) -> int | None:
        return None if team_id is None else self.teams[team_id]

    def team_stats(self, stats: TeamStats) -> TeamStats:
        return replace(stats, team_id=self.teams[stats.team_id])

    def skater_report(self, report: SkaterReport) -> SkaterReport:
        return SkaterReport(
            skaters=[self.player_stats(stats) for stats in report.skaters],
            excluded=[self.player_stats(skater) for skater in report.excluded],
        )

    def player_stats(self, stats: _PlayerTeam) -> _PlayerTeam:
        return replace(
            stats,
            player_id=self.players[stats.player_id],
            team_id=self.teams[stats.team_id],
        )

    def unit_report(self, report: UnitReport) -> UnitReport:
        return UnitReport(
            units=[self._unit(unit) for unit in report.units],
            excluded=[self._unit(unit) for unit in report.excluded],
        )

    def _unit(self, unit: _Unit) -> _Unit:
        return replace(
            unit,
            team_id=self.teams[unit.team_id],
            player_ids=frozenset(self.players[id_] for id_ in unit.player_ids),
        )


_PlayerTeam = TypeVar("_PlayerTeam", SkaterStats, ExcludedSkater, GoalieStats)
_Unit = TypeVar("_Unit", UnitStats, ExcludedUnit)


# -- writing -------------------------------------------------------------


def write_bundle(path: str | os.PathLike[str], report: Report) -> None:
    """Write `report` as a bundle at `path` (conventionally ending in
    `BUNDLE_EXTENSION`), replacing any file there. Written to a temporary
    file first, so a failed export never leaves a half-written bundle."""
    names = [chart.name for chart in report.charts]
    if len(set(names)) != len(names):
        raise ValueError("chart names must be unique within a report")
    manifest = {
        "format": _FORMAT,
        "schema_version": SCHEMA_VERSION,
        "exported_at": (
            None if report.exported_at is None else report.exported_at.isoformat()
        ),
    }
    target = Path(path)
    handle, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".partial"
    )
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr(_MANIFEST, _dumps(manifest))
            bundle.writestr(_REPORT, _dumps(_report_json(report)))
            for chart in report.charts:
                bundle.writestr(_asset_path(chart.name), chart.png)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _asset_path(chart_name: str) -> str:
    return f"assets/{chart_name}.png"


def _report_json(report: Report) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "kind": report.kind.value,
        "summary_markdown": report.summary,
        "subject_team": report.subject_team_id,
        "subject_player": report.subject_player_id,
        "aggregation_mode": _value(report.aggregation_mode),
        "teams": [
            {"id": key, "name": ref.name, "league_id": ref.league_id}
            for key, ref in report.teams.items()
        ],
        "players": [
            {
                "id": key,
                "full_name": ref.full_name,
                "jersey_number": ref.jersey_number,
            }
            for key, ref in report.players.items()
        ],
        "games": [
            {
                "id": game.game_id,
                "date": None if game.date is None else game.date.isoformat(),
                "home_team": game.home_team_id,
                "away_team": game.away_team_id,
                "home_score": game.home_score,
                "away_score": game.away_score,
            }
            for game in report.games
        ],
        "on_ice_coverage": [
            {
                "team": team_id,
                "included_games": list(coverage.included),
                "excluded_games": list(coverage.excluded),
            }
            for team_id, coverage in report.on_ice_coverage.items()
        ],
        "unresolved_shift_changes": [
            {"team": team_id, "count": count}
            for team_id, count in report.unresolved_shift_changes.items()
        ],
        "charts": [
            {"name": chart.name, "path": _asset_path(chart.name)}
            for chart in report.charts
        ],
    }
    if report.team_stats is not None:
        obj["team_stats"] = {
            "strength_state": report.team_stats.strength_state,
            "teams": [_team_stats_json(stats) for stats in report.team_stats.stats],
        }
    if report.skater_stats is not None:
        obj["skater_stats"] = {
            "strength_state": report.skater_stats.strength_state,
            "skaters": [
                _skater_json(stats) for stats in report.skater_stats.stats.skaters
            ],
            "excluded": [
                {
                    "player": skater.player_id,
                    "team": skater.team_id,
                    "jersey_number": skater.jersey_number,
                    "reason": skater.reason,
                }
                for skater in report.skater_stats.stats.excluded
            ],
        }
    if report.goalie_stats is not None:
        obj["goalie_stats"] = {
            "strength_state": report.goalie_stats.strength_state,
            "goalies": [_goalie_json(stats) for stats in report.goalie_stats.stats],
        }
    if report.unit_stats is not None:
        strength = report.unit_stats.strength_state
        natural = isinstance(strength, UnitStrength)
        obj["unit_stats"] = {
            "strength_state": None if natural else strength,
            "natural_strength": natural,
            "units": [_unit_json(unit) for unit in report.unit_stats.stats.units],
            "excluded": [
                _unit_key_json(unit) | {"reason": unit.reason}
                for unit in report.unit_stats.stats.excluded
            ],
        }
    return obj


def _value(member: enum.Enum | None) -> Any:
    return None if member is None else member.value


def _for_against_json(stats: ForAgainst) -> dict[str, int]:
    return {"for": stats.for_, "against": stats.against}


def _team_stats_json(stats: TeamStats) -> dict[str, Any]:
    quality = stats.shot_quality
    return {
        "team": stats.team_id,
        "corsi": _for_against_json(stats.corsi),
        "fenwick": _for_against_json(stats.fenwick),
        "goals": _for_against_json(stats.goals),
        "shots_on_goal": _for_against_json(stats.shots_on_goal),
        "shot_quality": {
            "attempts": quality.attempts,
            "by_type": {
                shot_type.value: count for shot_type, count in quality.by_type.items()
            },
            "by_context": dict(quality.by_context),
            "high_danger": quality.high_danger,
            "located": quality.located,
        },
    }


def _skater_json(stats: SkaterStats) -> dict[str, Any]:
    return {
        "player": stats.player_id,
        "team": stats.team_id,
        "jersey_number": stats.jersey_number,
        "position": _value(stats.position),
        "corsi": _for_against_json(stats.corsi),
        "fenwick": _for_against_json(stats.fenwick),
        "goals": _for_against_json(stats.goals),
        "zone_starts": {
            "offensive": stats.zone_starts.offensive,
            "defensive": stats.zone_starts.defensive,
            "undetermined": stats.zone_starts.undetermined,
        },
    }


def _goalie_json(stats: GoalieStats) -> dict[str, Any]:
    return {
        "player": stats.player_id,
        "team": stats.team_id,
        "jersey_number": stats.jersey_number,
        "shots_against": stats.shots_against,
        "goals_against": stats.goals_against,
        "high_danger_shots_against": stats.high_danger_shots_against,
        "high_danger_goals_against": stats.high_danger_goals_against,
        "time_in_net_ms": stats.time_in_net_ms,
    }


def _unit_key_json(unit: UnitStats | ExcludedUnit) -> dict[str, Any]:
    return {
        "team": unit.team_id,
        "unit_type": unit.unit_type.value,
        "unit_number": unit.unit_number,
        "players": sorted(unit.player_ids),
    }


def _unit_json(unit: UnitStats) -> dict[str, Any]:
    return _unit_key_json(unit) | {
        "corsi": _for_against_json(unit.corsi),
        "fenwick": _for_against_json(unit.fenwick),
        "goals": _for_against_json(unit.goals),
        "time_together_ms": unit.time_together_ms,
    }


# -- reading -------------------------------------------------------------


def read_bundle(path: str | os.PathLike[str]) -> Report:
    """Open the bundle at `path`. Raises `BundleTooNewError` for a bundle
    from a newer schema version, and `InvalidBundleError` for anything
    that isn't a readable bundle. An older bundle opens best-effort: any
    field it predates is simply absent from the returned `Report`."""
    try:
        bundle = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise InvalidBundleError("not a report bundle (not a zip archive)") from error
    with bundle:
        manifest = _read_json(bundle, _MANIFEST)
        if not isinstance(manifest, dict) or manifest.get("format") != _FORMAT:
            raise InvalidBundleError("not a report bundle (unrecognized manifest)")
        version = manifest.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise InvalidBundleError("report bundle manifest has no schema version")
        if version > SCHEMA_VERSION:
            raise BundleTooNewError(version)
        report = _read_json(bundle, _REPORT)
        try:
            return _report_from_json(report, manifest, bundle.read)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise InvalidBundleError(f"damaged report bundle ({error!r})") from error


def _read_json(bundle: zipfile.ZipFile, name: str) -> Any:
    try:
        return json.loads(bundle.read(name))
    except KeyError as error:
        raise InvalidBundleError(f"not a report bundle (no {name})") from error
    except ValueError as error:
        raise InvalidBundleError(f"damaged report bundle ({name})") from error


def _report_from_json(
    obj: dict[str, Any],
    manifest: dict[str, Any],
    read_asset: Callable[[str], bytes],
) -> Report:
    exported_at = manifest.get("exported_at")
    aggregation_mode = obj.get("aggregation_mode")
    return Report(
        kind=ReportKind(obj["kind"]),
        summary=obj.get("summary_markdown", ""),
        exported_at=None
        if exported_at is None
        else datetime.fromisoformat(exported_at),
        teams={
            team["id"]: TeamRef(team["name"], team.get("league_id"))
            for team in obj.get("teams", [])
        },
        players={
            player["id"]: PlayerRef(
                player.get("full_name"), player.get("jersey_number")
            )
            for player in obj.get("players", [])
        },
        games=tuple(
            GameRef(
                game_id=game["id"],
                date=None
                if game.get("date") is None
                else date_.fromisoformat(game["date"]),
                home_team_id=game.get("home_team"),
                away_team_id=game.get("away_team"),
                home_score=game.get("home_score"),
                away_score=game.get("away_score"),
            )
            for game in obj.get("games", [])
        ),
        subject_team_id=obj.get("subject_team"),
        subject_player_id=obj.get("subject_player"),
        aggregation_mode=(
            None if aggregation_mode is None else AggregationMode(aggregation_mode)
        ),
        team_stats=_section(
            obj,
            "team_stats",
            lambda section: tuple(_team_stats(item) for item in section["teams"]),
        ),
        skater_stats=_section(obj, "skater_stats", _skater_report),
        goalie_stats=_section(
            obj,
            "goalie_stats",
            lambda section: tuple(_goalie(item) for item in section["goalies"]),
        ),
        unit_stats=_unit_section(obj.get("unit_stats")),
        on_ice_coverage={
            item["team"]: GameCoverage(
                included=tuple(item["included_games"]),
                excluded=tuple(item["excluded_games"]),
            )
            for item in obj.get("on_ice_coverage", [])
        },
        unresolved_shift_changes={
            item["team"]: item["count"]
            for item in obj.get("unresolved_shift_changes", [])
        },
        charts=tuple(
            Chart(chart["name"], read_asset(chart["path"]))
            for chart in obj.get("charts", [])
        ),
    )


def _section(
    obj: dict[str, Any], key: str, parse: Callable[[dict[str, Any]], _T]
) -> Filtered[_T] | None:
    section = obj.get(key)
    if section is None:
        return None
    return Filtered(section.get("strength_state"), parse(section))


def _unit_section(section: dict[str, Any] | None) -> Filtered[UnitReport] | None:
    if section is None:
        return None
    strength = (
        NATURAL_STRENGTH
        if section.get("natural_strength")
        else section.get("strength_state")
    )
    report = UnitReport(
        units=[_unit(item) for item in section["units"]],
        excluded=[
            ExcludedUnit(**_unit_key(item), reason=item["reason"])
            for item in section.get("excluded", [])
        ],
    )
    return Filtered(strength, report)


def _for_against(obj: dict[str, int]) -> ForAgainst:
    return ForAgainst(for_=obj["for"], against=obj["against"])


def _team_stats(obj: dict[str, Any]) -> TeamStats:
    quality = obj["shot_quality"]
    return TeamStats(
        team_id=obj["team"],
        corsi=_for_against(obj["corsi"]),
        fenwick=_for_against(obj["fenwick"]),
        goals=_for_against(obj["goals"]),
        shots_on_goal=_for_against(obj["shots_on_goal"]),
        shot_quality=ShotQuality(
            attempts=quality["attempts"],
            by_type={
                ShotType(shot_type): count
                for shot_type, count in quality["by_type"].items()
            },
            by_context=dict(quality["by_context"]),
            high_danger=quality["high_danger"],
            located=quality["located"],
        ),
    )


def _skater_report(section: dict[str, Any]) -> SkaterReport:
    return SkaterReport(
        skaters=[
            SkaterStats(
                player_id=item["player"],
                team_id=item["team"],
                jersey_number=item["jersey_number"],
                corsi=_for_against(item["corsi"]),
                fenwick=_for_against(item["fenwick"]),
                goals=_for_against(item["goals"]),
                zone_starts=ZoneStarts(**item["zone_starts"]),
                position=(
                    None if item.get("position") is None else Position(item["position"])
                ),
            )
            for item in section["skaters"]
        ],
        excluded=[
            ExcludedSkater(
                player_id=item["player"],
                team_id=item["team"],
                jersey_number=item["jersey_number"],
                reason=item["reason"],
            )
            for item in section.get("excluded", [])
        ],
    )


def _goalie(obj: dict[str, Any]) -> GoalieStats:
    return GoalieStats(
        player_id=obj["player"],
        team_id=obj["team"],
        jersey_number=obj["jersey_number"],
        shots_against=obj["shots_against"],
        goals_against=obj["goals_against"],
        high_danger_shots_against=obj["high_danger_shots_against"],
        high_danger_goals_against=obj["high_danger_goals_against"],
        time_in_net_ms=obj["time_in_net_ms"],
    )


def _unit_key(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "team_id": obj["team"],
        "unit_type": UnitType(obj["unit_type"]),
        "unit_number": obj["unit_number"],
        "player_ids": frozenset(obj["players"]),
    }


def _unit(obj: dict[str, Any]) -> UnitStats:
    return UnitStats(
        **_unit_key(obj),
        corsi=_for_against(obj["corsi"]),
        fenwick=_for_against(obj["fenwick"]),
        goals=_for_against(obj["goals"]),
        time_together_ms=obj["time_together_ms"],
    )
