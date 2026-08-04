# Chat Backend (FastAPI)

FastAPI backend for the kiosk's AI chat and voice features. It streams responses
from OpenRouter, manages kiosk settings, and exposes tools hosted on external
MCP servers.

This is a **backend-only repo**. The kiosk web UI lives in
[`kiosk_echo_frontend`](https://github.com/jck411/kiosk_echo_frontend) (separate
repo). A minimal vanilla HTML admin UI is bundled at `/admin/`.

## Quick start

```bash
cp .env.example .env       # fill in keys
uv sync
./start.sh                 # https://0.0.0.0:8000 (uses certs/ if present)
```

Then open:
- `https://localhost:8000/admin/` — admin UI (system prompt, MCP, settings)
- `https://localhost:8000/api/...` — JSON API
- `https://localhost:8000/health` — health check

## Layout

| Path | Purpose |
|------|---------|
| `src/backend/` | FastAPI app source |
| `src/backend/admin/` | Vanilla admin UI (no build step) |
| `data/clients/kiosk/` | Runtime kiosk settings and presets |
| `data/` | Runtime mutable data (gitignored) |
| `tests/` | pytest suite |
| `scripts/deploy.sh` | Deploy to Proxmox LXC |

## Kiosk frontend

The kiosk repo deploys its built `dist/` to `/opt/kiosk-frontend/dist/` on the
LXC. The backend mounts that directory at `/kiosk/` via the `KIOSK_STATIC_DIR`
env var (default `/opt/kiosk-frontend/dist`). If the path is missing, `/kiosk/`
returns 404 — the rest of the backend keeps working.

## Tests

```bash
uv run pytest -q
```

## Deploy

```bash
./scripts/deploy.sh         # push + pull on LXC
./scripts/deploy.sh deps    # also run `uv sync` on LXC + restart
./scripts/deploy.sh status  # service status + current commit
```

See `docs/PROXMOX_DEPLOYMENT.md` for full details.
