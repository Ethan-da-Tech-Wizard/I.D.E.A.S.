# ==============================================================================
# Program: app/ui/engine/history.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: undo/redo for the Engine workspace. Rather than recording individual commands, it stores
# whole serialised workspace states (the dicts produced by EngineWorkspace.get_state()), which is
# simple and always consistent at the cost of memory; the limits below bound that cost.
# Used by: app/ui/engine/workspace.py (one EngineHistory per workspace).
# Design decisions: states are deep-copied on the way in and out, so later edits to live objects
# cannot corrupt history; recording a new edit clears the redo list (standard linear history);
# oversized states raise SecurityError instead of silently growing memory.
# Limits: 64 entries, 512 KiB per state by default (see EngineHistory.__init__).
# Tested by: tests/test_engine_integration.py (size limit) and workspace undo/redo tests.
# ==============================================================================

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from app.security import SecurityError


class EngineHistory:
    """Bounded undo/redo stack for serialized Engine workspace states."""

    def __init__(self, max_entries: int = 64, max_state_bytes: int = 512 * 1024) -> None:
        """Create an empty history.

        Args:
            max_entries: how many undo steps to keep; the oldest is dropped when exceeded.
            max_state_bytes: largest allowed JSON size of one state (UTF-8 bytes).
        """
        # Both limits are public attributes so the workspace (or tests) can tune them.
        self.max_entries = max_entries
        self.max_state_bytes = max_state_bytes
        # Two stacks of snapshots: _undo holds states to return to, _redo holds states undone from.
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []

    def record(self, state: dict[str, Any]) -> None:
        """Record a state before a mutating edit."""
        # Validate first so a rejected state leaves both stacks untouched.
        self._validate_state_size(state)
        # Deep copy: the caller keeps mutating its own dict after recording.
        self._undo.append(deepcopy(state))
        # Bounded memory: drop the oldest entry (index 0) once over the cap.
        if len(self._undo) > self.max_entries:
            del self._undo[0]
        # A new edit after an undo creates a new future; the old redo branch no longer applies.
        self._redo.clear()

    def undo(self, current_state: dict[str, Any]) -> dict[str, Any] | None:
        """Return the previous state and move current state to redo."""
        # Nothing to undo: signal with None rather than raising, so the UI can just ignore the shortcut.
        if not self._undo:
            return None
        self._validate_state_size(current_state)
        # Park the present state on the redo stack so redo() can come back to it.
        self._redo.append(deepcopy(current_state))
        # Most recently recorded state is the one to return to (LIFO).
        return self._undo.pop()

    def redo(self, current_state: dict[str, Any]) -> dict[str, Any] | None:
        """Return the next state and move current state to undo."""
        if not self._redo:
            return None
        self._validate_state_size(current_state)
        # Symmetric to undo(): the present becomes the next undo step.
        self._undo.append(deepcopy(current_state))
        return self._redo.pop()

    def can_undo(self) -> bool:
        """Return whether an undo state is available."""
        return bool(self._undo)

    def can_redo(self) -> bool:
        """Return whether a redo state is available."""
        return bool(self._redo)

    def _validate_state_size(self, state: dict[str, Any]) -> None:
        """Raise SecurityError if the state's JSON encoding exceeds max_state_bytes.

        Serialising is the cheapest honest measure of size for a nested dict. sort_keys makes the size deterministic.
        """
        encoded = json.dumps(state, sort_keys=True).encode("utf-8")
        if len(encoded) > self.max_state_bytes:
            raise SecurityError("Engine history state is too large.")
