"""Kiosk preset routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.client_settings import (
    KioskPreset,
    KioskPresets,
    KioskPresetUpdate,
    KioskSettings,
)
from backend.services.client_settings_service import KioskSettingsService
from backend.services.mcp_management import MCPManagementService
from backend.services.tool_preferences import KioskToolPreferences

from .settings import get_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/presets", tags=["Kiosk Presets"])


def get_tool_preferences(request: Request) -> KioskToolPreferences:
    service = getattr(request.app.state, "tool_preferences", None)
    if service is None:
        raise RuntimeError("Kiosk tool preferences service is not configured")
    return service


def get_mcp_management(request: Request) -> MCPManagementService:
    service = getattr(request.app.state, "mcp_management_service", None)
    if service is None:
        raise RuntimeError("MCP management service is not configured")
    return service


async def _apply_mcp_snapshot(
    preset: KioskPreset,
    prefs: KioskToolPreferences,
    mgmt: MCPManagementService,
) -> None:
    await prefs.set_enabled_servers(preset.enabled_servers)

    current_status = await mgmt.get_status()
    for server in current_status:
        server_id = server["id"]
        preset_disabled = preset.disabled_tools.get(server_id, [])
        if preset_disabled == server.get("disabled_tools", []):
            continue
        try:
            await mgmt.update_disabled_tools(server_id, preset_disabled)
        except KeyError:
            logger.debug("Skipping unknown server '%s' during preset apply", server_id)


@router.get("", response_model=KioskPresets)
async def get_presets(
    service: KioskSettingsService = Depends(get_service),
) -> KioskPresets:
    return service.get_presets()


@router.post("", response_model=KioskPresets)
async def create_preset(
    preset: KioskPreset,
    service: KioskSettingsService = Depends(get_service),
) -> KioskPresets:
    return service.add_preset(preset)


@router.put("/{index}", response_model=KioskPresets)
async def update_preset(
    index: int,
    update: KioskPresetUpdate,
    service: KioskSettingsService = Depends(get_service),
) -> KioskPresets:
    try:
        return service.update_preset(index, update)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{index}", response_model=KioskPresets)
async def delete_preset(
    index: int,
    service: KioskSettingsService = Depends(get_service),
) -> KioskPresets:
    try:
        return service.delete_preset(index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{index}/activate", response_model=KioskSettings)
async def activate_preset(
    index: int,
    service: KioskSettingsService = Depends(get_service),
    prefs: KioskToolPreferences = Depends(get_tool_preferences),
    mgmt: MCPManagementService = Depends(get_mcp_management),
) -> KioskSettings:
    try:
        preset = service.get_presets().presets[index]
        result = service.activate_preset(index)
        await _apply_mcp_snapshot(preset, prefs, mgmt)
        return result
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/by-name/{name}/apply", response_model=KioskSettings)
async def apply_preset_by_name(
    name: str,
    service: KioskSettingsService = Depends(get_service),
    prefs: KioskToolPreferences = Depends(get_tool_preferences),
    mgmt: MCPManagementService = Depends(get_mcp_management),
) -> KioskSettings:
    for index, preset in enumerate(service.get_presets().presets):
        if preset.name == name:
            result = service.load_preset_settings(index)
            await _apply_mcp_snapshot(preset, prefs, mgmt)
            return result
    raise HTTPException(status_code=404, detail=f"Preset not found: {name}")


@router.delete("/by-name/{name}", response_model=KioskPresets)
async def delete_preset_by_name(
    name: str,
    service: KioskSettingsService = Depends(get_service),
) -> KioskPresets:
    for index, preset in enumerate(service.get_presets().presets):
        if preset.name == name:
            return service.delete_preset(index)
    raise HTTPException(status_code=404, detail=f"Preset not found: {name}")


@router.post("/by-name/{name}/set-active", response_model=KioskPresets)
async def set_active_preset_by_name(
    name: str,
    service: KioskSettingsService = Depends(get_service),
    prefs: KioskToolPreferences = Depends(get_tool_preferences),
    mgmt: MCPManagementService = Depends(get_mcp_management),
) -> KioskPresets:
    for index, preset in enumerate(service.get_presets().presets):
        if preset.name == name:
            service.activate_preset(index)
            await _apply_mcp_snapshot(preset, prefs, mgmt)
            return service.get_presets()
    raise HTTPException(status_code=404, detail=f"Preset not found: {name}")


__all__ = ["router"]
