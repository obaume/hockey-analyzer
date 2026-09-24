"""ClipExporter selection/plan logic (ticket 23) against hand-built
transient ORM objects in a `GameData`, never touching a database or real
footage: the encode step is a mock asserted against the plan's segments
and output paths."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from hockey_analyzer.domain import clip_export
from hockey_analyzer.domain.clip_export import (
    ClipEncoder,
    ClipFilter,
    ClipSegment,
    OutputShape,
    Padding,
    plan_export,
    run_export,
    select_clips,
)
from hockey_analyzer.domain.enums import EventSource, EventType, ShotOutcome, ShotType
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import (
    Faceoff,
    Game,
    GameRosterEntry,
    Penalty,
    PeriodStart,
    Player,
    ShiftChange,
    ShotAttempt,
    Stoppage,
    Team,
)

SECOND = 1000
FOOTAGE_MS = 3600 * SECOND
OUT = Path("exports")
NO_PADDING = Padding(before_ms=0, after_ms=0)

HOME = 1
AWAY = 2
ALICE = 100
BOB = 101
CARL = 102


class _Game:
    """One game's events and roster. Events get ids in the order added;
    `at` is in seconds of footage. A period 1 start and opening faceoff at
    0s are logged up front so every event has a game clock."""

    def __init__(self, *, user_side=HOME, game_date=date(2026, 9, 20)):
        home = Team(id=HOME, name="Ice Breakers", is_user_team=user_side == HOME)
        away = Team(id=AWAY, name="Rivals HC", is_user_team=user_side == AWAY)
        self.game = Game(
            id=1,
            date=game_date,
            home_team_id=HOME,
            away_team_id=AWAY,
            home_team=home,
            away_team=away,
            video_path="C:/footage/game.mp4",
        )
        self.events = []
        self.roster = [
            self._entry(ALICE, HOME, 9, "Alice Müller"),
            self._entry(BOB, HOME, 17, "Bob Smith"),
            self._entry(CARL, AWAY, 4, None),
        ]
        self.add(PeriodStart(period_number=1), at=0)
        self.opening_faceoff = self.faceoff(at=0)

    def _entry(self, player_id, team_id, jersey, name):
        return GameRosterEntry(
            game_id=self.game.id,
            player_id=player_id,
            team_id=team_id,
            jersey_number=jersey,
            player=Player(id=player_id, full_name=name),
        )

    def add(self, event, *, at, confirmed=True, source=EventSource.MANUAL):
        event.id = len(self.events) + 1
        event.game_id = self.game.id
        event.video_timestamp = at * SECOND
        event.confirmed = confirmed
        event.source = source
        self.events.append(event)
        return event

    def shot(
        self,
        at,
        *,
        shooter=None,
        assists=(),
        outcome=ShotOutcome.SAVED,
        team=HOME,
        **kw,
    ):
        assist1, assist2 = (*assists, None, None)[:2]
        return self.add(
            ShotAttempt(
                shot_team_id=team,
                shot_outcome=outcome,
                shot_type=ShotType.WRIST,
                shooter_id=shooter,
                shooter_unknown=shooter is None,
                assist1_id=assist1,
                assist2_id=assist2,
            ),
            at=at,
            **kw,
        )

    def faceoff(self, at, *, a=None, b=None):
        return self.add(
            Faceoff(
                faceoff_participant_a_id=a,
                faceoff_participant_a_unknown=a is None,
                faceoff_participant_b_id=b,
                faceoff_participant_b_unknown=b is None,
            ),
            at=at,
        )

    def penalty(self, at, *, player):
        return self.add(Penalty(penalty_team_id=AWAY, penalty_player_id=player), at=at)

    def shift(self, at, *, player, on=True):
        return self.add(
            ShiftChange(shift_team_id=HOME, shift_player_id=player, shift_on_ice=on),
            at=at,
        )

    def data(self):
        return GameData(game=self.game, events=self.events, roster=self.roster)


def _ids(events):
    return [event.id for event in events]


def _plan(game, selection, **kw):
    kw.setdefault("shape", OutputShape.PER_CLIP)
    kw.setdefault("footage_duration_ms", FOOTAGE_MS)
    kw.setdefault("output_dir", OUT)
    kw.setdefault("padding", NO_PADDING)
    return plan_export(game.data(), selection, **kw)


# -- Filters ----------------------------------------------------------------


def test_no_filter_matches_every_event_in_video_order():
    game = _Game()
    late = game.shot(50)
    early = game.shot(20)

    selection = select_clips(game.data(), ClipFilter())

    assert _ids(selection.candidates) == [1, 2, early.id, late.id]


def test_event_type_filter():
    game = _Game()
    shot = game.shot(20)
    game.penalty(30, player=CARL)

    selection = select_clips(game.data(), ClipFilter(event_type=EventType.SHOT_ATTEMPT))

    assert _ids(selection.candidates) == [shot.id]


def test_outcome_filter_matches_only_shot_attempts_with_that_outcome():
    game = _Game()
    goal = game.shot(20, outcome=ShotOutcome.GOAL)
    game.shot(30, outcome=ShotOutcome.MISSED)
    game.penalty(40, player=CARL)

    selection = select_clips(game.data(), ClipFilter(shot_outcome=ShotOutcome.GOAL))

    assert _ids(selection.candidates) == [goal.id]


@pytest.mark.parametrize(
    "make_event",
    [
        pytest.param(lambda g: g.shot(20, shooter=ALICE), id="shooter"),
        pytest.param(lambda g: g.shot(20, shooter=BOB, assists=[ALICE]), id="assist1"),
        pytest.param(
            lambda g: g.shot(20, shooter=BOB, assists=[BOB, ALICE]), id="assist2"
        ),
        pytest.param(lambda g: g.shift(20, player=ALICE), id="shift participant"),
        pytest.param(lambda g: g.faceoff(20, a=ALICE, b=CARL), id="faceoff side a"),
        pytest.param(lambda g: g.faceoff(20, a=CARL, b=ALICE), id="faceoff side b"),
        pytest.param(lambda g: g.penalty(20, player=ALICE), id="penalized player"),
    ],
)
def test_player_filter_matches_the_player_in_any_role(make_event):
    game = _Game()
    event = make_event(game)
    game.shot(30, shooter=BOB)

    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    assert _ids(selection.candidates) == [event.id]


def test_filters_combine_with_and_semantics():
    game = _Game()
    alice_goal = game.shot(20, shooter=ALICE, outcome=ShotOutcome.GOAL)
    game.shot(30, shooter=ALICE, outcome=ShotOutcome.SAVED)
    game.shot(40, shooter=BOB, outcome=ShotOutcome.GOAL)
    game.shift(50, player=ALICE)

    selection = select_clips(
        game.data(),
        ClipFilter(
            player_id=ALICE,
            event_type=EventType.SHOT_ATTEMPT,
            shot_outcome=ShotOutcome.GOAL,
        ),
    )

    assert _ids(selection.candidates) == [alice_goal.id]


def test_unconfirmed_vision_events_are_candidates():
    game = _Game()
    vision = game.shot(20, shooter=ALICE, confirmed=False, source=EventSource.VISION)

    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    assert _ids(selection.selected) == [vision.id]


# -- Hand-pick / deselect ---------------------------------------------------


def test_every_candidate_is_selected_by_default():
    game = _Game()
    game.shot(20, shooter=ALICE)
    game.shot(30, shooter=ALICE)

    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    assert selection.selected == selection.candidates


def test_deselected_candidates_drop_out_of_the_selection_but_stay_candidates():
    game = _Game()
    first = game.shot(20, shooter=ALICE)
    second = game.shot(30, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    picked = selection.deselect(first.id)

    assert _ids(picked.selected) == [second.id]
    assert _ids(picked.candidates) == [first.id, second.id]
    assert not picked.is_selected(first.id)
    assert _ids(selection.selected) == [first.id, second.id], "not mutated"


def test_a_deselected_candidate_can_be_picked_again():
    game = _Game()
    first = game.shot(20, shooter=ALICE)
    second = game.shot(30, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    picked = selection.deselect(first.id).select(first.id)

    assert _ids(picked.selected) == [first.id, second.id]


def test_only_candidates_can_be_picked():
    game = _Game()
    game.shot(20, shooter=ALICE)
    other = game.shot(30, shooter=BOB)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    with pytest.raises(KeyError):
        selection.select(other.id)
    with pytest.raises(KeyError):
        selection.deselect(other.id)


# -- Zero matches -----------------------------------------------------------


def test_zero_matches_disables_export():
    game = _Game()
    game.shot(20, shooter=BOB)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    for shape in OutputShape:
        plan = _plan(game, selection, shape=shape)
        assert not plan.can_export
        assert plan.outputs == ()


def test_deselecting_every_match_disables_export():
    game = _Game()
    shot = game.shot(20, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    plan = _plan(game, selection.deselect(shot.id), shape=OutputShape.REEL)

    assert not plan.can_export


def test_running_a_disabled_plan_is_refused_without_encoding():
    game = _Game()
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))
    encoder = Mock(spec=ClipEncoder)

    with pytest.raises(ValueError):
        run_export(_plan(game, selection), encoder)
    encoder.encode.assert_not_called()


def test_there_is_no_cap_on_match_count():
    game = _Game()
    for second in range(10, 510):
        game.shot(second, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    plan = _plan(game, selection)

    assert len(plan.outputs) == 500


# -- Padding ----------------------------------------------------------------


def test_padding_widens_each_clip_around_its_event():
    game = _Game()
    shot = game.shot(100, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    plan = _plan(game, selection, padding=Padding(before_ms=8000, after_ms=3000))

    assert plan.outputs[0].segments == (
        ClipSegment(event_id=shot.id, start_ms=92 * SECOND, end_ms=103 * SECOND),
    )


def test_padding_defaults_when_not_overridden():
    game = _Game()
    game.shot(100, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    plan = plan_export(
        game.data(),
        selection,
        shape=OutputShape.PER_CLIP,
        footage_duration_ms=FOOTAGE_MS,
        output_dir=OUT,
    )

    default = clip_export.DEFAULT_PADDING
    assert default.before_ms > 0 and default.after_ms > 0
    (segment,) = plan.outputs[0].segments
    assert segment.start_ms == 100 * SECOND - default.before_ms
    assert segment.end_ms == 100 * SECOND + default.after_ms


def test_padding_clamps_at_footage_start():
    game = _Game()
    shot = game.shot(2, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    plan = _plan(game, selection, padding=Padding(before_ms=5000, after_ms=5000))

    assert plan.outputs[0].segments == (
        ClipSegment(event_id=shot.id, start_ms=0, end_ms=7 * SECOND),
    )


def test_padding_clamps_at_footage_end():
    game = _Game()
    shot = game.shot(98, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    plan = _plan(
        game,
        selection,
        padding=Padding(before_ms=5000, after_ms=5000),
        footage_duration_ms=100 * SECOND,
    )

    assert plan.outputs[0].segments == (
        ClipSegment(event_id=shot.id, start_ms=93 * SECOND, end_ms=100 * SECOND),
    )


def test_negative_padding_is_rejected():
    with pytest.raises(ValueError):
        Padding(before_ms=-1, after_ms=0)


# -- Per-clip filenames -----------------------------------------------------


def test_per_clip_filenames_carry_event_type_player_and_game_clock():
    game = _Game()
    game.shot(125, shooter=ALICE)
    game.penalty(200, player=CARL)
    game.faceoff(300, a=CARL, b=BOB)
    selection = select_clips(game.data(), ClipFilter(player_id=CARL))

    plan = _plan(game, selection)

    assert [output.path for output in plan.outputs] == [
        # Penalty at 200s of live play; Carl has no name, so his jersey.
        OUT / "penalty_no4_P1-16m40s.mp4",
        # The penalty stopped the clock; the faceoff is where it resumes.
        OUT / "faceoff_no4_P1-16m40s.mp4",
    ]


def test_player_name_is_slugged_for_the_filename():
    game = _Game()
    game.shot(125, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    (output,) = _plan(game, selection).outputs

    assert output.path == OUT / "shot_attempt_alice-müller_P1-17m55s.mp4"


def test_per_clip_filenames_omit_the_player_when_not_player_scoped():
    game = _Game()
    game.shot(125, shooter=ALICE, outcome=ShotOutcome.GOAL)
    selection = select_clips(game.data(), ClipFilter(shot_outcome=ShotOutcome.GOAL))

    (output,) = _plan(game, selection).outputs

    assert output.path == OUT / "shot_attempt_P1-17m55s.mp4"


def test_clips_sharing_a_game_clock_get_distinct_filenames():
    game = _Game()
    game.add(Stoppage(), at=60)
    game.shift(70, player=ALICE, on=False)
    game.shift(75, player=ALICE, on=True)
    selection = select_clips(
        game.data(), ClipFilter(player_id=ALICE, event_type=EventType.SHIFT_CHANGE)
    )

    plan = _plan(game, selection)

    assert [output.path.name for output in plan.outputs] == [
        "shift_change_alice-müller_P1-19m00s.mp4",
        "shift_change_alice-müller_P1-19m00s_2.mp4",
    ]


def test_events_without_a_game_clock_fall_back_to_the_video_timestamp():
    game = _Game()
    game.events.clear()
    game.add(
        ShotAttempt(
            shot_outcome=ShotOutcome.SAVED, shot_type=ShotType.WRIST, shooter_id=ALICE
        ),
        at=3725,
    )
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    (output,) = _plan(game, selection).outputs

    assert output.path.name == "shot_attempt_alice-müller_video-1h02m05s.mp4"


def test_per_clip_plan_has_one_output_per_selected_event_in_video_order():
    game = _Game()
    late = game.shot(90, shooter=ALICE)
    early = game.shot(30, shooter=ALICE)
    skipped = game.shot(60, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE)).deselect(
        skipped.id
    )

    plan = _plan(game, selection)

    assert [output.segments[0].event_id for output in plan.outputs] == [
        early.id,
        late.id,
    ]


# -- Reel -------------------------------------------------------------------


def test_reel_is_one_output_of_every_selected_clip_in_chronological_order():
    game = _Game()
    late = game.shot(90, shooter=ALICE)
    early = game.shot(30, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))

    plan = _plan(
        game,
        selection,
        shape=OutputShape.REEL,
        padding=Padding(before_ms=2000, after_ms=1000),
    )

    (reel,) = plan.outputs
    assert reel.segments == (
        ClipSegment(event_id=early.id, start_ms=28 * SECOND, end_ms=31 * SECOND),
        ClipSegment(event_id=late.id, start_ms=88 * SECOND, end_ms=91 * SECOND),
    )


def test_reel_filename_carries_date_opponent_and_filter_summary():
    game = _Game()
    game.shot(30, shooter=ALICE, outcome=ShotOutcome.GOAL)
    selection = select_clips(
        game.data(),
        ClipFilter(
            player_id=ALICE,
            event_type=EventType.SHOT_ATTEMPT,
            shot_outcome=ShotOutcome.GOAL,
        ),
    )

    (reel,) = _plan(game, selection, shape=OutputShape.REEL).outputs

    assert (
        reel.path
        == OUT / "2026-09-20_vs-rivals-hc_alice-müller-shot_attempt-goal_reel.mp4"
    )


def test_reel_opponent_is_the_side_that_is_not_the_user_team():
    game = _Game(user_side=AWAY)
    game.shot(30)
    selection = select_clips(game.data(), ClipFilter(event_type=EventType.SHOT_ATTEMPT))

    (reel,) = _plan(game, selection, shape=OutputShape.REEL).outputs

    assert reel.path.name == "2026-09-20_vs-ice-breakers_shot_attempt_reel.mp4"


def test_reel_filename_placeholders_for_missing_game_details():
    game = _Game(game_date=None)
    game.game.home_team = game.game.away_team = None
    selection = select_clips(game.data(), ClipFilter())

    (reel,) = _plan(game, selection, shape=OutputShape.REEL).outputs

    assert reel.path.name == "undated_vs-unknown_all_reel.mp4"


# -- Encode seam ------------------------------------------------------------


def test_run_export_encodes_each_clip_from_the_game_footage():
    game = _Game()
    first = game.shot(30, shooter=ALICE)
    second = game.penalty(90, player=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))
    plan = _plan(game, selection, padding=Padding(before_ms=1000, after_ms=1000))
    encoder = Mock(spec=ClipEncoder)

    written = run_export(plan, encoder)

    assert encoder.encode.call_args_list == [
        call(
            "C:/footage/game.mp4",
            [ClipSegment(first.id, 29 * SECOND, 31 * SECOND)],
            OUT / "shot_attempt_alice-müller_P1-19m30s.mp4",
        ),
        call(
            "C:/footage/game.mp4",
            [ClipSegment(second.id, 89 * SECOND, 91 * SECOND)],
            OUT / "penalty_alice-müller_P1-18m30s.mp4",
        ),
    ]
    assert written == [call_.args[2] for call_ in encoder.encode.call_args_list]


def test_run_export_encodes_a_reel_as_one_call_with_every_segment():
    game = _Game()
    first = game.shot(30, shooter=ALICE)
    second = game.shot(90, shooter=ALICE)
    selection = select_clips(game.data(), ClipFilter(player_id=ALICE))
    plan = _plan(game, selection, shape=OutputShape.REEL)
    encoder = Mock(spec=ClipEncoder)

    run_export(plan, encoder)

    encoder.encode.assert_called_once_with(
        "C:/footage/game.mp4",
        [
            ClipSegment(first.id, 30 * SECOND, 30 * SECOND),
            ClipSegment(second.id, 90 * SECOND, 90 * SECOND),
        ],
        OUT / "2026-09-20_vs-rivals-hc_alice-müller_reel.mp4",
    )


def test_planning_needs_the_game_footage_attached():
    game = _Game()
    game.game.video_path = None
    selection = select_clips(game.data(), ClipFilter())

    with pytest.raises(ValueError):
        _plan(game, selection)
