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


class MetadataSourceSummary(BaseModel):
    """Safe, server-owned metadata-source capability summary."""

    id: str
    label: str
    category: Literal["local", "installed_tool", "external_api", "external_input"]
    role: Literal["local_input", "analysis_only", "track_enrichment"]
    enabled: bool
    configured: bool
    requires_credentials: bool
    credentials_status: Literal["not_required", "missing", "saved", "invalid", "unknown"]
    credential_fields: list[str]
    saved_credential_fields: list[str]
    connection_status: Literal["not_tested", "unavailable", "ready", "failed", "not_implemented", "needs_setup", "misconfigured"]
    needs_setup: bool
    selectable_for_enrichment: bool
    priority: int
    best_for: list[str]
    current_behavior: Literal["implemented", "preview_only", "settings_only", "planned"]
    configuration_note: str | None = None
    safety: list[str]


class MetadataSourcesResponse(BaseModel):
    sources: list[MetadataSourceSummary]


class MetadataSourceTestResponse(BaseModel):
    source_id: str
    connection_status: Literal["not_tested", "unavailable", "ready", "failed", "not_implemented"]
    message: str
    network_used: bool = False


class MetadataSourceClearResponse(BaseModel):
    source_id: str
    cleared: bool
    message: str
