"""`UndoHistory` (#64): the linear undo/redo history behind
`TaggingSession`, held in memory only -- nothing here is persisted, and
the schema doesn't change for it.

A step is recorded as row snapshots rather than as inverse operations:
while an action runs inside `record()`, every tracked row it inserts,
updates or deletes -- across however many flushes and commits the action
makes -- is read straight from the database the first time it's about to
change, and again once the action is done. Undo writes each row's "before"
back, redo its "after"; a row that didn't exist on that side is deleted.
Rows come back with the ids they had, so earlier steps in the history
still point at the right rows. Reading the database rather than the ORM's
attribute history is deliberate: attributes never loaded on an object
(e.g. a column left to its default on insert) have no prior value there.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import NamedTuple

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from hockey_analyzer.domain.models import Base

# One row's column values, keyed by mapped attribute name.
_Row = dict[str, object]


class Step(NamedTuple):
    """One undoable action: what it did, in words, and the event it
    concerned -- the one to select after undoing or redoing it, if it
    still exists then."""

    description: str
    event_id: int | None


class StepRecording:
    """Handed to the action being recorded, which fills in its `Step`'s
    description and event once it knows them -- often only after its
    saves (a new event's id, the player a jersey number resolved to)."""

    def __init__(self) -> None:
        self.description = ""
        self.event_id: int | None = None


class _RowChange(NamedTuple):
    model: type[Base]
    key: int
    before: _Row | None
    after: _Row | None


class _UndoEntry(NamedTuple):
    step: Step
    changes: tuple[_RowChange, ...]


class UndoHistory:
    """Undo/redo over the rows of `tracked_models` changed through
    `db_session`. `tracked_models` is listed parents first (a model before
    any model holding a foreign key to it): restoring rows writes in that
    order and deletes in the reverse, so no foreign key is ever left
    dangling mid-way."""

    def __init__(
        self, db_session: Session, tracked_models: Sequence[type[Base]]
    ) -> None:
        self._db = db_session
        self._models = tuple(tracked_models)
        self._undo: list[_UndoEntry] = []
        self._redo: list[_UndoEntry] = []
        self._recording: StepRecording | None = None
        self.last_step: Step | None = None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_description(self) -> str | None:
        return self._undo[-1].step.description if self._undo else None

    @property
    def redo_description(self) -> str | None:
        return self._redo[-1].step.description if self._redo else None

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    @contextmanager
    def record(self) -> Iterator[StepRecording]:
        """Record everything the body changes as one step. Nested inside
        another `record()`, the body joins the outer step. A body that
        changes nothing records nothing, leaving redo available; one that
        raises records nothing either. `last_step` is the step the
        outermost body recorded, or `None`."""
        if self._recording is not None:
            yield self._recording
            return

        recording = StepRecording()
        before: dict[Base, _Row | None] = {}

        def capture(session: Session, flush_context, instances) -> None:
            for obj in session.new:
                if isinstance(obj, self._models):
                    before.setdefault(obj, None)
            for obj in (*session.dirty, *session.deleted):
                if isinstance(obj, self._models) and obj not in before:
                    before[obj] = self._read(type(obj), inspect(obj).identity[0])

        self._recording = recording
        self.last_step = None
        event.listen(self._db, "before_flush", capture)
        try:
            yield recording
        finally:
            event.remove(self._db, "before_flush", capture)
            self._recording = None

        changes = []
        for obj, old in before.items():
            identity = inspect(obj).identity
            if identity is None:
                continue
            new = self._read(type(obj), identity[0])
            if new != old:
                changes.append(_RowChange(type(obj), identity[0], old, new))
        if not changes:
            return
        step = Step(recording.description, recording.event_id)
        self._undo.append(_UndoEntry(step, tuple(changes)))
        self._redo.clear()
        self.last_step = step

    def undo(self) -> Step | None:
        """Put every row the last step changed back as it was before it;
        `None` when there's nothing to undo."""
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._restore([(change, change.before) for change in entry.changes])
        self._redo.append(entry)
        return entry.step

    def redo(self) -> Step | None:
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._restore([(change, change.after) for change in entry.changes])
        self._undo.append(entry)
        return entry.step

    def _restore(self, targets: list[tuple[_RowChange, _Row | None]]) -> None:
        for model in self._models:
            for change, row in targets:
                if row is not None and issubclass(change.model, model):
                    existing = self._db.get(change.model, change.key)
                    if existing is None:
                        self._db.add(change.model(**row))
                    else:
                        for name, value in row.items():
                            setattr(existing, name, value)
            self._db.flush()
        for model in reversed(self._models):
            for change, row in targets:
                if row is None and issubclass(change.model, model):
                    existing = self._db.get(change.model, change.key)
                    if existing is not None:
                        self._db.delete(existing)
            self._db.flush()
        self._db.commit()

    def _read(self, model: type[Base], key: int) -> _Row | None:
        """`model`'s row `key` as the database holds it right now, or
        `None` if there is none. Read on the session's connection, not
        through the ORM, so a read from inside a flush can't set off
        another one."""
        mapper = inspect(model)
        attrs = mapper.column_attrs
        stmt = select(*(attr.columns[0] for attr in attrs)).where(
            mapper.primary_key[0] == key
        )
        row = self._db.connection().execute(stmt).one_or_none()
        if row is None:
            return None
        return {attr.key: value for attr, value in zip(attrs, row, strict=True)}
