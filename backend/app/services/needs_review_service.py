"""
Unified Needs Review aggregation (Cycle 10).

Read-only aggregator across already-existing specialist review sources --
this module never introduces new review/decision state. Categories:

  METADATA             -> metadata_repair_queue_service (suspicious filename,
                           duplicate-looking title/artist)
  IDENTITY_ENRICHMENT   -> metadata_repair_queue_service (missing artist/title,
                           the same signal that routes a user to Enrichment
                           Review) plus enrichment_review_service's own
                           pending decision queue (populated by online_lookup)
  GENRE                 -> metadata_repair_queue_service (missing genre)
  ANALYSIS              -> metadata_repair_queue_service (unknown bpm/key)
  QUALITY               -> quality_review_service (unresolved findings)

Each item exposes: track_id, category, severity, reason_code, summary,
current_value, recommended_value, confidence, provenance, actions
(deep-link route + label). No write actions live here -- resolving an item
means following its deep link to the existing specialist page.
"""
from __future__ import annotations

from typing import Any

from . import metadata_repair_queue_service, quality_review_service, enrichment_review_service

_METADATA_ISSUE_TYPES = {"suspicious_filename", "duplicate_title_artist"}
_IDENTITY_ISSUE_TYPES = {"missing_artist", "missing_title"}
_GENRE_ISSUE_TYPES = {"missing_genre", "missing_normalized_genre"}
_ANALYSIS_ISSUE_TYPES = {"unknown_bpm", "unknown_key"}

_REPAIR_QUEUE_ROUTES = {
    "missing_artist": ("/enrichment-review", "Enrichment Review"),
    "missing_title": ("/enrichment-review", "Enrichment Review"),
    "missing_genre": ("/genres", "Genre Taxonomy"),
    "missing_normalized_genre": ("/genres", "Genre Taxonomy"),
    "suspicious_filename": ("/metadata-repair", "Metadata Repair"),
    "duplicate_title_artist": ("/duplicates", "Duplicates"),
    "unknown_bpm": ("/bpm-review", "BPM Review"),
    "unknown_key": ("/jobs", "Jobs"),
}


def _repair_queue_items() -> list[dict[str, Any]]:
    # Read-only by design, matching every other GET in this app's smoke
    # contract: this reads whatever metadata_repair_queue_service.refresh()
    # last computed (from Metadata Repair's own refresh action, or from
    # preparation_service's Process All, which calls refresh() as part of
    # its already-write-permitted batch run) rather than recomputing on
    # every Needs Review page load.
    queue = metadata_repair_queue_service.get()
    items: list[dict[str, Any]] = []
    for entry in queue.get("items", []):
        if entry.get("status") != "open":
            continue
        issue_type = entry.get("issue_type")
        if issue_type in _IDENTITY_ISSUE_TYPES:
            category = "IDENTITY_ENRICHMENT"
        elif issue_type in _GENRE_ISSUE_TYPES:
            category = "GENRE"
        elif issue_type in _ANALYSIS_ISSUE_TYPES:
            category = "ANALYSIS"
        elif issue_type in _METADATA_ISSUE_TYPES:
            category = "METADATA"
        else:
            continue
        route, label = _REPAIR_QUEUE_ROUTES.get(issue_type, ("/issues", "Issues"))
        items.append({
            "track_id": entry.get("track_id"),
            "category": category,
            "severity": entry.get("severity", "medium").upper(),
            "reason_code": issue_type,
            "summary": issue_type.replace("_", " ").capitalize(),
            "current_value": entry.get("current_value") or None,
            "recommended_value": None,
            "confidence": None,
            "provenance": "metadata_repair_queue (deterministic local scan)",
            "actions": [{"label": f"Open {label}", "route": route}],
            "filename": entry.get("title") or entry.get("artist") or None,
        })
    return items


def _enrichment_pending_items() -> list[dict[str, Any]]:
    review = enrichment_review_service.get_review()
    items: list[dict[str, Any]] = []
    for entry in review.get("items", []):
        if entry.get("decision") != "pending":
            continue
        confidence = str(entry.get("confidence", "low")).upper()
        items.append({
            "track_id": entry.get("track_id"),
            "category": "IDENTITY_ENRICHMENT",
            "severity": "HIGH" if confidence == "LOW" else "MEDIUM",
            "reason_code": f"enrichment_candidate_{entry.get('source_id')}",
            "summary": entry.get("reason") or "Online enrichment candidate pending review",
            "current_value": entry.get("current_fields"),
            "recommended_value": entry.get("suggested_fields"),
            "confidence": confidence,
            "provenance": entry.get("source_id"),
            "actions": [{"label": "Open Enrichment Review", "route": "/enrichment-review"}],
            "filename": entry.get("filename"),
        })
    return items


def _quality_items() -> list[dict[str, Any]]:
    review = quality_review_service.get_review()
    items: list[dict[str, Any]] = []
    for entry in review.get("items", []):
        if entry.get("decision") not in ("unresolved", None):
            continue
        flags = entry.get("flags") or []
        severity = "HIGH" if any(f in ("unreadable", "unsupported_format") for f in flags) else "MEDIUM"
        items.append({
            "track_id": entry.get("track_id"),
            "category": "QUALITY",
            "severity": severity,
            "reason_code": ",".join(flags) or "quality_finding",
            "summary": f"Quality issue: {', '.join(flags) or entry.get('status', 'unknown')}",
            "current_value": {"codec": entry.get("codec"), "bitrate_kbps": entry.get("bitrate_kbps")},
            "recommended_value": None,
            "confidence": None,
            "provenance": "quality_review_service (ffprobe)",
            "actions": [{"label": "Open Quality Review", "route": "/quality-review"}],
            "filename": entry.get("filename"),
        })
    return items


_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def list_items(category: str | None = None) -> dict[str, Any]:
    all_items = _repair_queue_items() + _enrichment_pending_items() + _quality_items()
    all_items.sort(key=lambda item: _SEVERITY_ORDER.get(item["severity"], 3))

    counts: dict[str, int] = {"ALL": len(all_items)}
    for cat in ("METADATA", "IDENTITY_ENRICHMENT", "GENRE", "ANALYSIS", "QUALITY"):
        counts[cat] = sum(1 for item in all_items if item["category"] == cat)

    items = all_items if not category or category == "ALL" else [
        item for item in all_items if item["category"] == category
    ]
    return {
        "items": items,
        "counts": counts,
        "message": "Read-only aggregation of existing review queues. Resolve items on their specialist page.",
    }
