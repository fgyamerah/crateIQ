"""Safe, library-scoped settings diagnostics and small local preferences."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..core.crate_db import crates_db_path
from ..core.library_root import assert_path_under_root, assert_safe_new_root_path, library_db_path, selected_library_root
from ..core.preflight import redact_path, run_preflight
from .providers import (
    acoustid_client, beatport_client, deezer_client, discogs_client,
    lastfm_client, spotify_client, youtube_client,
)
from .waveform_readiness_service import get_waveform_readiness

_PROVIDER_ADAPTERS = {
    "acoustid": acoustid_client, "discogs": discogs_client, "beatport": beatport_client,
    "spotify": spotify_client, "deezer": deezer_client, "lastfm": lastfm_client,
    "youtube": youtube_client,
}

_DEFAULT_ANALYSIS_PREFERENCES = {
    "analyze_bpm": False,
    "analyze_key": False,
    "use_mik_when_present": True,
    "preserve_existing_bpm_key_cues": True,
    "missing_data_only": True,
    "use_external_tools": True,
}
_DEFAULT_PREFERENCES = {
    "default_export_path_mode": "filename",
    "analysis": _DEFAULT_ANALYSIS_PREFERENCES,
}
_VALID_PATH_MODES = {"filename", "relative", "absolute"}
_EDITABLE_ANALYSIS_PREFERENCES = {"analyze_bpm", "analyze_key", "use_external_tools"}
_LOCKED_ANALYSIS_PREFERENCES = {
    "use_mik_when_present",
    "preserve_existing_bpm_key_cues",
    "missing_data_only",
}
_REPO_ROOT = Path(__file__).resolve().parents[3]
# This is deliberately repo-scoped rather than library-scoped: saving a new
# root must not write into the library that has not yet been selected.
LOCAL_ENV_PATH = _REPO_ROOT / ".run" / "local" / "crateiq.env"
METADATA_SOURCES_PATH = _REPO_ROOT / ".run" / "local" / "metadata_sources.json"
RESTART_COMMAND = "scripts/crateiq-local-services.sh stop && scripts/crateiq-local-services.sh start"

_METADATA_SOURCE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {"id": "local_tags", "label": "Local tags", "category": "local", "requires_credentials": False, "default_enabled": True, "priority": 10, "best_for": ["Existing title, artist, album, genre metadata"], "current_behavior": "implemented"},
    {"id": "mixed_in_key", "label": "Mixed In Key", "category": "external_input", "requires_credentials": False, "default_enabled": True, "priority": 20, "best_for": ["Trusted BPM, key, Camelot, and cue metadata"], "current_behavior": "implemented"},
    {"id": "filename_hints", "label": "Filename hints", "category": "local", "requires_credentials": False, "default_enabled": True, "priority": 90, "best_for": ["Low-confidence fallback hints"], "current_behavior": "implemented"},
    {"id": "beets", "label": "Beets", "category": "installed_tool", "requires_credentials": False, "default_enabled": True, "priority": 60, "best_for": ["Missing non-critical local-index metadata review"], "current_behavior": "implemented", "configuration_note": "Explicit per-track lookup only, via Enrichment Review's online-lookup action. Distance-scored MusicBrainz-backed matching from beets' own matching engine."},
    {"id": "musicbrainz", "label": "MusicBrainz", "category": "external_api", "requires_credentials": False, "default_enabled": False, "priority": 40, "best_for": ["Official releases and albums"], "current_behavior": "implemented", "configuration_note": "Explicit per-track lookup only, via Enrichment Review's online-lookup action. Rate-limited to MusicBrainz's own 1 request/second limit; results are cached locally."},
    {"id": "acoustid", "label": "AcoustID", "category": "external_api", "requires_credentials": True, "default_enabled": False, "priority": 35, "best_for": ["Strong fingerprint-based identity evidence"], "current_behavior": "implemented", "credential_fields": ("client_key",), "configuration_note": "Lookup only against a Chromaprint fingerprint of the managed Inbox copy; fingerprints are never submitted to AcoustID. Requires a free self-serve application client key from acoustid.org. Rate-limited to 3 requests/second per AcoustID's published terms."},
    {"id": "discogs", "label": "Discogs", "category": "external_api", "requires_credentials": True, "default_enabled": False, "priority": 50, "best_for": ["Electronic, vinyl, remix, and release metadata"], "current_behavior": "implemented", "credential_fields": ("personal_access_token",), "configuration_note": "Uses a self-serve Discogs personal access token (Settings -> Developers on discogs.com). Rate-limited to Discogs' own 60 requests/minute authenticated limit. Discogs-derived results must retain provider attribution/link-back per Discogs API terms."},
    {"id": "spotify", "label": "Spotify", "category": "external_api", "requires_credentials": True, "default_enabled": False, "priority": 70, "best_for": ["Mainstream track, artist, and album matching via ISRC"], "current_behavior": "implemented", "credential_fields": ("client_id", "client_secret"), "configuration_note": "Uses the Client Credentials flow against your own Spotify Developer app. As of the February 2026 Web API changes, Development Mode requires the app owner to hold an active Spotify Premium subscription, is capped at 5 users, and several endpoint families were removed; Extended Quota Mode requires a registered organization with 250k+ monthly active users, which CrateIQ as local-first software does not have. Spotify's own track-level genre field is unreliable/absent in practice; genre is never inferred from artist-level data here."},
    {"id": "deezer", "label": "Deezer", "category": "external_api", "requires_credentials": False, "default_enabled": False, "priority": 80, "best_for": ["Alternate track, artist, and album matching, ISRC corroboration"], "current_behavior": "implemented", "configuration_note": "Deezer's public search endpoint (api.deezer.com/search) requires no credentials -- verified with a real live request during this cycle's research. OAuth app_id/app_secret would only be needed for user-level actions (playlists, favorites) this adapter does not use, and Deezer is not currently issuing new OAuth app credentials in any case."},
    {"id": "beatport", "label": "Beatport", "category": "external_api", "requires_credentials": True, "default_enabled": False, "priority": 55, "best_for": ["DJ, electronic, genre, and style metadata; high genre authority for strong identity matches"], "current_behavior": "implemented", "credential_fields": ("access_token",), "configuration_note": "Beatport's v4 API (api.beatport.com/v4) uses OAuth 2.0 authorization-code grant, not a simple API key -- there is no public self-service signup, and access is brokered case-by-case through Beatport's Partner Portal / business-development team. Once approved, complete Beatport's OAuth flow externally and paste the resulting bearer access token here; CrateIQ does not implement the interactive OAuth consent redirect itself."},
    {"id": "lastfm", "label": "Last.fm", "category": "external_api", "requires_credentials": True, "default_enabled": False, "priority": 85, "best_for": ["Community tag/genre corroboration, track correction"], "current_behavior": "implemented", "credential_fields": ("api_key",), "configuration_note": "Uses a free self-serve Last.fm API key (last.fm/api/account/create). Community tags are evidence, not an unquestionable canonical genre -- always mapped through the existing Genre Taxonomy. Free for non-commercial use only."},
    {"id": "youtube", "label": "YouTube", "category": "external_api", "requires_credentials": True, "default_enabled": False, "priority": 95, "best_for": ["Low-authority corroboration/discovery only"], "current_behavior": "implemented", "credential_fields": ("api_key",), "configuration_note": "Uses the official YouTube Data API v3 via a Google Cloud API key. Never used alone for high-confidence metadata. Default quota is 10,000 units/day and a single search.list call costs 100 units (~100 searches/day); queried only as a late-stage fallback after stronger providers, never scraped."},
)
_METADATA_SOURCE_BY_ID = {source["id"]: source for source in _METADATA_SOURCE_DEFINITIONS}


def _load_metadata_source_settings() -> dict[str, Any]:
    try:
        raw = json.loads(METADATA_SOURCES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"sources": {}}
    return raw if isinstance(raw, dict) and isinstance(raw.get("sources"), dict) else {"sources": {}}


def _save_metadata_source_settings(settings: dict[str, Any]) -> None:
    METADATA_SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=METADATA_SOURCES_PATH.parent, delete=False) as handle:
        handle.write(json.dumps(settings, indent=2) + "\n")
        temporary = Path(handle.name)
    temporary.replace(METADATA_SOURCES_PATH)


def _metadata_source_tool_available(source_id: str) -> bool:
    if source_id != "beets":
        return True
    report = run_preflight()
    return any(check["name"] == "binary_beet" and check["status"] == "pass" for check in report["checks"])


def get_metadata_sources() -> dict[str, Any]:
    stored = _load_metadata_source_settings()["sources"]
    sources: list[dict[str, Any]] = []
    for definition in _METADATA_SOURCE_DEFINITIONS:
        source_id = definition["id"]
        state = stored.get(source_id, {}) if isinstance(stored.get(source_id), dict) else {}
        credentials = state.get("credentials", {}) if isinstance(state.get("credentials"), dict) else {}
        credential_fields = tuple(definition.get("credential_fields", ()))
        saved_fields = sorted(field for field in credential_fields if isinstance(credentials.get(field), str) and credentials[field].strip())
        requires_credentials = bool(definition["requires_credentials"])
        configured = bool(saved_fields) if credential_fields else (source_id != "beets" or _metadata_source_tool_available(source_id))
        if requires_credentials:
            credential_status = "saved" if set(credential_fields).issubset(saved_fields) else "missing"
        else:
            credential_status = "not_required"
        if source_id == "beets":
            connection_status = "ready" if _metadata_source_tool_available(source_id) else "unavailable"
        elif source_id == "musicbrainz":
            connection_status = "ready"
        elif source_id in _PROVIDER_ADAPTERS:
            saved_credentials = {field: credentials[field] for field in saved_fields}
            connection_status = _PROVIDER_ADAPTERS[source_id].capability(saved_credentials).status
        elif definition["category"] == "external_api":
            connection_status = "not_implemented"
        else:
            connection_status = "ready"
        sources.append({
            "id": source_id, "label": definition["label"], "category": definition["category"],
            "enabled": bool(state.get("enabled", definition["default_enabled"])), "configured": configured,
            "requires_credentials": requires_credentials, "credentials_status": credential_status,
            "credential_fields": list(credential_fields), "saved_credential_fields": saved_fields,
            "connection_status": connection_status, "priority": state.get("priority", definition["priority"]),
            "best_for": definition["best_for"], "current_behavior": definition["current_behavior"],
            "configuration_note": definition.get("configuration_note"),
            "safety": ["review_first", "db_only", "no_tag_writes", "no_file_writes"],
        })
    return {"sources": sorted(sources, key=lambda source: (source["priority"], source["label"]))}


def update_metadata_sources(updates: list[dict[str, Any]]) -> dict[str, Any]:
    settings = _load_metadata_source_settings()
    sources = settings["sources"]
    for update in updates:
        source_id = update["id"]
        definition = _METADATA_SOURCE_BY_ID.get(source_id)
        if definition is None:
            raise ValueError("Unknown metadata source")
        state = sources.setdefault(source_id, {})
        if update.get("enabled") is not None:
            state["enabled"] = bool(update["enabled"])
        if update.get("priority") is not None:
            state["priority"] = int(update["priority"])
        if update.get("credentials") is not None:
            allowed = set(definition.get("credential_fields", ()))
            invalid = set(update["credentials"]) - allowed
            if invalid:
                raise ValueError("Unsupported credential field for metadata source")
            credentials = state.setdefault("credentials", {})
            for field, value in update["credentials"].items():
                if value is None or not value.strip():
                    credentials.pop(field, None)
                else:
                    credentials[field] = value.strip()
    _save_metadata_source_settings(settings)
    return get_metadata_sources()


def get_metadata_source_credentials(source_id: str) -> dict[str, str]:
    """
    Server-side-only accessor for a source's saved credential values.

    Never call this from an API response path -- credentials must never
    appear in an HTTP response body, only ever be used in-process to build
    an outbound request to that provider's own API. Returns only the
    fields that are actually saved (missing keys mean "not configured").
    """
    if source_id not in _METADATA_SOURCE_BY_ID:
        raise ValueError("Unknown metadata source")
    stored = _load_metadata_source_settings()["sources"]
    state = stored.get(source_id, {}) if isinstance(stored.get(source_id), dict) else {}
    credentials = state.get("credentials", {})
    return {k: v for k, v in credentials.items() if isinstance(v, str) and v.strip()} if isinstance(credentials, dict) else {}


def clear_metadata_source_credentials(source_id: str) -> dict[str, Any]:
    if source_id not in _METADATA_SOURCE_BY_ID:
        raise ValueError("Unknown metadata source")
    settings = _load_metadata_source_settings()
    state = settings["sources"].setdefault(source_id, {})
    state.pop("credentials", None)
    _save_metadata_source_settings(settings)
    return {"source_id": source_id, "cleared": True, "message": "Saved local credentials were cleared."}


def test_metadata_source(source_id: str) -> dict[str, Any]:
    if source_id not in _METADATA_SOURCE_BY_ID:
        raise ValueError("Unknown metadata source")
    source = next(item for item in get_metadata_sources()["sources"] if item["id"] == source_id)
    if source_id == "beets":
        ready = source["connection_status"] == "ready"
        return {"source_id": source_id, "connection_status": "ready" if ready else "unavailable", "message": "Beets executable detection only; no Beets command was run.", "network_used": False}
    if source_id in {"local_tags", "filename_hints", "mixed_in_key"}:
        return {"source_id": source_id, "connection_status": "ready", "message": "Local metadata source is available; no scan or tag write was performed.", "network_used": False}
    if source_id == "musicbrainz":
        return {"source_id": source_id, "connection_status": "ready", "message": "MusicBrainz lookup is implemented as an explicit, per-track action in Enrichment Review. This button does not perform a live network check.", "network_used": False}
    if source_id == "acoustid":
        # No generic artist/title search exists for AcoustID -- lookup needs
        # a real fingerprint from a real track. Capability check only.
        cap = acoustid_client.capability(get_metadata_source_credentials(source_id))
        return {"source_id": source_id, "connection_status": cap.status, "message": f"{cap.message} A live fingerprint lookup runs per-track during enrichment, not from this generic test.", "network_used": False}
    if source_id in _PROVIDER_ADAPTERS:
        adapter = _PROVIDER_ADAPTERS[source_id]
        credentials = get_metadata_source_credentials(source_id)
        cap = adapter.capability(credentials)
        if cap.status != "ready":
            return {"source_id": source_id, "connection_status": cap.status, "message": cap.message, "network_used": False}
        # A bounded, real, single search call -- the smallest genuine proof
        # of connectivity this API supports. Not cached: this is an explicit
        # user-triggered test, not part of any automatic/batch path.
        result = adapter.search_track("Daft Punk", "One More Time", credentials=credentials)
        if result.error:
            status = "failed" if result.network_used else cap.status
            return {"source_id": source_id, "connection_status": status, "message": result.error, "network_used": result.network_used}
        return {
            "source_id": source_id, "connection_status": "ready",
            "message": f"Live test search succeeded ({len(result.candidates)} candidate(s) returned).",
            "network_used": result.network_used,
        }
    return {"source_id": source_id, "connection_status": "not_implemented", "message": "External connection testing and track lookup are not implemented in this local-only foundation.", "network_used": False}


def _settings_path(root: Path) -> Path:
    return assert_path_under_root(root / "logs" / "app_settings.json", root)


def _load_preferences(root: Path) -> dict[str, Any]:
    path = _settings_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "default_export_path_mode": _DEFAULT_PREFERENCES["default_export_path_mode"],
            "analysis": dict(_DEFAULT_ANALYSIS_PREFERENCES),
        }
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("default_export_path_mode")
    raw_analysis = raw.get("analysis")
    analysis = dict(_DEFAULT_ANALYSIS_PREFERENCES)
    if isinstance(raw_analysis, dict):
        for key in _DEFAULT_ANALYSIS_PREFERENCES:
            value = raw_analysis.get(key)
            if isinstance(value, bool):
                analysis[key] = value
    # Locked safety policies must never be disabled by stale or hand-edited
    # local preferences. Keep them explicit in the response rather than
    # silently treating a false value as a supported choice.
    for key in _LOCKED_ANALYSIS_PREFERENCES:
        analysis[key] = True
    return {
        "default_export_path_mode": mode if mode in _VALID_PATH_MODES else "filename",
        "analysis": analysis,
    }


def _is_demo_root(root: Path) -> bool:
    return root.resolve(strict=False) == (_REPO_ROOT / ".run" / "demo-library").resolve(strict=False)


def _validated_library_root(value: str) -> Path:
    """
    Legacy/advanced entry point: the root must already exist. Managed
    workspace onboarding uses workspace_service.classify_root_candidate() /
    create_root_directory() instead, which additionally support a
    not-yet-existing final directory.
    """
    resolved = assert_safe_new_root_path(value)
    if not resolved.exists():
        raise ValueError("library_root does not exist")
    if not resolved.is_dir():
        raise ValueError("library_root must be a directory")
    if not os.access(resolved, os.R_OK):
        raise ValueError("library_root is not readable")
    return resolved


def validate_library_root(value: str) -> dict[str, Any]:
    root = _validated_library_root(value)
    return {
        "library_root": redact_path(root),
        "valid": True,
        "message": "Library root is valid. Saving it creates a pending change that requires restart.",
    }


def _pending_library_root() -> Path | None:
    try:
        for line in LOCAL_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("CRATEIQ_LIBRARY_ROOT="):
                value = line.partition("=")[2]
                return _validated_library_root(value)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return None


def _write_pending_library_root(root: Path) -> None:
    LOCAL_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LOCAL_ENV_PATH.with_suffix(".tmp")
    temporary.write_text(
        "# Managed by CrateIQ Settings. This file is local-only and contains no secrets.\n"
        f"CRATEIQ_LIBRARY_ROOT={root}\n",
        encoding="utf-8",
    )
    temporary.replace(LOCAL_ENV_PATH)


def _tool_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    tools = []
    for check in report["checks"]:
        if not check["name"].startswith("binary_"):
            continue
        metadata = check.get("metadata", {})
        tools.append({
            "name": check["name"].removeprefix("binary_").replace("_", "-"),
            "status": check["status"],
            "message": check["message"],
            "source": metadata.get("source") or metadata.get("env_override") or "PATH",
            "resolved": None,
        })
    return tools


def get_capabilities(
    report: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return action-level availability without invoking optional tools.

    Preflight's overall status remains useful for diagnostics, but it is not a
    product capability contract: an unavailable optional executable must not
    make import, browsing, crates, or exports unavailable.
    """
    report = report or run_preflight()
    preferences = preferences or _load_preferences(selected_library_root())
    analysis_preferences = preferences["analysis"]
    tools = {item["name"]: item for item in _tool_rows(report)}

    def has_tool(name: str) -> bool:
        return tools.get(name, {}).get("status") == "pass"

    def has_aubio() -> bool:
        """The safe in-app runner accepts only ``aubio tempo``, not a fallback."""
        if not has_tool("aubio"):
            return False
        override = os.environ.get("AUBIO_BIN", "").strip()
        if override:
            return bool(shutil.which(override))
        return bool(shutil.which("aubio"))

    def optional_tool_capability(
        *,
        tool: str,
        purpose: str,
        message: str,
        enabled: bool = False,
    ) -> dict[str, Any]:
        available = has_tool(tool)
        return {
            "available": available,
            "status": "available" if available else "missing",
            "required_tool": tool,
            "purpose": purpose,
            "message": message if available else f"{tool} is not available. {message}",
            "enabled": enabled,
            # Availability means setup is complete. The action remains clearly
            # deferred until its dedicated DB-only runner is implemented.
            "action_state": "coming_soon" if available else "setup_required",
        }

    quality_available = has_tool("ffprobe")
    waveform = report.get("waveform") or get_waveform_readiness(report.get("checks", ()))
    waveform_status = waveform["status"]
    return {
        "core": {
            "library_import": {
                "available": True,
                "status": "available",
                "purpose": "Initialize, scan, and import a local library index.",
                "message": "Import without analysis is always available.",
                "action_state": "ready",
            },
            "browse": {
                "available": True,
                "status": "available",
                "purpose": "Browse and search the local CrateIQ index.",
                "message": "Browsing does not require optional analysis tools.",
                "action_state": "ready",
            },
            "manual_crates": {
                "available": True,
                "status": "available",
                "purpose": "Create and order local Manual Crates.",
                "message": "Manual Crates use local CrateIQ state only.",
                "action_state": "ready",
            },
            "smart_crates": {
                "available": True,
                "status": "available",
                "purpose": "Suggest crates from metadata already in the local index.",
                "message": "Smart Crates do not require new BPM or key analysis.",
                "action_state": "ready",
            },
            "exports": {
                "available": True,
                "status": "available",
                "purpose": "Create portable and staged crate exports.",
                "message": "Saved crate exports do not require optional analysis tools.",
                "action_state": "ready",
            },
        },
        "analysis": {
            "mixed_in_key_coverage": {
                "available": True,
                "status": "available",
                "required_source": "Existing Mixed In Key or compatible metadata",
                "purpose": "Review source-aware BPM, key, Camelot, and cue coverage.",
                "message": "Explicit read-only tag preview and DB-only import are available; cue tag parsing remains unavailable.",
                "enabled": True,
                "locked": True,
                "action_state": "ready",
            },
            "bpm_analysis": optional_tool_capability(
                tool="aubio",
                purpose="Analyze only tracks missing trusted BPM.",
                message="Install or configure aubio to enable optional BPM analysis. Import remains available without it.",
                enabled=analysis_preferences["analyze_bpm"],
            ) | ({"available": True, "status": "available", "action_state": "ready", "message": "aubio is available for explicit preview-first, DB-only BPM analysis."} if has_aubio() else {"available": False, "status": "missing", "action_state": "setup_required"}),
            "key_analysis": optional_tool_capability(
                tool="keyfinder-cli",
                purpose="Analyze only tracks missing trusted key or Camelot values.",
                message="Install or configure keyfinder-cli to enable optional key analysis. Import remains available without it.",
                enabled=analysis_preferences["analyze_key"],
            ) | ({"action_state": "ready", "message": "keyfinder-cli is available for explicit preview-first, DB-only key/Camelot analysis."} if has_tool("keyfinder-cli") else {}),
            "beets_enrichment": optional_tool_capability(
                tool="beet",
                purpose="Run the explicit Beets import/enrichment workflow.",
                message="Install or configure beet to use the optional Beets workflow.",
            ) | ({"action_state": "ready", "message": "beet is available for preview-limited, review-first enrichment planning."} if has_tool("beet") else {}),
            "duplicate_detection": optional_tool_capability(
                tool="rmlint",
                purpose="Preview duplicate candidates without any file action.",
                message="Install or configure rmlint to run the optional preview-only duplicate detector.",
            ) | ({"action_state": "ready", "message": "rmlint is available for a constrained, preview-only duplicate scan. No file actions exist."} if has_tool("rmlint") else {}),
            "audio_quality_probe": {
                "available": quality_available,
                "status": "available" if quality_available else "missing",
                "required_tools": ["ffprobe"],
                "purpose": "Preview existing audio metadata and potential file issues.",
                "message": (
                    "ffprobe is available for a bounded, preview-only audio metadata check. No transcode is used."
                    if quality_available
                    else "ffprobe is not available. Install or configure it for optional audio probing."
                ),
                "enabled": analysis_preferences["use_external_tools"],
                "action_state": "ready" if quality_available else "setup_required",
            },
            "waveform_generation": {
                "available": waveform_status in {"detected", "ready"},
                "status": waveform_status,
                "required_tools": ["ffmpeg", "ffprobe"],
                "purpose": "Prepare optional derived waveform data in CrateIQ-owned cache storage.",
                "message": waveform["message"],
                "enabled": waveform["enabled"],
                "action_state": "coming_soon" if waveform_status in {"detected", "ready"} else "setup_required",
                "cache_ready": waveform["cache_ready"],
                "engine": waveform["engine"],
            },
        },
        "policies": {
            "preserve_mik_values": True,
            "preserve_existing_bpm_key_cues": True,
            "missing_data_only": True,
            "no_tag_writes": True,
        },
    }


def get_settings() -> dict[str, Any]:
    root = selected_library_root()
    pending_root = _pending_library_root()
    report = run_preflight()
    preferences = _load_preferences(root)
    return {
        "library": {
            "mode": "demo" if _is_demo_root(root) else "configured",
            "library_root": redact_path(root),
            "processed_db": redact_path(library_db_path(root)),
            "manual_crates_db": redact_path(crates_db_path()),
            "exports_root": redact_path(assert_path_under_root(root / "exports", root)),
            "library_initialized": library_db_path(root).is_file(),
            "pending_library_root": redact_path(pending_root) if pending_root else None,
            "pending_library_initialized": bool(pending_root and (pending_root / "logs" / "processed.db").is_file()),
            "restart_required": pending_root is not None and pending_root != root.resolve(strict=False),
            "restart_command": RESTART_COMMAND,
            "readiness_status": report["status"],
        },
        "tools": _tool_rows(report),
        "safety": {
            "mixed_in_key_authoritative": True,
            "missing_data_only_analysis": True,
            "no_automatic_file_or_tag_modification": True,
            "no_live_serato_writes": True,
            "no_live_rekordbox_database_writes": True,
            "preview_before_export_or_apply": True,
        },
        "preferences": preferences,
        "capabilities": get_capabilities(report, preferences),
    }


def update_preferences(
    default_export_path_mode: str | None,
    analysis_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = selected_library_root()
    preferences = _load_preferences(root)
    if default_export_path_mode is not None:
        if default_export_path_mode not in _VALID_PATH_MODES:
            raise ValueError("default_export_path_mode must be filename, relative, or absolute")
        preferences["default_export_path_mode"] = default_export_path_mode
    if analysis_updates:
        locked_updates = [key for key in _LOCKED_ANALYSIS_PREFERENCES if analysis_updates.get(key) is False]
        if locked_updates:
            raise ValueError(f"{locked_updates[0]} is a locked safety policy and must remain enabled")
        for key, value in analysis_updates.items():
            if key in _EDITABLE_ANALYSIS_PREFERENCES:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
                preferences["analysis"][key] = value
            elif key in _LOCKED_ANALYSIS_PREFERENCES and value is not True:
                raise ValueError(f"{key} is a locked safety policy and must remain enabled")
    path = _settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(preferences, indent=2) + "\n",
        encoding="utf-8",
    )
    return get_settings()


def update_library_root(value: str) -> dict[str, Any]:
    _write_pending_library_root(_validated_library_root(value))
    return get_settings()
