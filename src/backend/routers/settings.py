"""Kiosk settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.schemas.client_settings import (
    KioskSettings,
    LlmSettings,
    LlmSettingsUpdate,
    SttSettings,
    SttSettingsUpdate,
    TtsSettings,
    TtsSettingsUpdate,
    UiSettings,
    UiSettingsUpdate,
)
from backend.services.client_settings_service import (
    KioskSettingsService,
    get_kiosk_settings_service,
)

router = APIRouter(prefix="/api/settings", tags=["Kiosk Settings"])


def get_service() -> KioskSettingsService:
    return get_kiosk_settings_service()


@router.get("/llm", response_model=LlmSettings)
async def get_llm_settings(
    service: KioskSettingsService = Depends(get_service),
) -> LlmSettings:
    return service.get_llm()


@router.put("/llm", response_model=LlmSettings)
async def update_llm_settings(
    update: LlmSettingsUpdate,
    service: KioskSettingsService = Depends(get_service),
) -> LlmSettings:
    return service.update_llm(update)


@router.post("/llm/reset", response_model=LlmSettings)
async def reset_llm_settings(
    service: KioskSettingsService = Depends(get_service),
) -> LlmSettings:
    return service.replace_llm(LlmSettings())


@router.get("/stt", response_model=SttSettings)
async def get_stt_settings(
    service: KioskSettingsService = Depends(get_service),
) -> SttSettings:
    return service.get_stt()


@router.put("/stt", response_model=SttSettings)
async def update_stt_settings(
    update: SttSettingsUpdate,
    service: KioskSettingsService = Depends(get_service),
) -> SttSettings:
    try:
        return service.update_stt(update)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/stt/reset", response_model=SttSettings)
async def reset_stt_settings(
    service: KioskSettingsService = Depends(get_service),
) -> SttSettings:
    return service.reset_stt()


@router.get("/tts", response_model=TtsSettings)
async def get_tts_settings(
    service: KioskSettingsService = Depends(get_service),
) -> TtsSettings:
    return service.get_tts()


@router.put("/tts", response_model=TtsSettings)
async def update_tts_settings(
    update: TtsSettingsUpdate,
    service: KioskSettingsService = Depends(get_service),
) -> TtsSettings:
    try:
        return service.update_tts(update)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tts/reset", response_model=TtsSettings)
async def reset_tts_settings(
    service: KioskSettingsService = Depends(get_service),
) -> TtsSettings:
    return service.reset_tts()


@router.get("/tts/voices")
async def get_tts_voices(provider: str = "openai") -> list[dict[str, str]]:
    """Return supported voice names for a TTS provider."""
    voices = {
        "openai": [
            ("alloy", "Alloy (Neutral)"),
            ("echo", "Echo (Male)"),
            ("fable", "Fable (British)"),
            ("onyx", "Onyx (Male, Deep)"),
            ("nova", "Nova (Female)"),
            ("shimmer", "Shimmer (Female)"),
        ],
        "deepgram": [
            ("aura-asteria-en", "Asteria (Female)"),
            ("aura-luna-en", "Luna (Female)"),
            ("aura-stella-en", "Stella (Female)"),
            ("aura-athena-en", "Athena (Female)"),
            ("aura-hera-en", "Hera (Female)"),
            ("aura-orion-en", "Orion (Male)"),
            ("aura-arcas-en", "Arcas (Male)"),
            ("aura-perseus-en", "Perseus (Male)"),
            ("aura-angus-en", "Angus (Male, Irish)"),
            ("aura-orpheus-en", "Orpheus (Male)"),
            ("aura-helios-en", "Helios (Male, British)"),
            ("aura-zeus-en", "Zeus (Male)"),
        ],
        "elevenlabs": [
            ("Rachel", "Rachel"),
            ("Drew", "Drew"),
            ("Clyde", "Clyde"),
            ("Paul", "Paul"),
            ("Domi", "Domi"),
            ("Dave", "Dave"),
            ("Fin", "Fin"),
            ("Sarah", "Sarah"),
            ("Antoni", "Antoni"),
            ("Thomas", "Thomas"),
            ("Charlie", "Charlie"),
            ("Emily", "Emily"),
        ],
    }
    return [{"id": voice_id, "name": name} for voice_id, name in voices.get(provider, [])]


@router.get("/ui", response_model=UiSettings)
async def get_ui_settings(
    service: KioskSettingsService = Depends(get_service),
) -> UiSettings:
    return service.get_ui()


@router.put("/ui", response_model=UiSettings)
async def update_ui_settings(
    update: UiSettingsUpdate,
    service: KioskSettingsService = Depends(get_service),
) -> UiSettings:
    return service.update_ui(update)


@router.post("/ui/reset", response_model=UiSettings)
async def reset_ui_settings(
    service: KioskSettingsService = Depends(get_service),
) -> UiSettings:
    return service.reset_ui()


@router.get("", response_model=KioskSettings)
async def get_all_settings(
    service: KioskSettingsService = Depends(get_service),
) -> KioskSettings:
    return service.get_all()


@router.post("/reset", response_model=KioskSettings)
async def reset_all_settings(
    service: KioskSettingsService = Depends(get_service),
) -> KioskSettings:
    return service.reset_all()


__all__ = ["router"]
