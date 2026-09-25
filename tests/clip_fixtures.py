"""A hand-built one-game `GameData` for clip export tests (tickets 23, 24,
50): transient ORM objects, no database; plus recording fakes for the
encoder and the clip preview's media player."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer

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


class FakeEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list, Path]] = []

    def encode(self, source_path, segments, output_path) -> None:
        self.calls.append((source_path, list(segments), output_path))


class _FakeSignal:
    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self, *args) -> None:
        for slot in self._slots:
            slot(*args)


class FakePreviewPlayer:
    """Records what the preview asked of it. Seeking lands immediately and
    reports the new position, as a loaded `QMediaPlayer` does; `advance_to`
    stands in for playback running on to a later position."""

    def __init__(self) -> None:
        self.positionChanged = _FakeSignal()
        self.playbackStateChanged = _FakeSignal()
        self.mediaStatusChanged = _FakeSignal()
        self._source = QUrl()
        self._position = 0
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.calls: list[str] = []

    def setAudioOutput(self, output) -> None:
        pass

    def setVideoSink(self, sink) -> None:
        pass

    def source(self) -> QUrl:
        return self._source

    def setSource(self, url: QUrl) -> None:
        self.calls.append("setSource")
        self._source = url

    def position(self) -> int:
        return self._position

    def setPosition(self, ms: int) -> None:
        self.calls.append("setPosition")
        self._position = ms
        self.positionChanged.emit(ms)

    def playbackState(self) -> QMediaPlayer.PlaybackState:
        return self._state

    def play(self) -> None:
        self.calls.append("play")
        self._set_state(QMediaPlayer.PlaybackState.PlayingState)

    def pause(self) -> None:
        self.calls.append("pause")
        self._set_state(QMediaPlayer.PlaybackState.PausedState)

    def stop(self) -> None:
        self.calls.append("stop")
        self._set_state(QMediaPlayer.PlaybackState.StoppedState)

    def _set_state(self, state) -> None:
        if state != self._state:
            self._state = state
            self.playbackStateChanged.emit(state)

    @property
    def playing(self) -> bool:
        return self._state == QMediaPlayer.PlaybackState.PlayingState

    def advance_to(self, ms: int) -> None:
        self._position = ms
        self.positionChanged.emit(ms)
