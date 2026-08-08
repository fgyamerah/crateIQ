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

from unittest.mock import MagicMock, patch

import pytest

from backend.app.services import musicbrainz_client as mbc


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
