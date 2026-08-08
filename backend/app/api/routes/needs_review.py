"""
Unified Needs Review API (Cycle 10).

  GET /api/needs-review?category=ALL|METADATA|IDENTITY_ENRICHMENT|GENRE|ANALYSIS|QUALITY

Read-only aggregation across existing specialist review queues -- see
needs_review_service for the source mapping. No write actions are exposed
here; each item carries a deep-link to its specialist page instead.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ...services import needs_review_service

router = APIRouter(tags=["needs-review"])

_VALID_CATEGORIES = {"ALL", "METADATA", "IDENTITY_ENRICHMENT", "GENRE", "ANALYSIS", "QUALITY"}


@router.get("/needs-review")
async def get_needs_review(
    category: Optional[str] = Query(default="ALL", description="Filter by review category"),
):
    normalized = (category or "ALL").upper()
    if normalized not in _VALID_CATEGORIES:
        normalized = "ALL"
    return needs_review_service.list_items(normalized)
