"""Routes for managing chat attachment uploads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ..chat.orchestrator import ChatOrchestrator
from ..services.attachments import (
    AttachmentError,
    AttachmentService,
    AttachmentTooLarge,
    UnsupportedAttachmentType,
)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def get_attachment_service(request: Request) -> AttachmentService:
    service = getattr(request.app.state, "attachment_service", None)
    if service is None:
        raise HTTPException(status_code=500, detail="Attachment service unavailable")
    return service


def get_orchestrator(request: Request) -> ChatOrchestrator:
    orchestrator = getattr(request.app.state, "chat_orchestrator", None)
    if orchestrator is None:
        raise HTTPException(status_code=500, detail="Chat orchestrator unavailable")
    return orchestrator


class AttachmentResource(BaseModel):
    """Response payload describing a stored attachment."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="attachment_id")
    sessionId: str = Field(alias="session_id")
    mimeType: str = Field(alias="mime_type")
    sizeBytes: int = Field(alias="size_bytes")
    displayUrl: str = Field(alias="display_url")
    deliveryUrl: str = Field(alias="delivery_url")
    uploadedAt: str = Field(alias="created_at")
    expiresAt: str | None = Field(alias="expires_at")
    metadata: dict[str, Any] | None = None


class AttachmentUploadResponse(BaseModel):
    attachment: AttachmentResource


def _normalize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            # SQLite stores timestamps as "YYYY-MM-DD HH:MM:SS" by default
            parsed = datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            return value
        return parsed.isoformat()
    return str(value)


def _serialize_attachment(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record)
    payload["created_at"] = _normalize_timestamp(record.get("created_at"))
    payload["expires_at"] = _normalize_timestamp(record.get("expires_at"))
    payload.setdefault("metadata", None)
    signed_url = record.get("signed_url")
    if signed_url:
        payload.setdefault("display_url", signed_url)
        payload.setdefault("delivery_url", signed_url)
    return payload


@router.post(
    "",
    response_model=AttachmentUploadResponse,
    status_code=201,
    response_model_by_alias=False,
)
async def upload_attachment(
    orchestrator: ChatOrchestrator = Depends(get_orchestrator),
    service: AttachmentService = Depends(get_attachment_service),
    file: UploadFile = File(...),
    session_id: str = Form(...),
) -> AttachmentUploadResponse:
    await orchestrator.wait_until_ready()
    try:
        record = await service.save_user_upload(session_id=session_id, upload=file)
    except UnsupportedAttachmentType as exc:
        raise HTTPException(
            status_code=415, detail=f"Unsupported attachment type: {exc}"
        ) from exc
    except AttachmentTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resource = AttachmentResource(**_serialize_attachment(record))
    return AttachmentUploadResponse(attachment=resource)


__all__ = [
    "router",
    "get_attachment_service",
    "get_orchestrator",
]
