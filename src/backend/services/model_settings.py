"""Expose the kiosk's active model settings to the chat pipeline."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict

from ..openrouter import OpenRouterClient, OpenRouterError
from ..schemas.client_settings import LlmSettings
from ..services.client_settings_service import get_kiosk_settings_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCapabilities:
    """Cached capability information fetched from OpenRouter."""

    supports_tools: bool | None
    supported_parameters: frozenset[str]


_PARAMETER_GUARD_LIST: tuple[str, ...] = (
    "tools",
    "tool_choice",
    "parallel_tool_calls",
)


def _is_truthy(value: Any) -> bool:
    """Best-effort truthiness check for heterogeneous API payloads."""

    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered not in {"", "false", "0", "none", "null", "no", "disabled"}
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _normalize_supported_parameter(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.lower()


def _extract_model_capabilities(model_entry: Dict[str, Any]) -> ModelCapabilities:
    capabilities = model_entry.get("capabilities")
    supports_tools: bool | None = None
    negative_flag = False

    if isinstance(capabilities, dict):
        for key in (
            "tools",
            "functions",
            "function_calling",
            "tool_choice",
            "tool_calls",
        ):
            value = capabilities.get(key)
            if value is None:
                continue
            if _is_truthy(value):
                supports_tools = True
                break
            if value is False:
                negative_flag = True

    if supports_tools is None:
        for key in ("tools", "functions", "supports_tools", "supports_functions"):
            value = model_entry.get(key)
            if value is None:
                continue
            if _is_truthy(value):
                supports_tools = True
                break
            if value is False:
                negative_flag = True

    supported_parameters: set[str] = set()
    raw_params = model_entry.get("supported_parameters")
    if isinstance(raw_params, (list, tuple, set)):
        for item in raw_params:
            normalized = _normalize_supported_parameter(item)
            if normalized:
                supported_parameters.add(normalized)
        if supports_tools is None:
            indicator_keys = {
                "tools",
                "tool_choice",
                "parallel_tool_calls",
                "functions",
                "function_calling",
            }
            if indicator_keys.intersection(supported_parameters):
                supports_tools = True
            else:
                negative_flag = True

    if supports_tools is None and negative_flag:
        supports_tools = False

    if supports_tools:
        supported_parameters.update(
            key for key in _PARAMETER_GUARD_LIST if key not in supported_parameters
        )
    return ModelCapabilities(
        supports_tools=supports_tools,
        supported_parameters=frozenset(supported_parameters),
    )


class ModelSettingsService:
    """Read active model settings from the kiosk settings store."""

    def __init__(
        self,
        default_model: str = "openai/gpt-4o-mini",
        *,
        default_system_prompt: str | None = None,
    ) -> None:
        self._default_model = default_model
        self._default_system_prompt = default_system_prompt
        self._lock = asyncio.Lock()
        self._capabilities_lock = asyncio.Lock()
        self._capabilities_cache: dict[str, ModelCapabilities] = {}

    def _get_service(self):
        """Get the kiosk settings service."""
        return get_kiosk_settings_service()

    def _get_llm(self) -> LlmSettings:
        """Get current kiosk LLM settings."""
        return self._get_service().get_llm()

    async def get_settings(self) -> "ActiveModelSettingsResponse":
        """Get current model settings."""
        async with self._lock:
            llm = self._get_llm()
            return ActiveModelSettingsResponse(
                model=llm.model,
                supports_tools=llm.supports_tools,
            )

    async def get_openrouter_overrides(self) -> tuple[str, Dict[str, Any]]:
        """Return the active model id and OpenRouter payload overrides."""
        llm = self._get_llm()
        overrides: Dict[str, Any] = {}

        if llm.temperature is not None:
            overrides["temperature"] = llm.temperature
        if llm.max_tokens is not None:
            overrides["max_tokens"] = llm.max_tokens

        return llm.model, overrides

    async def get_system_prompt(self) -> str | None:
        """Get the system prompt."""
        llm = self._get_llm()
        return llm.system_prompt or self._default_system_prompt

    async def update_system_prompt(self, prompt: str | None) -> str | None:
        """Update the system prompt."""
        from ..schemas.client_settings import LlmSettingsUpdate
        service = self._get_service()
        update = LlmSettingsUpdate(system_prompt=prompt)
        result = service.update_llm(update)
        return result.system_prompt

    async def model_supports_tools(
        self, *, client: OpenRouterClient | None = None
    ) -> bool:
        """Return whether the active model is flagged as supporting tool use."""
        llm = self._get_llm()
        if llm.supports_tools is not None:
            return bool(llm.supports_tools)

        capability = await self._get_model_capabilities(llm.model, client=client)
        if capability and capability.supports_tools is not None:
            return capability.supports_tools
        return True

    async def _get_model_capabilities(
        self,
        model_id: str,
        *,
        client: OpenRouterClient | None = None,
    ) -> ModelCapabilities | None:
        async with self._capabilities_lock:
            cached = self._capabilities_cache.get(model_id)
        if cached is not None:
            return cached

        if client is None:
            return None

        try:
            payload = await client.list_models()
        except OpenRouterError as exc:
            logger.info(
                "Unable to refresh model capabilities for %s: %s", model_id, exc.detail
            )
            return None
        except Exception as exc:
            logger.warning(
                "Unexpected error refreshing model capabilities for %s: %s",
                model_id,
                exc,
            )
            return None

        capability: ModelCapabilities | None = None
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("id") == model_id:
                    capability = _extract_model_capabilities(item)
                    break

        if capability is None:
            capability = ModelCapabilities(None, frozenset())

        async with self._capabilities_lock:
            self._capabilities_cache[model_id] = capability

        return capability

    async def get_model_capabilities(
        self,
        *,
        model_id: str | None = None,
        client: OpenRouterClient | None = None,
    ) -> ModelCapabilities | None:
        if model_id is None:
            llm = self._get_llm()
            model_id = llm.model
        if not model_id:
            return None
        return await self._get_model_capabilities(model_id, client=client)

    async def sanitize_payload_for_model(
        self,
        model_id: str,
        payload: Dict[str, Any],
        *,
        client: OpenRouterClient | None = None,
    ) -> ModelCapabilities | None:
        capability = await self._get_model_capabilities(model_id, client=client)
        if capability is None:
            return None

        if capability.supported_parameters:
            normalized_allowed = capability.supported_parameters
            for key in list(payload.keys()):
                if key in _PARAMETER_GUARD_LIST and key.lower() not in normalized_allowed:
                    payload.pop(key, None)

        if (
            capability.supports_tools is False
            and any(key in payload for key in _PARAMETER_GUARD_LIST)
        ):
            for key in _PARAMETER_GUARD_LIST:
                payload.pop(key, None)

        return capability


# Minimal response class for API compatibility
class ActiveModelSettingsResponse:
    """Response object for model settings."""

    def __init__(
        self,
        model: str,
        supports_tools: bool | None = None,
        provider: Dict[str, Any] | None = None,
        parameters: Dict[str, Any] | None = None,
    ):
        self.model = model
        self.supports_tools = supports_tools
        self.provider = provider
        self.parameters = parameters

    def as_openrouter_overrides(self) -> Dict[str, Any]:
        """Convert to OpenRouter overrides dict."""
        overrides: Dict[str, Any] = {}
        if self.provider:
            overrides["provider"] = self.provider
        if self.parameters:
            overrides.update(self.parameters)
        return overrides


__all__ = ["ModelCapabilities", "ModelSettingsService", "ActiveModelSettingsResponse"]
