"""Configuration loader and aggregator for MCP tool servers.

The backend is a pure MCP client.
Servers are external (LXC 117) — the backend only connects to URLs and discovers tools.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from anyio import ClosedResourceError
from mcp.types import CallToolResult, Tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from .mcp_client import MCPToolClient

logger = logging.getLogger(__name__)
MCP_CLIENT_REFRESH_TIMEOUT = 10.0
MCP_CLIENT_CLOSE_TIMEOUT = 3.0


def _consume_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("Background MCP task finished with error: %s", exc)


async def _run_with_timeout(coro: Any, timeout: float) -> Any:
    task = asyncio.create_task(coro)
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        try:
            return task.result()
        except asyncio.CancelledError as exc:
            raise TimeoutError from exc
    task.cancel()
    task.add_done_callback(_consume_task_result)
    raise TimeoutError


# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------


class MCPServerConfig(BaseModel):
    """Declarative description of an MCP server the backend connects to."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Stable identifier for the server")
    url: str = Field(
        ...,
        description="Full MCP endpoint URL (e.g. http://192.168.1.117:9004/mcp)",
    )
    enabled: bool = Field(default=True, description="Whether to connect to this server")
    bearer_token_env_var: str | None = Field(
        default=None,
        description="Optional env var containing a bearer token for this MCP server",
    )
    disabled_tools: set[str] = Field(
        default_factory=set, description="Tool names to hide from LLM"
    )

    @field_validator("disabled_tools", mode="before")
    @classmethod
    def _normalize_disabled_tools(cls, value: Any) -> Any:
        if value is None:
            return set()
        if isinstance(value, (list, tuple, set)):
            return {str(item) for item in value if isinstance(item, str)}
        if isinstance(value, str):
            return {value}
        raise TypeError("disabled_tools must be a sequence of strings or null")

    def http_headers(self) -> dict[str, str]:
        """Return resolved HTTP headers without storing secret values in config."""
        if not self.bearer_token_env_var:
            return {}
        token = os.environ.get(self.bearer_token_env_var, "").strip()
        if not token:
            logger.warning(
                "MCP server '%s' expects bearer token env var '%s', but it is unset",
                self.id,
                self.bearer_token_env_var,
            )
            return {}
        return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_server_configs(
    path: Path, *, fallback: Sequence[dict[str, Any]] | None = None
) -> list[MCPServerConfig]:
    """Load MCP server definitions from JSON, optionally merging fallback entries."""

    definitions: list[dict[str, Any]] = []

    if fallback:
        definitions.extend(fallback)

    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in MCP server config {path}: {exc}"
            ) from exc

        if isinstance(payload, dict):
            items = payload.get("servers")
            if items is None:
                raise ValueError(
                    f"Expected 'servers' key in MCP server config file {path}"
                )
        elif isinstance(payload, list):
            items = payload
        else:
            raise ValueError(
                f"Unsupported MCP server config format in {path}: expected list or object"
            )

        if not isinstance(items, list):
            raise ValueError(
                f"Invalid MCP server config in {path}: 'servers' must be a list"
            )
        definitions.extend(items)

    errors: list[str] = []
    configs_by_id: dict[str, MCPServerConfig] = {}
    order: list[str] = []

    for raw in definitions:
        try:
            config = MCPServerConfig.model_validate(raw)
        except (ValidationError, ValueError) as exc:
            errors.append(str(exc))
            continue

        if config.id in configs_by_id:
            logger.info(
                "Overriding MCP server definition for id '%s' with later entry",
                config.id,
            )
            order = [existing for existing in order if existing != config.id]

        configs_by_id[config.id] = config
        order.append(config.id)

    if errors:
        message = "\n".join(errors)
        raise ValueError(f"Failed to load MCP server configuration:\n{message}")

    return [configs_by_id[item_id] for item_id in order]


# ---------------------------------------------------------------------------
# Port-range discovery (for local servers)
# ---------------------------------------------------------------------------


def _load_mcp_port_range() -> range:
    """Load MCP port range from config file."""
    # Check runtime data dir first, then bundled defaults
    config_paths = [
        Path(__file__).parents[3] / "data" / "mcp_ports.conf",
        Path(__file__).parent.parent / "data" / "mcp_ports.conf",
    ]
    start, end = 9004, 9015  # MCP discovery range. Scanned on all discovery_hosts.
    for config_path in config_paths:
        try:
            for line in config_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("MCP_PORT_START="):
                    start = int(line.split("=", 1)[1])
                elif line.startswith("MCP_PORT_END="):
                    end = int(line.split("=", 1)[1])
            break  # Found a valid config file
        except (FileNotFoundError, ValueError):
            continue
    return range(start, end + 1)


MCP_DISCOVERY_PORTS = _load_mcp_port_range()


# ---------------------------------------------------------------------------
# Internal tool binding
# ---------------------------------------------------------------------------


@dataclass
class _ToolBinding:
    name: str
    tool: Tool
    client: MCPToolClient
    config: MCPServerConfig
    description: str | None


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class MCPToolAggregator:
    """Aggregate tools from multiple MCP servers behind a single interface.

    This is a pure *MCP client* — it never spawns or manages server processes.
    Servers must already be running; the aggregator connects by URL.
    """

    def __init__(
        self,
        configs: Sequence[MCPServerConfig],
        *,
        lazy_mode: bool = False,
    ) -> None:
        self._configs = list(configs)
        self._config_map: dict[str, MCPServerConfig] = {
            cfg.id: cfg for cfg in self._configs
        }
        self._lazy_mode = lazy_mode

        self._clients: dict[str, MCPToolClient] = {}
        self._bindings: dict[str, _ToolBinding] = {}
        self._binding_order: list[_ToolBinding] = []
        self._openai_tools: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._connected = False
        self._tool_catalog: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def tools(self) -> list[Any]:
        """Return the raw MCP tool descriptors across all servers."""
        return [b.tool for b in self._binding_order]

    @property
    def has_configs(self) -> bool:
        """Whether any server configurations have been loaded."""
        return bool(self._configs)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to all configured MCP servers and build the tool registry.

        In lazy_mode this is a no-op (use discover_and_connect instead).
        """
        async with self._lock:
            if self._connected:
                return

            if self._lazy_mode:
                logger.debug("MCP aggregator in lazy mode, skipping startup connection")
                self._connected = True
                return

            logger.info(
                "Starting MCP aggregator with %d configured server(s)",
                len(self._configs),
            )

            for config in self._configs:
                if not config.enabled:
                    logger.info("Skipping disabled MCP server '%s'", config.id)
                    continue
                await self._launch_server(config)

            if not self._clients:
                logger.warning("No MCP servers connected; tool execution is disabled")

            await self._refresh_locked()
            self._connected = True

    async def discover_and_connect(self) -> dict[str, bool]:
        """Scan MCP_DISCOVERY_PORTS for running servers and connect.

        Returns ``{port: is_running}`` for every port in the range.
        """
        async with self._lock:
            discovered: dict[str, bool] = {}

            for port in MCP_DISCOVERY_PORTS:
                is_running = await self.is_server_running("127.0.0.1", port)
                discovered[str(port)] = is_running

                if not is_running:
                    continue

                url = f"http://127.0.0.1:{port}/mcp"
                config = self._config_for_url_or_port(url, port)

                if config is None:
                    logger.info("MCP server on port %d has no config, skipping", port)
                    continue
                if config.id in self._clients:
                    continue

                logger.info(
                    "Discovered MCP server '%s' on port %d, connecting…",
                    config.id,
                    port,
                )
                await self._launch_server(config)

            await self._refresh_locked()
            self._connected = True

            logger.info(
                "MCP discovery complete: %d server(s) connected, %d tool(s)",
                len(self._clients),
                len(self._binding_order),
            )
            return discovered

    async def connect_to_url(self, url: str, server_id: str | None = None) -> str:
        """Connect to a specific MCP server by URL.

        If *server_id* is ``None`` it is derived from the MCP server's
        self-reported name, falling back to ``host-port``.
        Returns the server id actually used.
        """
        async with self._lock:
            # Connect first to discover server name when no ID is given
            temp_client: MCPToolClient | None = None
            if server_id is None:
                temp_client = MCPToolClient(url=url, server_id=url)
                try:
                    await temp_client.connect()
                except Exception:
                    logger.exception("Failed to probe MCP server at %s", url)
                    raise

                # Try to use the server's self-reported name from InitializeResult
                init_result = getattr(temp_client, "_init_result", None)
                if init_result is not None:
                    server_info = getattr(init_result, "serverInfo", None)
                    if server_info is not None:
                        name = getattr(server_info, "name", None)
                        if isinstance(name, str) and name.strip():
                            server_id = name.strip().lower().replace(" ", "-")
                if server_id is None:
                    from urllib.parse import urlparse

                    parsed = urlparse(url)
                    server_id = f"{parsed.hostname or 'unknown'}-{parsed.port or 0}"

            config = self._config_map.get(server_id)
            if config is None:
                config = MCPServerConfig(id=server_id, url=url)
                self._configs.append(config)
                self._config_map[server_id] = config

            if config.id in self._clients:
                # Already connected — close the temp client if we opened one
                if temp_client is not None:
                    await temp_client.close()
                logger.info("Server '%s' already connected", config.id)
            elif temp_client is not None:
                # Re-use the already-connected temp client
                self._clients[config.id] = temp_client
            else:
                await self._launch_server(config)

            await self._refresh_locked()
            return server_id

    async def apply_configs(self, configs: Sequence[MCPServerConfig]) -> None:
        """Apply a new configuration set, reconnecting as needed."""
        async with self._lock:
            new_configs = list(configs)
            new_map: dict[str, MCPServerConfig] = {c.id: c for c in new_configs}
            old_map = self._config_map
            self._configs = new_configs
            self._config_map = new_map

            # Disconnect servers that were removed or whose URL changed.
            for server_id, client in list(self._clients.items()):
                new_cfg = new_map.get(server_id)
                if new_cfg is None:
                    await client.close()
                    self._clients.pop(server_id, None)
                    continue
                old_cfg = old_map.get(server_id)
                if old_cfg and old_cfg.url != new_cfg.url:
                    await client.close()
                    self._clients.pop(server_id, None)

            # Connect new servers.
            for config in new_configs:
                if config.id in self._clients:
                    continue
                await self._launch_server(config)

            await self._refresh_locked()
            self._connected = True

            logger.info(
                "MCP config applied: %d server(s) connected, %d tool(s)",
                len(self._clients),
                len(self._binding_order),
            )

    async def refresh(self) -> None:
        """Refresh tool catalogues for all running servers."""
        async with self._lock:
            await self._refresh_locked()

    async def close(self) -> None:
        """Disconnect all clients and reset state."""
        async with self._lock:
            for server_id, client in list(self._clients.items()):
                try:
                    await _run_with_timeout(client.close(), MCP_CLIENT_CLOSE_TIMEOUT)
                except TimeoutError:
                    logger.warning("Timeout closing MCP client '%s'", server_id)
                except Exception as exc:
                    logger.warning("Error closing MCP client '%s': %s", server_id, exc)

            self._clients.clear()
            self._bindings.clear()
            self._binding_order.clear()
            self._openai_tools.clear()
            self._connected = False

    # ------------------------------------------------------------------
    # Tool retrieval
    # ------------------------------------------------------------------

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Return tool descriptors formatted for OpenRouter/OpenAI."""
        return [copy.deepcopy(spec) for spec in self._openai_tools]

    def get_openai_tools_for_servers(
        self, server_ids: set[str]
    ) -> list[dict[str, Any]]:
        """Return tool descriptors filtered to the given server IDs."""
        return [
            copy.deepcopy(spec)
            for binding, spec in zip(self._binding_order, self._openai_tools)
            if binding.config.id in server_ids
        ]

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        """Execute a tool routed to the correct MCP server."""
        binding = self._bindings.get(name)
        if binding is None:
            raise ValueError(f"Unknown tool: {name}")
        logger.info("[MCP] Dispatching '%s' to server '%s'", name, binding.config.id)
        try:
            result = await binding.client.call_tool(name, arguments)
            logger.info(
                "[MCP] Tool '%s' completed (server '%s')", name, binding.config.id
            )
            return result
        except Exception as exc:
            logger.error("[MCP] Tool '%s' FAILED: %s", name, exc)
            raise

    @staticmethod
    def format_tool_result(result: Any) -> str:
        return MCPToolClient.format_tool_result(result)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def active_servers(self) -> list[str]:
        """Return identifiers for servers that are currently connected."""
        return list(self._clients.keys())

    def get_configs(self) -> list[MCPServerConfig]:
        """Return a deep copy of the current configuration list."""
        return [cfg.model_copy(deep=True) for cfg in self._configs]

    def describe_servers(self) -> list[dict[str, Any]]:
        """Return runtime metadata for each configured server."""
        active_tools: dict[str, dict[str, _ToolBinding]] = {}
        for b in self._binding_order:
            active_tools.setdefault(b.config.id, {})[b.name] = b

        details: list[dict[str, Any]] = []
        for config in self._configs:
            known = self._tool_catalog.get(config.id, [])
            active_map = active_tools.get(config.id, {})
            tool_entries: list[dict[str, Any]] = []

            for name in sorted(known):
                tool_entries.append(
                    {
                        "name": name,
                        "enabled": name in active_map,
                    }
                )

            details.append(
                {
                    "id": config.id,
                    "url": config.url,
                    "connected": config.id in self._clients,
                    "tool_count": len(active_map),
                    "tools": tool_entries,
                    "disabled_tools": sorted(config.disabled_tools),
                }
            )
        return details

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _config_for_url_or_port(self, url: str, port: int) -> MCPServerConfig | None:
        """Find a config matching the given URL or port."""
        for cfg in self._configs:
            if cfg.url == url:
                return cfg
            if f":{port}/mcp" in cfg.url:
                return cfg
        return None

    async def is_server_running(self, host: str, port: int) -> bool:
        """Check if a server is accepting connections on the given port."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=0.5,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def _launch_server(self, config: MCPServerConfig) -> None:
        """Connect to an already-running MCP server at the configured URL."""
        try:
            client = MCPToolClient(
                url=config.url,
                server_id=config.id,
                headers=config.http_headers(),
            )
            await client.connect()
        except Exception:
            logger.exception("Failed to connect to MCP server '%s'", config.id)
            return
        self._clients[config.id] = client

    async def _refresh_locked(self) -> None:
        """Rebuild the aggregated tool index from all connected servers."""
        bindings: list[_ToolBinding] = []
        binding_map: dict[str, _ToolBinding] = {}
        openai_tools: list[dict[str, Any]] = []
        tool_catalog: dict[str, list[str]] = {}

        for config in self._configs:
            # Preserve previous catalog for disconnected servers
            previous = self._tool_catalog.get(config.id, [])
            tool_catalog[config.id] = list(previous)

            client = self._clients.get(config.id)
            if client is None:
                continue

            try:
                await _run_with_timeout(
                    client.refresh_tools(), MCP_CLIENT_REFRESH_TIMEOUT
                )
            except (ClosedResourceError, TimeoutError):
                logger.warning(
                    "MCP server '%s' closed or timed out during refresh; removing",
                    config.id,
                )
                try:
                    await _run_with_timeout(client.close(), MCP_CLIENT_CLOSE_TIMEOUT)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error closing MCP client '%s': %s", config.id, exc)
                self._clients.pop(config.id, None)
                tool_catalog[config.id] = []
                continue
            except Exception:
                logger.exception("Failed to refresh tools for '%s'", config.id)
                tool_catalog[config.id] = []
                continue

            all_tools = list(client.tools)
            tool_catalog[config.id] = [t.name for t in all_tools]

            specs_by_name: dict[str, dict[str, Any]] = {}
            for spec in client.get_openai_tools():
                func = spec.get("function")
                if isinstance(func, dict):
                    name = func.get("name")
                    if isinstance(name, str):
                        specs_by_name[name] = copy.deepcopy(spec)

            for tool in all_tools:
                if tool.name in config.disabled_tools:
                    continue
                spec = specs_by_name.get(tool.name)
                if not spec:
                    continue

                if tool.name in binding_map:
                    logger.warning(
                        "Duplicate tool '%s' from server '%s', skipping",
                        tool.name,
                        config.id,
                    )
                    continue

                func = spec.get("function", {})
                description = func.get("description")
                if not isinstance(description, str):
                    description = None

                binding = _ToolBinding(
                    name=tool.name,
                    tool=tool,
                    client=client,
                    config=config,
                    description=description,
                )
                bindings.append(binding)
                binding_map[tool.name] = binding

                # Annotate description with server id for downstream filtering
                enriched = copy.deepcopy(spec)
                enriched_func = enriched.get("function", {})
                desc = enriched_func.get("description") or ""
                if not desc:
                    enriched_func["description"] = f"[{config.id}]"
                elif f"[{config.id}]" not in desc:
                    enriched_func["description"] = f"[{config.id}] {desc}"
                enriched["function"] = enriched_func
                openai_tools.append(enriched)

        self._bindings = binding_map
        self._binding_order = bindings
        self._openai_tools = openai_tools
        self._tool_catalog = {
            k: v for k, v in tool_catalog.items() if k in self._config_map
        }


__all__ = [
    "MCPServerConfig",
    "MCPToolAggregator",
    "load_server_configs",
]
