"""Shared pytest safety setup for clean local test runs."""

from __future__ import annotations

import asyncio
import functools
import os
import shutil
import tempfile
from pathlib import Path

import pytest


_TEST_MUSIC_ROOT = Path(tempfile.mkdtemp(prefix="crateiq-pytest-"))

# Pipeline and backend config resolve these roots during test collection. Always
# isolate both so tests cannot create state under /music or a real library.
os.environ["DJ_MUSIC_ROOT"] = str(_TEST_MUSIC_ROOT)
os.environ["CRATEIQ_LIBRARY_ROOT"] = str(_TEST_MUSIC_ROOT)


def pytest_sessionfinish() -> None:
    shutil.rmtree(_TEST_MUSIC_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_waveform_extractor_verification():
    """Keep W6's process-wide extractor verification out of other tests.

    The verification result is cached in a module global so the readiness GET
    never spawns anything. That global would otherwise leak between tests and
    silently change readiness assertions in unrelated modules.
    """
    from backend.app.services import waveform_readiness_service

    waveform_readiness_service.reset_extractor_verification()
    yield
    waveform_readiness_service.reset_extractor_verification()


def async_test(fn):
    """Run an ``async def`` test via ``asyncio.run``, with no pytest-asyncio
    dependency. Used only by the small set of W2 waveform-extractor tests
    that exercise real ``async``/``await`` control flow against fake process
    objects.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper
