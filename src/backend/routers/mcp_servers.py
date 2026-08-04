"""API routes for managing MCP server settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..schemas.mcp_servers import (
    MCPServerConnectPayload,
    MCPServerDiscoverPayload,
    MCPServerStatus,
    MCPServerStatusResponse,
    MCPServerUpdatePayload,
    MCPToolInfo,
    MCPToolTogglePayload,
    ToolPreferences,
    ToolPreferencesUpdate,
)
from ..services.tool_preferences import KioskToolPreferences
from ..services.mcp_management import MCPManagementService
from ..services.mcp_server_settings import MCPServerSettingsService

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


# ------------------------------------------------------------------
# Dependency helpers
# ------------------------------------------------------------------


def get_mcp_management(request: Request) -> MCPManagementService:
    service = getattr(request.app.state, "mcp_management_service", None)
    if service is None:
        raise RuntimeError("MCP management service is not configured")
    return service


def get_mcp_settings_service(request: Request) -> MCPServerSettingsService:
    service = getattr(request.app.state, "mcp_server_settings_service", None)
    if service is None:
        raise RuntimeError("MCP server settings service is not configured")
    return service


def get_tool_preferences(request: Request) -> KioskToolPreferences:
    service = getattr(request.app.state, "tool_preferences", None)
    if service is None:
        raise RuntimeError("Kiosk tool preferences service is not configured")
    return service


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _build_status_response(
    mgmt: MCPManagementService,
    settings: MCPServerSettingsService,
) -> MCPServerStatusResponse:
    runtime = await mgmt.get_status()
    servers: list[MCPServerStatus] = []
    for entry in runtime:
        tools = [
            MCPToolInfo(name=t["name"], enabled=t.get("enabled", True))
            for t in entry.get("tools", [])
        ]
        servers.append(
            MCPServerStatus(
                id=entry["id"],
                url=entry.get("url", ""),
                connected=entry.get("connected", False),
                tool_count=entry.get("tool_count", 0),
                tools=tools,
                disabled_tools=entry.get("disabled_tools", []),
            )
        )
    updated_at = await settings.updated_at()
    return MCPServerStatusResponse(servers=servers, updated_at=updated_at)


# ------------------------------------------------------------------
# Server endpoints
# ------------------------------------------------------------------


@router.get("/servers/", response_model=MCPServerStatusResponse)
async def read_mcp_servers(
    mgmt: MCPManagementService = Depends(get_mcp_management),
    settings: MCPServerSettingsService = Depends(get_mcp_settings_service),
) -> MCPServerStatusResponse:
    """List all configured MCP servers with connection status and tools."""
    return await _build_status_response(mgmt, settings)


@router.post("/servers/connect", response_model=MCPServerStatus)
async def connect_mcp_server(
    payload: MCPServerConnectPayload,
    mgmt: MCPManagementService = Depends(get_mcp_management),
) -> MCPServerStatus:
    """Connect to a new MCP server by URL, discover its tools."""
    try:
        entry = await mgmt.connect_server(payload.url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    tools = [
        MCPToolInfo(name=t["name"], enabled=t.get("enabled", True))
        for t in entry.get("tools", [])
    ]
    return MCPServerStatus(
        id=entry["id"],
        url=entry.get("url", payload.url),
        connected=entry.get("connected", False),
        tool_count=entry.get("tool_count", 0),
        tools=tools,
        disabled_tools=entry.get("disabled_tools", []),
    )


@router.delete("/servers/{server_id}")
async def remove_mcp_server(
    server_id: str,
    mgmt: MCPManagementService = Depends(get_mcp_management),
) -> dict[str, str]:
    """Disconnect and remove a server from the registry."""
    try:
        await mgmt.remove_server(server_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Server not found: {server_id}")
    return {"status": "removed", "server_id": server_id}


@router.post("/servers/discover", response_model=MCPServerStatusResponse)
async def discover_mcp_servers(
    payload: MCPServerDiscoverPayload | None = None,
    mgmt: MCPManagementService = Depends(get_mcp_management),
    settings: MCPServerSettingsService = Depends(get_mcp_settings_service),
) -> MCPServerStatusResponse:
    """Scan for MCP servers on a network host (or reconnect configured servers)."""
    if payload and payload.ports:
        await mgmt.discover_servers(payload.host, payload.ports)
    else:
        # No payload = reconnect all configured servers
        await mgmt.reconnect_all()
    return await _build_status_response(mgmt, settings)


@router.patch("/servers/{server_id}", response_model=MCPServerStatusResponse)
async def update_mcp_server(
    server_id: str,
    payload: MCPServerUpdatePayload,
    mgmt: MCPManagementService = Depends(get_mcp_management),
    settings: MCPServerSettingsService = Depends(get_mcp_settings_service),
) -> MCPServerStatusResponse:
    """Update disabled_tools for a server."""
    try:
        if payload.disabled_tools is not None:
            await mgmt.update_disabled_tools(server_id, payload.disabled_tools)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Server not found: {server_id}")
    return await _build_status_response(mgmt, settings)


@router.post("/servers/{server_id}/tools", response_model=MCPServerStatusResponse)
async def toggle_mcp_tool(
    server_id: str,
    payload: MCPToolTogglePayload,
    mgmt: MCPManagementService = Depends(get_mcp_management),
    settings: MCPServerSettingsService = Depends(get_mcp_settings_service),
) -> MCPServerStatusResponse:
    """Enable or disable a specific tool on a server."""
    try:
        await mgmt.toggle_tool(server_id, payload.tool_name, payload.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Server not found: {server_id}")
    return await _build_status_response(mgmt, settings)


@router.post("/servers/refresh", response_model=MCPServerStatusResponse)
async def refresh_mcp_servers(
    mgmt: MCPManagementService = Depends(get_mcp_management),
    settings: MCPServerSettingsService = Depends(get_mcp_settings_service),
) -> MCPServerStatusResponse:
    """Reconnect configured servers, discover new ones on known hosts, and re-list tools."""
    await mgmt.reconnect_all()
    return await _build_status_response(mgmt, settings)


# ------------------------------------------------------------------
# Kiosk preference endpoints
# ------------------------------------------------------------------


@router.get("/preferences", response_model=ToolPreferences)
async def get_tool_preferences_route(
    prefs: KioskToolPreferences = Depends(get_tool_preferences),
) -> ToolPreferences:
    """Get MCP server preferences for the kiosk."""
    return ToolPreferences(enabled_servers=await prefs.get_enabled_servers())


@router.put("/preferences", response_model=ToolPreferences)
async def update_tool_preferences(
    payload: ToolPreferencesUpdate,
    prefs: KioskToolPreferences = Depends(get_tool_preferences),
) -> ToolPreferences:
    """Update which MCP servers the kiosk may use."""
    await prefs.set_enabled_servers(payload.enabled_servers)
    return ToolPreferences(enabled_servers=payload.enabled_servers)


__all__ = ["router"]
