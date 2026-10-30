"""Shared API response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    time: datetime


class ReadinessResponse(BaseModel):
    status: str
    database: bool
    redis: bool
    schema_revision: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    detail: str


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int
