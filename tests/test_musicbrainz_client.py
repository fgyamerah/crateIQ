"""
Targeted tests for backend.app.services.musicbrainz_client.

All network access is mocked -- these tests must never make a real HTTP
call. Live acceptance is verified manually/interactively, not in the
automated suite (see PROJECT_CONTEXT.md Cycle 6 entry).

Also guards the specific safety incident this module was built to avoid:
beets.config must be materialized with user=False so a real
~/.config/beets/config.yaml (or library.db, if a real beets Library were
ever opened) is never touched by CrateIQ.
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
import requests

from backend.app.api.routes import health as health_route
from backend.app.services import musicbrainz_client as mbc
from backend.app.services import preparation_service
from tests.conftest import async_test


@pytest.fixture(autouse=True)
def _reset_config_flag():
    mbc._configured = False
    yield
    mbc._configured = False


def test_ensure_isolated_config_reads_without_user_file(monkeypatch):
    calls = []

    class FakeConfig:
        def clear(self):
            calls.append("clear")

        def read(self, user=True, defaults=True):
            calls.append(("read", user, defaults))

    import beets
    monkeypatch.setattr(beets, "config", FakeConfig())

    mbc._ensure_isolated_config()

    assert calls == ["clear", ("read", False, True)]
    assert mbc._configured is True


def test_ensure_isolated_config_is_idempotent(monkeypatch):
    calls = []

    class FakeConfig:
        def clear(self):
            calls.append("clear")

        def read(self, user=True, defaults=True):
            calls.append("read")

    import beets
    monkeypatch.setattr(beets, "config", FakeConfig())

    mbc._ensure_isolated_config()
    mbc._ensure_isolated_config()
    mbc._ensure_isolated_config()

    assert calls == ["clear", "read"], "must only materialize config once per process"


def test_search_recordings_returns_empty_for_blank_title():
    assert mbc.search_recordings("Artist", "") == []
    assert mbc.search_recordings("", "") == []


def test_search_recordings_parses_real_shaped_response():
    fake_plugin = MagicMock()
    fake_plugin.mb_api.search.return_value = [
        {
            "id": "abc-123",
            "title": "One More Time",
            "score": 100,
            "artist_credit": [{"artist": {"name": "Daft Punk"}}],
            "releases": [{"title": "Discovery", "date": "2001-03-07"}],
        },
    ]
    with patch.object(mbc, "_plugin", return_value=fake_plugin):
        results = mbc.search_recordings("Daft Punk", "One More Time")
    assert results == [{
        "mb_recording_id": "abc-123",
        "artist": "Daft Punk",
        "title": "One More Time",
        "album": "Discovery",
        "date": "2001-03-07",
        "score": 100,
    }]


def test_search_recordings_never_raises_on_network_failure():
    with patch.object(mbc, "_plugin", side_effect=ConnectionError("boom")):
        result = mbc.search_recordings("Artist", "Title")
    assert isinstance(result, mbc.MusicBrainzError)
    assert "MusicBrainz lookup failed" in result.message


def test_match_track_candidates_never_raises_on_failure():
    with patch.object(mbc, "_plugin", side_effect=TimeoutError("slow")):
        result = mbc.match_track_candidates("Artist", "Title")
    assert isinstance(result, mbc.MusicBrainzError)


def test_match_track_candidates_uses_beets_recommendation_thresholds():
    info_strong = MagicMock(track_id="t1", artist="A", title="T", album=None)
    info_weak = MagicMock(track_id="t2", artist="A2", title="T2", album=None)
    fake_plugin = MagicMock()
    fake_plugin.item_candidates.return_value = [info_strong, info_weak]

    fake_distances = {"t1": 0.01, "t2": 0.3}

    def fake_track_distance(item, info, incl_artist=True):
        dist = MagicMock()
        dist.distance = fake_distances[info.track_id]
        return dist

    with patch.object(mbc, "_plugin", return_value=fake_plugin), \
         patch("beets.autotag.track_distance", side_effect=fake_track_distance):
        results = mbc.match_track_candidates("Artist", "Title")

    assert results[0]["mb_recording_id"] == "t1"
    assert results[0]["confidence"] == "HIGH"
    assert results[1]["mb_recording_id"] == "t2"
    assert results[1]["confidence"] == "LOW"


def _stub_process_all_after_enrichment(monkeypatch) -> None:
    """Keep responsiveness regressions focused on the provider stage."""
    from backend.app.services import analysis_jobs_service, metadata_repair_queue_service

    monkeypatch.setattr(preparation_service, "clean_tracks", lambda *_args: {"cleaned_count": 0})
    monkeypatch.setattr(
        preparation_service, "write_tracks",
        lambda *_args: {"written_count": 0, "failed_count": 0, "warnings": []},
    )
    monkeypatch.setattr(
        preparation_service.preparation_operations_service,
        "is_cancel_requested", lambda *_args: False,
    )
    monkeypatch.setattr(preparation_service, "_finish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analysis_jobs_service, "run", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(metadata_repair_queue_service, "refresh", lambda: None)
    monkeypatch.setattr(health_route.read_only_service, "db_exists", lambda: True)


async def _exercise_gated_process_all_lookup(monkeypatch, provider_error=None):
    """Run a real synchronous mbc call behind Process All's async boundary."""
    provider_started = threading.Event()
    release_provider = threading.Event()
    session_closed = threading.Event()
    observed = []

    class FakeSession:
        def close(self):
            session_closed.set()

    class FakeApi:
        session = FakeSession()

        def search(self, *_args, **_kwargs):
            provider_started.set()
            if not release_provider.wait(2):
                raise TimeoutError("test provider gate was not released")
            if provider_error is not None:
                raise provider_error
            return []

    class FakePlugin:
        mb_api = FakeApi()

    monkeypatch.setattr(mbc, "_plugin", lambda: FakePlugin())

    def gated_enrich_tracks(*_args):
        result = mbc.search_recordings("Test Artist", "Test Title")
        observed.append(result)
        warnings = [result.message] if isinstance(result, mbc.MusicBrainzError) else []
        return {"enriched_count": 0, "warnings": warnings}

    monkeypatch.setattr(preparation_service, "enrich_tracks", gated_enrich_tracks)
    _stub_process_all_after_enrichment(monkeypatch)

    # This timer is only a deadlock guard. With the fixed implementation the
    # test releases the provider itself after proving the loop stayed live.
    watchdog = threading.Timer(1.0, release_provider.set)
    watchdog.start()
    task = asyncio.create_task(preparation_service.run_process_all("op", None, [1]))
    try:
        assert await asyncio.to_thread(provider_started.wait, 0.5)
        assert not release_provider.is_set(), "the event loop must regain control before the deadlock watchdog fires"
        assert not task.done(), "Process All must remain pending while MusicBrainz is gated"

        health = await asyncio.wait_for(health_route.health(), timeout=0.2)
        assert health.ok is True
        assert not task.done(), "health must complete without releasing the provider operation"

        release_provider.set()
        await asyncio.wait_for(task, timeout=1.0)
    finally:
        release_provider.set()
        watchdog.cancel()
        if not task.done():
            await asyncio.wait_for(task, timeout=1.0)

    assert session_closed.is_set(), "MusicBrainz session must close after every lookup path"
    return observed[0]


@async_test
async def test_process_all_musicbrainz_wait_keeps_event_loop_and_health_responsive(monkeypatch):
    result = await _exercise_gated_process_all_lookup(monkeypatch)
    assert result == []


@async_test
async def test_process_all_musicbrainz_ssl_failure_keeps_loop_responsive_and_closes_session(monkeypatch):
    result = await _exercise_gated_process_all_lookup(
        monkeypatch, requests.exceptions.SSLError("controlled TLS failure"),
    )
    assert isinstance(result, mbc.MusicBrainzError)
    assert result.message == "MusicBrainz lookup failed (SSLError)."
