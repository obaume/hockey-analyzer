# Hockey Analyzer

[![CI](https://github.com/obaume/hockey-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/obaume/hockey-analyzer/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)

A local, offline desktop app for tagging hockey game footage and turning it into advanced stats and shareable video clips.

Load a recording of a game, log what happens (faceoffs, shots, penalties, line changes...) by clicking on a rink diagram as the video plays, and get Corsi, Fenwick, PDO, zone starts, goalie stats and more, per game or across a hand-picked set of games. No account, no server: everything stays in a database on your machine.

> **Status: early personal project (v0.1.0).** It's built for my own team's analysis and shared as-is. Expect rough edges, and expect the database schema to change between versions without migrations. Issues and feedback are welcome.

## Features

- **Video playback built for tagging**: play/pause, jump back and forward, frame stepping, and speed control, all on the keyboard.
- **Event tagging** against the footage: period start/end, stoppages, penalties, shift changes, faceoffs, and shot attempts (goal / saved / missed / blocked, with shot type and context such as rush, rebound, screened, one-timer). Located events are placed by clicking on a rink diagram.
- **IIHF or NHL rink** chosen per game, so coordinates and zones match the rink the game was played on.
- **Line changes and units**: assign forward lines, defense pairs, power-play and penalty-kill units per game, and log a whole line change with one key.
- **Stats**, per game or aggregated across selected games, filterable by strength state (5v5, power play, etc.):
  - Team: Corsi (CF%), Fenwick (FF%), shooting %, save %, PDO, shot-quality breakdown (shot types, contexts, high-danger share)
  - Skaters: on-ice Corsi / Fenwick, plus-minus, zone start %
  - Position rollups (C / LW / RW / D)
  - Lines and pairs: stats for when the full unit is on the ice together
  - Goalies: SV%, high-danger SV%, GAA, minutes played
- **Clip export**: cut the selected events into individual `.mp4` clips or a single highlight reel (H.264/AAC, plays on any phone), with a configurable before/after padding. ffmpeg is bundled, so there's nothing extra to install.
- **Swiss league (SIHF) import**: paste a game link from the SIHF site to create the game with teams, rosters and score pre-filled. This is the only league supported; games from any other league are set up manually.

Definitions of every term and stat live in [CONTEXT.md](CONTEXT.md). Planned work (xG, RAPM, shareable report bundles, ...) is tracked in [GitHub issues](https://github.com/obaume/hockey-analyzer/issues).

## What you need

- **Python 3.11 or newer**
- **Windows** (primary platform) or **macOS** (tested in CI). Linux is untested but should work.
- **One continuous video file per game.** The game clock is derived from the footage, so each period must be recorded without cuts. Any format your system's Qt Multimedia backend can play will work; MP4 (H.264) is the safe choice.

## Install

```sh
git clone https://github.com/obaume/hockey-analyzer.git
cd hockey-analyzer
pip install .
```

Then start the app with:

```sh
hockey-analyzer
```

Your data is stored in a single SQLite file, `hockey_analyzer.db`, created on first run:

- Windows: `%APPDATA%\Hockey Analyzer\`
- macOS: `~/Library/Application Support/Hockey Analyzer/`

Back that file up if you care about your tagging work.

## Quick start

1. **Create a game**: *Game → New Game…* to set it up by hand (teams, rink type), or *Game → Import from League Link…* for an SIHF game. The rink type (IIHF / NHL) can't be changed afterwards.
2. **Load the footage**: *File → Open Video…* (`Ctrl+O`). The video is remembered for that game, so reopening it later via *Game → Select Game…* loads it automatically.
3. **Set up units** (optional): *Game → Units…* to assign players to lines, pairs, and special-teams units.
4. **Tag events** as you watch: `Space` plays/pauses, `←`/`→` jump 5 seconds, `,`/`.` step one frame, `[`/`]` change speed. Number keys `1`–`7` log an event type and `8` logs a line change. Shots and faceoffs ask you to click their location on the rink. Players are identified by jersey number (`h`/`a` switches home/away), and new players can be added on the fly.
5. **Look at the numbers**: *Game → Stats…* for the current game, or *Game → Multi-Game Stats…* to aggregate across several games.
6. **Export clips**: *Game → Export Clips…* to filter events and export them as individual clips or a highlight reel.

Shortcuts are registered in `src/hockey_analyzer/ui/main_window.py` (playback) and `src/hockey_analyzer/ui/tagging_panel.py` (tagging) if you want the full list.

## Development

```sh
pip install -e ".[dev]"   # or scripts/build.ps1 on Windows for the editable install
pytest                    # tests (Qt tests run through pytest-qt)
ruff check . && ruff format --check .
```

CI runs lint and the test suite on every PR and push to `main`: tests on Windows (Python 3.11 and 3.12) and macOS (Python 3.12).

The code is split into a GUI-free `domain/` layer (models, game clock, stats engine, clip planning), `league_import/`, and a PySide6 `ui/` layer. Design decisions are recorded as ADRs in [docs/adr/](docs/adr/), and the domain vocabulary in [CONTEXT.md](CONTEXT.md).

**Workflow:** most of this project is built with AI coding agents working from GitHub issues. [CLAUDE.md](CLAUDE.md) and [docs/agents/](docs/agents/) describe the issue tracker, triage labels and domain-doc conventions they follow; the `.agents/` and `.claude/` directories hold the agent skills used.

## License

[GPL-3.0-or-later](LICENSE). The rink rendering relies on the GPL-3.0 [`hockey-rink`](https://pypi.org/project/hockey-rink/) library, so the app as a whole is distributed under the GPL. See [ADR-0011](docs/adr/0011-gpl-3-license.md).
