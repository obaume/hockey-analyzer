from __future__ import annotations

import pytest

from hockey_analyzer.ui.shortcuts import ShortcutConflictError, ShortcutRegistry


def test_dispatch_fires_action_bound_in_an_active_scope():
    registry = ShortcutRegistry()
    calls = []
    registry.enter_scope("playback")
    registry.register("Space", "playback", lambda: calls.append("play_pause"))

    handled = registry.dispatch("Space")

    assert handled is True
    assert calls == ["play_pause"]


def test_dispatch_of_an_unbound_key_is_a_no_op():
    registry = ShortcutRegistry()
    registry.enter_scope("playback")

    handled = registry.dispatch("Space")

    assert handled is False


def test_a_binding_in_an_inactive_scope_does_not_fire():
    registry = ShortcutRegistry()
    calls = []
    registry.register("Space", "playback", lambda: calls.append("play_pause"))

    handled = registry.dispatch("Space")

    assert handled is False
    assert calls == []


def test_registering_a_key_already_claimed_by_another_active_scope_raises():
    registry = ShortcutRegistry()
    registry.enter_scope("playback")
    registry.enter_scope("jersey-entry")
    registry.register("h", "playback", lambda: None)

    with pytest.raises(ShortcutConflictError):
        registry.register("h", "jersey-entry", lambda: None)


def test_registering_a_key_twice_in_the_same_scope_raises():
    registry = ShortcutRegistry()
    registry.register("h", "playback", lambda: None)

    with pytest.raises(ShortcutConflictError):
        registry.register("h", "playback", lambda: None)


def test_entering_a_scope_that_conflicts_with_an_already_active_scope_raises():
    registry = ShortcutRegistry()
    calls = []
    registry.register("h", "playback", lambda: calls.append("playback"))
    registry.register("h", "jersey-entry", lambda: calls.append("jersey-entry"))
    registry.enter_scope("playback")

    with pytest.raises(ShortcutConflictError):
        registry.enter_scope("jersey-entry")

    # The conflicting scope must not be left half-active: "h" still
    # resolves only to the scope that was already active.
    registry.dispatch("h")
    assert calls == ["playback"]


def test_exiting_a_scope_stops_its_bindings_from_firing():
    registry = ShortcutRegistry()
    calls = []
    registry.enter_scope("playback")
    registry.register("Space", "playback", lambda: calls.append("play_pause"))

    registry.exit_scope("playback")
    handled = registry.dispatch("Space")

    assert handled is False
    assert calls == []


def test_unregister_stops_a_binding_from_firing():
    registry = ShortcutRegistry()
    calls = []
    registry.enter_scope("playback")
    registry.register("Space", "playback", lambda: calls.append("play_pause"))

    registry.unregister("Space", "playback")
    handled = registry.dispatch("Space")

    assert handled is False
    assert calls == []


def test_a_key_freed_by_unregister_can_be_claimed_by_another_active_scope():
    registry = ShortcutRegistry()
    registry.enter_scope("playback")
    registry.enter_scope("jersey-entry")
    registry.register("h", "playback", lambda: None)

    registry.unregister("h", "playback")

    registry.register("h", "jersey-entry", lambda: None)  # must not raise


def test_unregistering_an_unbound_key_raises_key_error():
    registry = ShortcutRegistry()

    with pytest.raises(KeyError):
        registry.unregister("Space", "playback")


def test_a_key_freed_by_exiting_a_scope_can_be_claimed_by_another_scope():
    registry = ShortcutRegistry()
    registry.enter_scope("playback")
    registry.register("h", "playback", lambda: None)
    registry.register("h", "jersey-entry", lambda: None)

    registry.exit_scope("playback")

    registry.enter_scope("jersey-entry")  # must not raise


def test_suspending_a_scope_that_was_never_entered_raises():
    registry = ShortcutRegistry()

    with pytest.raises(ValueError):
        registry.suspend_scope("playback")


def test_suspending_an_active_scope_stops_its_bindings_from_firing():
    registry = ShortcutRegistry()
    calls = []
    registry.enter_scope("playback")
    registry.register("h", "playback", lambda: calls.append("toggle_home"))

    registry.suspend_scope("playback")
    handled = registry.dispatch("h")

    assert handled is False
    assert calls == []


def test_a_key_freed_by_suspension_can_be_claimed_while_suspended():
    registry = ShortcutRegistry()
    registry.enter_scope("playback")
    registry.register("h", "playback", lambda: None)
    registry.suspend_scope("playback")

    registry.enter_scope("jersey-entry")
    registry.register("h", "jersey-entry", lambda: None)  # must not raise


def test_resuming_a_scope_whose_key_was_claimed_while_suspended_raises():
    registry = ShortcutRegistry()
    registry.enter_scope("playback")
    registry.register("h", "playback", lambda: None)
    registry.suspend_scope("playback")
    registry.enter_scope("jersey-entry")
    registry.register("h", "jersey-entry", lambda: None)

    with pytest.raises(ShortcutConflictError):
        registry.resume_scope("playback")


def test_resuming_a_suspended_scope_re_arms_its_bindings():
    registry = ShortcutRegistry()
    calls = []
    registry.enter_scope("playback")
    registry.register("h", "playback", lambda: calls.append("toggle_home"))
    registry.suspend_scope("playback")

    registry.resume_scope("playback")
    handled = registry.dispatch("h")

    assert handled is True
    assert calls == ["toggle_home"]
