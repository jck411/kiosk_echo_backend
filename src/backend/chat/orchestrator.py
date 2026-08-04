"""Chat orchestrator coordinating repository, streaming handler, and MCP tools."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Iterable,
)

from ..logging_settings import parse_logging_settings
from ..openrouter import OpenRouterClient
from ..repository import ChatRepository
from ..schemas.chat import ChatCompletionRequest
from ..services.attachment_urls import refresh_message_attachments
from ..services.conversation_logging import ConversationLogWriter, MemoryBackupLogger
from ..services.mcp_server_settings import MCPServerSettingsService
from ..services.model_settings import ModelSettingsService
from ..services.time_context import build_prompt_context_block, create_time_snapshot
from .mcp_registry import MCPToolAggregator
from .streaming import SseEvent, StreamingHandler

if TYPE_CHECKING:
    from ..config import Settings
    from ..services.attachments import AttachmentService
    from ..services.client_profiles import ClientProfileService
    from ..services.tool_preferences import KioskToolPreferences
    from ..services.mcp_management import MCPManagementService

logger = logging.getLogger(__name__)


ToolPayload = list[dict[str, Any]]
def _iter_attachment_ids(content: Any) -> Iterable[str]:
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                candidate = metadata.get("attachment_id")
                if isinstance(candidate, str):
                    yield candidate


def _build_enhanced_system_prompt(base_prompt: str | None) -> str:
    """Prepend the current time context to the configured system prompt."""

    context_block = build_prompt_context_block(create_time_snapshot())
    base = (base_prompt or "").strip()
    if base:
        return f"{context_block}\n\n{base}"
    return context_block


class ChatOrchestrator:
    """High-level coordination for chat sessions."""

    def __init__(
        self,
        settings: Settings,
        model_settings: ModelSettingsService,
        mcp_settings: MCPServerSettingsService,
    ):
        src_dir = Path(__file__).resolve().parents[2]
        project_root = src_dir.parent

        db_path = settings.chat_database_path
        if not db_path.is_absolute():
            db_path = project_root / db_path

        self._repo = ChatRepository(db_path)
        self._client = OpenRouterClient(settings)
        self._mcp_client = MCPToolAggregator(
            [],
            lazy_mode=True,  # Skip MCP connections at startup for faster boot
        )
        conversation_log_dir = settings.conversation_log_dir
        if not conversation_log_dir.is_absolute():
            conversation_log_dir = project_root / conversation_log_dir
        logging_settings = parse_logging_settings(
            project_root / "logging_settings.conf"
        )
        self._conversation_logger = ConversationLogWriter(
            conversation_log_dir,
            min_level=logging_settings.conversations_level,
        )
        self._memory_backup_logger = MemoryBackupLogger(
            conversation_log_dir / "memory_backups"
        )
        self._model_settings = model_settings
        self._mcp_settings = mcp_settings
        self._streaming = StreamingHandler(
            self._client,
            self._repo,
            self._mcp_client,
            default_model=settings.default_model,
            model_settings=model_settings,
            conversation_logger=self._conversation_logger,
            memory_backup_logger=self._memory_backup_logger,
        )
        self._settings = settings
        self._init_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._profile_service: ClientProfileService | None = None
        self._tool_preferences: KioskToolPreferences | None = None
        self._mcp_management: MCPManagementService | None = None

    def set_profile_service(self, service: "ClientProfileService | None") -> None:
        """Inject the profile service after application startup wiring."""
        self._profile_service = service

    def set_tool_preferences(self, service: "KioskToolPreferences | None") -> None:
        """Inject kiosk tool preferences after application startup wiring."""
        self._tool_preferences = service

    def set_mcp_management(self, service: "MCPManagementService | None") -> None:
        """Inject the MCP management service for auto-discovery."""
        self._mcp_management = service

    async def initialize(self) -> None:
        """Initialize database and connect to configured MCP servers."""

        async with self._init_lock:
            if self._ready.is_set():
                return

            await self._repo.initialize()

            # Connect to configured MCP servers and discover new ones
            # on known hosts. Servers are external (always-on).
            try:
                configs = await self._mcp_settings.get_configs()
                await self._mcp_client.apply_configs(configs)

                # Auto-discover additional servers on known hosts
                mgmt = self._mcp_management
                if mgmt is not None:
                    discovered = await mgmt.discover_known_hosts()
                    if discovered:
                        logger.info(
                            "Auto-discovered %d new MCP server(s)",
                            len(discovered),
                        )
            except Exception as exc:
                logger.warning("MCP startup connect failed (non-fatal): %s", exc)

            self._ready.set()
            logger.info(
                "Chat orchestrator ready: %d tool(s) available",
                len(self._mcp_client.tools),
            )

    async def shutdown(self) -> None:
        """Clean up held resources."""

        try:
            await asyncio.wait_for(self._client.aclose(), timeout=2.0)
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("Error closing OpenRouter client: %s", exc)

        try:
            await asyncio.wait_for(self._mcp_client.close(), timeout=5.0)
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("Error closing MCP client: %s", exc)

        try:
            await asyncio.wait_for(self._repo.close(), timeout=2.0)
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("Error closing repository: %s", exc)

        self._ready.clear()

    async def wait_until_ready(self) -> None:
        """Block until initialization has completed."""

        await self._ready.wait()

    @property
    def repository(self) -> ChatRepository:
        """Expose the underlying repository for shared services."""

        return self._repo

    def set_attachment_service(self, service: "AttachmentService | None") -> None:
        """Inject the attachment service after application startup wiring."""

        self._streaming.set_attachment_service(service)

    async def process_stream(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncGenerator[SseEvent, None]:
        """Process a chat request and yield SSE events."""

        await self._ready.wait()

        if not request.messages:
            raise ValueError("At least one message is required to start a turn")
        incoming_messages = request.messages

        session_id = request.session_id or uuid.uuid4().hex
        existing = await self._repo.session_exists(session_id)
        await self._repo.ensure_session(session_id)

        request_metadata = (
            request.metadata if isinstance(request.metadata, dict) else None
        )

        assistant_parent_message_id: str | None = None
        if request_metadata:
            parent_candidate = request_metadata.get("client_parent_message_id")
            if isinstance(parent_candidate, str):
                assistant_parent_message_id = parent_candidate

        model_settings = self._model_settings
        stored_messages = await self._repo.get_messages(session_id)
        system_messages = [
            message for message in stored_messages if message.get("role") == "system"
        ]
        incoming_has_system = any(
            message.role == "system" for message in incoming_messages
        )
        system_prompt_value = await model_settings.get_system_prompt()
        system_prompt = _build_enhanced_system_prompt(system_prompt_value)
        has_system_message = bool(system_messages)

        if (
            system_prompt
            and not stored_messages
            and not has_system_message
            and not incoming_has_system
        ):
            await self._repo.add_message(
                session_id,
                role="system",
                content=system_prompt,
            )

        for message in incoming_messages:
            if message.role == "assistant":
                logger.debug(
                    "Ignoring assistant-authored message provided by client for session %s",
                    session_id,
                )
                continue
            content = message.content
            metadata: dict[str, Any] = {}
            if message.name:
                metadata["name"] = message.name
            extra = message.model_dump(
                exclude={
                    "role",
                    "content",
                    "tool_call_id",
                    "name",
                    "client_message_id",
                },
                exclude_none=True,
            )
            if extra:
                metadata.update(extra)
            await self._repo.add_message(
                session_id,
                role=message.role,
                content=content,
                tool_call_id=message.tool_call_id,
                metadata=metadata or None,
                client_message_id=message.client_message_id,
            )
            attachment_ids = list(dict.fromkeys(_iter_attachment_ids(content)))
            if attachment_ids:
                await self._repo.mark_attachments_used(session_id, attachment_ids)

        conversation = await self._repo.get_messages(session_id)
        conversation = await refresh_message_attachments(
            conversation,
            self._repo,
            ttl=self._settings.attachment_signed_url_ttl,
        )

        # Reconnect configured MCP servers if none connected yet
        if not self._mcp_client.tools:
            try:
                configs = await self._mcp_settings.get_configs()
                if configs:
                    await self._mcp_client.apply_configs(configs)
            except Exception as exc:
                logger.warning("MCP reconnect attempt failed: %s", exc)

        # Determine allowed servers based on profile or client preferences
        profile_id: str | None = None
        if request_metadata:
            profile_candidate = request_metadata.get("profile_id")
            if isinstance(profile_candidate, str) and profile_candidate.strip():
                profile_id = profile_candidate.strip()

        allowed_servers: set[str] | None = None

        if profile_id and self._profile_service:
            profile = await self._profile_service.get_profile(profile_id)
            if profile is not None:
                allowed_servers = set(profile.enabled_servers)
                logger.info(
                    "Using profile '%s' with %d enabled servers",
                    profile_id,
                    len(allowed_servers),
                )
            else:
                logger.warning(
                    "Profile '%s' not found, falling back to preferences",
                    profile_id,
                )

        if allowed_servers is None and self._tool_preferences:
            pref_servers = await self._tool_preferences.get_enabled_servers()
            if pref_servers is not None:
                allowed_servers = set(pref_servers)

        if allowed_servers is not None:
            tools_payload = self._mcp_client.get_openai_tools_for_servers(
                allowed_servers
            )
        else:
            tools_payload = self._mcp_client.get_openai_tools()

        filter_source = f"profile:{profile_id}" if profile_id else "kiosk"
        logger.info(
            "Kiosk session %s: %d tools (source=%s, servers=%s)",
            session_id,
            len(tools_payload),
            filter_source,
            list(allowed_servers) if allowed_servers else "all",
        )

        if not existing:
            yield {
                "event": "session",
                "data": json.dumps({"session_id": session_id}),
            }

        async for event in self._streaming.stream_conversation(
            session_id,
            request,
            conversation,
            tools_payload,
            assistant_parent_message_id,
            model_settings=model_settings,
        ):
            yield event

    async def clear_session(self, session_id: str) -> None:
        """Remove stored state for a session and reset MCP server sessions."""

        await self._repo.clear_session(session_id)

        # Reset stateful MCP servers (browser, shell sessions)
        # Fire-and-forget with best-effort cleanup
        reset_tools = [
            ("browser_close", {}),
            ("shell_reset_all_sessions", {}),
        ]
        for tool_name, args in reset_tools:
            try:
                await asyncio.wait_for(
                    self._mcp_client.call_tool(tool_name, args),
                    timeout=3.0,
                )
                logger.debug("Reset tool '%s' completed", tool_name)
            except asyncio.TimeoutError:
                logger.debug(
                    "Reset tool '%s' timed out (server may not be running)", tool_name
                )
            except Exception as exc:
                # Tool may not exist if server not connected - that's fine
                logger.debug("Reset tool '%s' failed: %s", tool_name, exc)

    async def delete_message(self, session_id: str, client_message_id: str) -> bool:
        """Delete a specific message within a session."""

        await self._ready.wait()
        deleted = await self._repo.delete_message(session_id, client_message_id)
        return deleted > 0

    def get_openrouter_client(self) -> OpenRouterClient:
        """Expose the underlying OpenRouter client."""

        return self._client

    def get_mcp_client(self) -> MCPToolAggregator:
        """Expose the underlying MCP aggregator for shared services."""

        return self._mcp_client


__all__ = ["ChatOrchestrator"]
