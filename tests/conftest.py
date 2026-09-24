from __future__ import annotations

import pytest

from hockey_analyzer.db import create_sqlite_engine, init_db, make_session_factory
from hockey_analyzer.domain.game_setup import GameSetupService
from hockey_analyzer.domain.models import Game, Team
from hockey_analyzer.domain.tagging_session import TaggingSession


@pytest.fixture
def session():
    engine = create_sqlite_engine()
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        yield session


@pytest.fixture
def game_and_teams(session):
    team_a = Team(name="Icebreakers")
    team_b = Team(name="Rivals")
    session.add_all([team_a, team_b])
    session.flush()

    game = Game()
    session.add(game)
    session.flush()

    return game, team_a, team_b


@pytest.fixture
def tagging_session(session, game_and_teams):
    game, team_a, team_b = game_and_teams
    return TaggingSession(
        session, game_id=game.id, home_team_id=team_a.id, away_team_id=team_b.id
    )


@pytest.fixture
def game_setup_service(session):
    return GameSetupService(session)
