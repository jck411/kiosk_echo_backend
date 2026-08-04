"""Kiosk settings schemas."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from backend.services.time_context import EASTERN_TIMEZONE_NAME

# =============================================================================
# LLM Settings
# =============================================================================


class LlmSettings(BaseModel):
    """LLM configuration for a client."""

    model: str = Field(
        default="openai/gpt-4o-mini",
        description="OpenRouter model identifier",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="System prompt for the assistant",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    max_tokens: int = Field(
        default=500,
        ge=1,
        le=128000,
        description="Maximum tokens in response",
    )
    supports_tools: Optional[bool] = Field(
        default=None,
        description="Whether model supports tool calling",
    )
    parameters: Optional[dict[str, Any]] = Field(
        default=None,
        description="Full OpenRouter model parameters for the frontend",
    )
    # Kiosk-specific conversation mode
    conversation_mode: bool = Field(
        default=False,
        description="Enable continuous conversation mode",
    )
    conversation_timeout_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Timeout in conversation mode if no speech detected",
    )


class LlmSettingsUpdate(BaseModel):
    """Partial update for LLM settings."""

    model: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=128000)
    supports_tools: Optional[bool] = None
    parameters: Optional[dict[str, Any]] = None
    conversation_mode: Optional[bool] = None
    conversation_timeout_seconds: Optional[float] = Field(default=None, ge=1.0, le=60.0)


# =============================================================================
# STT Settings (Speech-to-Text)
# =============================================================================


class SttSettings(BaseModel):
    """STT configuration for a client."""

    mode: Literal["conversation", "command"] = Field(
        default="command",
        description="STT mode: 'conversation' (Flux v2) or 'command' (Nova-3 v1)",
    )

    # Command mode engine selection
    command_engine: Literal["deepgram", "azure"] = Field(
        default="deepgram",
        description="STT engine for command mode: 'deepgram' (Nova) or 'azure'",
    )

    # Conversation mode (Flux) settings
    eot_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="End-of-turn detection threshold",
    )
    eot_timeout_ms: int = Field(
        default=1000,
        ge=100,
        le=30000,
        description="End-of-turn timeout in milliseconds",
    )
    keyterms: list[str] = Field(
        default_factory=list,
        description="Keywords to boost recognition",
    )
    pause_timeout_seconds: int = Field(
        default=30,
        ge=0,
        le=600,
        description="Close session after X seconds when paused (0 = disabled)",
    )
    listen_timeout_seconds: int = Field(
        default=15,
        ge=0,
        le=600,
        description="Close session after X seconds of no speech while listening (0 = disabled)",
    )

    # Command mode (Nova-3) settings
    command_model: str = Field(
        default="nova-3-en",
        description="Model for command mode",
    )
    command_utterance_end_ms: int = Field(
        default=1000,
        ge=500,
        le=5000,
        description="Silence duration (ms) to detect end of utterance (min 1000 recommended)",
    )
    command_endpointing: int = Field(
        default=300,
        ge=10,
        le=5000,
        description="Endpointing threshold (ms) - 300 recommended for fast commands",
    )
    command_interim_results: bool = Field(
        default=True,
        description="Return interim (partial) results",
    )
    command_smart_format: bool = Field(
        default=True,
        description="Apply smart formatting to transcripts",
    )
    command_numerals: bool = Field(
        default=True,
        description="Convert spoken numbers to numerals",
    )

    # Azure engine settings
    azure_silence_timeout_ms: int = Field(
        default=500,
        ge=100,
        le=5000,
        description="Silence duration (ms) to finalize a speech segment",
    )
    azure_initial_silence_timeout_ms: int = Field(
        default=5000,
        ge=1000,
        le=30000,
        description="How long (ms) to wait for speech before timing out",
    )
    azure_enable_dictation: bool = Field(
        default=True,
        description="Enable dictation mode for automatic punctuation and capitalization",
    )


class SttSettingsUpdate(BaseModel):
    """Partial update for STT settings."""

    mode: Optional[Literal["conversation", "command"]] = None

    # Command mode engine selection
    command_engine: Optional[Literal["deepgram", "azure"]] = None

    # Conversation mode (Flux) settings
    eot_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    eot_timeout_ms: Optional[int] = Field(default=None, ge=100, le=30000)
    keyterms: Optional[list[str]] = None
    pause_timeout_seconds: Optional[int] = Field(default=None, ge=0, le=600)
    listen_timeout_seconds: Optional[int] = Field(default=None, ge=0, le=600)

    # Command mode (Nova-3) settings
    command_model: Optional[str] = None
    command_utterance_end_ms: Optional[int] = Field(default=None, ge=500, le=5000)
    command_endpointing: Optional[int] = Field(default=None, ge=10, le=5000)
    command_interim_results: Optional[bool] = None
    command_smart_format: Optional[bool] = None
    command_numerals: Optional[bool] = None

    # Azure engine settings
    azure_silence_timeout_ms: Optional[int] = Field(default=None, ge=100, le=5000)
    azure_initial_silence_timeout_ms: Optional[int] = Field(
        default=None, ge=1000, le=30000
    )
    azure_enable_dictation: Optional[bool] = None


# =============================================================================
# TTS Settings (Text-to-Speech)
# =============================================================================


class TtsSettings(BaseModel):
    """TTS configuration for a client."""

    enabled: bool = Field(
        default=True,
        description="Whether TTS is enabled",
    )
    provider: str = Field(
        default="openai",
        description="TTS provider: 'openai' is currently supported",
    )
    model: str = Field(
        default="tts-1",
        description="TTS model: 'tts-1' (faster) or 'tts-1-hd' (higher quality)",
    )
    voice: str = Field(
        default="alloy",
        description="OpenAI TTS voice: alloy, echo, fable, onyx, nova, shimmer",
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Speech speed multiplier (0.25 to 4.0)",
    )
    response_format: str = Field(
        default="pcm",
        description="Audio format: pcm, mp3, opus, aac, flac, wav",
    )
    sample_rate: int = Field(
        default=24000,
        ge=8000,
        le=48000,
        description="Audio sample rate in Hz (24000 for OpenAI)",
    )
    stream_chunk_bytes: int = Field(
        default=4096,
        ge=512,
        le=65536,
        description="Streaming TTS chunk size in bytes (larger reduces overhead)",
    )
    # Segmentation pipeline options
    use_segmentation: bool = Field(
        default=True,
        description="Whether to segment text at delimiters for faster initial audio",
    )
    delimiters: list[str] = Field(
        default_factory=lambda: ["\n", ". ", "? ", "! ", "* ", ", ", ": "],
        description="Delimiters to split text at for segmentation",
    )
    first_phrase_min_chars: int = Field(
        default=50,
        ge=0,
        le=500,
        description=(
            "Minimum characters to accumulate before emitting the first segmented phrase "
            "(0 = emit immediately)."
        ),
    )
    segmentation_logging_enabled: bool = Field(
        default=False,
        description="Emit logs when segmentation waits for delimiters before pushing the first phrase",
    )
    # Frontend audio buffer settings
    buffering_enabled: bool = Field(
        default=True,
        description="Enable audio buffering for smoother playback. Disable for lowest latency on fast devices.",
    )
    startup_delay_enabled: bool = Field(
        default=True,
        description="Add 60ms startup delay before first audio. Disable for instant playback on fast devices.",
    )
    low_latency_audio: bool = Field(
        default=False,
        description="Use low-latency audio mode (interactive). May cause stuttering on slow devices.",
    )
    initial_buffer_sec: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="Seconds of audio to buffer before playback starts (higher = smoother, slower start)",
    )
    max_ahead_sec: float = Field(
        default=1.5,
        ge=0.3,
        le=5.0,
        description="Maximum seconds of audio to buffer ahead during playback",
    )
    min_chunk_sec: float = Field(
        default=0.1,
        ge=0.02,
        le=0.5,
        description="Minimum seconds per scheduled audio chunk",
    )


class TtsSettingsUpdate(BaseModel):
    """Partial update for TTS settings."""

    enabled: Optional[bool] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    voice: Optional[str] = None
    speed: Optional[float] = Field(default=None, ge=0.25, le=4.0)
    response_format: Optional[str] = None
    sample_rate: Optional[int] = Field(default=None, ge=8000, le=48000)
    stream_chunk_bytes: Optional[int] = Field(default=None, ge=512, le=65536)
    use_segmentation: Optional[bool] = None
    delimiters: Optional[list[str]] = None
    first_phrase_min_chars: Optional[int] = Field(
        default=None,
        ge=0,
        le=500,
    )
    segmentation_logging_enabled: Optional[bool] = None
    buffering_enabled: Optional[bool] = None
    startup_delay_enabled: Optional[bool] = None
    low_latency_audio: Optional[bool] = None
    initial_buffer_sec: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_ahead_sec: Optional[float] = Field(default=None, ge=0.3, le=5.0)
    min_chunk_sec: Optional[float] = Field(default=None, ge=0.02, le=0.5)


# =============================================================================
# MCP Server References
# =============================================================================


# =============================================================================
# Preset Filters
# =============================================================================


class MultiSelectFilter(BaseModel):
    """Filter with include/exclude lists."""

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class PresetModelFilters(BaseModel):
    """Filters saved with a preset."""

    inputModalities: Optional[MultiSelectFilter] = None
    outputModalities: Optional[MultiSelectFilter] = None
    minContext: Optional[int] = None
    minPromptPrice: Optional[float] = None
    maxPromptPrice: Optional[float] = None
    sort: Optional[str] = None
    series: Optional[MultiSelectFilter] = None
    providers: Optional[MultiSelectFilter] = None
    supportedParameters: Optional[MultiSelectFilter] = None
    moderation: Optional[MultiSelectFilter] = None


# =============================================================================
# Kiosk presets
# =============================================================================


class KioskPreset(BaseModel):
    """A preset configuration bundle including LLM settings and MCP preferences."""

    name: str = Field(description="Display name for the preset")
    llm: LlmSettings = Field(default_factory=LlmSettings)
    stt: Optional[SttSettings] = None
    tts: Optional[TtsSettings] = None
    model_filters: Optional[PresetModelFilters] = Field(
        default=None, description="Saved model explorer filters"
    )
    enabled_servers: list[str] = Field(
        default_factory=list,
        description="MCP server IDs enabled for this preset (empty = none)",
    )
    disabled_tools: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-server disabled tool names: {server_id: [tool_name, ...]}",
    )
    created_at: Optional[str] = Field(
        default=None, description="ISO timestamp when preset was created"
    )
    updated_at: Optional[str] = Field(
        default=None, description="ISO timestamp when preset was last modified"
    )


class KioskPresetUpdate(BaseModel):
    """Partial update for a preset."""

    name: Optional[str] = None
    llm: Optional[LlmSettingsUpdate] = None
    stt: Optional[SttSettingsUpdate] = None
    tts: Optional[TtsSettingsUpdate] = None
    model_filters: Optional[PresetModelFilters] = None
    enabled_servers: Optional[list[str]] = None
    disabled_tools: Optional[dict[str, list[str]]] = None


class KioskPresets(BaseModel):
    """Collection of kiosk presets."""

    presets: list[KioskPreset] = Field(default_factory=list)
    active_index: Optional[int] = Field(
        default=None,
        description="Index of currently active preset",
    )


# =============================================================================
# UI Settings (for kiosk frontend behavior)
# =============================================================================


class UiSettings(BaseModel):
    """UI behavior settings for frontend clients."""

    idle_return_delay_ms: int = Field(
        default=10000,
        ge=1000,
        le=60000,
        description="Delay (ms) before returning to default screen after going IDLE",
    )
    display_timezone: str = Field(
        default=EASTERN_TIMEZONE_NAME,
        description="IANA timezone name for displaying times (e.g., 'America/New_York')",
    )
    slideshow_max_photos: int = Field(
        default=30,
        ge=5,
        le=100,
        description="Maximum photos to sync for slideshow (lower = less memory usage)",
    )


class UiSettingsUpdate(BaseModel):
    """Partial update for UI settings."""

    idle_return_delay_ms: Optional[int] = Field(default=None, ge=1000, le=60000)
    display_timezone: Optional[str] = Field(
        default=None,
        description="IANA timezone name for displaying times",
    )
    slideshow_max_photos: Optional[int] = Field(
        default=None,
        ge=5,
        le=100,
        description="Maximum photos to sync for slideshow",
    )


# =============================================================================
# Complete kiosk settings bundle
# =============================================================================


class KioskSettings(BaseModel):
    """Complete kiosk settings bundle (excluding MCP server configuration)."""

    llm: LlmSettings = Field(default_factory=LlmSettings)
    stt: Optional[SttSettings] = None
    tts: Optional[TtsSettings] = None
    ui: Optional[UiSettings] = None


__all__ = [
    "LlmSettings",
    "LlmSettingsUpdate",
    "SttSettings",
    "SttSettingsUpdate",
    "TtsSettings",
    "TtsSettingsUpdate",
    "UiSettings",
    "UiSettingsUpdate",
    "KioskPreset",
    "KioskPresetUpdate",
    "KioskPresets",
    "KioskSettings",
    "MultiSelectFilter",
    "PresetModelFilters",
]
