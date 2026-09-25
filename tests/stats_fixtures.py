"""Hand-built `GameData` fixtures shared by the StatsEngine tests: transient
ORM objects assembled in memory, never touching a database, so each test
states exactly the events its expected numbers come from."""

from __future__ import annotations

from hockey_analyzer.domain.enums import (
    Position,
    RinkType,
    ShotOutcome,
    ShotType,
    UnitType,
)
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import (
    Faceoff,
    Game,
    GameRosterEntry,
    GameUnitAssignment,
    PeriodEnd,
    PeriodStart,
    Player,
    ShiftChange,
    ShotAttempt,
    Stoppage,
    Team,
)

HOME = 1
AWAY = 2

# Fixed "attacking toward" x for each side's shots in period 1; teams
# switch ends for period 2. Shot coordinates below are placed well inside
# the relevant offensive zone unless a test says otherwise.
HOME_NET_X = 80.0
AWAY_NET_X = -80.0


class GameBuilder:
    """Accumulates one game's events/roster, assigning ids and timestamps
    in the order they're added (so insertion order == video order unless a
    test passes `at=` explicitly)."""

    def __init__(
        self,
        *,
        opponent_shifts_complete=False,
        rink_type=RinkType.IIHF,
        game_id=1,
        home=HOME,
        away=AWAY,
    ):
        self.game = Game(
            id=game_id,
            home_team_id=home,
            away_team_id=away,
            rink_type=rink_type,
            opponent_shifts_complete=opponent_shifts_complete,
        )
        self.events = []
        self.roster = []
        self.units = []
        self._next_id = 1
        self._clock = 0

    def _add(self, event, at):
        if at is None:
            self._clock += 1000
            at = self._clock
        else:
            self._clock = max(self._clock, at)
        event.id = self._next_id
        self._next_id += 1
        event.game_id = self.game.id
        event.video_timestamp = at
        self.events.append(event)
        return event

    def player(
        self, team_id, jersey, *, position=Position.CENTER, name=None, player_id=None
    ):
        """`player_id` defaults to a fresh id; pass one explicitly to have
        the same player appear across several games."""
        if player_id is None:
            player_id = len(self.roster) + 100
        player = Player(id=player_id, full_name=name, position=position)
        self.roster.append(
            GameRosterEntry(
                game_id=self.game.id,
                player_id=player_id,
                team_id=team_id,
                jersey_number=jersey,
                player=player,
            )
        )
        return player_id

    def shot(
        self,
        team_id,
        outcome=ShotOutcome.SAVED,
        *,
        strength="5v5",
        at=None,
        x=None,
        y=0.0,
        shot_type=ShotType.WRIST,
        **context,
    ):
        if x is None:
            x = HOME_NET_X - 30 if team_id == HOME else AWAY_NET_X + 30
        return self._add(
            ShotAttempt(
                shot_team_id=team_id,
                shot_outcome=outcome,
                shot_type=shot_type,
                shot_x=x,
                shot_y=y,
                shooter_unknown=True,
                strength_state=strength,
                **{f"shot_{flag}": value for flag, value in context.items()},
            ),
            at,
        )

    def shift(self, team_id, player_id, on, *, at=None, unknown=False):
        return self._add(
            ShiftChange(
                shift_team_id=team_id,
                shift_player_id=None if unknown else player_id,
                shift_player_unknown=unknown,
                shift_on_ice=on,
            ),
            at,
        )

    def faceoff(self, x, *, at=None, strength="5v5"):
        return self._add(
            Faceoff(
                faceoff_x=x,
                faceoff_y=0.0,
                faceoff_participant_a_unknown=True,
                faceoff_participant_b_unknown=True,
                strength_state=strength,
            ),
            at,
        )

    def stoppage(self, *, at=None):
        return self._add(Stoppage(), at)

    def period_start(self, number, *, at=None):
        return self._add(PeriodStart(period_number=number), at)

    def period_end(self, number, *, at=None):
        return self._add(PeriodEnd(period_number=number), at)

    def unit(self, team_id, unit_type, number, *player_ids):
        for player_id in player_ids:
            self.units.append(
                GameUnitAssignment(
                    game_id=self.game.id,
                    player_id=player_id,
                    team_id=team_id,
                    unit_type=unit_type,
                    unit_number=number,
                )
            )

    def build(self):
        return GameData(
            game=self.game,
            events=list(self.events),
            roster=self.roster,
            unit_assignments=self.units,
        )


def named_game(**kwargs):
    """A small complete game for the report tests: home "Icebreakers" (#14
    Jordan Kim, C, on Forward-Line 1; goalie #30) vs. away "Rivals" (#91 on
    their Forward-Line 1, never tracked on ice). With Kim and #30 on: a
    home 5v5 attempt, an away 5v5 goal, and a home 5v4 attempt. Every call
    rosters the same player ids, so several calls (with distinct `game_id`s)
    make one multi-game selection of the same players."""
    game = GameBuilder(**kwargs)
    game.game.home_team = Team(id=HOME, name="Icebreakers")
    game.game.away_team = Team(id=AWAY, name="Rivals")
    kim = game.player(HOME, 14, name="Jordan Kim")
    goalie = game.player(HOME, 30, position=Position.GOALIE)
    rival = game.player(AWAY, 91)
    game.unit(HOME, UnitType.FORWARD_LINE, 1, kim)
    game.unit(AWAY, UnitType.FORWARD_LINE, 1, rival)
    game.faceoff(0.0)
    game.shift(HOME, kim, True)
    game.shift(HOME, goalie, True)
    game.shot(HOME)
    game.shot(AWAY, ShotOutcome.GOAL)
    game.shot(HOME, strength="5v4")
    return game.build()
