"""
CrateIQ — FastAPI backend entry point.

Start the server:
  uvicorn backend.app.main:app --reload --port 8000

From the project root (crateIQ/):
  uvicorn backend.app.main:app --reload --port 8000 --app-dir .
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import analysis as analysis_router
from .api.routes import beets_review as beets_review_router
from .api.routes import enrichment_review as enrichment_review_router
from .api.routes import crates as crates_router
from .api.routes import duplicates as duplicates_router
from .api.routes import exports as exports_router
from .api.routes import health as health_router
from .api.routes import genres as genres_router
from .api.routes import insights as insights_router
from .api.routes import jobs as jobs_router
from .api.routes import library as library_router
from .api.routes import metadata_repair as metadata_repair_router
from .api.routes import metadata_repair_queue as metadata_repair_queue_router
from .api.routes import metadata_sanitation as metadata_sanitation_router
from .api.routes import playlists as playlists_router
from .api.routes import quality_review as quality_review_router
from .api.routes import reconciliation as reconciliation_router
from .api.routes import runtime as runtime_router
from .api.routes import reviews as reviews_router
from .api.routes import settings as settings_router
from .api.routes import sync as sync_router
from .api.routes import smart_crates as smart_crates_router
from .api.routes import tracks as tracks_router
from .api.routes import waveforms as waveforms_router
from .core.config import BACKEND_VERSION, PIPELINE_PY, TOOLKIT_ROOT
from .core.db import init_db
from .services import read_only as read_only_service
from .services import waveform_cache_service, waveform_job_service
from .services.waveform_readiness_service import (
    WaveformRuntimeError,
    resolve_cache_runtime,
    verify_extractor_runtime,
)
from .services.waveform_scheduler import get_scheduler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Hard ceiling for the optional extractor `-version` check at startup. The
# supervisor already caps each call at VERSION_CHECK_TIMEOUT_SECONDS; this is
# the outer guarantee that a wedged binary cannot delay the backend.
_EXTRACTOR_VERIFY_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    log.info("CrateIQ backend v%s starting up", BACKEND_VERSION)
    log.info(
        "CrateIQ runtime configured pipeline_entrypoint=%s pipeline_db_present=%s",
        PIPELINE_PY.is_file(),
        read_only_service.db_exists(),
    )
    log.debug("CrateIQ root: %s", TOOLKIT_ROOT)
    log.debug("pipeline.py: %s", PIPELINE_PY)
    log.debug("Library root: %s", read_only_service.get_library_root())
    log.debug("Pipeline DB: %s", read_only_service.get_db_path())

    init_db()

    # Close out waveform jobs left active by a previous process. A restart
    # must never silently resume music-library analysis, so interrupted work
    # is marked terminal and requires a renewed explicit request. This only
    # rewrites operational rows in jobs.db; no audio is read.
    try:
        waveform_job_service.recover_interrupted_jobs()
    except Exception:  # pragma: no cover - recovery must never block startup
        log.exception("waveform job recovery skipped")

    # Lightweight cache reconciliation: sweep abandoned temp files and repair
    # tracks claiming a `ready` artifact whose file is gone. This touches only
    # CrateIQ-owned cache/jobs state — no audio is decoded, hashed, scanned, or
    # regenerated.
    try:
        _config, _validated = resolve_cache_runtime()
        waveform_cache_service.startup_reconcile(
            _validated, max_cache_bytes=_config.max_cache_bytes
        )
    except WaveformRuntimeError:
        pass  # feature disabled or cache unsafe: nothing to reconcile
    except Exception:  # pragma: no cover - maintenance must never block startup
        log.exception("waveform cache reconciliation skipped")

    # Verify the optional extractor toolchain once, here, so the readiness GET
    # stays a pure read and never spawns anything. This runs `ffmpeg -version`
    # and `ffprobe -version` only: no media path is passed to either binary.
    # It is skipped entirely when the feature is disabled or the binaries were
    # never detected, and it is bounded so it can never stall startup.
    try:
        await asyncio.wait_for(verify_extractor_runtime(), timeout=_EXTRACTOR_VERIFY_TIMEOUT)
    except asyncio.TimeoutError:
        log.warning("waveform extractor verification timed out")
    except Exception:  # pragma: no cover - optional feature must never block startup
        log.exception("waveform extractor verification skipped")

    # Start idle waveform workers. The in-memory queue starts empty and no
    # persisted job is re-enqueued, so nothing is analyzed automatically.
    scheduler = get_scheduler()
    try:
        await scheduler.start()
    except Exception:  # pragma: no cover - optional feature must never block startup
        log.exception("waveform scheduler did not start")

    yield

    # --- shutdown ---
    try:
        await scheduler.stop()
    except Exception:  # pragma: no cover - shutdown must never raise
        log.exception("waveform scheduler shutdown error")
    log.info("CrateIQ backend shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CrateIQ API",
    description=(
        "Local-first REST API for the CrateIQ library intelligence pipeline. "
        "Submit pipeline jobs, track their progress, and stream their logs."
    ),
    version=BACKEND_VERSION,
    lifespan=lifespan,
)

# Allow the local frontend dev server (Phase 2) to call the API.
# In production restrict origins explicitly.
cors_origins = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5175"
).split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_request_timing(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    log.info(
        "request method=%s path=%s status=%s duration_ms=%.1f",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

API_PREFIX = "/api"

app.include_router(health_router.router,     prefix=API_PREFIX)
app.include_router(genres_router.router,     prefix=API_PREFIX)
app.include_router(runtime_router.router,    prefix=API_PREFIX)
app.include_router(reviews_router.router,    prefix=API_PREFIX)
app.include_router(settings_router.router,   prefix=API_PREFIX)
app.include_router(jobs_router.router,       prefix=API_PREFIX)
app.include_router(library_router.router,    prefix=API_PREFIX)
app.include_router(tracks_router.router,     prefix=API_PREFIX)
app.include_router(waveforms_router.router,  prefix=API_PREFIX)
app.include_router(insights_router.router,    prefix=API_PREFIX)
app.include_router(analysis_router.router,   prefix=API_PREFIX)
app.include_router(beets_review_router.router, prefix=API_PREFIX)
app.include_router(enrichment_review_router.router, prefix=API_PREFIX)
app.include_router(crates_router.router,     prefix=API_PREFIX)
app.include_router(duplicates_router.router, prefix=API_PREFIX)
app.include_router(smart_crates_router.router, prefix=API_PREFIX)
app.include_router(playlists_router.router,  prefix=API_PREFIX)
app.include_router(quality_review_router.router, prefix=API_PREFIX)
app.include_router(metadata_repair_router.router, prefix=API_PREFIX)
app.include_router(metadata_repair_queue_router.router, prefix=API_PREFIX)
app.include_router(metadata_sanitation_router.router, prefix=API_PREFIX)
app.include_router(reconciliation_router.router, prefix=API_PREFIX)
app.include_router(exports_router.router,    prefix=API_PREFIX)
app.include_router(sync_router.router,       prefix=API_PREFIX)
