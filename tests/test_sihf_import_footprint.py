"""Reading a league game must not import sportsdataverse's ML stack: xgboost's
macOS wheel won't load without Homebrew's `libomp`, so a league import that
pulls it in crashes on a stock Mac (see `league_import.sihf._sihf`). Run in a
fresh interpreter, since another test may already have imported anything."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_PROBE = """
import sys
from sihf_fixtures import sihf_game_overview
from hockey_analyzer.league_import.sihf import parse_game_overview

parse_game_overview(sihf_game_overview())
print(",".join(m for m in ("xgboost", "sklearn", "scipy") if m in sys.modules))
"""


def test_reading_a_league_game_leaves_the_ml_stack_unimported():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent,
        check=True,
    )

    assert result.stdout.strip() == ""
