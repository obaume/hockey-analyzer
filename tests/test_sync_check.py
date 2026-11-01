"""The Sync check (see CONTEXT.md's Sync check entry) as a pure function
over hand-built `GameData`: tagged goals and penalties matched against the
game's Official events by kind, period, team and elapsed time in the
period, and the findings that leaves."""

from __future__ import annotations

import pytest
from stats_fixtures import AWAY, HOME, UNKNOWN, GameBuilder

from hockey_analyzer.domain.enums import EventSource, OfficialEventKind, Side
from hockey_analyzer.domain.sync_check import (
    DRIFT_THRESHOLD_MS,
    MATCH_TOLERANCE_MS,
    FindingKind,
    sync_check,
)

SECOND = 1000
GOAL, PENALTY = OfficialEventKind.GOAL, OfficialEventKind.PENALTY
# Where each period starts in the footage; every period is played without
# a stoppage the tests don't log, so elapsed time is footage time since.
PERIOD_AT = {1: 0, 2: 2_000 * SECOND, 3: 4_000 * SECOND, 4: 6_000 * SECOND}

KIM, KIM_LICENCE = 14, "300014"
LEE = 9  # no licence number: compared by jersey
RIVAL, RIVAL_LICENCE = 91, "300091"


class _Game:
    """A game with home #14 Kim (licensed) and #9 Lee (not), and away #91
    (licensed), each period opened with a faceoff at `PERIOD_AT`."""

    def __init__(self, *, periods=3):
        self.builder = GameBuilder()
        self.kim = self.builder.player(HOME, KIM, league_id=KIM_LICENCE)
        self.lee = self.builder.player(HOME, LEE)
        self.rival = self.builder.player(AWAY, RIVAL, league_id=RIVAL_LICENCE)
        self._periods = periods

    def _at(self, period, seconds):
        return PERIOD_AT[period] + round(seconds * SECOND)

    def _open_periods(self):
        for number in range(1, self._periods + 1):
            self.builder.period_start(number, at=PERIOD_AT[number])
            self.builder.faceoff(0.0, at=PERIOD_AT[number])

    def goal(self, period, seconds, team=HOME, **kwargs):
        """A tagged goal `seconds` into `period`, play restarted at once."""
        at = self._at(period, seconds)
        goal = self.builder.goal(team, at=at, **kwargs)
        self.builder.faceoff(0.0, at=at)
        return goal

    def penalty(self, period, seconds, team=HOME, **kwargs):
        at = self._at(period, seconds)
        penalty = self.builder.penalty(team, at=at, **kwargs)
        self.builder.faceoff(0.0, at=at)
        return penalty

    def official(self, kind, period, seconds, side=Side.HOME, **kwargs):
        return self.builder.official(kind, period, seconds, side, **kwargs)

    def check(self):
        self._open_periods()
        # Restore video order: the period openers were added last.
        self.builder.events.sort(key=lambda event: (event.video_timestamp, event.id))
        return sync_check(self.builder.build())


def _kinds(result):
    return [finding.kind for finding in result.findings]


KIM_OFFICIAL = (KIM_LICENCE, KIM)
LEE_OFFICIAL = ("300009", LEE)


# -- no Official events -----------------------------------------------------------


def test_a_game_without_official_events_says_so():
    game = _Game()
    game.goal(1, 100, shooter=game.kim)

    result = game.check()

    assert result.has_official_events is False
    assert result.findings == ()


def test_matching_tagging_has_official_events_and_no_findings():
    game = _Game()
    game.goal(1, 100, shooter=game.kim)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL, strength_tag="EQ")

    result = game.check()

    assert result.has_official_events is True
    assert result.findings == ()


# -- missing and extra ------------------------------------------------------------


def test_an_untagged_official_goal_is_missing():
    game = _Game()
    official = game.official(GOAL, 2, 300, player=KIM_OFFICIAL)

    (finding,) = game.check().findings

    assert finding.kind is FindingKind.MISSING
    assert (finding.official, finding.period, finding.tagged_event_id) == (
        official,
        2,
        None,
    )


def test_a_tagged_penalty_with_no_official_one_is_extra():
    game = _Game()
    penalty = game.penalty(1, 400, player=game.kim)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)
    game.goal(1, 100, shooter=game.kim)

    (finding,) = game.check().findings

    assert finding.kind is FindingKind.EXTRA
    assert (finding.tagged_event_id, finding.period) == (penalty.id, 1)
    assert finding.tagged_elapsed_ms == 400 * SECOND


@pytest.mark.parametrize(
    ("tagged", "official"),
    [
        ((1, 100), (GOAL, 1, 100, Side.AWAY)),  # other team
        ((1, 100), (PENALTY, 1, 100, Side.HOME)),  # other kind
        ((2, 100), (GOAL, 1, 100, Side.HOME)),  # other period
    ],
)
def test_a_match_needs_the_same_kind_team_and_period(tagged, official):
    game = _Game()
    game.goal(*tagged, shooter=game.kim)
    kind, period, seconds, side = official
    game.official(kind, period, seconds, side, player=KIM_OFFICIAL)

    assert sorted(_kinds(game.check())) == [FindingKind.EXTRA, FindingKind.MISSING]


# -- tolerance ------------------------------------------------------------------


def test_tolerance_is_fifteen_seconds():
    assert MATCH_TOLERANCE_MS == 15 * SECOND


@pytest.mark.parametrize("offset", [15, -15])
def test_a_goal_right_at_the_tolerance_matches(offset):
    game = _Game()
    game.goal(1, 300 + offset, shooter=game.kim)
    game.official(GOAL, 1, 300, player=KIM_OFFICIAL)

    assert game.check().findings == ()


@pytest.mark.parametrize("offset", [15.001, -15.001])
def test_a_goal_just_past_the_tolerance_doesnt(offset):
    game = _Game()
    game.goal(1, 300 + offset, shooter=game.kim)
    game.official(GOAL, 1, 300, player=KIM_OFFICIAL)

    assert sorted(_kinds(game.check())) == [FindingKind.EXTRA, FindingKind.MISSING]


# -- nearest first, one to one --------------------------------------------------


def test_the_nearest_official_goal_is_matched():
    game = _Game()
    game.goal(1, 104, shooter=game.kim)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)
    far = game.official(GOAL, 1, 110, player=KIM_OFFICIAL)

    (finding,) = game.check().findings

    assert (finding.kind, finding.official) == (FindingKind.MISSING, far)


def test_an_equally_near_tie_goes_to_the_earlier_official_goal():
    game = _Game()
    game.goal(1, 105, shooter=game.kim)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)
    later = game.official(GOAL, 1, 110, player=KIM_OFFICIAL)

    (finding,) = game.check().findings

    assert (finding.kind, finding.official) == (FindingKind.MISSING, later)


def test_two_goals_close_together_are_not_swapped():
    game = _Game()
    game.goal(1, 101, shooter=game.kim)
    game.goal(1, 109, shooter=game.lee)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)
    game.official(GOAL, 1, 110, player=LEE_OFFICIAL)

    assert game.check().findings == ()


def test_matching_is_one_to_one():
    game = _Game()
    game.goal(1, 100, shooter=game.kim)
    second = game.goal(1, 102, shooter=game.kim)
    game.official(GOAL, 1, 101, player=KIM_OFFICIAL)

    (finding,) = game.check().findings

    assert (finding.kind, finding.tagged_event_id) == (FindingKind.EXTRA, second.id)


# -- elapsed time -----------------------------------------------------------------


def test_an_overtime_goal_matches_on_elapsed_time_in_overtime():
    game = _Game(periods=4)
    game.goal(4, 356, team=AWAY, shooter=game.rival)
    game.official(GOAL, 4, 356, Side.AWAY, player=(RIVAL_LICENCE, RIVAL))

    assert game.check().findings == ()


def test_stoppages_dont_count_toward_elapsed_time():
    game = _Game()
    game.builder.stoppage(at=50 * SECOND)
    game.builder.faceoff(0.0, at=80 * SECOND)
    game.goal(1, 130, shooter=game.kim)  # 100 s of live play
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)

    assert game.check().findings == ()


# -- unconfirmed vision events ----------------------------------------------------


def test_an_unconfirmed_vision_goal_takes_no_part():
    game = _Game()
    game.goal(1, 100, shooter=game.kim, source=EventSource.VISION)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)

    assert _kinds(game.check()) == [FindingKind.MISSING]


def test_a_confirmed_vision_goal_takes_part():
    game = _Game()
    game.goal(1, 100, shooter=game.kim, source=EventSource.VISION, confirmed=True)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)

    assert game.check().findings == ()


def test_an_unconfirmed_vision_stoppage_doesnt_stop_the_clock():
    game = _Game()
    game.builder.stoppage(at=50 * SECOND).source = EventSource.VISION
    game.goal(1, 100, shooter=game.kim)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)

    assert game.check().findings == ()


# -- skipped --------------------------------------------------------------------


def test_goalie_changes_are_not_checked():
    game = _Game()
    game.official(OfficialEventKind.GOALIE_CHANGE, 3, 1100, player=KIM_OFFICIAL)

    result = game.check()

    assert result.has_official_events is True
    assert result.findings == ()


# -- drift ----------------------------------------------------------------------


def test_a_clock_gap_growing_over_a_period_is_drift():
    game = _Game()
    tagged = [
        game.goal(2, seconds, shooter=game.kim) for seconds in (100, 304, 508, 712)
    ]
    for seconds in (100, 300, 500, 700):
        game.official(GOAL, 2, seconds, player=KIM_OFFICIAL)

    (finding,) = game.check().findings

    assert finding.kind is FindingKind.DRIFT
    assert finding.period == 2
    assert finding.drift_ms == 12 * SECOND
    # Where the gap first grows the most: the place to look for the missed
    # stoppage.
    assert finding.tagged_event_id == tagged[1].id


def test_drift_points_at_the_biggest_jump_in_the_gap():
    game = _Game()
    tagged = [
        game.goal(1, seconds, shooter=game.kim) for seconds in (100, 301, 512, 713)
    ]
    for seconds in (100, 300, 500, 700):
        game.official(GOAL, 1, seconds, player=KIM_OFFICIAL)

    (finding,) = game.check().findings

    assert finding.tagged_event_id == tagged[2].id


def test_a_steady_clock_gap_is_no_drift():
    game = _Game()
    for seconds in (100, 300, 500):
        game.goal(1, seconds + 10, shooter=game.kim)
        game.official(GOAL, 1, seconds, player=KIM_OFFICIAL)

    assert game.check().findings == ()


def test_a_gap_growing_less_than_the_threshold_is_no_drift():
    assert DRIFT_THRESHOLD_MS == 5 * SECOND
    game = _Game()
    for seconds, gap in ((100, 0), (300, 2), (500, 4)):
        game.goal(1, seconds + gap, shooter=game.kim)
        game.official(GOAL, 1, seconds, player=KIM_OFFICIAL)

    assert game.check().findings == ()


def test_drift_is_per_period():
    game = _Game()
    game.goal(1, 100, shooter=game.kim)
    game.goal(2, 712, shooter=game.kim)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)
    game.official(GOAL, 2, 700, player=KIM_OFFICIAL)

    assert game.check().findings == ()


# -- disagreements on players -----------------------------------------------------


def _disagreement(result):
    (finding,) = result.findings
    assert finding.kind is FindingKind.DISAGREEMENT
    return finding


def test_a_different_licensed_scorer_is_a_disagreement():
    game = _Game()
    goal = game.goal(1, 100, shooter=game.kim)
    official = game.official(GOAL, 1, 100, player=("300077", 77))

    finding = _disagreement(game.check())

    assert (finding.field, finding.tagged_event_id, finding.official) == (
        "shooter",
        goal.id,
        official,
    )
    assert finding.fillable is False


def test_a_player_without_a_licence_number_is_compared_by_jersey():
    game = _Game()
    game.goal(1, 100, shooter=game.lee)
    game.goal(1, 300, shooter=game.lee)
    game.official(GOAL, 1, 100, player=("300009", LEE))
    game.official(GOAL, 1, 300, player=("300010", 10))

    finding = _disagreement(game.check())

    assert finding.official.elapsed_seconds == 300


def test_a_licensed_player_is_compared_by_licence_not_jersey():
    game = _Game()
    game.goal(1, 100, shooter=game.kim)
    game.official(GOAL, 1, 100, player=(KIM_LICENCE, 99))

    assert game.check().findings == ()


def test_an_unknown_scorer_is_a_fillable_disagreement():
    game = _Game()
    game.goal(1, 100, shooter=UNKNOWN)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)

    finding = _disagreement(game.check())

    assert (finding.field, finding.fillable) == ("shooter", True)


def test_an_unknown_assist_is_a_fillable_disagreement():
    game = _Game()
    game.goal(1, 100, shooter=game.kim, assist1=UNKNOWN)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL, assist1=LEE_OFFICIAL)

    finding = _disagreement(game.check())

    assert (finding.field, finding.fillable) == ("assist1", True)


def test_a_missing_assist_is_a_disagreement_but_not_fillable():
    game = _Game()
    game.goal(1, 100, shooter=game.kim)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL, assist2=LEE_OFFICIAL)

    finding = _disagreement(game.check())

    assert (finding.field, finding.fillable) == ("assist2", False)


def test_an_assist_the_league_doesnt_list_is_a_disagreement():
    game = _Game()
    game.goal(1, 100, shooter=game.kim, assist1=game.lee)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)

    finding = _disagreement(game.check())

    assert (finding.field, finding.fillable) == ("assist1", False)


def test_a_different_penalised_player_is_a_disagreement():
    game = _Game()
    game.penalty(1, 100, team=AWAY, player=game.rival)
    game.official(PENALTY, 1, 100, Side.AWAY, player=("300092", 92))

    finding = _disagreement(game.check())

    assert (finding.field, finding.fillable) == ("player", False)


def test_an_unknown_penalised_player_is_fillable():
    game = _Game()
    game.penalty(1, 100, team=AWAY)
    game.official(PENALTY, 1, 100, Side.AWAY, player=(RIVAL_LICENCE, RIVAL))

    assert _disagreement(game.check()).fillable is True


# -- disagreements on strength ----------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "strength", "team", "agrees"),
    [
        ("EQ", "5v5", HOME, True),
        ("EQ", "4v4", AWAY, True),
        ("EQ", "5v4", HOME, False),
        ("PP1", "5v4", HOME, True),
        ("PP2", "5v3", HOME, True),
        ("PP1", "4v5", AWAY, True),
        ("PP1", "5v5", HOME, False),
        ("PP1", "4v5", HOME, False),
        ("SH1", "4v5", HOME, True),
        ("SH1", "5v4", AWAY, True),
        ("SH1", "5v5", HOME, False),
        # An empty net alone: the strength before the goalie was pulled
        # is unknown, so it says nothing.
        ("EN", "5v6", HOME, True),
        ("EN", "5v5", HOME, True),
        # Several tags, as the league prints them: strength first, and
        # only the strength ones say anything about it...
        ("PP1,GWG", "5v4", HOME, True),
        ("PP1,GWG", "5v5", HOME, False),
        ("EQ,GWG", "5v5", AWAY, True),
        ("GWG,SH1", "4v5", HOME, True),
        # ...and an empty net, wherever it is, gives the other team one
        # extra skater on top of what the strength tag says.
        ("EQ,EN", "5v6", HOME, True),
        ("EQ,EN", "6v5", AWAY, True),
        ("EQ,EN", "5v5", HOME, False),
        ("PP1,EN", "5v5", HOME, True),
        ("PP1,EN", "5v6", HOME, False),
        ("SH1, EN,GWG", "4v6", HOME, True),
        ("SH1, EN,GWG", "5v6", HOME, False),
    ],
)
def test_the_strength_tag_maps_onto_skater_counts(tag, strength, team, agrees):
    game = _Game()
    if team == HOME:
        game.goal(1, 100, shooter=game.kim, strength=strength)
        game.official(GOAL, 1, 100, player=KIM_OFFICIAL, strength_tag=tag)
    else:
        game.goal(1, 100, team=AWAY, shooter=game.rival, strength=strength)
        game.official(
            GOAL, 1, 100, Side.AWAY, player=(RIVAL_LICENCE, RIVAL), strength_tag=tag
        )

    result = game.check()

    if agrees:
        assert result.findings == ()
    else:
        finding = _disagreement(result)
        assert (finding.field, finding.fillable) == ("strength", False)


@pytest.mark.parametrize(
    ("tag", "strength"),
    [("EQ", None), ("EQ", "PP"), ("PS", "5v5"), ("GWG", "5v4"), (None, "5v4")],
)
def test_a_strength_that_cant_be_read_is_not_compared(tag, strength):
    game = _Game()
    game.goal(1, 100, shooter=game.kim, strength=strength)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL, strength_tag=tag)

    assert game.check().findings == ()


def test_penalties_are_not_compared_on_strength():
    game = _Game()
    game.penalty(1, 100, player=game.kim).strength_state = "5v4"
    game.official(PENALTY, 1, 100, player=KIM_OFFICIAL, strength_tag="PP1")

    assert game.check().findings == ()


def test_findings_are_grouped_by_kind():
    game = _Game()
    game.goal(1, 100, shooter=UNKNOWN)
    game.goal(1, 900, shooter=game.kim)
    game.official(GOAL, 1, 100, player=KIM_OFFICIAL)
    game.official(GOAL, 3, 50, player=KIM_OFFICIAL)

    result = game.check()

    assert _kinds(result) == [
        FindingKind.MISSING,
        FindingKind.EXTRA,
        FindingKind.DISAGREEMENT,
    ]
    assert [f.kind for f in result.of_kind(FindingKind.EXTRA)] == [FindingKind.EXTRA]
