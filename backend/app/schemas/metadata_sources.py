"""Safe request/response shapes for local metadata-source settings."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MetadataSourceUpdate(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)
    credentials: dict[str, str | None] | None = None


class MetadataSourcesUpdateRequest(BaseModel):
    sources: list[MetadataSourceUpdate] = Field(min_length=1, max_length=10)


class MetadataSourcesResponse(BaseModel):
    sources: list[dict]


class MetadataSourceTestResponse(BaseModel):
    source_id: str
    connection_status: Literal["not_tested", "unavailable", "ready", "failed", "not_implemented"]
    message: str
    network_used: bool = False


class MetadataSourceClearResponse(BaseModel):
    source_id: str
    cleared: bool
    message: str
