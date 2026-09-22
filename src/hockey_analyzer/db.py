"""Engine/session setup for the SQLite-backed persistence layer. No GUI or
video dependency."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hockey_analyzer.domain import game_activity  # noqa: F401 -- import is for its `before_flush` registration side effect, not its names
from hockey_analyzer.domain.models import Base


def create_sqlite_engine(path: str | Path | None = None, *, echo: bool = False) -> Engine:
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


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
