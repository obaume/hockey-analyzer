"""Engine/session setup for the SQLite-backed persistence layer. No GUI or
video dependency."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hockey_analyzer import schema_migrations
from hockey_analyzer.domain import (
    game_activity,  # noqa: F401 -- import is for its `before_flush` registration side effect, not its names
)
from hockey_analyzer.domain.models import Base


def create_sqlite_engine(
    path: str | Path | None = None, *, echo: bool = False
) -> Engine:
    """An engine backed by a temp/real file at `path`, or a shared
    in-memory database when `path` is omitted."""
    if path is None:
        return create_engine(
            "sqlite:///:memory:",
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(f"sqlite:///{Path(path)}", echo=echo)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# A migration upgrades the schema by one version inside the transaction it's
# handed, and may return messages to show the user once at startup.
Migration = Callable[[Connection], Sequence[str] | None]

# Ordered schema history -- see docs/adr/0012. MIGRATIONS[i] upgrades a
# database from version i to version i + 1; append, never reorder or edit a
# shipped one. A fresh database skips them all: create_all builds the latest
# shape directly, so every migration must leave the schema matching the models.
MIGRATIONS: list[Migration] = [
    schema_migrations.faceoff_home_away,
]
LATEST_VERSION = len(MIGRATIONS)


class SchemaVersionError(Exception):
    """The database's schema version can't be brought to this app's.

    `messages` holds what migrations that did commit before the failure
    wanted shown -- they won't run again, so this is the only chance."""

    def __init__(self, text: str, messages: Sequence[str] = ()) -> None:
        super().__init__(text)
        self.messages = list(messages)


class DatabaseTooNewError(SchemaVersionError):
    """The database was written by a newer app, at a version past ours."""


class MigrationFailedError(SchemaVersionError):
    """A migration raised; the database is left at the version before it."""


def init_db(
    engine: Engine, *, migrations: Sequence[Migration] = MIGRATIONS
) -> list[str]:
    """Bring the database to the latest schema version and return the
    messages the migrations that ran want shown to the user.

    A fresh database is built by create_all and stamped at the latest
    version; an older one is backed up to `<file>.bak-v<version>` and then
    upgraded one migration (one transaction) at a time."""
    latest = len(migrations)
    messages: list[str] = []
    with engine.connect() as conn:
        # Take transactions into our own hands: pysqlite otherwise commits
        # DDL as it goes, so a failing migration couldn't be rolled back.
        conn.execution_options(isolation_level="AUTOCOMMIT")
        version = _user_version(conn)
        if version > latest:
            raise DatabaseTooNewError(
                f"This database is at schema version {version}, but this version "
                f"of Hockey Analyzer only understands up to version {latest}. "
                "It was written by a newer version of the app -- update the app "
                "to open it."
            )
        if not inspect(conn).get_table_names():
            with _transaction(conn):
                Base.metadata.create_all(conn)
                _set_user_version(conn, latest)
            return messages
        if version == latest:
            return messages

        backup_path = _back_up(conn, version)
        for target, migration in enumerate(migrations[version:], start=version + 1):
            try:
                with _transaction(conn):
                    messages.extend(migration(conn) or ())
                    _set_user_version(conn, target)
            except Exception as error:
                backup_note = (
                    f" A copy from before the upgrade is at {backup_path}."
                    if backup_path
                    else ""
                )
                raise MigrationFailedError(
                    f"Upgrading the database to schema version {target} failed; "
                    f"it was left at version {target - 1}.{backup_note}",
                    messages,
                ) from error
    return messages


@contextmanager
def _transaction(conn: Connection) -> Iterator[None]:
    # Foreign key enforcement can only be switched outside a transaction.
    # It's off while migrating so a migration can rebuild a table (SQLite's
    # way to change a column) without cascading into rows referencing it;
    # foreign_key_check then vets the result before it's committed.
    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    conn.exec_driver_sql("BEGIN")
    try:
        yield
        violations = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise SchemaVersionError(f"Foreign key violations: {violations}")
        conn.exec_driver_sql("COMMIT")
    except BaseException:
        conn.exec_driver_sql("ROLLBACK")
        raise
    finally:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def _user_version(conn: Connection) -> int:
    return conn.exec_driver_sql("PRAGMA user_version").scalar_one()


def _set_user_version(conn: Connection, version: int) -> None:
    # PRAGMA takes no bound parameters; `version` is always an int we computed.
    conn.exec_driver_sql(f"PRAGMA user_version = {int(version)}")


def _back_up(conn: Connection, version: int) -> Path | None:
    """Copy the database file beside itself before it's migrated; an
    in-memory database has no file to keep."""
    database = conn.engine.url.database
    if not database or database == ":memory:":
        return None
    backup_path = Path(f"{database}.bak-v{version}")
    # SQLite's online backup rather than a file copy, so the copy is
    # consistent even with the database open.
    with closing(sqlite3.connect(backup_path)) as destination:
        conn.connection.driver_connection.backup(destination)
    return backup_path


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
