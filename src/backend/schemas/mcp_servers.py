"""Schemas for MCP server settings API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ------------------------------------------------------------------
# Tool info
# ------------------------------------------------------------------


class MCPToolInfo(BaseModel):
    """A tool exposed by an MCP server."""

    name: str
    enabled: bool = True


# ------------------------------------------------------------------
# Server status
# ------------------------------------------------------------------


class MCPServerStatus(BaseModel):
    """Combined config + runtime status for an MCP server."""

    id: str
    url: str
    connected: bool
    tool_count: int = 0
    tools: list[MCPToolInfo] = Field(default_factory=list)
    disabled_tools: list[str] = Field(default_factory=list)


class MCPServerStatusResponse(BaseModel):
    """Response payload containing current server configurations."""

    servers: list[MCPServerStatus]
    updated_at: datetime | None = None


# ------------------------------------------------------------------
# Request payloads
# ------------------------------------------------------------------


class MCPServerConnectPayload(BaseModel):
    """Payload for connecting to a new MCP server by URL."""

    url: str


class MCPServerDiscoverPayload(BaseModel):
    """Payload for scanning a network host for MCP servers."""

    host: str = "127.0.0.1"
    ports: list[int] = Field(default_factory=list)


class MCPServerUpdatePayload(BaseModel):
    """Partial update for a single server."""

    disabled_tools: list[str] | None = None


class MCPToolTogglePayload(BaseModel):
    """Toggle a specific tool on/off."""

    tool_name: str
    enabled: bool


# ------------------------------------------------------------------
# Kiosk preferences
# ------------------------------------------------------------------


class ToolPreferences(BaseModel):
    """MCP servers exposed to the kiosk."""

    enabled_servers: list[str] | None


class ToolPreferencesUpdate(BaseModel):
    """Update the MCP servers exposed to the kiosk."""

    enabled_servers: list[str]


__all__ = [
    "ToolPreferences",
    "ToolPreferencesUpdate",
    "MCPServerConnectPayload",
    "MCPServerDiscoverPayload",
    "MCPServerStatus",
    "MCPServerStatusResponse",
    "MCPServerUpdatePayload",
    "MCPToolInfo",
    "MCPToolTogglePayload",
]
