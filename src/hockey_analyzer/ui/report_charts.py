"""Charts baked into a report bundle at export time (ticket 26). A bundle
carries no raw events, so a chart is drawn here from the sender's live
games and frozen to a PNG -- the recipient only ever sees the image (see
ADR-0003 and CONTEXT.md's Report bundle chart entry).

The shot map draws every located shot attempt on the rink, one team per
half: teams switch ends every period, so each attempt is folded into its
team's own attacking half (nearly every attempt is taken there -- see
CONTEXT.md's Attacking direction entry) rather than drawn at whichever
end it was taken in that period.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from hockey_analyzer.domain.enums import ShotOutcome
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import ShotAttempt
from hockey_analyzer.domain.report_bundle import Chart
from hockey_analyzer.ui.rink_view import build_rink

SHOT_MAP = "shot-map"
_RIGHT_COLOR = "#1f77b4"
_LEFT_COLOR = "#d62728"


def shot_map_chart(
    games: Sequence[GameData], *, right_team_id: int | None = None
) -> Chart | None:
    """Every located attempt in `games`: `right_team_id`'s (default: the
    first game's home team's) attacking the right-hand net, everyone
    else's the left. Goals are filled, other attempts hollow. None when
    no attempt has a location. Drawn on the first game's rink type."""
    if not games:
        return None
    if right_team_id is None:
        right_team_id = games[0].game.home_team_id
    right: list[ShotAttempt] = []
    left: list[ShotAttempt] = []
    for data in games:
        for event in data.events:
            if (
                isinstance(event, ShotAttempt)
                and event.shot_x is not None
                and event.shot_y is not None
            ):
                (right if event.shot_team_id == right_team_id else left).append(event)
    if not right and not left:
        return None

    figure = Figure(figsize=(8, 3.6), dpi=100)
    FigureCanvasAgg(figure)
    figure.subplots_adjust(left=0.01, right=0.99, bottom=0.01, top=0.9)
    ax = figure.add_subplot(111)
    build_rink(games[0].game.rink_type).draw(ax=ax, display_range="full")
    for shots, side, color, label in (
        (right, 1, _RIGHT_COLOR, _team_label(games, right_team_id, "Team")),
        (left, -1, _LEFT_COLOR, "Opponents"),
    ):
        if not shots:
            continue
        goals = [shot for shot in shots if shot.shot_outcome is ShotOutcome.GOAL]
        others = [shot for shot in shots if shot.shot_outcome is not ShotOutcome.GOAL]
        ax.scatter(
            [side * abs(shot.shot_x) for shot in others],
            [shot.shot_y for shot in others],
            s=40,
            facecolors="none",
            edgecolors=color,
            linewidths=1.5,
            zorder=100,
            label=f"{label} attempts",
        )
        if goals:
            ax.scatter(
                [side * abs(shot.shot_x) for shot in goals],
                [shot.shot_y for shot in goals],
                s=90,
                marker="*",
                color=color,
                zorder=101,
                label=f"{label} goals",
            )
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.12), ncol=4, fontsize=8)

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return Chart(SHOT_MAP, buffer.getvalue())


def _team_label(games: Sequence[GameData], team_id: int | None, fallback: str) -> str:
    for data in reversed(games):
        game = data.game
        for side_id, team in (
            (game.home_team_id, game.home_team),
            (game.away_team_id, game.away_team),
        ):
            if side_id == team_id and team is not None:
                return team.name
    return fallback
