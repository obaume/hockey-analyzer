"""A hand-built one-game `GameData` for clip export tests (tickets 23, 24):
transient ORM objects, no database."""

from __future__ import annotations

from datetime import date

from hockey_analyzer.domain.enums import EventSource, ShotOutcome, ShotType
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
    Team,
)

SECOND = 1000

HOME = 1
AWAY = 2
ALICE = 100
BOB = 101
CARL = 102


class ClipGame:
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
