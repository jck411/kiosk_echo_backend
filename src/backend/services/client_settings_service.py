"""Persistent settings for the kiosk application."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from backend.schemas.client_settings import (
    KioskPreset,
    KioskPresets,
    KioskPresetUpdate,
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

logger = logging.getLogger(__name__)

# Bundled defaults ship with the backend package.
_BUNDLED_DATA_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "clients" / "kiosk"
)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _resolve_runtime_data_dir() -> Path:
    """Resolve where mutable kiosk settings should be written."""
    configured_dir = os.getenv("KIOSK_SETTINGS_DIR")
    if configured_dir:
        return Path(configured_dir).expanduser()
    return _PROJECT_ROOT / "data" / "clients" / "kiosk"


class KioskSettingsService:
    """Manage the kiosk's LLM, speech, UI, and preset settings."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.base_path = (
            Path(data_dir).expanduser() if data_dir else _resolve_runtime_data_dir()
        )
        # When using default runtime dir, fall back to bundled defaults for reads.
        self.default_path = None if data_dir else _BUNDLED_DATA_DIR
        self._cache: dict[str, object] = {}

    def _ensure_dir(self) -> None:
        """Ensure the client data directory exists."""
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _file_path(self, name: str) -> Path:
        """Get path to a settings file."""
        return self.base_path / f"{name}.json"

    @staticmethod
    def _read_json(path: Path) -> Optional[dict]:
        """Read JSON from disk, returning None for invalid/missing data."""
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
            return None

    def _load_json(self, name: str) -> Optional[dict]:
        """Load JSON from a settings file."""
        path = self._file_path(name)
        if path.exists():
            return self._read_json(path)

        if self.default_path is not None:
            bundled_path = self.default_path / f"{name}.json"
            if bundled_path.exists():
                return self._read_json(bundled_path)

        return None

    def _save_json(self, name: str, data: dict) -> None:
        """Save JSON to a settings file."""
        self._ensure_dir()
        path = self._file_path(name)
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            raise PermissionError(
                f"Cannot save {name} settings to '{path}'. "
                "Set KIOSK_SETTINGS_DIR to a writable directory."
            ) from exc
        logger.debug(f"Saved {path}")

    # =========================================================================
    # LLM Settings
    # =========================================================================

    def get_llm(self) -> LlmSettings:
        """Get LLM settings for this client."""
        if "llm" in self._cache:
            return self._cache["llm"]  # type: ignore

        data = self._load_json("llm")
        if data:
            try:
                settings = LlmSettings.model_validate(data)
            except Exception as e:
                logger.warning("Invalid kiosk LLM settings: %s", e)
                settings = LlmSettings()
        else:
            settings = LlmSettings()

        self._cache["llm"] = settings
        return settings

    def update_llm(self, update: LlmSettingsUpdate) -> LlmSettings:
        """Update LLM settings with partial data."""
        current = self.get_llm()
        update_data = update.model_dump(exclude_none=True)
        merged = current.model_copy(update=update_data)

        # Sync top-level fields from the parameters dict so backend
        # services that read temperature/max_tokens directly stay correct.
        if merged.parameters:
            if "temperature" in merged.parameters:
                try:
                    merged.temperature = float(merged.parameters["temperature"])
                except (TypeError, ValueError):
                    pass
            if "max_tokens" in merged.parameters:
                try:
                    merged.max_tokens = int(merged.parameters["max_tokens"])
                except (TypeError, ValueError):
                    pass

        self._save_json("llm", merged.model_dump())
        self._cache["llm"] = merged
        return merged

    def replace_llm(self, settings: LlmSettings) -> LlmSettings:
        """Replace LLM settings entirely."""
        self._save_json("llm", settings.model_dump())
        self._cache["llm"] = settings
        return settings

    # =========================================================================
    # STT Settings
    # =========================================================================

    def get_stt(self) -> SttSettings:
        """Get STT settings for this client."""
        if "stt" in self._cache:
            return self._cache["stt"]  # type: ignore

        data = self._load_json("stt")
        if data:
            try:
                settings = SttSettings.model_validate(data)
            except Exception as e:
                logger.warning("Invalid kiosk STT settings: %s", e)
                settings = SttSettings()
        else:
            settings = SttSettings()

        self._cache["stt"] = settings
        return settings

    def update_stt(self, update: SttSettingsUpdate) -> SttSettings:
        """Update STT settings with partial data."""
        current = self.get_stt()
        update_data = update.model_dump(exclude_none=True)
        merged = current.model_copy(update=update_data)
        self._save_json("stt", merged.model_dump())
        self._cache["stt"] = merged
        return merged

    def reset_stt(self) -> SttSettings:
        """Reset STT settings to defaults."""
        settings = SttSettings()
        self._save_json("stt", settings.model_dump())
        self._cache["stt"] = settings
        return settings

    # =========================================================================
    # TTS Settings
    # =========================================================================

    def get_tts(self) -> TtsSettings:
        """Get TTS settings for this client."""
        if "tts" in self._cache:
            return self._cache["tts"]  # type: ignore

        data = self._load_json("tts")
        if data:
            try:
                settings = TtsSettings.model_validate(data)
            except Exception as e:
                logger.warning("Invalid kiosk TTS settings: %s", e)
                settings = TtsSettings()
        else:
            settings = TtsSettings()

        self._cache["tts"] = settings
        return settings

    def update_tts(self, update: TtsSettingsUpdate) -> TtsSettings:
        """Update TTS settings with partial data."""
        current = self.get_tts()
        update_data = update.model_dump(exclude_none=True)
        merged = current.model_copy(update=update_data)
        self._save_json("tts", merged.model_dump())
        self._cache["tts"] = merged
        return merged

    def reset_tts(self) -> TtsSettings:
        """Reset TTS settings to defaults."""
        settings = TtsSettings()
        self._save_json("tts", settings.model_dump())
        self._cache["tts"] = settings
        return settings

    # =========================================================================
    # UI Settings
    # =========================================================================

    def get_ui(self) -> UiSettings:
        """Get UI settings for this client."""
        if "ui" in self._cache:
            return self._cache["ui"]  # type: ignore

        data = self._load_json("ui")
        if data:
            try:
                settings = UiSettings.model_validate(data)
            except Exception as e:
                logger.warning("Invalid kiosk UI settings: %s", e)
                settings = UiSettings()
        else:
            settings = UiSettings()

        self._cache["ui"] = settings
        return settings

    def update_ui(self, update: UiSettingsUpdate) -> UiSettings:
        """Update UI settings with partial data."""
        current = self.get_ui()
        update_data = update.model_dump(exclude_none=True)
        merged = current.model_copy(update=update_data)
        self._save_json("ui", merged.model_dump())
        self._cache["ui"] = merged
        return merged

    def reset_ui(self) -> UiSettings:
        """Reset UI settings to defaults."""
        settings = UiSettings()
        self._save_json("ui", settings.model_dump())
        self._cache["ui"] = settings
        return settings

    # =========================================================================
    # Presets
    # =========================================================================

    def get_presets(self) -> KioskPresets:
        """Get all presets for this client."""
        if "presets" in self._cache:
            return self._cache["presets"]  # type: ignore

        data = self._load_json("presets")
        if data:
            try:
                presets = KioskPresets.model_validate(data)
            except Exception as e:
                logger.warning("Invalid kiosk presets: %s", e)
                presets = KioskPresets()
        else:
            presets = KioskPresets()

        self._cache["presets"] = presets
        return presets

    def update_preset(self, index: int, update: KioskPresetUpdate) -> KioskPresets:
        """Update a preset at the given index."""
        from datetime import datetime, timezone

        presets = self.get_presets()
        if index < 0 or index >= len(presets.presets):
            raise ValueError(f"Preset index {index} out of range")

        preset = presets.presets[index]

        update_data = update.model_dump(exclude_unset=True)

        # Drop null MCP fields — null is not a valid value in the new schema.
        # The frontend sends concrete [] / {} instead; null here means stale client.
        for mcp_key in ("enabled_servers", "disabled_tools"):
            if mcp_key in update_data and update_data[mcp_key] is None:
                update_data.pop(mcp_key)

        # Handle nested LLM update - replace entirely instead of merging
        if "llm" in update_data and update_data["llm"]:
            llm_update = update_data.pop("llm")
            preset.llm = LlmSettings.model_validate(llm_update)
        # Handle nested STT update
        if "stt" in update_data and update_data["stt"]:
            stt_update = update_data.pop("stt")
            if preset.stt:
                preset.stt = preset.stt.model_copy(update=stt_update)
            else:
                preset.stt = SttSettings.model_validate(stt_update)
        # Handle nested TTS update
        if "tts" in update_data and update_data["tts"]:
            tts_update = update_data.pop("tts")
            if preset.tts:
                preset.tts = preset.tts.model_copy(update=tts_update)
            else:
                preset.tts = TtsSettings.model_validate(tts_update)
        # Handle remaining fields
        for key, value in update_data.items():
            setattr(preset, key, value)

        # Update timestamp
        preset.updated_at = datetime.now(timezone.utc).isoformat()

        self._save_presets(presets)
        return presets

    def activate_preset(self, index: int) -> KioskSettings:
        """Activate a preset and apply its settings (LLM only - MCP is separate)."""
        presets = self.get_presets()
        if index < 0 or index >= len(presets.presets):
            raise ValueError(f"Preset index {index} out of range")

        preset = presets.presets[index]
        presets.active_index = index
        self._save_presets(presets)

        # Apply preset settings (LLM, STT, TTS only)
        self.replace_llm(preset.llm)
        if preset.stt:
            self._save_json("stt", preset.stt.model_dump())
            self._cache["stt"] = preset.stt
        if preset.tts:
            self._save_json("tts", preset.tts.model_dump())
            self._cache["tts"] = preset.tts

        return KioskSettings(
            llm=preset.llm,
            stt=preset.stt,
            tts=preset.tts,
        )

    def load_preset_settings(self, index: int) -> KioskSettings:
        """Load settings from a preset without setting it as active (default)."""
        presets = self.get_presets()
        if index < 0 or index >= len(presets.presets):
            raise ValueError(f"Preset index {index} out of range")

        preset = presets.presets[index]

        # Apply preset settings (LLM, STT, TTS only) - persistent for current session
        self.replace_llm(preset.llm)
        if preset.stt:
            self._save_json("stt", preset.stt.model_dump())
            self._cache["stt"] = preset.stt
        if preset.tts:
            self._save_json("tts", preset.tts.model_dump())
            self._cache["tts"] = preset.tts

        return KioskSettings(
            llm=preset.llm,
            stt=preset.stt,
            tts=preset.tts,
        )

    def add_preset(self, preset: KioskPreset) -> KioskPresets:
        """Add a new preset."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        if preset.created_at is None:
            preset = preset.model_copy(update={"created_at": now, "updated_at": now})
        presets = self.get_presets()
        presets.presets.append(preset)
        self._save_presets(presets)
        return presets

    def delete_preset(self, index: int) -> KioskPresets:
        """Delete a preset at the given index."""
        presets = self.get_presets()
        if index < 0 or index >= len(presets.presets):
            raise ValueError(f"Preset index {index} out of range")
        presets.presets.pop(index)
        # Adjust active index if needed
        if presets.active_index is not None:
            if presets.active_index == index:
                presets.active_index = None
            elif presets.active_index > index:
                presets.active_index -= 1
        self._save_presets(presets)
        return presets

    def _save_presets(self, presets: KioskPresets) -> None:
        """Save presets to file."""
        self._save_json("presets", presets.model_dump())
        self._cache["presets"] = presets

    # =========================================================================
    # Full Settings Bundle
    # =========================================================================

    def get_all(self) -> KioskSettings:
        """Get complete settings bundle (excludes MCP - that's global)."""
        return KioskSettings(
            llm=self.get_llm(),
            stt=self.get_stt(),
            tts=self.get_tts(),
            ui=self.get_ui(),
        )

    def reset_all(self) -> KioskSettings:
        """Reset all settings to defaults."""
        self._cache.clear()
        for name in ["llm", "stt", "tts", "ui", "presets"]:
            path = self._file_path(name)
            if path.exists():
                path.unlink()
        return self.get_all()


# =============================================================================
# Shared service
# =============================================================================

_service: KioskSettingsService | None = None


def get_kiosk_settings_service() -> KioskSettingsService:
    """Return the process-wide kiosk settings service."""
    global _service
    if _service is None:
        _service = KioskSettingsService()
    return _service


def clear_service_cache() -> None:
    """Clear the shared service (for testing)."""
    global _service
    _service = None


__all__ = [
    "KioskSettingsService",
    "get_kiosk_settings_service",
    "clear_service_cache",
]
