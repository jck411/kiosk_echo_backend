"""Tests for MCP server settings service and API router."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.chat.mcp_registry import MCPServerConfig
from backend.routers.mcp_servers import (
    get_mcp_management,
    get_mcp_settings_service,
    get_tool_preferences,
)
from backend.routers.mcp_servers import router as mcp_router
from backend.services.mcp_server_settings import MCPServerSettingsService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ------------------------------------------------------------------
# Stubs for router tests
# ------------------------------------------------------------------


class StubAggregator:
    async def apply_configs(self, configs: Any) -> None:
        pass


class StubMCPManagement:
    """Minimal management service stub."""

    def __init__(self, servers: list[dict[str, Any]] | None = None) -> None:
        self._servers = list(servers or [])
        self.reconnect_all_calls = 0
        self.refresh_calls = 0
        self.toggle_tool_calls: list[tuple[str, str, bool]] = []
        self._aggregator = StubAggregator()

    async def get_status(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self._servers]

    async def connect_server(self, url: str) -> dict[str, Any]:
        entry = {
            "id": "new-server",
            "url": url,
            "connected": True,
            "tools": [],
            "tool_count": 0,
            "disabled_tools": [],
        }
        self._servers.append(entry)
        return entry

    async def remove_server(self, server_id: str) -> None:
        before = len(self._servers)
        self._servers = [s for s in self._servers if s["id"] != server_id]
        if len(self._servers) == before:
            raise KeyError(server_id)

    async def discover_servers(
        self, host: str, ports: list[int]
    ) -> list[dict[str, Any]]:
        return []

    async def reconnect_all(self) -> None:
        self.reconnect_all_calls += 1

    async def toggle_tool(self, server_id: str, tool_name: str, enabled: bool) -> None:
        self.toggle_tool_calls.append((server_id, tool_name, enabled))

    async def update_disabled_tools(
        self, server_id: str, disabled_tools: list[str]
    ) -> None:
        for s in self._servers:
            if s["id"] == server_id:
                s["disabled_tools"] = disabled_tools
                return
        raise KeyError(server_id)

    async def refresh(self) -> None:
        self.refresh_calls += 1


class StubSettingsService:
    """Settings service stub for router tests."""

    def __init__(self, configs: list[MCPServerConfig] | None = None) -> None:
        self._configs = [c.model_copy(deep=True) for c in (configs or [])]
        self._updated_at: datetime | None = None

    async def get_configs(self) -> list[MCPServerConfig]:
        return [c.model_copy(deep=True) for c in self._configs]

    async def patch_server(
        self,
        server_id: str,
        *,
        disabled_tools: Any | None = None,
    ) -> MCPServerConfig:
        for i, cfg in enumerate(self._configs):
            if cfg.id == server_id:
                data = cfg.model_dump(exclude_none=False)
                if disabled_tools is not None:
                    data["disabled_tools"] = list(disabled_tools)
                updated = MCPServerConfig.model_validate(data)
                self._configs[i] = updated
                return updated
        raise KeyError(server_id)

    async def updated_at(self) -> datetime | None:
        return self._updated_at


class StubToolPreferences:
    def __init__(self) -> None:
        self._data: list[str] | None = None

    async def get_enabled_servers(self) -> list[str] | None:
        return self._data

    async def set_enabled_servers(self, server_ids: list[str]) -> None:
        self._data = server_ids


# ------------------------------------------------------------------
# Helper to build a test FastAPI app with stubs
# ------------------------------------------------------------------


def _make_app(
    mgmt: StubMCPManagement | None = None,
    settings: StubSettingsService | None = None,
    prefs: StubToolPreferences | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_router)
    if mgmt is not None:
        app.dependency_overrides[get_mcp_management] = lambda: mgmt
    if settings is not None:
        app.dependency_overrides[get_mcp_settings_service] = lambda: settings
    if prefs is not None:
        app.dependency_overrides[get_tool_preferences] = lambda: prefs
    return app


# ------------------------------------------------------------------
# MCPServerSettingsService unit tests
# ------------------------------------------------------------------


async def test_service_loads_fallback_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    fallback = [{"id": "alpha", "url": "http://127.0.0.1:9101/mcp"}]

    service = MCPServerSettingsService(path, fallback=fallback)
    configs = await service.get_configs()

    assert len(configs) == 1
    assert configs[0].id == "alpha"
    assert configs[0].url == "http://127.0.0.1:9101/mcp"

    new_config = MCPServerConfig(id="beta", url="http://127.0.0.1:9102/mcp")
    await service.replace_configs([new_config])

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["servers"][0]["id"] == "beta"
    assert raw["servers"][0]["url"] == "http://127.0.0.1:9102/mcp"


async def test_service_add_and_remove(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    service = MCPServerSettingsService(path)

    cfg = MCPServerConfig(id="my-server", url="http://127.0.0.1:9001/mcp")
    await service.add_server(cfg)
    configs = await service.get_configs()
    assert len(configs) == 1
    assert configs[0].id == "my-server"

    await service.remove_server("my-server")
    configs = await service.get_configs()
    assert len(configs) == 0


async def test_service_add_duplicate_raises(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    service = MCPServerSettingsService(path)

    cfg = MCPServerConfig(id="s1", url="http://127.0.0.1:9001/mcp")
    await service.add_server(cfg)

    with pytest.raises(ValueError, match="already exists"):
        await service.add_server(cfg)


async def test_service_remove_unknown_raises(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    service = MCPServerSettingsService(path)

    with pytest.raises(KeyError):
        await service.remove_server("nope")


async def test_service_patch_and_toggle_tool(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps({"servers": [{"id": "alpha", "url": "http://127.0.0.1:9001/mcp"}]}),
        encoding="utf-8",
    )

    service = MCPServerSettingsService(path)

    await service.toggle_tool("alpha", "echo", enabled=False)
    configs = await service.get_configs()
    assert configs[0].disabled_tools == {"echo"}

    await service.toggle_tool("alpha", "echo", enabled=True)
    configs = await service.get_configs()
    assert configs[0].disabled_tools == set()


async def test_service_updated_at(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    service = MCPServerSettingsService(path)
    assert await service.updated_at() is None

    cfg = MCPServerConfig(id="s1", url="http://127.0.0.1:9001/mcp")
    await service.add_server(cfg)
    ts = await service.updated_at()
    assert isinstance(ts, datetime)


# ------------------------------------------------------------------
# Router: GET /api/mcp/servers/
# ------------------------------------------------------------------


async def test_router_list_servers() -> None:
    servers = [
        {
            "id": "alpha",
            "url": "http://host:9001/mcp",
            "connected": True,
            "tool_count": 1,
            "tools": [{"name": "ping", "enabled": True}],
            "disabled_tools": [],
        }
    ]
    mgmt = StubMCPManagement(servers)
    settings = StubSettingsService()

    app = _make_app(mgmt=mgmt, settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/mcp/servers/")

    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload["servers"]) == 1
    assert payload["servers"][0]["id"] == "alpha"
    assert payload["servers"][0]["connected"] is True
    assert payload["servers"][0]["tool_count"] == 1


# ------------------------------------------------------------------
# Router: PATCH /api/mcp/servers/{server_id}
# ------------------------------------------------------------------


async def test_router_patch_disabled_tools() -> None:
    servers = [
        {
            "id": "alpha",
            "url": "http://host:9001/mcp",
            "connected": True,
            "tool_count": 0,
            "tools": [],
            "disabled_tools": [],
        }
    ]
    mgmt = StubMCPManagement(servers)
    settings = StubSettingsService(
        [MCPServerConfig(id="alpha", url="http://host:9001/mcp")]
    )

    app = _make_app(mgmt=mgmt, settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/mcp/servers/alpha", json={"disabled_tools": ["ping"]}
        )

    assert resp.status_code == 200


async def test_router_patch_unknown_server_404() -> None:
    mgmt = StubMCPManagement([])
    settings = StubSettingsService()

    app = _make_app(mgmt=mgmt, settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/mcp/servers/nonexistent", json={"disabled_tools": ["x"]}
        )

    assert resp.status_code == 404


# ------------------------------------------------------------------
# Router: POST /api/mcp/servers/refresh
# ------------------------------------------------------------------


async def test_router_refresh() -> None:
    mgmt = StubMCPManagement([])
    settings = StubSettingsService()

    app = _make_app(mgmt=mgmt, settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/mcp/servers/refresh")

    assert resp.status_code == 200
    assert mgmt.reconnect_all_calls == 1


# ------------------------------------------------------------------
# Router: POST /api/mcp/servers/connect
# ------------------------------------------------------------------


async def test_router_connect_server() -> None:
    mgmt = StubMCPManagement([])

    app = _make_app(mgmt=mgmt)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/mcp/servers/connect",
            json={"url": "http://new-server:9001/mcp"},
        )

    assert resp.status_code == 200
    assert resp.json()["id"] == "new-server"


# ------------------------------------------------------------------
# Router: DELETE /api/mcp/servers/{server_id}
# ------------------------------------------------------------------


async def test_router_delete_server() -> None:
    servers = [
        {
            "id": "alpha",
            "url": "http://host:9001/mcp",
            "connected": True,
            "tool_count": 0,
            "tools": [],
            "disabled_tools": [],
        }
    ]
    mgmt = StubMCPManagement(servers)

    app = _make_app(mgmt=mgmt)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/api/mcp/servers/alpha")

    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"


async def test_router_delete_unknown_server_404() -> None:
    mgmt = StubMCPManagement([])

    app = _make_app(mgmt=mgmt)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/api/mcp/servers/nope")

    assert resp.status_code == 404


# ------------------------------------------------------------------
# Router: POST /api/mcp/servers/{server_id}/tools
# ------------------------------------------------------------------


async def test_router_toggle_tool() -> None:
    servers = [
        {
            "id": "alpha",
            "url": "http://host:9001/mcp",
            "connected": True,
            "tool_count": 1,
            "tools": [{"name": "ping", "enabled": True}],
            "disabled_tools": [],
        }
    ]
    mgmt = StubMCPManagement(servers)
    settings = StubSettingsService(
        [MCPServerConfig(id="alpha", url="http://host:9001/mcp")]
    )

    app = _make_app(mgmt=mgmt, settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/mcp/servers/alpha/tools",
            json={"tool_name": "ping", "enabled": False},
        )

    assert resp.status_code == 200
    assert mgmt.toggle_tool_calls == [("alpha", "ping", False)]


# ------------------------------------------------------------------
# Router: Preferences endpoints
# ------------------------------------------------------------------


async def test_router_preferences_roundtrip() -> None:
    prefs = StubToolPreferences()

    app = _make_app(prefs=prefs)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Initially no preferences set (None = all servers enabled)
        resp = await client.get("/api/mcp/preferences")
        assert resp.status_code == 200
        assert resp.json()["enabled_servers"] is None

        # Update
        resp = await client.put(
            "/api/mcp/preferences",
            json={"enabled_servers": ["notes", "housekeeping"]},
        )
        assert resp.status_code == 200

        # Read back
        resp = await client.get("/api/mcp/preferences")
        assert resp.json()["enabled_servers"] == ["notes", "housekeeping"]
