"""Centralized keyboard-shortcut registry (see ADR-0007). Widgets register
(key, scope, action) bindings here instead of wiring `QShortcut`/
`keyPressEvent` directly; a key claimed by two simultaneously-active scopes
raises at the point the conflict becomes real (registration, scope entry,
or scope resume) rather than being silently dropped, matching Qt's native
ambiguous-shortcut behavior it replaces.
"""

from __future__ import annotations

from typing import Callable

Action = Callable[[], None]


class ShortcutConflictError(Exception):
    """A key would be bound in two simultaneously-active scopes."""


class ShortcutRegistry:
    def __init__(self) -> None:
        self._bindings: dict[str, dict[str, Action]] = {}
        self._active: set[str] = set()
        self._suspended: set[str] = set()

    def enter_scope(self, scope: str) -> None:
        if scope in self._active:
            return
        self._raise_if_conflicts_with_effective_scopes(scope)
        self._active.add(scope)

    def exit_scope(self, scope: str) -> None:
        self._active.discard(scope)
        self._suspended.discard(scope)

    def suspend_scope(self, scope: str) -> None:
        self._suspended.add(scope)

    def resume_scope(self, scope: str) -> None:
        if scope not in self._suspended:
            return
        self._raise_if_conflicts_with_effective_scopes(scope)
        self._suspended.discard(scope)

    def _raise_if_conflicts_with_effective_scopes(self, scope: str) -> None:
        for key in self._bindings.get(scope, {}):
            conflicting = self._active_scope_claiming(key, other_than=scope)
            if conflicting is not None:
                raise ShortcutConflictError(
                    f"scope {scope!r} would conflict with active scope "
                    f"{conflicting!r} on key {key!r}"
                )

    def register(self, key: str, scope: str, action: Action) -> None:
        scope_bindings = self._bindings.setdefault(scope, {})
        if key in scope_bindings:
            raise ShortcutConflictError(
                f"key {key!r} is already bound in scope {scope!r}"
            )
        if self._is_effectively_active(scope):
            conflicting = self._active_scope_claiming(key, other_than=scope)
            if conflicting is not None:
                raise ShortcutConflictError(
                    f"key {key!r} in scope {scope!r} is already bound in "
                    f"active scope {conflicting!r}"
                )
        scope_bindings[key] = action

    def unregister(self, key: str, scope: str) -> None:
        scope_bindings = self._bindings.get(scope, {})
        if key not in scope_bindings:
            raise KeyError(f"no binding for key {key!r} in scope {scope!r}")
        del scope_bindings[key]

    def _is_effectively_active(self, scope: str) -> bool:
        return scope in self._active and scope not in self._suspended

    def _effectively_active_scopes(self) -> set[str]:
        return self._active - self._suspended

    def _active_scope_claiming(self, key: str, *, other_than: str) -> str | None:
        for scope in self._effectively_active_scopes():
            if scope == other_than:
                continue
            if key in self._bindings.get(scope, {}):
                return scope
        return None

    def dispatch(self, key: str) -> bool:
        for scope in self._effectively_active_scopes():
            action = self._bindings.get(scope, {}).get(key)
            if action is not None:
                action()
                return True
        return False
