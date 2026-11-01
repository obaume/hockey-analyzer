"""Fill from official (see CONTEXT.md's Sync check): a Disagreement whose
tagged side is an Unknown player reference is resolved from the official
player, one confirmed field at a time -- never overwriting a named player,
and never rostering anyone on the fly."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from hockey_analyzer.domain.enums import EventType, ShotOutcome, ShotType
from hockey_analyzer.domain.models import GameRosterEntry, Player
from hockey_analyzer.domain.tagging_session import (
    NamedPlayerNotOverwrittenError,
    OfficialPlayerNotRosteredError,
)

KIM_LICENCE = "300014"


@pytest.fixture
def kim(tagging_session):
    """Home #14 Kim, rostered with a licence number."""
    entry = tagging_session.resolve_or_create_roster_entry(
        tagging_session.home_team_id, 14, full_name="Jordan Kim"
    )
    entry.player.league_id = KIM_LICENCE
    return entry.player


@pytest.fixture
def goal(tagging_session):
    """A home goal with an unknown shooter and no assists."""
    event = tagging_session.log_event(
        EventType.SHOT_ATTEMPT,
        5_000,
        shot_outcome=ShotOutcome.GOAL,
        shot_type=ShotType.WRIST,
    )
    return tagging_session.update_event(
        event.id, shot_team_id=tagging_session.home_team_id
    )


def test_an_unknown_shooter_is_filled_with_the_rostered_licence_holder(
    tagging_session, kim, goal
):
    tagging_session.fill_from_official(goal.id, "shooter", KIM_LICENCE)

    assert (goal.shooter_id, goal.shooter_unknown) == (kim.id, False)
    assert (
        tagging_session.undo_description
        == "Filled shooter from official: #14 Jordan Kim"
    )


def test_only_that_one_field_is_filled(tagging_session, kim, goal):
    tagging_session.update_event(goal.id, assist1_unknown=True)

    tagging_session.fill_from_official(goal.id, "assist1", KIM_LICENCE)

    assert (goal.assist1_id, goal.assist1_unknown) == (kim.id, False)
    assert (goal.shooter_id, goal.shooter_unknown) == (None, True)
    assert (goal.assist2_id, goal.assist2_unknown) == (None, False)


def test_a_fill_is_undone_like_any_edit(tagging_session, kim, goal):
    tagging_session.fill_from_official(goal.id, "shooter", KIM_LICENCE)

    tagging_session.undo()

    goal = tagging_session.get_event(goal.id)
    assert (goal.shooter_id, goal.shooter_unknown) == (None, True)


def test_an_unknown_penalised_player_is_filled(tagging_session, kim):
    penalty = tagging_session.log_event(EventType.PENALTY, 9_000)
    tagging_session.update_event(
        penalty.id, penalty_team_id=tagging_session.home_team_id
    )

    tagging_session.fill_from_official(penalty.id, "player", KIM_LICENCE)

    assert (penalty.penalty_player_id, penalty.penalty_player_unknown) == (
        kim.id,
        False,
    )


def test_a_named_player_is_never_overwritten(tagging_session, kim, goal):
    tagging_session.set_player_reference(
        goal.id, "home", reference="shooter", jersey_number=9
    )
    named = goal.shooter_id

    with pytest.raises(NamedPlayerNotOverwrittenError):
        tagging_session.fill_from_official(goal.id, "shooter", KIM_LICENCE)

    assert (goal.shooter_id, goal.shooter_unknown) == (named, False)


def test_a_field_left_empty_is_no_unknown_to_fill(tagging_session, kim, goal):
    with pytest.raises(NamedPlayerNotOverwrittenError):
        tagging_session.fill_from_official(goal.id, "assist2", KIM_LICENCE)

    assert (goal.assist2_id, goal.assist2_unknown) == (None, False)


def test_strength_is_never_filled(tagging_session, kim, goal):
    with pytest.raises(ValueError):
        tagging_session.fill_from_official(goal.id, "strength", KIM_LICENCE)


def test_an_official_player_not_on_the_roster_is_refused(
    tagging_session, session, goal
):
    # Known to this install, but not rostered in this game.
    session.add(Player(full_name="Jordan Kim", league_id=KIM_LICENCE))
    session.commit()

    with pytest.raises(OfficialPlayerNotRosteredError):
        tagging_session.fill_from_official(goal.id, "shooter", KIM_LICENCE)

    assert (goal.shooter_id, goal.shooter_unknown) == (None, True)
    assert session.scalar(select(func.count()).select_from(GameRosterEntry)) == 0


def test_an_official_player_rostered_for_the_other_team_is_refused(
    tagging_session, goal
):
    entry = tagging_session.resolve_or_create_roster_entry(
        tagging_session.away_team_id, 14
    )
    entry.player.league_id = KIM_LICENCE

    with pytest.raises(OfficialPlayerNotRosteredError):
        tagging_session.fill_from_official(goal.id, "shooter", KIM_LICENCE)
