"""
Tests for Inbox sorting: the extended whitelist in
backend.app.services.track_service (_build_order_by / VALID_SORT_KEYS) and
the GET /api/workspace/inbox/tracks route that validates sort/order and
passes them through.

Text/BPM/Key sorts group blank or NULL values last regardless of direction;
Readiness sorts by an explicit, deterministic field-completeness proxy
(READY < WARNING < BLOCKED ascending); ties break deterministically on id.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.core.db as backend_core_db
import backend.app.main as backend_main
from backend.app.services import track_service, workspace_service


def _write(path: Path, content: bytes = b"fake-audio-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture()
def managed_root(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    workspace_service.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    return root


@pytest.fixture()
def client(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    workspace_service.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    test_client = TestClient(backend_main.app)
    with test_client:
        yield test_client, root


def _insert(
    root: Path, filename: str, *, artist=None, title=None, genre=None,
    bpm=None, key_camelot=None, zone: str = "INBOX",
) -> int:
    path = root / "Inbox" / filename
    _write(path)
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, bpm, key_camelot,
                                    status, processed_at, pipeline_ver, storage_zone)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '2026-01-01T00:00:00Z', 'test', ?)""",
            (str(path), filename, artist, title, genre, bpm, key_camelot, zone),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _names(tracks):
    return [t.filename for t in tracks]


# ---------------------------------------------------------------------------
# Text columns
# ---------------------------------------------------------------------------

def test_artist_ascending_and_descending(managed_root):
    _insert(managed_root, "c.mp3", artist="Charlie")
    _insert(managed_root, "a.mp3", artist="alpha")
    _insert(managed_root, "b.mp3", artist="Bravo")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="artist", order="asc")
    assert _names(asc) == ["a.mp3", "b.mp3", "c.mp3"]

    desc, _ = track_service.list_tracks(storage_zone="INBOX", sort="artist", order="desc")
    assert _names(desc) == ["c.mp3", "b.mp3", "a.mp3"]


def test_title_ascending_and_descending(managed_root):
    _insert(managed_root, "c.mp3", title="Charlie")
    _insert(managed_root, "a.mp3", title="alpha")
    _insert(managed_root, "b.mp3", title="Bravo")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="title", order="asc")
    assert _names(asc) == ["a.mp3", "b.mp3", "c.mp3"]
    desc, _ = track_service.list_tracks(storage_zone="INBOX", sort="title", order="desc")
    assert _names(desc) == ["c.mp3", "b.mp3", "a.mp3"]


def test_genre_ascending_and_descending(managed_root):
    _insert(managed_root, "c.mp3", genre="House")
    _insert(managed_root, "a.mp3", genre="Afro House")
    _insert(managed_root, "b.mp3", genre="Dance")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="genre", order="asc")
    assert _names(asc) == ["a.mp3", "b.mp3", "c.mp3"]
    desc, _ = track_service.list_tracks(storage_zone="INBOX", sort="genre", order="desc")
    assert _names(desc) == ["c.mp3", "b.mp3", "a.mp3"]


def test_track_filename_ascending_and_descending(managed_root):
    _insert(managed_root, "Zebra.mp3")
    _insert(managed_root, "apple.mp3")
    _insert(managed_root, "Banana.mp3")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="filename", order="asc")
    assert _names(asc) == ["apple.mp3", "Banana.mp3", "Zebra.mp3"]
    desc, _ = track_service.list_tracks(storage_zone="INBOX", sort="filename", order="desc")
    assert _names(desc) == ["Zebra.mp3", "Banana.mp3", "apple.mp3"]


def test_key_ascending_and_descending_prefers_camelot(managed_root):
    _insert(managed_root, "c.mp3", key_camelot="8B")
    _insert(managed_root, "a.mp3", key_camelot="1A")
    _insert(managed_root, "b.mp3", key_camelot="5A")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="key", order="asc")
    assert _names(asc) == ["a.mp3", "b.mp3", "c.mp3"]


# ---------------------------------------------------------------------------
# Null/empty ordering: always LAST, in both directions
# ---------------------------------------------------------------------------

def test_blank_artist_grouped_last_ascending(managed_root):
    _insert(managed_root, "no-artist.mp3", artist=None)
    _insert(managed_root, "b.mp3", artist="Bravo")
    _insert(managed_root, "a.mp3", artist="Alpha")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="artist", order="asc")
    assert _names(asc) == ["a.mp3", "b.mp3", "no-artist.mp3"]


def test_blank_artist_grouped_last_descending_too(managed_root):
    _insert(managed_root, "no-artist.mp3", artist=None)
    _insert(managed_root, "b.mp3", artist="Bravo")
    _insert(managed_root, "a.mp3", artist="Alpha")

    desc, _ = track_service.list_tracks(storage_zone="INBOX", sort="artist", order="desc")
    assert _names(desc) == ["b.mp3", "a.mp3", "no-artist.mp3"], (
        "blank values must not flip to the front just because direction reversed"
    )


def test_bpm_sorts_numerically_not_lexicographically(managed_root):
    _insert(managed_root, "128.mp3", bpm=128)
    _insert(managed_root, "118.mp3", bpm=118)
    _insert(managed_root, "1225.mp3", bpm=122.5)
    _insert(managed_root, "no-bpm.mp3", bpm=None)
    _insert(managed_root, "120.mp3", bpm=120)

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="bpm", order="asc")
    assert _names(asc) == ["118.mp3", "120.mp3", "1225.mp3", "128.mp3", "no-bpm.mp3"], (
        "118 < 120 < 122.5 < 128 numerically; a lexicographic sort would wrongly "
        "place '1225' (122.5) before '118'/'120'"
    )


def test_bpm_null_last_in_descending_too(managed_root):
    _insert(managed_root, "128.mp3", bpm=128)
    _insert(managed_root, "no-bpm.mp3", bpm=None)
    _insert(managed_root, "118.mp3", bpm=118)

    desc, _ = track_service.list_tracks(storage_zone="INBOX", sort="bpm", order="desc")
    assert _names(desc) == ["128.mp3", "118.mp3", "no-bpm.mp3"]


def test_missing_key_grouped_last(managed_root):
    _insert(managed_root, "no-key.mp3", key_camelot=None)
    _insert(managed_root, "b.mp3", key_camelot="5A")
    _insert(managed_root, "a.mp3", key_camelot="1A")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="key", order="asc")
    assert _names(asc) == ["a.mp3", "b.mp3", "no-key.mp3"]


# ---------------------------------------------------------------------------
# Readiness: explicit deterministic ranking, not alphabetical
# ---------------------------------------------------------------------------

def test_readiness_orders_ready_before_warning_before_blocked(managed_root):
    _insert(managed_root, "blocked.mp3", artist=None, title="T", genre="G", bpm=120, key_camelot="1A")
    _insert(managed_root, "warning.mp3", artist="A", title="T", genre="G", bpm=None, key_camelot="1A")
    _insert(managed_root, "ready.mp3", artist="A", title="T", genre="G", bpm=120, key_camelot="1A")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="readiness", order="asc")
    assert _names(asc) == ["ready.mp3", "warning.mp3", "blocked.mp3"]

    desc, _ = track_service.list_tracks(storage_zone="INBOX", sort="readiness", order="desc")
    assert _names(desc) == ["blocked.mp3", "warning.mp3", "ready.mp3"]


def test_readiness_missing_any_required_field_is_blocked(managed_root):
    _insert(managed_root, "no-genre.mp3", artist="A", title="T", genre=None, bpm=120, key_camelot="1A")
    _insert(managed_root, "ready.mp3", artist="A", title="T", genre="G", bpm=120, key_camelot="1A")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="readiness", order="asc")
    assert _names(asc) == ["ready.mp3", "no-genre.mp3"]


# ---------------------------------------------------------------------------
# Ties, default sort, search combination, invalid keys
# ---------------------------------------------------------------------------

def test_ties_break_deterministically_on_id(managed_root):
    first = _insert(managed_root, "a1.mp3", artist="Same")
    second = _insert(managed_root, "a2.mp3", artist="Same")

    asc, _ = track_service.list_tracks(storage_zone="INBOX", sort="artist", order="asc")
    assert [t.id for t in asc] == sorted([first, second])


def test_default_sort_is_artist_then_title(managed_root):
    _insert(managed_root, "b.mp3", artist="Coldplay", title="Zebra")
    _insert(managed_root, "a.mp3", artist="Coldplay", title="Alpha")

    default, _ = track_service.list_tracks(storage_zone="INBOX")
    assert _names(default) == ["a.mp3", "b.mp3"], "equal Artist must break the tie on Title, ascending"


def test_sort_combines_with_search(managed_root):
    _insert(managed_root, "match-b.mp3", artist="Zeta", title="find me")
    _insert(managed_root, "match-a.mp3", artist="Alpha", title="find me too")
    _insert(managed_root, "no-match.mp3", artist="Beta", title="irrelevant")

    results, total = track_service.list_tracks(storage_zone="INBOX", q="find", sort="artist", order="asc")
    assert total == 2
    assert _names(results) == ["match-a.mp3", "match-b.mp3"]


def test_unknown_sort_key_falls_back_to_default_at_service_layer(managed_root):
    """Defense in depth: the service never raises on an unknown key, unlike the route."""
    _insert(managed_root, "a.mp3", artist="Alpha")
    results, _ = track_service.list_tracks(storage_zone="INBOX", sort="'; DROP TABLE tracks; --", order="asc")
    assert _names(results) == ["a.mp3"]


def test_sorting_does_not_change_which_tracks_are_returned(managed_root):
    ids = {
        _insert(managed_root, "a.mp3", artist="A", bpm=100),
        _insert(managed_root, "b.mp3", artist="B", bpm=200),
        _insert(managed_root, "c.mp3", artist="C", bpm=None),
    }
    by_artist, _ = track_service.list_tracks(storage_zone="INBOX", sort="artist", order="asc")
    by_bpm, _ = track_service.list_tracks(storage_zone="INBOX", sort="bpm", order="desc")
    assert {t.id for t in by_artist} == ids == {t.id for t in by_bpm}


# ---------------------------------------------------------------------------
# HTTP route validation
# ---------------------------------------------------------------------------

def test_route_rejects_invalid_sort_key(client):
    test_client, _root = client
    resp = test_client.get("/api/workspace/inbox/tracks", params={"sort": "not_a_real_column"})
    assert resp.status_code == 422


def test_route_rejects_invalid_order(client):
    test_client, _root = client
    resp = test_client.get("/api/workspace/inbox/tracks", params={"order": "sideways"})
    assert resp.status_code == 422


def test_route_accepts_every_documented_sort_key(client):
    test_client, root = client
    _insert(root, "a.mp3", artist="A", title="T", genre="G", bpm=120, key_camelot="1A")
    for key in sorted(track_service.VALID_SORT_KEYS):
        resp = test_client.get("/api/workspace/inbox/tracks", params={"sort": key, "order": "asc"})
        assert resp.status_code == 200, f"sort={key} should be accepted"


def test_route_sort_result_matches_service_layer(client):
    test_client, root = client
    _insert(root, "c.mp3", genre="House")
    _insert(root, "a.mp3", genre="Afro House")

    resp = test_client.get("/api/workspace/inbox/tracks", params={"sort": "genre", "order": "asc"})
    assert resp.status_code == 200
    body = resp.json()
    assert [item["filename"] for item in body["items"]] == ["a.mp3", "c.mp3"]
