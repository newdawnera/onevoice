"""Validated request models for the public API."""

from __future__ import annotations

from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class ResultRequest(StrictRequestModel):
    text: str = Field(min_length=1, max_length=100_000)
    role: Optional[str] = Field(default=None, max_length=100)
    target_language: Optional[str] = Field(default=None, max_length=100)


class AiHelperRequest(StrictRequestModel):
    task_type: Literal[
        "autocomplete",
        "q_and_a",
        "detect_topics",
        "extract_actions",
    ]
    context: dict[str, Any]
    is_json: bool = False


class ManualReminderRequest(StrictRequestModel):
    action_item_id: UUID
