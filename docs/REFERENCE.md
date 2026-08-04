# Operations Reference

This document provides detailed operational information about the backend's major subsystems. Use it for troubleshooting, understanding data flows, and extending functionality.

## Architecture Overview

The backend follows a layered architecture:

```
Routers (HTTP) → Services (business logic) → Repository (data access)
                    ↓
                MCP Tools (external integrations)
                    ↓
                OpenRouter API (LLM provider)
```

**Core components:**
- `ChatOrchestrator`: Coordinates tool selection, LLM calls, and response streaming
- `OpenRouterClient`: HTTP client for OpenRouter API with streaming support
- `ChatRepository`: SQLite data layer for conversations and attachments
- `MCPToolAggregator`: Manages lifecycle of MCP servers and tool discovery
- Service layer: `AttachmentService`, `ModelSettingsService`, and the kiosk
  settings service.

## Kiosk settings and presets

- **Storage**: `data/clients/kiosk/` contains LLM, STT, TTS, UI, and preset JSON.
- **Settings API**: `GET/PUT /api/settings/{llm|stt|tts|ui}`.
- **Presets API**: `/api/presets`.
- **MCP preferences**: `GET/PUT /api/mcp/preferences`.
- **Admin UI**: `/admin/` edits only the kiosk configuration.

## MCP servers

- **Config file**: `data/mcp_servers.json` (persisted by
  `MCPServerSettingsService`).
- **Runtime**: `ChatOrchestrator` loads the configs and keeps an instance of
  `chat.mcp_registry.MCPToolAggregator` warm.
- **Defaults**: The app bootstraps a calculator, housekeeping utilities, and
  Google integrations when no persisted config exists (see `backend.app`).
- **API surface**:
  - `GET /api/mcp/servers`
  - `PUT /api/mcp/servers`
  - `POST /api/mcp/servers/refresh`
- **Operational tips**:
  - Toggle servers in the UI or via API instead of editing JSON by hand; the
    aggregator hot-reloads definitions so the running instance stays in sync.
  - The aggregator prefixes tool names when multiple servers expose the same
    tool, which keeps OpenAI-compatible tool payloads conflict-free.
  - Tool names are prefixed with their server id (for example,
    `custom-gmail__gmail_create_draft`) to avoid collisions when aggregating
    multiple MCP integrations.

## Attachments and Gmail tooling

- **Service**: `backend.services.attachments.AttachmentService` uploads bytes to
  private Google Cloud Storage, records metadata in SQLite, and keeps signed
  URLs fresh when messages are serialized.
- **Environment knobs**: `ATTACHMENTS_MAX_SIZE_BYTES` and
  `ATTACHMENTS_RETENTION_DAYS`.
- **Route**: `POST /api/uploads` creates the object and returns a signed URL.
- **Behaviour**:
  - MCP servers (running on Proxmox) can persist downloads to GCS through the
    shared attachment service and return signed URLs to the caller.
  - Attachment records are associated with chat sessions; touching a message
    marks referenced files as recently used so retention policies work as
    expected.
  - A background job periodically reaps expired records and deletes the
    associated blobs from GCS.

## Speech-to-text auto submit

- **Frontend**: `frontend/src/lib/stores/speech.ts` and related helpers wire up
  Deepgram streaming and auto-submit.
- **Backend**: `/api/stt/deepgram/token` mints temporary keys when the browser
  cannot hold the long-lived API secret.
- **Detection strategy**:
  - Prefer Deepgram's `speech_final` events to detect the end of an utterance.
  - Fall back to `UtteranceEnd` events if a final result never arrives.
  - Both paths respect the configurable delay exposed in the speech settings UI
    so users can fine-tune the behaviour for noisy rooms.
- **Configuration**: Adjustable parameters live under the speech settings panel
  (model id, interim results, VAD thresholds, auto-submit delay, etc.). Values
  are validated before the websocket session is negotiated.

## Data directory structure

| Path                     | Purpose                                               |
|--------------------------|-------------------------------------------------------|
| `data/chat_sessions.db`  | SQLite store for chat history and attachment metadata |
| `data/mcp_servers.json`  | Persisted MCP server definitions                      |
| `data/suggestions.json`  | Saved suggestion templates                            |
| `data/tokens/`           | OAuth tokens minted during Google authorization flows |

All `data/` contents are gitignored by default. Do not commit credentials or user data.

## Configuration precedence

Settings are loaded with this priority (highest to lowest):

1. **Environment variables** (`.env` file or system environment)
2. **JSON config files** (`data/*.json`)
3. **Built-in defaults** (defined in `config.py`)

## Development workflow

### Adding a new MCP server

MCP servers are now external services running on Proxmox. To add a new server:

1. Deploy the server to Proxmox as a systemd service
2. Add the server URL to `data/mcp_servers.json`
3. Test with `POST /api/mcp/servers/refresh`
4. Configure client preferences via the UI

### Adding a new route

1. Create router module in `routers/` (e.g., `routers/new_feature.py`)
2. Define FastAPI route handlers with proper type hints
3. Use dependency injection for services/repository
4. Include router in `app.py` via `app.include_router()`
5. Add tests in `tests/test_new_feature.py`

### Database migrations

SQLite schema changes should be handled carefully:

1. Create migration SQL in a tracked location (e.g., `migrations/`)
2. Apply via `aiosqlite` in a startup hook or manual script
3. Test migration on a copy of production data
4. Document schema changes in this file

Current schema (simplified):

```sql
-- conversations table
CREATE TABLE conversations (
    session_id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT
);

-- messages table
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    role TEXT,
    content TEXT,
    created_at TEXT,
    FOREIGN KEY (session_id) REFERENCES conversations(session_id)
);

-- attachments table
CREATE TABLE attachments (
    attachment_id TEXT PRIMARY KEY,
    session_id TEXT,
    gcs_blob TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    signed_url TEXT,
    signed_url_expires_at TEXT,
    created_at TEXT,
    FOREIGN KEY (session_id) REFERENCES conversations(session_id)
);
```

## Monitoring and logging

Logs are structured and written to `logs/` with daily rotation:

- `logs/app/YYYY-MM-DD/` - Application logs (INFO level by default)
- `logs/conversations/YYYY-MM-DD/` - Detailed conversation logs for debugging

Configure log levels via `logging_settings.conf` or environment variables.

**Key log points:**
- Chat requests: Session ID, model, message count
- Tool calls: Tool name, arguments, duration
- Errors: Full stack traces with context
- MCP lifecycle: Server start/stop/errors

## Performance considerations

### Database

- SQLite is sufficient for single-instance deployments
- Consider connection pooling if concurrent load increases
- Use `PRAGMA journal_mode=WAL` for better concurrent read performance
- Attachments metadata is indexed by `session_id` and `attachment_id`

### MCP servers

- Servers are kept alive across requests (process pool)
- Tool discovery is cached until explicit refresh
- Failed servers are marked unavailable but don't block requests
- Large tool responses (>1MB) may cause memory pressure

### Streaming

- SSE responses use chunked transfer encoding
- OpenRouter client maintains persistent HTTP connections
- Tool results are streamed incrementally when possible
- Memory usage scales with concurrent session count

### GCS operations

- Signed URLs are cached in database to minimize API calls
- Upload validation happens in-memory before GCS write
- Consider GCS lifecycle policies for automatic cleanup beyond retention period
- Batch delete operations for cleanup jobs

## Security notes

- **Never log API keys or tokens** - sanitize before writing logs
- **Validate all file uploads** - enforce type and size limits
- **Use signed URLs with short TTL** - default 7 days for attachments
- **Sanitize user content** before tool calls to prevent injection
- **OAuth tokens** are stored with restrictive file permissions
- **GCS bucket** should be private with IAM-based access only

## Troubleshooting guide

### Symptom: Chat responses hang or timeout

**Possible causes:**
- OpenRouter API slow/down - check their status page
- MCP tool blocking on I/O - check tool logs
- Network issues - verify connectivity

**Debug steps:**
1. Check `logs/app/` for errors or timeouts
2. Test OpenRouter directly: `curl https://openrouter.ai/api/v1/models`
3. Disable MCP servers and retry: `PUT /api/mcp/servers` with empty array
4. Increase timeout in `config.py` → `openrouter_timeout`

### Symptom: Attachments not loading in UI

**Possible causes:**
- Signed URL expired
- GCS bucket permissions incorrect
- Network/firewall blocking GCS

**Debug steps:**
1. Check attachment record in database: `SELECT * FROM attachments WHERE attachment_id = ?`
2. Verify `signed_url_expires_at` is in the future
3. Test URL directly in browser (should prompt download or show image)
4. Check service account has `storage.objects.get` permission
5. Force URL refresh: fetch chat history again (triggers automatic re-signing)

### Symptom: MCP tools not appearing

**Possible causes:**
- Server failed to start
- Missing environment variables
- Configuration syntax error

**Debug steps:**
1. Check `GET /api/mcp/servers` for server status
2. Review `logs/app/` for server startup errors
3. Verify required env vars: `env | grep OPENROUTER` (example)
4. Validate `data/mcp_servers.json` syntax
5. Manual refresh: `POST /api/mcp/servers/refresh`

### Symptom: Tests failing with database errors

**Possible causes:**
- Stale test database
- File permissions
- Concurrent test execution

**Debug steps:**
1. Clean test artifacts: `rm -rf tests/data/*.db`
2. Re-run tests: `uv run pytest -v`
3. Check file permissions: `ls -la tests/data/`
4. Run tests serially: `pytest -n 0`

## Backup and recovery

### Database backup

```bash
# Backup
sqlite3 data/chat_sessions.db ".backup data/chat_sessions_backup.db"

# Restore
cp data/chat_sessions_backup.db data/chat_sessions.db
```

### GCS attachments backup

Use `gsutil` or GCS console to replicate bucket:

```bash
gsutil -m cp -r gs://source-bucket gs://backup-bucket
```

### Configuration backup

```bash
# Backup all configs
tar -czf config_backup.tar.gz data/*.json credentials/
```

## Extension points

### Custom MCP server

MCP servers are now external services deployed to Proxmox. To create a new server:

1. Create a new repository with FastMCP
2. Implement tools using `@mcp.tool()` decorators
3. Deploy as a systemd service on Proxmox
4. Add the server URL to `data/mcp_servers.json`

### Custom service

Create in `src/backend/services/`, follow the pattern:

```python
class MyService:
    def __init__(self, repository: ChatRepository):
        self._repository = repository

    async def my_operation(self, ...) -> ...:
        # Business logic
        pass
```

Inject via FastAPI dependency in router.

### Custom route

Add to `src/backend/routers/`:

```python
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/api/my-feature", tags=["my-feature"])

@router.get("/")
async def my_endpoint():
    return {"status": "ok"}
```

Include in `app.py`:
```python
from .routers import my_feature
app.include_router(my_feature.router)
```

Keep these directories under version control only when you need deterministic
fixtures; the repository ignores them by default so local state stays local.
