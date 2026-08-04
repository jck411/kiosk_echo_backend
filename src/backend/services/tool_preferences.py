"""MCP server preferences for the kiosk."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class KioskToolPreferences:
    """Persist the MCP servers exposed to the kiosk."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._loaded = False
        self._enabled_servers: list[str] | None = None

    def _load(self) -> list[str] | None:
        if self._loaded:
            return self._enabled_servers

        self._loaded = True
        if not self._path.exists():
            return None

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read kiosk tool preferences: %s", exc)
            return None

        if not isinstance(raw, dict):
            return None
        entry = raw.get("kiosk")
        if not isinstance(entry, dict):
            return None
        servers = entry.get("enabled_servers")
        if not isinstance(servers, list):
            return None

        self._enabled_servers = [item for item in servers if isinstance(item, str)]
        return self._enabled_servers

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "kiosk": {"enabled_servers": self._enabled_servers or []},
        }
        self._path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    async def get_enabled_servers(self) -> list[str] | None:
        """Return enabled server IDs, or ``None`` when all are allowed."""
        async with self._lock:
            result = self._load()
            return list(result) if result is not None else None

    async def set_enabled_servers(self, server_ids: list[str]) -> None:
        """Replace the kiosk's enabled MCP server IDs."""
        async with self._lock:
            self._loaded = True
            self._enabled_servers = list(server_ids)
            self._save()


__all__ = ["KioskToolPreferences"]
