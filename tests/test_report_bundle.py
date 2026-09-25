"""Report bundle (ticket 25): `build_*_report` turns tagged games into a
frozen, id-free `Report` snapshot, and `write_bundle`/`read_bundle` move it
to and from the zip file on disk (see CONTEXT.md's Report bundle entries)."""

from __future__ import annotations

import datetime
import json
import zipfile

import pytest
from stats_fixtures import AWAY, HOME, GameBuilder

from hockey_analyzer.domain.enums import Position, ShotOutcome, ShotType, UnitType
from hockey_analyzer.domain.models import Team
from hockey_analyzer.domain.report_bundle import (
    AggregationMode,
    BundleTooNewError,
    Chart,
    InvalidBundleError,
    PlayerRef,
    ReportKind,
    StrengthFilters,
    TeamRef,
    build_game_report,
    build_player_report,
    build_team_report,
    read_bundle,
    write_bundle,
)
from hockey_analyzer.domain.stats_engine import (
    ALL_SITUATIONS,
    NATURAL_STRENGTH,
    OPPONENT_SHIFTS_INCOMPLETE,
    ForAgainst,
    GameCoverage,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"fake chart pixels"


def _small_game(**kwargs):
    game = GameBuilder(**kwargs)
    center = game.player(HOME, 14, name="Jordan Kim")
    game.faceoff(0.0)
    game.shift(HOME, center, True)
    game.shot(HOME)
    game.shot(AWAY)
    game.shot(HOME)
    return game.build()


def test_game_report_round_trips_through_the_bundle_file(tmp_path):
    report = build_game_report(
        _small_game(),
        summary="# Great game\n\nWe **outshot** them.",
        charts=[Chart("shot-map", PNG)],
    )
    path = tmp_path / "report.hockeyreport"

    write_bundle(path, report)

    assert read_bundle(path) == report


def test_bundle_is_a_zip_of_manifest_report_and_png_assets(tmp_path):
    report = build_game_report(
        _small_game(), summary="", charts=[Chart("shot-map", PNG)]
    )
    path = tmp_path / "report.hockeyreport"

    write_bundle(path, report)

    with zipfile.ZipFile(path) as bundle:
        assert sorted(bundle.namelist()) == [
            "assets/shot-map.png",
            "manifest.json",
            "report.json",
        ]
        assert bundle.read("assets/shot-map.png") == PNG
        manifest = json.loads(bundle.read("manifest.json"))
        report_json = bundle.read("report.json").decode()
    assert manifest["schema_version"] == 1
    assert "fake chart pixels" not in report_json


def test_game_report_carries_the_computed_skater_numbers():
    report = build_game_report(_small_game(), summary="")

    (skater,) = report.skater_stats.stats.skaters
    assert skater.corsi == ForAgainst(2, 1)
    assert report.players[skater.player_id].full_name == "Jordan Kim"
    assert report.players[skater.player_id].jersey_number == 14


def _round_trip(report, tmp_path):
    path = tmp_path / "report.hockeyreport"
    write_bundle(path, report)
    return read_bundle(path)


def test_team_identity_is_league_id_when_known_else_name():
    data = _small_game()
    data.game.home_team = Team(id=HOME, name="Icebreakers", league_id="sihf-77")
    data.game.away_team = Team(id=AWAY, name="Rivals")

    report = build_game_report(data, summary="")

    home, away = report.teams.values()
    assert home == TeamRef("Icebreakers", "sihf-77")
    assert home.identity == ("league_id", "sihf-77")
    assert away == TeamRef("Rivals")
    assert away.identity == ("name", "Rivals")


def test_players_are_carried_as_name_and_jersey_never_as_database_ids(tmp_path):
    game = GameBuilder()
    game.player(HOME, 14, name="Jordan Kim", player_id=501)
    game.player(HOME, 30, name=None, player_id=502, position=Position.GOALIE)

    report = _round_trip(build_game_report(game.build(), summary=""), tmp_path)

    assert sorted(report.players.values(), key=lambda ref: ref.jersey_number) == [
        PlayerRef("Jordan Kim", 14),
        PlayerRef(None, 30),
    ]
    assert not {501, 502} & set(report.players)
    (skater,) = report.skater_stats.stats.skaters
    (goalie,) = report.goalie_stats.stats
    assert report.players[skater.player_id] == PlayerRef("Jordan Kim", 14)
    assert report.players[goalie.player_id] == PlayerRef(None, 30)


def test_game_report_has_team_skater_goalie_and_shot_quality_stats(tmp_path):
    game = GameBuilder()
    skater = game.player(HOME, 14)
    goalie = game.player(AWAY, 1, position=Position.GOALIE)
    game.faceoff(0.0)
    game.shift(HOME, skater, True)
    game.shift(AWAY, goalie, True)
    game.shot(HOME, ShotOutcome.GOAL, shot_type=ShotType.SLAP, rebound=True)
    game.shot(HOME, ShotOutcome.SAVED)
    game.shot(AWAY, ShotOutcome.MISSED)

    report = _round_trip(build_game_report(game.build(), summary=""), tmp_path)

    home_stats, away_stats = report.team_stats.stats
    assert home_stats.corsi == ForAgainst(2, 1)
    assert home_stats.goals == ForAgainst(1, 0)
    assert home_stats.shot_quality.by_type == {ShotType.SLAP: 1, ShotType.WRIST: 1}
    assert home_stats.shot_quality.by_context["rebound"] == 1
    assert away_stats.corsi == ForAgainst(1, 2)
    (skater_stats,) = report.skater_stats.stats.skaters
    assert skater_stats.plus_minus == 1
    (goalie_stats,) = report.goalie_stats.stats
    assert (goalie_stats.shots_against, goalie_stats.goals_against) == (2, 1)


def test_each_stat_group_keeps_its_own_strength_filter(tmp_path):
    game = GameBuilder()
    skater = game.player(HOME, 14)
    goalie = game.player(AWAY, 1, position=Position.GOALIE)
    game.faceoff(0.0)
    game.shift(HOME, skater, True)
    game.shift(AWAY, goalie, True)
    game.shot(HOME, strength="5v5")
    game.shot(HOME, strength="5v4")
    filters = StrengthFilters(team="5v4", skaters="5v5", goalies=ALL_SITUATIONS)

    report = _round_trip(
        build_game_report(game.build(), summary="", filters=filters), tmp_path
    )

    assert report.team_stats.strength_state == "5v4"
    assert report.team_stats.stats[0].corsi == ForAgainst(1, 0)
    assert report.skater_stats.strength_state == "5v5"
    assert report.skater_stats.stats.skaters[0].corsi == ForAgainst(1, 0)
    assert report.goalie_stats.strength_state is ALL_SITUATIONS
    assert report.goalie_stats.stats[0].shots_against == 2


def test_incomplete_data_caveats_survive_the_round_trip(tmp_path):
    game = GameBuilder(opponent_shifts_complete=False)
    home_skater = game.player(HOME, 14)
    game.player(AWAY, 22)
    game.faceoff(0.0)
    game.shift(HOME, home_skater, True)
    game.shift(HOME, None, True, unknown=True)
    game.shift(HOME, None, False, unknown=True)
    game.shot(HOME)

    report = _round_trip(build_game_report(game.build(), summary=""), tmp_path)

    (excluded,) = report.skater_stats.stats.excluded
    assert excluded.jersey_number == 22
    assert excluded.reason == OPPONENT_SHIFTS_INCOMPLETE
    home_key, away_key = report.teams
    assert report.unresolved_shift_changes == {home_key: 2}
    assert report.on_ice_coverage[away_key].excluded == (1,)


def _two_game_season():
    """Home plays two games against different opponents; the second
    opponent's shifts were never marked complete."""
    first = GameBuilder(game_id=10, away=2, opponent_shifts_complete=True)
    second = GameBuilder(game_id=11, away=3)
    for game, (date, home_score, away_score) in (
        (first, (datetime.date(2026, 1, 10), 3, 1)),
        (second, (datetime.date(2026, 1, 17), 2, 4)),
    ):
        game.game.date = date
        game.game.home_score, game.game.away_score = home_score, away_score
        game.game.home_team = Team(id=HOME, name="Icebreakers")
        center = game.player(HOME, 14, name="Jordan Kim", player_id=7)
        wing = game.player(HOME, 17, name="Sam Lee", player_id=8)
        game.player(game.game.away_team_id, 4, player_id=90 + game.game.id)
        game.unit(HOME, UnitType.FORWARD_LINE, 1, center, wing)
        game.faceoff(0.0)
        game.shift(HOME, center, True)
        game.shift(HOME, wing, True)
        game.shot(HOME)
        game.shot(game.game.away_team_id)
        game.shot(HOME)
    first.game.away_team = Team(id=2, name="Rivals")
    second.game.away_team = Team(id=3, name="Sharks", league_id="sihf-3")
    return [first.build(), second.build()]


def test_team_report_aggregates_over_the_picked_games_with_provenance(tmp_path):
    report = _round_trip(
        build_team_report(_two_game_season(), HOME, summary="Two-game swing."),
        tmp_path,
    )

    assert report.kind is ReportKind.TEAM
    assert report.aggregation_mode is AggregationMode.SUM_THEN_COMPUTE
    assert report.teams[report.subject_team_id] == TeamRef("Icebreakers")
    home = report.subject_team_id
    rivals, sharks = (
        key for key, ref in report.teams.items() if ref.name in ("Rivals", "Sharks")
    )
    first, second = report.games
    assert (first.date, first.home_score, first.away_score) == (
        datetime.date(2026, 1, 10),
        3,
        1,
    )
    assert first.opponent_of(home) == rivals
    assert second.opponent_of(home) == sharks
    assert (second.home_score, second.away_score) == (2, 4)
    assert report.on_ice_coverage[home] == GameCoverage(
        included=(first.game_id, second.game_id), excluded=()
    )
    assert report.on_ice_coverage[sharks] == GameCoverage(
        included=(), excluded=(second.game_id,)
    )

    home_stats = next(s for s in report.team_stats.stats if s.team_id == home)
    assert home_stats.corsi == ForAgainst(4, 2)
    kim = next(
        s
        for s in report.skater_stats.stats.skaters
        if report.players[s.player_id].full_name == "Jordan Kim"
    )
    assert kim.corsi == ForAgainst(4, 2)
    (line,) = report.unit_stats.stats.units
    assert report.unit_stats.strength_state is NATURAL_STRENGTH
    assert {report.players[p].full_name for p in line.player_ids} == {
        "Jordan Kim",
        "Sam Lee",
    }
    assert line.corsi == ForAgainst(4, 2)


def test_player_report_names_its_subject_player(tmp_path):
    report = _round_trip(
        build_player_report(_two_game_season(), 8, summary=""), tmp_path
    )

    assert report.kind is ReportKind.PLAYER
    assert report.players[report.subject_player_id] == PlayerRef("Sam Lee", 17)
    assert len(report.games) == 2


def test_reports_for_teams_or_players_outside_the_picked_games_are_refused():
    with pytest.raises(ValueError):
        build_team_report(_two_game_season(), 99, summary="")
    with pytest.raises(ValueError):
        build_player_report(_two_game_season(), 99, summary="")


def test_game_report_without_unit_assignments_has_no_unit_section():
    assert build_game_report(_small_game(), summary="").unit_stats is None


def _keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _keys(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _keys(value)


def test_rapm_and_xg_dependent_stats_are_not_written_at_all(tmp_path):
    path = tmp_path / "report.hockeyreport"
    write_bundle(path, build_team_report(_two_game_season(), HOME, summary=""))

    with zipfile.ZipFile(path) as bundle:
        report_json = json.loads(bundle.read("report.json"))
    assert not [
        key
        for key in _keys(report_json)
        if "rapm" in key.lower() or "xg" in key.lower()
    ]


def test_summary_markdown_is_stored_verbatim(tmp_path):
    summary = "## Takeaways\n\n- PK was *sharp*\n- Ünïcode ✓\n"

    report = _round_trip(build_game_report(_small_game(), summary=summary), tmp_path)

    assert report.summary == summary


def test_charts_must_be_png_with_a_file_safe_name():
    with pytest.raises(ValueError):
        Chart("shot-map", b"GIF89a...")
    with pytest.raises(ValueError):
        Chart("../escape", PNG)


def _synthetic_bundle(path, manifest, report_json):
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("report.json", json.dumps(report_json))
    return path


_FORMAT = "hockey-analyzer-report-bundle"
_MINIMAL_REPORT = {"kind": "game", "summary_markdown": "Old news."}


def test_bundle_from_a_newer_schema_version_is_rejected_with_a_clear_message(
    tmp_path,
):
    path = _synthetic_bundle(
        tmp_path / "future.hockeyreport",
        {"format": _FORMAT, "schema_version": 2},
        _MINIMAL_REPORT | {"brand_new_section": {"anything": 1}},
    )

    with pytest.raises(BundleTooNewError, match="Update the app") as rejection:
        read_bundle(path)
    assert rejection.value.schema_version == 2


def test_bundle_from_an_older_schema_version_opens_with_newer_fields_absent(
    tmp_path,
):
    path = _synthetic_bundle(
        tmp_path / "old.hockeyreport",
        {"format": _FORMAT, "schema_version": 0},
        {
            "kind": "game",
            "summary_markdown": "Old news.",
            "teams": [{"id": 1, "name": "Icebreakers"}],
            "skater_stats": {
                "strength_state": "5v5",
                "skaters": [
                    {
                        "player": 1,
                        "team": 1,
                        "jersey_number": 9,
                        "corsi": {"for": 3, "against": 1},
                        "fenwick": {"for": 2, "against": 1},
                        "goals": {"for": 1, "against": 0},
                        "zone_starts": {
                            "offensive": 1,
                            "defensive": 0,
                            "undetermined": 0,
                        },
                    }
                ],
            },
        },
    )

    report = read_bundle(path)

    assert report.summary == "Old news."
    assert report.teams == {1: TeamRef("Icebreakers")}
    (skater,) = report.skater_stats.stats.skaters
    assert skater.corsi == ForAgainst(3, 1)
    assert skater.position is None
    assert report.skater_stats.stats.excluded == []
    assert report.exported_at is None
    assert report.team_stats is None
    assert report.goalie_stats is None
    assert report.unit_stats is None
    assert report.players == {}
    assert report.charts == ()
    assert report.unresolved_shift_changes == {}


@pytest.mark.parametrize(
    "manifest",
    [
        {"schema_version": 1},
        {"format": _FORMAT},
        {"format": _FORMAT, "schema_version": "1"},
    ],
)
def test_a_bundle_without_a_recognizable_manifest_is_invalid(tmp_path, manifest):
    path = _synthetic_bundle(tmp_path / "odd.hockeyreport", manifest, _MINIMAL_REPORT)

    with pytest.raises(InvalidBundleError):
        read_bundle(path)


def test_a_file_that_is_not_a_zip_is_invalid(tmp_path):
    path = tmp_path / "notes.hockeyreport"
    path.write_text("just some text")

    with pytest.raises(InvalidBundleError):
        read_bundle(path)


def test_a_bundle_missing_a_chart_asset_it_lists_is_invalid(tmp_path):
    path = _synthetic_bundle(
        tmp_path / "broken.hockeyreport",
        {"format": _FORMAT, "schema_version": 1},
        _MINIMAL_REPORT
        | {"charts": [{"name": "shot-map", "path": "assets/shot-map.png"}]},
    )

    with pytest.raises(InvalidBundleError):
        read_bundle(path)


def _corrupt_member(path, name):
    """Flip a byte inside `name`'s stored data, leaving the archive's
    directory intact -- as a damaged download or disk would."""
    with zipfile.ZipFile(path) as bundle:
        info = bundle.getinfo(name)
    raw = bytearray(path.read_bytes())
    # Local file header: 30 fixed bytes, then the name and extra field,
    # whose lengths sit at offsets 26 and 28.
    header = info.header_offset
    name_length = int.from_bytes(raw[header + 26 : header + 28], "little")
    extra_length = int.from_bytes(raw[header + 28 : header + 30], "little")
    data = header + 30 + name_length + extra_length
    raw[data + info.compress_size // 2] ^= 0xFF
    path.write_bytes(bytes(raw))


@pytest.mark.parametrize(
    "member", ["manifest.json", "report.json", "assets/shot-map.png"]
)
def test_a_bundle_with_a_corrupted_member_is_invalid(tmp_path, member):
    path = tmp_path / "damaged.hockeyreport"
    write_bundle(
        path,
        build_game_report(_small_game(), summary="", charts=[Chart("shot-map", PNG)]),
    )
    _corrupt_member(path, member)

    with pytest.raises(InvalidBundleError):
        read_bundle(path)
