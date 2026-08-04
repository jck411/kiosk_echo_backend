"""Application factory for the FastAPI service."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .chat.orchestrator import ChatOrchestrator
from .config import get_settings
from .logging_handlers import DateStampedFileHandler, cleanup_old_logs
from .logging_settings import parse_logging_settings
from .routers.alarms import router as alarms_router
from .routers.azure_transcription import router as azure_stt_router
from .routers.chat import router as chat_router
from .routers.google_auth import router as google_auth_router
from .routers.keyword_detection import router as keyword_router
from .routers.kiosk_calendar import router as kiosk_calendar_router
from .routers.mcp_servers import router as mcp_router
from .routers.monarch_auth import router as monarch_auth_router
from .routers.profiles import router as profiles_router
from .routers.presets import router as presets_router
from .routers.settings import router as settings_router
from .routers.slideshow import router as slideshow_router
from .routers.spotify_auth import router as spotify_auth_router
from .routers.suggestions import router as suggestions_router
from .routers.uploads import router as uploads_router
from .routers.weather import router as weather_router
from .services.alarm_repository import AlarmRepository
from .services.alarm_scheduler import AlarmSchedulerService
from .services.attachments import AttachmentService
from .services.attachments_cleanup import cleanup_expired_attachments
from .services.client_profiles import ClientProfileService
from .services.mcp_management import MCPManagementService
from .services.mcp_server_settings import MCPServerSettingsService
from .services.model_settings import ModelSettingsService
from .services.suggestions import SuggestionsService
from .services.tool_preferences import KioskToolPreferences


def _configure_logging() -> None:
    """Configure application logging from the simple settings file."""

    project_root = Path(__file__).resolve().parent.parent.parent
    raw_settings = parse_logging_settings(project_root / "logging_settings.conf")

    handlers: list[logging.Handler] = []

    if raw_settings.sessions_level is not None:
        file_handler = DateStampedFileHandler(directory="logs/app", encoding="utf-8")
        file_handler.setLevel(raw_settings.sessions_level)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handlers.append(file_handler)

    if raw_settings.terminal_level is not None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(raw_settings.terminal_level)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        handlers.append(console_handler)

    level_candidates = [
        level
        for level in (raw_settings.sessions_level, raw_settings.terminal_level)
        if level is not None
    ]
    root_level = min(level_candidates) if level_candidates else logging.CRITICAL

    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(
        level=root_level,
        handlers=handlers,
        force=True,
    )

    # Align common loggers with the configured levels
    backend_level = raw_settings.sessions_level or logging.CRITICAL + 1
    logging.getLogger("backend").setLevel(backend_level)
    logging.getLogger("uvicorn").setLevel(backend_level)
    logging.getLogger("uvicorn.error").setLevel(backend_level)
    logging.getLogger("uvicorn.access").setLevel(backend_level)
    logging.getLogger("watchfiles").setLevel(max(backend_level, logging.WARNING))

    # Disable any max length restrictions on log records
    logging.logMultiprocessing = False
    logging.logProcesses = False
    logging.logThreads = False


def create_app() -> FastAPI:
    # Configure logging first thing
    _configure_logging()

    settings = get_settings()

    project_root = Path(__file__).resolve().parent.parent.parent

    def _resolve_under(base: Path, p: Path) -> Path:
        # Allow absolute paths as-is (useful for tests and external mounts).
        if p.is_absolute():
            return p.resolve()
        resolved = (base / p).resolve()
        if not resolved.is_relative_to(base):
            raise ValueError(f"Configured path {resolved} escapes project root {base}")
        return resolved

    model_settings_service = ModelSettingsService(
        default_model=settings.default_model,
        default_system_prompt=settings.openrouter_system_prompt,
    )

    mcp_servers_path = _resolve_under(project_root, settings.mcp_servers_path)

    mcp_settings_service = MCPServerSettingsService(mcp_servers_path)

    suggestions_path = _resolve_under(project_root, settings.suggestions_path)

    suggestions_service = SuggestionsService(suggestions_path)

    profiles_path = _resolve_under(project_root, Path("data/client_profiles"))
    client_profile_service = ClientProfileService(profiles_path)

    orchestrator = ChatOrchestrator(
        settings,
        model_settings_service,
        mcp_settings_service,
    )

    # MCP management and kiosk tool preferences
    mcp_management_service = MCPManagementService(
        orchestrator.get_mcp_client(),
        mcp_settings_service,
    )

    tool_preferences_path = _resolve_under(
        project_root, Path("data/client_tool_preferences.json")
    )
    tool_preferences = KioskToolPreferences(tool_preferences_path)
    orchestrator.set_tool_preferences(tool_preferences)
    orchestrator.set_mcp_management(mcp_management_service)

    attachment_service = AttachmentService(
        orchestrator.repository,
        max_size_bytes=settings.attachments_max_size_bytes,
        retention_days=settings.attachments_retention_days,
    )
    orchestrator.set_attachment_service(attachment_service)
    orchestrator.set_profile_service(client_profile_service)

    cleanup_interval_hours = max(1, min(24, settings.attachments_retention_days or 1))
    cleanup_interval_seconds = cleanup_interval_hours * 3600
    cleanup_task: asyncio.Task | None = None

    # Alarm scheduler setup
    alarms_db_path = _resolve_under(project_root, Path("data/alarms.db"))
    alarm_repository = AlarmRepository(alarms_db_path)
    alarm_scheduler = AlarmSchedulerService(alarm_repository)

    async def _attachment_cleanup_loop() -> None:
        while True:
            try:
                await cleanup_expired_attachments(orchestrator.repository)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.warning("Attachment cleanup run failed: %s", exc)
            try:
                await asyncio.sleep(cleanup_interval_seconds)
            except asyncio.CancelledError:
                raise

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal cleanup_task
        await orchestrator.initialize()

        # Initialize alarm scheduler (loads pending alarms from DB)
        await alarm_scheduler.initialize()
        logging.info("Alarm scheduler initialized")

        # Clean up old log files
        logging_settings = parse_logging_settings(
            project_root / "logging_settings.conf"
        )
        if logging_settings.retention_hours > 0:
            try:
                log_dirs = ["logs/app", "logs/conversations"]
                files_deleted, errors = cleanup_old_logs(
                    log_dirs,
                    logging_settings.retention_hours,
                    logger=logging.getLogger("backend"),
                )
                if files_deleted > 0:
                    logging.info(
                        f"Cleaned up {files_deleted} old log file(s) "
                        f"(retention: {logging_settings.retention_hours}h)"
                    )
            except Exception as exc:
                logging.warning("Log cleanup failed: %s", exc)

        try:
            await cleanup_expired_attachments(orchestrator.repository)
        except Exception as exc:
            logging.warning("Initial attachment cleanup failed: %s", exc)
        cleanup_task = asyncio.create_task(_attachment_cleanup_loop())
        try:
            yield
        finally:
            if cleanup_task is not None:
                cleanup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cleanup_task
            # Shutdown alarm scheduler
            try:
                await asyncio.wait_for(alarm_scheduler.shutdown(), timeout=5.0)
            except asyncio.TimeoutError:
                logging.warning("Alarm scheduler shutdown timed out after 5s")
            except Exception as exc:
                logging.warning("Error during alarm scheduler shutdown: %s", exc)
            # Add timeout to prevent hanging during shutdown (especially in tests)
            try:
                await asyncio.wait_for(orchestrator.shutdown(), timeout=10.0)
            except asyncio.TimeoutError:
                logging.warning("Orchestrator shutdown timed out after 10s")
            except Exception as exc:
                logging.warning("Error during orchestrator shutdown: %s", exc)

    app = FastAPI(
        title="OpenRouter Chat Backend",
        version="0.1.0",
        description="Streaming chat backend powered by OpenRouter and MCP.",
        lifespan=lifespan,
    )

    app.state.model_settings_service = model_settings_service
    app.state.chat_orchestrator = orchestrator
    app.state.mcp_server_settings_service = mcp_settings_service
    app.state.mcp_management_service = mcp_management_service
    app.state.tool_preferences = tool_preferences
    app.state.attachment_service = attachment_service
    app.state.suggestions_service = suggestions_service
    app.state.client_profile_service = client_profile_service
    app.state.alarm_scheduler = alarm_scheduler

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_permissions_policy(request, call_next):
        response = await call_next(request)
        response.headers["Permissions-Policy"] = "microphone=(self)"
        return response

    app.include_router(chat_router)
    app.include_router(mcp_router)
    app.include_router(azure_stt_router)
    app.include_router(keyword_router)
    app.include_router(settings_router)
    app.include_router(presets_router)
    app.include_router(profiles_router)
    app.include_router(weather_router)
    app.include_router(slideshow_router)
    app.include_router(kiosk_calendar_router)
    app.include_router(alarms_router)

    # helper for voice assistant imports to avoid circular deps if any,
    # though here it should be fine.
    from .routers import voice_assistant
    from .services.kiosk_chat_service import KioskChatService
    from .services.stt_service import STTService
    from .services.tts_service import TTSService
    from .services.voice_session import VoiceConnectionManager

    try:
        app.state.voice_manager = VoiceConnectionManager()
        app.state.stt_service = STTService()
        app.state.tts_service = TTSService()
        # Initialize KioskChatService with the orchestrator for tool support
        app.state.kiosk_chat_service = KioskChatService(orchestrator)
        # Wire alarm scheduler to voice manager for WebSocket notifications
        alarm_scheduler.set_voice_manager(app.state.voice_manager)
        app.include_router(voice_assistant.router)
        logging.info("Voice Assistant initialized successfully.")
    except Exception as e:
        logging.error(f"Failed to initialize Voice Assistant: {e}")
        # We don't crash the whole app, just this feature won't work

    app.include_router(
        suggestions_router, prefix="/api/suggestions", tags=["suggestions"]
    )
    app.include_router(uploads_router)
    app.include_router(
        google_auth_router,
        prefix="/api/google-auth",
        tags=["google-auth"],
    )
    app.include_router(
        monarch_auth_router,
        prefix="/api/monarch-auth",
        tags=["monarch-auth"],
    )
    app.include_router(
        spotify_auth_router,
        prefix="/api/spotify-auth",
        tags=["spotify-auth"],
    )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        """Serve a basic favicon to prevent 404 errors in browser console."""
        # Return a simple transparent 1x1 pixel PNG as favicon
        # This prevents the common favicon.ico 404 error
        # 1x1 transparent PNG in base64
        png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x00\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        return Response(content=png_data, media_type="image/png")

    @app.get("/health", tags=["health"])
    async def healthcheck() -> dict[str, str | None]:
        active_model, _ = await model_settings_service.get_openrouter_overrides()
        return {
            "status": "ok",
            "default_model": settings.default_model,
            "active_model": active_model,
        }

    from fastapi.staticfiles import StaticFiles

    # Serve the bundled vanilla admin UI at /admin/ (no build step).
    admin_dir = Path(__file__).resolve().parent / "admin"
    if admin_dir.exists():
        app.mount(
            "/admin",
            StaticFiles(directory=admin_dir, html=True),
            name="admin",
        )
    else:
        logging.warning(f"Admin UI not found at {admin_dir}; /admin will 404.")

    # Serve the kiosk frontend from an external directory. The kiosk repo
    # (kiosk_echo_frontend) deploys its built dist/ to this path on the LXC.
    # Mount unconditionally with check_dir=False so a fresh deploy doesn't
    # require a backend restart — StaticFiles will 404 until index.html exists.
    kiosk_dir = Path(settings.kiosk_static_dir).expanduser()
    try:
        kiosk_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.warning(f"Could not create kiosk dir {kiosk_dir}: {exc}")
    app.mount(
        "/kiosk",
        StaticFiles(directory=kiosk_dir, html=True, check_dir=False),
        name="kiosk",
    )
    if (kiosk_dir / "index.html").exists():
        logging.info(f"Kiosk frontend mounted from {kiosk_dir}")
    else:
        logging.info(
            f"Kiosk static dir {kiosk_dir} is empty; /kiosk will 404 "
            "until the kiosk frontend is deployed."
        )

    return app


__all__ = ["create_app"]
