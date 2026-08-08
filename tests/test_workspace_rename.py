"""
Tests for the safe Inbox Track/file rename contract
(backend.app.services.workspace_service.rename_inbox_track) and its PATCH
/api/workspace/inbox/tracks/{track_id} route.

Rename only ever touches the managed Inbox copy's filename -- never audio
bytes, never TITLE metadata, never anything outside <root>/Inbox. These
tests use fake (non-audio) bytes throughout since rename is a pure
filesystem + index operation with no tag I/O.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.core.db as backend_core_db
import backend.app.main as backend_main
from backend.app.services import workspace_service


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


def _seed_track(root: Path, filename: str, *, zone: str = "INBOX", title: str = "Original Title") -> int:
    path = root / ("Inbox" if zone == "INBOX" else "Library") / filename
    _write(path)
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, ?, 'DJ Koze', ?, 'House', 'pending',
                       '2026-01-01T00:00:00Z', 'test', ?)""",
            (str(path), filename, title, zone),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------

def test_rename_succeeds_and_preserves_extension_and_title(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3", title="Original Title")

    result = workspace_service.rename_inbox_track(managed_root, track_id, "Wanderer")

    assert result["status"] == "renamed"
    assert result["filename"] == "Wanderer.mp3"
    assert (managed_root / "Inbox" / "Wanderer.mp3").is_file()
    assert not (managed_root / "Inbox" / "Traveler.mp3").exists()

    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["filename"] == "Wanderer.mp3"
    assert row["filepath"] == str(managed_root / "Inbox" / "Wanderer.mp3")
    assert row["title"] == "Original Title", "TITLE must never change from a filename rename"
    assert row["storage_zone"] == "INBOX"


def test_rename_no_op_for_identical_name(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    result = workspace_service.rename_inbox_track(managed_root, track_id, "Traveler")
    assert result["status"] == "no_change"
    assert (managed_root / "Inbox" / "Traveler.mp3").is_file()


def test_rename_strips_accidental_extension_typed_by_user(managed_root):
    """If the client accidentally echoes the locked extension back, it must not double up."""
    track_id = _seed_track(managed_root, "Traveler.mp3")
    result = workspace_service.rename_inbox_track(managed_root, track_id, "Wanderer.mp3")
    assert result["filename"] == "Wanderer.mp3"


def test_rename_preserves_unicode(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    result = workspace_service.rename_inbox_track(managed_root, track_id, "Trávelér 旅行者")
    assert result["filename"] == "Trávelér 旅行者.mp3"
    assert (managed_root / "Inbox" / "Trávelér 旅行者.mp3").is_file()


def test_rename_leaves_external_original_and_playback_path_intact(tmp_path, managed_root):
    source_dir = tmp_path / "external-downloads"
    source_file = source_dir / "Original Download.mp3"
    _write(source_file, b"external-original-bytes")
    original_bytes = source_file.read_bytes()

    import_result = workspace_service.import_sources(managed_root, [str(source_file)], confirm=True)
    assert import_result["imported_count"] == 1

    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id FROM tracks WHERE storage_zone = 'INBOX'").fetchone()
    track_id = row["id"]

    result = workspace_service.rename_inbox_track(managed_root, track_id, "Renamed Copy")

    assert result["status"] == "renamed"
    assert source_file.is_file(), "external original must never be touched by an Inbox rename"
    assert source_file.read_bytes() == original_bytes
    assert Path(result["filepath"]).is_file(), "renamed Inbox copy must exist at its new path for playback"


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------

def test_rename_rejects_non_inbox_track(managed_root):
    track_id = _seed_track(managed_root, "Promoted.mp3", zone="LIBRARY")
    with pytest.raises(ValueError, match="Inbox"):
        workspace_service.rename_inbox_track(managed_root, track_id, "New Name")


def test_rename_rejects_unknown_track(managed_root):
    with pytest.raises(ValueError, match="not found"):
        workspace_service.rename_inbox_track(managed_root, 999999, "New Name")


def test_rename_rejects_path_separator(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    with pytest.raises(ValueError, match="separator"):
        workspace_service.rename_inbox_track(managed_root, track_id, "evil/name")


def test_rename_rejects_backslash_separator(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    with pytest.raises(ValueError, match="separator"):
        workspace_service.rename_inbox_track(managed_root, track_id, "evil\\name")


def test_rename_rejects_traversal(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    with pytest.raises(ValueError):
        workspace_service.rename_inbox_track(managed_root, track_id, "..")


def test_rename_rejects_empty(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    with pytest.raises(ValueError, match="empty"):
        workspace_service.rename_inbox_track(managed_root, track_id, "   ")


def test_rename_rejects_control_characters(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    with pytest.raises(ValueError, match="control"):
        workspace_service.rename_inbox_track(managed_root, track_id, "bad\x01name")


def test_rename_rejects_reserved_windows_name(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    with pytest.raises(ValueError, match="reserved"):
        workspace_service.rename_inbox_track(managed_root, track_id, "CON")


def test_rename_rejects_trailing_dot_or_space(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    with pytest.raises(ValueError):
        workspace_service.rename_inbox_track(managed_root, track_id, "Wanderer ")


def test_rename_rejects_collision_without_auto_suffix(managed_root):
    track_id = _seed_track(managed_root, "Traveler.mp3")
    _seed_track(managed_root, "Wanderer.mp3")
    with pytest.raises(ValueError, match="already exists"):
        workspace_service.rename_inbox_track(managed_root, track_id, "Wanderer")
    # No "(2)" suffix must ever be silently created.
    assert not (managed_root / "Inbox" / "Wanderer (2).mp3").exists()


def test_rename_rejects_symlink_escape(tmp_path, managed_root):
    outside_target = tmp_path / "outside.mp3"
    _write(outside_target)
    symlink_path = managed_root / "Inbox" / "Symlinked.mp3"
    symlink_path.symlink_to(outside_target)

    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, 'Symlinked.mp3', 'A', 'T', 'G', 'pending',
                       '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(symlink_path),),
        )
        track_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # A symlink resolving outside the managed root is rejected by the
    # path-under-root containment check before an is_symlink() check is even
    # reached -- both are valid rejections of the same escape attempt.
    with pytest.raises(ValueError, match="Inbox"):
        workspace_service.rename_inbox_track(managed_root, track_id, "New Name")


def test_rename_rejects_symlink_within_inbox(managed_root):
    """A symlink that resolves to another real file still inside Inbox must be rejected too."""
    real_target = managed_root / "Inbox" / "Real.mp3"
    _write(real_target)
    symlink_path = managed_root / "Inbox" / "Linked.mp3"
    symlink_path.symlink_to(real_target)

    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, 'Linked.mp3', 'A', 'T', 'G', 'pending',
                       '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(symlink_path),),
        )
        track_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    with pytest.raises(ValueError, match="symlink"):
        workspace_service.rename_inbox_track(managed_root, track_id, "New Name")


def test_rename_does_not_touch_intentional_traveler_duplicate_pair(managed_root):
    """Regression: the intentional duplicate-testing pair must survive unrelated renames untouched."""
    dup_a = _seed_track(managed_root, "Traveler.mp3")
    dup_b_path = managed_root / "Inbox" / "Traveler (dup).mp3"
    _write(dup_b_path)
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, 'Traveler (dup).mp3', 'DJ Koze', 'Original Title', 'House', 'pending',
                       '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(dup_b_path),),
        )

    other_id = _seed_track(managed_root, "Unrelated.mp3")
    workspace_service.rename_inbox_track(managed_root, other_id, "Renamed Unrelated")

    assert (managed_root / "Inbox" / "Traveler.mp3").is_file()
    assert (managed_root / "Inbox" / "Traveler (dup).mp3").is_file()


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------

def test_patch_route_renames_via_filename_field(client):
    test_client, root = client
    track_id = _seed_track(root, "Traveler.mp3")

    resp = test_client.patch(f"/api/workspace/inbox/tracks/{track_id}", json={"filename": "Wanderer"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["rename"]["status"] == "renamed"
    assert body["rename"]["filename"] == "Wanderer.mp3"
    assert body["errors"] == []


def test_patch_route_rejects_empty_body(client):
    test_client, root = client
    track_id = _seed_track(root, "Traveler.mp3")
    resp = test_client.patch(f"/api/workspace/inbox/tracks/{track_id}", json={})
    assert resp.status_code == 422


def test_patch_route_reports_rename_collision_as_422_with_reason(client):
    test_client, root = client
    track_id = _seed_track(root, "Traveler.mp3")
    _seed_track(root, "Wanderer.mp3")

    resp = test_client.patch(f"/api/workspace/inbox/tracks/{track_id}", json={"filename": "Wanderer"})

    assert resp.status_code == 422
    assert "already exists" in resp.json()["detail"]
