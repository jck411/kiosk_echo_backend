"""Service for managing quick prompt suggestions."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import List

from pydantic import ValidationError

from ..schemas.suggestions import Suggestion

logger = logging.getLogger(__name__)


class SuggestionsService:
    """Manage quick prompt suggestions displayed at the top of the chat."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._suggestions: List[Suggestion] = []
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Load suggestions from disk."""
        if not self._path.exists():
            self._suggestions = self._get_defaults()
            return

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read suggestions file %s: %s", self._path, exc)
            self._suggestions = self._get_defaults()
            return

        if isinstance(raw, dict):
            items = raw.get("suggestions", [])
        elif isinstance(raw, list):
            items = raw
        else:
            items = []

        loaded: List[Suggestion] = []
        for item in items:
            try:
                suggestion = Suggestion.model_validate(item)
                loaded.append(suggestion)
            except ValidationError as exc:
                logger.warning("Skipping invalid suggestion entry: %s", exc)
                continue

        self._suggestions = loaded if loaded else self._get_defaults()

    def _get_defaults(self) -> List[Suggestion]:
        """Get default suggestions (empty - user must add their own)."""
        return []

    def _save_to_disk(self) -> None:
        """Save suggestions to disk."""
        payload = {
            "suggestions": [
                suggestion.model_dump(mode="json") for suggestion in self._suggestions
            ]
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        self._path.write_text(serialized + "\n", encoding="utf-8")

    async def get_suggestions(self) -> List[Suggestion]:
        """Get all suggestions."""
        async with self._lock:
            return [s.model_copy(deep=True) for s in self._suggestions]

    async def add_suggestion(self, label: str, text: str) -> List[Suggestion]:
        """Add a new suggestion."""
        suggestion = Suggestion(label=label, text=text)
        async with self._lock:
            self._suggestions.append(suggestion)
            self._save_to_disk()
            return [s.model_copy(deep=True) for s in self._suggestions]

    async def delete_suggestion(self, index: int) -> List[Suggestion]:
        """Delete a suggestion by index."""
        async with self._lock:
            if index < 0 or index >= len(self._suggestions):
                raise IndexError(f"Invalid suggestion index: {index}")
            self._suggestions.pop(index)
            self._save_to_disk()
            return [s.model_copy(deep=True) for s in self._suggestions]

    async def replace_suggestions(
        self, suggestions: List[Suggestion]
    ) -> List[Suggestion]:
        """Replace all suggestions."""
        async with self._lock:
            self._suggestions = [s.model_copy(deep=True) for s in suggestions]
            self._save_to_disk()
            return [s.model_copy(deep=True) for s in self._suggestions]


__all__ = ["SuggestionsService"]
