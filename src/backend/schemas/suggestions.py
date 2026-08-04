"""Suggestion schemas."""

from pydantic import BaseModel, Field


class Suggestion(BaseModel):
    """A quick prompt suggestion for the chat interface."""

    label: str = Field(..., min_length=1, description="Display label for the suggestion")
    text: str = Field(..., min_length=1, description="The prompt text to use when selected")


__all__ = ["Suggestion"]
