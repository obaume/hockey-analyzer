# Schema migrations via SQLite's `user_version`, not Alembic

Until now `init_db` only ran `create_all`, which creates missing tables but never alters existing ones, so any column change silently left an existing user's database behind. The first change that needs one (reshaping `faceoff` from participant A/B plus per-participant team into home/away participants with a winning side) forced a choice. We decided on a small in-house runner: `PRAGMA user_version` records the schema version of the database file, and an ordered list of plain Python migration functions upgrades it step by step at startup. A fresh database is built with `create_all` and stamped at the latest version directly, and the database file is copied to a `.bak-v<old version>` sidecar before any migration runs.

## Considered Options

- **Alembic** — the standard SQLAlchemy migration tool. Rejected: this is a single-user, local, single-file SQLite app with a handful of tables and no parallel branches of schema history, so autogenerate, branching and multi-database support buy little, while it adds a dependency, an `alembic/` tree and config, and SQLite's lack of `ALTER COLUMN`/`DROP COLUMN` means every non-trivial migration needs Alembic's batch mode (table copy) anyway — the same work a hand-written migration function does.

## Consequences

- The version is stored in the database file itself, so every install in the wild carries it; switching to Alembic later would need a one-off bridge from `user_version` to Alembic's version table.
- Every future schema change must ship a migration function and bump the latest version; `create_all` alone is no longer enough.
