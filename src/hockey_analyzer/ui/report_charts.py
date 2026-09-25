"""Charts baked into a report bundle at export time (ticket 26). A bundle
carries no raw events, so a chart is drawn here from the sender's live
games and frozen to a PNG -- the recipient only ever sees the image (see
ADR-0003 and CONTEXT.md's Report bundle chart entry).

The shot map draws every located shot attempt on the rink, one team per
end: teams switch ends every period, so each attempt is turned toward its
team's attacking end as derived by `stats_engine.oriented_shots` (see
CONTEXT.md's Attacking direction entry) -- an attempt whose direction
can't be derived is left off rather than guessed.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import NamedTuple

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import ShotOutcome
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.report_bundle import Chart
from hockey_analyzer.ui.rink_view import build_rink
from hockey_analyzer.ui.stat_tables import team_names

SHOT_MAP = "shot-map"
_RIGHT_COLOR = "#1f77b4"
_LEFT_COLOR = "#d62728"


class ShotPoint(NamedTuple):
    x: float
    y: float
    goal: bool


class ShotSides(NamedTuple):
    """A shot map's attempts, already placed: `right` attacking the
    right-hand net, `left` the other; `right_team_ids` are the teams drawn
    on the right, in first-seen order."""

    right: list[ShotPoint]
    left: list[ShotPoint]
    right_team_ids: list[int]


def shot_sides(
    games: Sequence[GameData],
    *,
    right_team_id: int | None = None,
    player_id: int | None = None,
) -> ShotSides:
    """Every located attempt in `games`, split by side: `right_team_id`'s
    (default: the first game's home team's) attack right. With `player_id`
    (a player report), each game's right side is instead whichever team
    that player was rostered on in it -- a player who changed teams
    between the picked games stays on the right throughout -- falling
    back to `right_team_id` for a game they weren't rostered in."""
    if right_team_id is None and games:
        right_team_id = games[0].game.home_team_id
    sides = ShotSides([], [], [])
    for data in games:
        game_right = next(
            (
                entry.team_id
                for entry in data.roster
                if player_id is not None and entry.player_id == player_id
            ),
            right_team_id,
        )
        for shot, x, y in stats_engine.oriented_shots(data):
            goal = shot.shot_outcome is ShotOutcome.GOAL
            if shot.shot_team_id == game_right:
                sides.right.append(ShotPoint(x, y, goal))
                if game_right not in sides.right_team_ids:
                    sides.right_team_ids.append(game_right)
            else:
                sides.left.append(ShotPoint(-x, -y, goal))
    return sides


def shot_map_chart(
    games: Sequence[GameData],
    *,
    right_team_id: int | None = None,
    player_id: int | None = None,
) -> Chart | None:
    """Every located attempt in `games`, split as `shot_sides` splits
    them. Goals are stars, other attempts hollow circles. None when no
    attempt can be placed. Drawn on the first game's rink type."""
    right, left, right_team_ids = shot_sides(
        games, right_team_id=right_team_id, player_id=player_id
    )
    if not right and not left:
        return None

    figure = Figure(figsize=(8, 3.6), dpi=100)
    FigureCanvasAgg(figure)
    figure.subplots_adjust(left=0.01, right=0.99, bottom=0.01, top=0.9)
    ax = figure.add_subplot(111)
    build_rink(games[0].game.rink_type).draw(ax=ax, display_range="full")
    names = team_names(games)
    right_name = " / ".join(names.get(id_, "Team") for id_ in right_team_ids)
    for points, color, label in (
        (right, _RIGHT_COLOR, right_name),
        (left, _LEFT_COLOR, "Opponents"),
    ):
        attempts = [point for point in points if not point.goal]
        goals = [point for point in points if point.goal]
        if attempts:
            ax.scatter(
                [point.x for point in attempts],
                [point.y for point in attempts],
                s=40,
                facecolors="none",
                edgecolors=color,
                linewidths=1.5,
                zorder=100,
                label=f"{label} attempts",
            )
        if goals:
            ax.scatter(
                [point.x for point in goals],
                [point.y for point in goals],
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
