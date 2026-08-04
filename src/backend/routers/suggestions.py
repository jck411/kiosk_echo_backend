"""REST API endpoints for managing quick prompt suggestions."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..schemas.suggestions import Suggestion
from ..services.suggestions import SuggestionsService

router = APIRouter()


class AddSuggestionPayload(BaseModel):
    """Payload for adding a new suggestion."""

    label: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)


class SuggestionsResponse(BaseModel):
    """Response containing all suggestions."""

    suggestions: List[Suggestion]


def get_suggestions_service(request: Request) -> SuggestionsService:
    """Dependency to access the global suggestions service."""
    service = getattr(request.app.state, "suggestions_service", None)
    if service is None:  # pragma: no cover - defensive
        raise RuntimeError("Suggestions service is not configured")
    return service


@router.get("", response_model=SuggestionsResponse)
async def get_suggestions(
    service: SuggestionsService = Depends(get_suggestions_service),
) -> SuggestionsResponse:
    """Get all quick prompt suggestions."""
    suggestions = await service.get_suggestions()
    return SuggestionsResponse(suggestions=suggestions)


@router.post(
    "", response_model=SuggestionsResponse, status_code=status.HTTP_201_CREATED
)
async def add_suggestion(
    payload: AddSuggestionPayload,
    service: SuggestionsService = Depends(get_suggestions_service),
) -> SuggestionsResponse:
    """Add a new quick prompt suggestion."""
    suggestions = await service.add_suggestion(payload.label, payload.text)
    return SuggestionsResponse(suggestions=suggestions)


@router.delete("/{index}", response_model=SuggestionsResponse)
async def delete_suggestion(
    index: int,
    service: SuggestionsService = Depends(get_suggestions_service),
) -> SuggestionsResponse:
    """Delete a suggestion by index."""
    try:
        suggestions = await service.delete_suggestion(index)
        return SuggestionsResponse(suggestions=suggestions)
    except IndexError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.put("", response_model=SuggestionsResponse)
async def replace_suggestions(
    payload: SuggestionsResponse,
    service: SuggestionsService = Depends(get_suggestions_service),
) -> SuggestionsResponse:
    """Replace all suggestions."""
    suggestions = await service.replace_suggestions(payload.suggestions)
    return SuggestionsResponse(suggestions=suggestions)


__all__ = ["router"]
