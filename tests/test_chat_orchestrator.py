"""Integration tests for the chat orchestrator streaming pipeline."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from src.backend.chat.orchestrator import _build_enhanced_system_prompt


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_snapshot_stub() -> Any:
    tz = dt.timezone(dt.timedelta(hours=-5), name="EST")
    now_local = dt.datetime(2024, 1, 2, 14, 30, tzinfo=tz)
    now_utc = dt.datetime(2024, 1, 2, 19, 30, tzinfo=dt.timezone.utc)

    class _StubSnapshot:
        def __init__(self) -> None:
            self.tzinfo = tz
            self.now_local = now_local
            self.now_utc = now_utc
            self.iso_utc = now_utc.isoformat()

        @property
        def date(self) -> dt.date:
            return self.now_local.date()

        def format_time(self) -> str:
            return self.now_local.strftime("%H:%M:%S %Z")

        def timezone_display(self) -> str:
            return "America/New_York"

    return _StubSnapshot()


def test_build_enhanced_system_prompt_includes_time_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _make_snapshot_stub()
    monkeypatch.setattr(
        "src.backend.chat.orchestrator.create_time_snapshot",
        lambda: snapshot,
    )

    result = _build_enhanced_system_prompt("Base system prompt")

    assert result.startswith("# Current Date & Time Context")
    assert "- Today's date: 2024-01-02 (Tuesday)" in result
    assert "- Current time: 14:30:00 EST" in result
    assert "- Timezone: America/New_York" in result
    assert f"- ISO timestamp (UTC): {snapshot.iso_utc}" in result
    assert result.endswith("Base system prompt")
    assert "\n\nBase system prompt" in result


def test_build_enhanced_system_prompt_without_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _make_snapshot_stub()
    monkeypatch.setattr(
        "src.backend.chat.orchestrator.create_time_snapshot",
        lambda: snapshot,
    )

    result = _build_enhanced_system_prompt(None)

    assert result.startswith("# Current Date & Time Context")
    assert "Use this context when interpreting relative dates" in result
    assert result.endswith("etc.")


def test_iter_attachment_ids_extracts_from_content() -> None:
    """Test that _iter_attachment_ids correctly extracts attachment IDs."""
    from src.backend.chat.orchestrator import _iter_attachment_ids

    content = [
        {"type": "text", "text": "Hello"},
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/image.jpg"},
            "metadata": {"attachment_id": "abc123"},
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/image2.jpg"},
            "metadata": {"attachment_id": "def456"},
        },
    ]

    ids = list(_iter_attachment_ids(content))
    assert ids == ["abc123", "def456"]


def test_iter_attachment_ids_handles_missing_metadata() -> None:
    from src.backend.chat.orchestrator import _iter_attachment_ids

    content = [
        {"type": "text", "text": "Hello"},
        {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}},
    ]

    ids = list(_iter_attachment_ids(content))
    assert ids == []


def test_iter_attachment_ids_handles_non_dict_items() -> None:
    from src.backend.chat.orchestrator import _iter_attachment_ids

    content = [
        "string item",
        123,
        {"type": "text", "text": "Valid item"},
        None,
    ]

    ids = list(_iter_attachment_ids(content))
    assert ids == []
