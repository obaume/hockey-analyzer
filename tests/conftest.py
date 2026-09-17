from __future__ import annotations

import pytest

from hockey_analyzer.db import create_sqlite_engine, init_db, make_session_factory
from hockey_analyzer.domain.models import Game, Team


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
