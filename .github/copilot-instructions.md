# Copilot Instructions — kiosk_echo_backend

FastAPI backend for AI chat with MCP tool orchestration. **Backend-only repo.**
The kiosk web frontend lives in a separate repo (`kiosk_echo_frontend`) and is
served from a configurable static directory at runtime.

---

## Core Operating Principles

### Prefer the best change, not the smallest
- Choose the approach that produces the cleanest, simplest, most maintainable result.
- If a rewrite is clearly better than patching, rewrite.
- Avoid layered fixes that preserve messy structure.

### Zero legacy leftovers
When changing or replacing behavior:
- Do **not** leave old code behind (no commented-out code, unused modules, dead branches).
- Remove obsolete files, configs, scripts, routes, flags, and assets.
- Repo-wide sweep for old names, config keys, dead references in docs/tests, unused imports.

### Keep the codebase shrinking
- Prefer deletion over preservation when something is no longer needed.
- Avoid parallel implementations of the same concept.

### Tests must stay relevant
- Update tests to reflect new design; remove tests for removed behavior.

### Documentation must be concise and current
- Keep docs short and correct. One source of truth per topic — don't duplicate.

### Always protect sensitive / local-only files
- Never commit secrets, credentials, tokens, real `.env`, private keys, local DBs, logs.

---

## Project Structure

- `src/backend/` — FastAPI application source
- `src/backend/admin/` — Vanilla HTML/JS admin UI (no build step), served at `/admin/`
- `data/` — Runtime mutable data (preferences, DBs, tokens, uploads) — **never in git**
- `src/backend/data/clients/` — Bundled default settings (read-only fallback)
- `docs/` — Documentation
- `scripts/` — Automation / deployment tooling
- `tests/` — Test suite

---

## Frontends (separate repos)

- **`kiosk_echo_frontend`** — kiosk display UI for Echo Show devices. Builds to
  `dist/`, deployed to `/opt/kiosk-frontend/dist/` on LXC. Mounted by the backend
  at `/kiosk/` via the `KIOSK_STATIC_DIR` setting.
- **Admin UI** lives inside this repo at `src/backend/admin/` — vanilla HTML,
  no Vite, no Node. Covers system prompt, MCP enable/disable, and raw JSON
  editing of client LLM/STT/TTS/UI settings.

---

## Deployment

- `./scripts/deploy.sh` — push, pull on LXC, optionally `uv sync`, restart service
- Backend Python changes auto-reload — just `git push` then pull on server
- Server is LXC `${PROXMOX_LXC_ID}` — no direct SSH; access via Proxmox host
  using `pct exec` (see `docs/PROXMOX_DEPLOYMENT.md`)
- Service user `backend`, default `APP_DIR=/opt/chat-backend`, systemd unit `chat-backend`

### File ownership (critical)
The backend service runs as `User=backend`. After any `git pull` on the server,
the runtime `data/` directory must remain writable by the service user.

```bash
chown -R backend:backend /opt/chat-backend/data/
```

The deploy script handles this automatically.

---

## Architecture

- MCP tools are external (LXC 117, ports 9004–9015) — never embed tool logic
- `ChatOrchestrator` coordinates streaming, tools, persistence
- `StreamingHandler` manages SSE events and tool execution loops
- Runtime data lives in `data/` at project root (not `src/backend/data/`)

---

## Code Style

- Python ≥3.13 with `from __future__ import annotations`
- Async for all I/O with explicit timeouts
- Type hints on all signatures; Pydantic for schemas
- Ruff for formatting, linting, import sorting

---

## Security

- Never commit `.env`, `credentials/`, `certs/`, or `data/tokens/`
- All credentials and sensitive configuration in `.env` (see `.env.example`)
- Never hardcode passwords or API keys in source files or documentation

---

## Primary Doc Pointers

- `docs/PROXMOX_DEPLOYMENT.md` — Server setup, deploy workflows
- `docs/AI_PLAYBOOK.md` — Coding guidelines for AI assistants
- `docs/DEVELOPMENT_ENVIRONMENT.md` — Local dev setup
