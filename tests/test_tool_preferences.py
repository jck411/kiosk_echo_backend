from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.tool_preferences import KioskToolPreferences

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_missing_file_allows_all_servers(tmp_path: Path) -> None:
    prefs = KioskToolPreferences(tmp_path / "prefs.json")

    assert await prefs.get_enabled_servers() is None


async def test_roundtrip_persists_only_kiosk_preferences(tmp_path: Path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text(
        json.dumps({"legacy": {"enabled_servers": ["old"]}}),
        encoding="utf-8",
    )
    prefs = KioskToolPreferences(path)

    await prefs.set_enabled_servers(["notes", "housekeeping"])

    assert await prefs.get_enabled_servers() == ["notes", "housekeeping"]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "kiosk": {"enabled_servers": ["notes", "housekeeping"]}
    }


async def test_existing_kiosk_preferences_are_loaded(tmp_path: Path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text(
        json.dumps({"kiosk": {"enabled_servers": ["clock"]}}),
        encoding="utf-8",
    )

    assert await KioskToolPreferences(path).get_enabled_servers() == ["clock"]


async def test_corrupt_file_allows_all_servers(tmp_path: Path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text("not json", encoding="utf-8")

    assert await KioskToolPreferences(path).get_enabled_servers() is None
