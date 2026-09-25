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

_Point = tuple[float, float, bool]  # x, y, is a goal


def shot_map_chart(
    games: Sequence[GameData], *, right_team_id: int | None = None
) -> Chart | None:
    """Every located attempt in `games`: `right_team_id`'s (default: the
    first game's home team's) attacking the right-hand net, everyone
    else's the left. Goals are stars, other attempts hollow circles. None
    when no attempt can be placed. Drawn on the first game's rink type."""
    if not games:
        return None
    if right_team_id is None:
        right_team_id = games[0].game.home_team_id
    right: list[_Point] = []
    left: list[_Point] = []
    for data in games:
        for shot, x, y in stats_engine.oriented_shots(data):
            goal = shot.shot_outcome is ShotOutcome.GOAL
            if shot.shot_team_id == right_team_id:
                right.append((x, y, goal))
            else:
                left.append((-x, -y, goal))
    if not right and not left:
        return None

    figure = Figure(figsize=(8, 3.6), dpi=100)
    FigureCanvasAgg(figure)
    figure.subplots_adjust(left=0.01, right=0.99, bottom=0.01, top=0.9)
    ax = figure.add_subplot(111)
    build_rink(games[0].game.rink_type).draw(ax=ax, display_range="full")
    right_name = team_names(games).get(right_team_id, "Team")
    for points, color, label in (
        (right, _RIGHT_COLOR, right_name),
        (left, _LEFT_COLOR, "Opponents"),
    ):
        attempts = [point for point in points if not point[2]]
        goals = [point for point in points if point[2]]
        if attempts:
            ax.scatter(
                [x for x, _y, _goal in attempts],
                [y for _x, y, _goal in attempts],
                s=40,
                facecolors="none",
                edgecolors=color,
                linewidths=1.5,
                zorder=100,
                label=f"{label} attempts",
            )
        if goals:
            ax.scatter(
                [x for x, _y, _goal in goals],
                [y for _x, y, _goal in goals],
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
