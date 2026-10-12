from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from app.security import SecurityError


class EngineHistory:
    """Bounded undo/redo stack for serialized Engine workspace states."""

    def __init__(self, max_entries: int = 64, max_state_bytes: int = 512 * 1024) -> None:
        self.max_entries = max_entries
        self.max_state_bytes = max_state_bytes
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []

    def record(self, state: dict[str, Any]) -> None:
        """Record a state before a mutating edit."""
        self._validate_state_size(state)
        self._undo.append(deepcopy(state))
        if len(self._undo) > self.max_entries:
            del self._undo[0]
        self._redo.clear()

    def undo(self, current_state: dict[str, Any]) -> dict[str, Any] | None:
        """Return the previous state and move current state to redo."""
        if not self._undo:
            return None
        self._validate_state_size(current_state)
        self._redo.append(deepcopy(current_state))
        return self._undo.pop()

    def redo(self, current_state: dict[str, Any]) -> dict[str, Any] | None:
        """Return the next state and move current state to undo."""
        if not self._redo:
            return None
        self._validate_state_size(current_state)
        self._undo.append(deepcopy(current_state))
        return self._redo.pop()

    def can_undo(self) -> bool:
        """Return whether an undo state is available."""
        return bool(self._undo)

    def can_redo(self) -> bool:
        """Return whether a redo state is available."""
        return bool(self._redo)

    def _validate_state_size(self, state: dict[str, Any]) -> None:
        encoded = json.dumps(state, sort_keys=True).encode("utf-8")
        if len(encoded) > self.max_state_bytes:
            raise SecurityError("Engine history state is too large.")
