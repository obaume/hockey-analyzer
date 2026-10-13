## Commands

The project is managed with uv. Run everything through it, never `pip` or a bare `python`/`pytest` (those hit whatever Python is on PATH, not the project's 3.12 `.venv`).

- `uv sync`: install or refresh the environment
- `uv run pytest`: tests
- `uv run ruff check .` and `uv run ruff format .`: lint and format
- `uv add <pkg>` / `uv add --group dev <pkg>`: add a dependency (keeps `uv.lock` in step; commit both files)

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in this repo (`obaume/hockey-analyzer`), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
