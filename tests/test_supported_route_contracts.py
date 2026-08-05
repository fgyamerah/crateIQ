"""
Supported-route smoke-test contract.

Each supported frontend route (frontend/src/App.tsx) is mapped to the primary
read-only backend endpoints it depends on. These tests catch drift between the
supported UI surface and the backend API without running pipeline jobs,
spawning subprocesses, or touching a real music library.

Scope rules:
- GET endpoints only. Mutating/job-submission endpoints (job start/cancel,
  sync preview/run, export validate/run, review actions, apply paths,
  reanalyze, set-builder dispatch, reconciliation plan validation) are
  deliberately NOT exercised; they are listed in DEFERRED_ENDPOINTS below and
  tracked in NEXT_TASKS.txt.
- Temporary fixture roots only (conftest already isolates the library root).
- Shape assertions are minimal (status code, container type, a few stable
  keys) so the contract catches breakage without being brittle.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.core.db as backend_core_db
import backend.app.main as backend_main

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_TSX = REPO_ROOT / "frontend" / "src" / "App.tsx"


# ---------------------------------------------------------------------------
# Route → API contract table
#
# endpoint spec: (path, expect, keys)
#   expect: "dict" or "list"
#   keys:   stable keys that must be present when expect == "dict"
# ---------------------------------------------------------------------------

ROUTE_CONTRACTS: list[dict] = [
    {
        "route": "/",
        "purpose": "Library workspace and track browsing",
        "access": "read-only",
        "endpoints": [
            ("/api/tracks", "dict", ("items", "limit", "offset", "total")),
            ("/api/tracks/stats", "dict", ("total", "by_status", "missing_bpm")),
            ("/api/stats", "dict", ("tracks_count", "canonical_source")),
            ("/api/library/overview", "dict",
             ("total_tracks", "parse_confidence_breakdown", "genre_top_counts")),
        ],
    },
    {
        "route": "/issues",
        "purpose": "Track issue review",
        "access": "read-only",
        "endpoints": [
            ("/api/tracks/issues", "dict", ("missing_artist", "missing_title")),
        ],
    },
    {
        "route": "/enrichment",
        "purpose": "Enrichment queue review",
        "access": "read-only list; review actions deferred",
        "endpoints": [
            ("/api/enrichment/queue", "dict", ("items", "counts", "total")),
            ("/api/enrichment/review/summary", "dict",
             ("pending_count", "approved_count", "rejected_count")),
        ],
    },
    {
        "route": "/beets-review",
        "purpose": "Selected-field local metadata enrichment review",
        "access": "read-only saved candidates; review/apply actions deferred",
        "endpoints": [
            ("/api/enrichment/beets/review", "dict", ("summary", "items", "safety")),
        ],
    },
    {
        "route": "/enrichment-review",
        "purpose": "Multi-source local enrichment comparison review",
        "access": "read-only saved suggestions; explicit DB-only selected-field apply",
        "endpoints": [
            ("/api/enrichment/review", "dict", ("summary", "items", "safety", "sources")),
        ],
    },
    {
        "route": "/genres",
        "purpose": "DB-only genre taxonomy and normalization review",
        "access": "review-first local-index-only normalization",
        "endpoints": [
            ("/api/genres/taxonomy", "dict", ("genres",)),
            ("/api/genres/review", "dict", ("summary", "items", "safety")),
        ],
    },
    {
        "route": "/music-review",
        "purpose": "DB-only DJ music review queue",
        "access": "review status, rating, notes, and play history only",
        "endpoints": [
            ("/api/reviews/tracks", "dict", ("items", "summary", "safety")),
        ],
    },
    {
        "route": "/audit",
        "purpose": "Latest path/library audit report",
        "access": "read-only",
        "endpoints": [
            # Returns the report dict, or {"available": false} with no audit.
            ("/api/audit/latest", "dict", ()),
        ],
    },
    {
        "route": "/folders",
        "purpose": "Folder-level library view",
        "access": "read-only",
        "endpoints": [
            ("/api/library/folders", "list", ()),
        ],
    },
    {
        "route": "/quality",
        "purpose": "Library quality summary",
        "access": "read-only",
        "endpoints": [
            ("/api/library/quality", "dict",
             ("total_tracks", "issues_by_type", "coverage")),
        ],
    },
    {
        "route": "/quality-review",
        "purpose": "Database-only ffprobe finding review",
        "access": "read-only saved preview; refresh and decisions deferred",
        "endpoints": [
            ("/api/quality/review", "dict", ("summary", "items", "safety")),
        ],
    },
    {
        "route": "/metadata-repair",
        "purpose": "Deterministic metadata repair review",
        "access": "read-only list; review/apply actions deferred",
        "endpoints": [
            ("/api/metadata-repair/queue", "dict", ("items", "counts", "total")),
            ("/api/metadata-repair/summary", "dict",
             ("queue_total", "pending_count", "approved_count")),
        ],
    },
    {
        "route": "/metadata-sanitation",
        "purpose": "Metadata sanitation review",
        "access": "read-only list; review/apply actions deferred",
        "endpoints": [
            ("/api/metadata-sanitation/queue", "dict", ("items", "counts", "total")),
            ("/api/metadata-sanitation/summary", "dict",
             ("queue_total", "pending_count", "approved_count")),
        ],
    },
    {
        "route": "/bpm-review",
        "purpose": "BPM anomaly review",
        "access": "read-only list; scan/reanalyze actions deferred",
        "endpoints": [
            ("/api/analysis/bpm-anomalies/summary", "dict", ("by_status", "by_reason")),
            ("/api/analysis/bpm-anomalies", "list", ()),
        ],
    },
    {
        "route": "/jobs",
        "purpose": "Optional Analysis Jobs catalog and pipeline job history",
        "access": "read-only candidate previews and job list; runners deferred",
        "endpoints": [
            ("/api/jobs", "list", ()),
            ("/api/analysis/jobs", "dict", ("jobs",)),
            ("/api/analysis/jobs/history", "dict", ("history", "message")),
        ],
    },
    {
        "route": "/duplicates",
        "purpose": "Database-only duplicate candidate review",
        "access": "read-only saved preview; refresh and decisions deferred",
        "endpoints": [
            ("/api/duplicates/review", "dict", ("summary", "groups", "safety")),
        ],
    },
    {
        "route": "/crates",
        "purpose": "Manual local crate review",
        "access": "read-only list; crate editing deferred",
        "endpoints": [
            ("/api/crates", "list", ()),
        ],
    },
    {
        "route": "/smart-crates",
        "purpose": "Smart crate suggestions",
        "access": "read-only presets; preview/save deferred",
        "endpoints": [
            ("/api/smart-crates/presets", "list", ()),
        ],
    },
    {
        "route": "/set-builder",
        "purpose": "Saved set review",
        "access": "read-only list; set generation deferred",
        "endpoints": [
            ("/api/playlists", "list", ()),
        ],
    },
    {
        "route": "/exports",
        "purpose": "Export job history",
        "access": "read-only list; validate/run deferred",
        "endpoints": [
            ("/api/exports", "list", ()),
        ],
    },
    {
        "route": "/sync",
        "purpose": "SSD sync configuration and history",
        "access": "read-only; preview/run deferred",
        "endpoints": [
            ("/api/sync/config", "dict", ("sources", "dest", "rsync_bin", "ssd_mounted")),
            ("/api/sync", "list", ()),
        ],
    },
    {
        "route": "/reconciliation",
        "purpose": "Reconciliation ledger",
        "access": "read-only; plan validation deferred",
        "endpoints": [
            ("/api/reconciliation/ledger", "list", ()),
        ],
    },
    {
        "route": "/settings",
        "purpose": "Local settings diagnostics, analysis capabilities, and safe preferences",
        "access": "read-only diagnostics; explicit local preference update",
        "endpoints": [
            ("/api/settings", "dict", ("library", "tools", "safety", "preferences", "capabilities")),
            ("/api/settings/capabilities", "dict", ("core", "analysis", "policies")),
            ("/api/settings/runtime", "dict", ("status", "checks")),
            ("/api/analysis/mik/coverage", "dict", ("summary", "cue_support", "write_behavior")),
        ],
    },
]

# Infrastructure endpoints every supported page relies on indirectly.
INFRA_ENDPOINTS: list[tuple[str, str, tuple[str, ...]]] = [
    ("/api/health", "dict", ("ok", "library_root", "db_path", "db_exists")),
    ("/api/version", "dict", ("backend_version", "toolkit_version", "pipeline_py")),
    ("/api/runtime/readiness", "dict", ("status", "checks")),
]

# Mutating / job-submitting endpoints per route that the smoke suite must
# never call. Kept here so the boundary is explicit and testable.
DEFERRED_ENDPOINTS: dict[str, list[str]] = {
    "/smart-crates": ["/api/smart-crates/preview (POST)", "/api/smart-crates/save (POST)"],
    "/crates": [
        "/api/crates (POST)", "/api/crates/{id} (PATCH, DELETE)",
        "/api/crates/{id}/tracks (POST, DELETE, PATCH reorder)",
    ],
    "/jobs": [
        "/api/jobs (POST)", "/api/jobs/{id}/cancel (POST)",
        "/api/analysis/jobs/{job_type}/run (POST, runner pending)",
    ],
    "/duplicates": [
        "/api/duplicates/review/preview-refresh (POST, bounded preview snapshot)",
        "/api/duplicates/review/groups/{group_id}/items/{track_id} (PATCH, DB-only review decision)",
    ],
    "/quality-review": [
        "/api/quality/review/preview-refresh (POST, bounded ffprobe snapshot)",
        "/api/quality/review/tracks/{track_id} (PATCH, DB-only review decision)",
    ],
    "/set-builder": ["/api/playlists/set-builder (POST)"],
    "/exports": ["/api/exports/validate (POST)", "/api/exports/run (POST)"],
    "/sync": ["/api/sync/preview (POST, spawns rsync)", "/api/sync/run (POST)"],
    "/bpm-review": [
        "/api/analysis/bpm-check (POST, writes anomaly rows)",
        "/api/analysis/reanalyze (POST, dispatches job)",
    ],
    "/enrichment": [
        "/api/enrichment/review/{id}/* (POST)",
        "/api/enrichment/apply-approved/* (POST)",
    ],
    "/beets-review": [
        "/api/enrichment/beets/preview-refresh (POST, local candidate snapshot)",
        "/api/enrichment/beets/tracks/{track_id} (PATCH, DB-only selection)",
        "/api/enrichment/beets/apply (POST, explicit selected-field DB-only apply)",
    ],
    "/enrichment-review": [
        "/api/enrichment/review/preview-refresh (POST, local suggestions only)",
        "/api/enrichment/review/tracks/{track_id}/suggestions/{suggestion_id} (PATCH, DB-only selection)",
        "/api/enrichment/review/apply (POST, explicit selected-field DB-only apply)",
    ],
    "/genres": [
        "/api/genres/review/preview-refresh (POST, local genre values only)",
        "/api/genres/review/apply (POST, explicit DB-only normalized genre apply)",
    ],
    "/music-review": [
        "/api/reviews/tracks/{track_id} (PATCH, DB-only review state)",
        "/api/reviews/tracks/{track_id}/played (POST, DB-only play count)",
    ],
    "/metadata-repair": [
        "/api/metadata-repair/{id}/* (POST)",
        "/api/metadata-repair/apply-approved/* (POST)",
    ],
    "/metadata-sanitation": [
        "/api/metadata-sanitation/{id}/* (POST)",
        "/api/metadata-sanitation/apply-approved/* (POST)",
    ],
    "/reconciliation": ["/api/reconciliation/validate-plan (POST)"],
    "/settings": [
        "/api/settings (PATCH, local preference update)",
        "/api/analysis/mik/preview (POST, explicit read-only tag scan)",
        "/api/analysis/mik/import (POST, local-index-only import)",
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TRACKS_SCHEMA = """
CREATE TABLE tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    artist TEXT,
    title TEXT,
    genre TEXT,
    bpm REAL,
    key_musical TEXT,
    key_camelot TEXT,
    duration_sec REAL,
    bitrate_kbps INTEGER,
    filesize_bytes INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    error_msg TEXT,
    processed_at TEXT,
    pipeline_ver TEXT,
    quality_tier TEXT,
    parse_confidence TEXT
)
"""


def _create_fixture_db(root: Path) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_TRACKS_SCHEMA)
    conn.execute(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical,
            key_camelot, duration_sec, bitrate_kbps, filesize_bytes, status,
            processed_at, pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(root / "library" / "house" / "alpha.mp3"),
            "alpha.mp3", "Alpha", "First", "House", 120.0, "Am", "8A",
            300.0, 320, 1234, "ok", "2026-05-05T10:00:00Z", "1.4.0",
            "HIGH", "HIGH",
        ),
    )
    conn.commit()
    conn.close()
    return db_path


def _make_client(tmp_path: Path, monkeypatch, with_db: bool):
    root = tmp_path / "library_root"
    root.mkdir(parents=True, exist_ok=True)
    if with_db:
        _create_fixture_db(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    # Redirect the backend jobs DB into the temp dir so the real init_db can
    # run safely and jobs-backed list endpoints have their (empty) tables.
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    return TestClient(backend_main.app), root


@pytest.fixture()
def client(tmp_path, monkeypatch):
    test_client, root = _make_client(tmp_path, monkeypatch, with_db=True)
    with test_client:
        yield test_client, root


@pytest.fixture()
def client_no_db(tmp_path, monkeypatch):
    test_client, root = _make_client(tmp_path, monkeypatch, with_db=False)
    with test_client:
        yield test_client, root


def _all_smoke_endpoints():
    for contract in ROUTE_CONTRACTS:
        for endpoint in contract["endpoints"]:
            yield contract["route"], endpoint
    for endpoint in INFRA_ENDPOINTS:
        yield "(infra)", endpoint


def _assert_endpoint(test_client, route: str, endpoint) -> None:
    path, expect, keys = endpoint
    resp = test_client.get(path)
    assert resp.status_code == 200, (
        f"{route}: GET {path} returned {resp.status_code}"
    )
    payload = resp.json()
    if expect == "list":
        assert isinstance(payload, list), f"{route}: GET {path} expected a list"
    else:
        assert isinstance(payload, dict), f"{route}: GET {path} expected an object"
        missing = [k for k in keys if k not in payload]
        assert not missing, f"{route}: GET {path} missing keys {missing}"


# ---------------------------------------------------------------------------
# Contract-table integrity
# ---------------------------------------------------------------------------

def test_contract_table_matches_frontend_router():
    """The contract table must cover exactly the routes mounted in App.tsx."""
    source = APP_TSX.read_text(encoding="utf-8")
    supported = set()
    for line in source.splitlines():
        if "<Route" not in line or "Navigate" in line or "Redirect" in line:
            continue
        if "index" in line and 'path="' not in line:
            supported.add("/")
            continue
        match = re.search(r'path="([^"]+)"', line)
        if match:
            supported.add("/" + match.group(1))
    contract_routes = {c["route"] for c in ROUTE_CONTRACTS}
    assert contract_routes == supported, (
        "Route drift between App.tsx and the smoke contract. "
        f"Only in App.tsx: {sorted(supported - contract_routes)}; "
        f"only in contract: {sorted(contract_routes - supported)}"
    )


def test_smoke_surface_is_get_only_and_excludes_deferred():
    """Self-consistency: the smoke surface is GET-only and deferred entries
    are mutating methods, so the two surfaces remain disjoint even when a
    path supports both safe GET and write methods."""
    for entries in DEFERRED_ENDPOINTS.values():
        for entry in entries:
            assert any(method in entry for method in ("(POST", "(PATCH", "(DELETE")), (
                f"deferred entry {entry!r} must be a mutating method; "
                "safe GETs belong in the smoke contract instead"
            )
    for route, (path, _expect, _keys) in _all_smoke_endpoints():
        assert path.startswith("/api/"), (route, path)
        assert " " not in path and "{" not in path, (
            f"{route}: {path} must be a concrete GET path"
        )


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

def test_supported_route_contracts_with_fixture_library(client):
    test_client, _root = client
    for route, endpoint in _all_smoke_endpoints():
        _assert_endpoint(test_client, route, endpoint)


def test_supported_route_contracts_without_pipeline_db(client_no_db):
    """A fresh root without processed.db must degrade safely, not crash."""
    test_client, _root = client_no_db
    for route, endpoint in _all_smoke_endpoints():
        _assert_endpoint(test_client, route, endpoint)
    # Known safe-empty forms
    assert test_client.get("/api/health").json()["db_exists"] is False
    assert test_client.get("/api/audit/latest").json() == {"available": False}
    assert test_client.get("/api/jobs").json() == []
    assert test_client.get("/api/exports").json() == []
    assert test_client.get("/api/playlists").json() == []
    tracks = test_client.get("/api/tracks").json()
    assert tracks["items"] == [] and tracks["total"] == 0


def test_smoke_surface_is_read_only_and_spawns_no_subprocess(
    client, monkeypatch
):
    """The whole smoke surface must not mutate the pipeline DB or spawn any
    subprocess (no pipeline jobs, no rsync, no exports)."""
    import subprocess

    def _forbidden(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("smoke surface must not spawn subprocesses")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)

    test_client, root = client
    db_path = root / "logs" / "processed.db"
    before = db_path.read_bytes()
    for route, endpoint in _all_smoke_endpoints():
        _assert_endpoint(test_client, route, endpoint)
    assert db_path.read_bytes() == before
