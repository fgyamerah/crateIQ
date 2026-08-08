"""
Targeted tests for backend.app.services.workspace_service (Cycle 9).

Covers: workspace state classification, safe idempotent configuration,
copy-based import (never moves, verifies, handles collisions/duplicates,
rejects symlink escapes), and promotion readiness/execution (required
fields, metadata-write verification, BPM/key/waveform as warnings only,
destination path formation, collision handling, truthful failure states).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app.services import library_setup_service, tag_write_service, workspace_service as svc


def _write(path: Path, content: bytes = b"fake-audio-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# workspace_state
# ---------------------------------------------------------------------------

def test_not_configured_for_missing_or_empty_root(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert svc.workspace_state(missing)["state"] == "not_configured"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert svc.workspace_state(empty)["state"] == "not_configured"


def test_legacy_direct_library_detected_and_never_restructured(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    _write(root / "track.mp3")

    state = svc.workspace_state(root)
    assert state["state"] == "legacy_direct_library"

    with pytest.raises(ValueError):
        svc.configure_workspace(root)

    # Existing file untouched, no Inbox/Library/Quarantine created.
    assert (root / "track.mp3").is_file()
    assert not (root / "Inbox").exists()
    assert not (root / "Library").exists()


def test_configure_workspace_creates_zones_and_marker(tmp_path):
    root = tmp_path / "managed"
    result = svc.configure_workspace(root)

    assert result["state"] == "managed_workspace"
    assert (root / "Inbox").is_dir()
    assert (root / "Library").is_dir()
    assert (root / "Quarantine").is_dir()
    assert (root / svc.WORKSPACE_MARKER_NAME).is_file()
    assert (root / "logs" / "processed.db").is_file()


def test_configure_workspace_is_idempotent(tmp_path):
    root = tmp_path / "managed"
    first = svc.configure_workspace(root)
    second = svc.configure_workspace(root)
    assert first["state"] == second["state"] == "managed_workspace"


# ---------------------------------------------------------------------------
# import_sources
# ---------------------------------------------------------------------------

def test_import_requires_confirm(tmp_path):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    with pytest.raises(ValueError):
        svc.import_sources(root, [str(tmp_path)], confirm=False)


def test_import_requires_managed_workspace(tmp_path):
    root = tmp_path / "not-configured"
    with pytest.raises(ValueError):
        svc.import_sources(root, [str(tmp_path)], confirm=True)


def test_import_copies_folder_and_preserves_original(tmp_path):
    root = tmp_path / "managed"
    svc.configure_workspace(root)

    external = tmp_path / "external_source"
    track = external / "song.mp3"
    original_bytes = b"original-audio-bytes"
    _write(track, original_bytes)
    original_hash = _sha(track)

    result = svc.import_sources(root, [str(external)], confirm=True)

    assert result["imported_count"] == 1
    assert track.read_bytes() == original_bytes, "external original must never be modified"
    assert _sha(track) == original_hash

    inbox_copy = root / "Inbox" / "song.mp3"
    assert inbox_copy.is_file()
    assert inbox_copy.read_bytes() == original_bytes

    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        row = conn.execute("SELECT filepath, storage_zone FROM tracks").fetchone()
    assert row == (str(inbox_copy), "INBOX")


def test_import_handles_nested_folders(tmp_path):
    root = tmp_path / "managed"
    svc.configure_workspace(root)

    external = tmp_path / "external_source"
    _write(external / "top.mp3")
    _write(external / "sub" / "deep" / "nested.flac")

    result = svc.import_sources(root, [str(external)], confirm=True)

    assert result["imported_count"] == 2
    assert (root / "Inbox" / "top.mp3").is_file()
    assert (root / "Inbox" / "nested.flac").is_file()


def test_import_skips_unsupported_files(tmp_path):
    root = tmp_path / "managed"
    svc.configure_workspace(root)

    external = tmp_path / "external_source"
    _write(external / "notes.txt")
    _write(external / "song.mp3")

    result = svc.import_sources(root, [str(external)], confirm=True)

    assert result["imported_count"] == 1
    assert not (root / "Inbox" / "notes.txt").exists()


def test_import_collision_gets_deterministic_suffix_not_overwrite(tmp_path):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    (root / "Inbox" / "song.mp3").parent.mkdir(parents=True, exist_ok=True)
    _write(root / "Inbox" / "song.mp3", b"already-here")

    external = tmp_path / "external_source"
    _write(external / "song.mp3", b"different-new-content")

    result = svc.import_sources(root, [str(external)], confirm=True)

    assert result["imported_count"] == 1
    assert (root / "Inbox" / "song.mp3").read_bytes() == b"already-here"
    assert (root / "Inbox" / "song (2).mp3").read_bytes() == b"different-new-content"


def test_import_identical_content_reported_as_duplicate_not_recopied(tmp_path):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    _write(root / "Inbox" / "song.mp3", b"same-bytes")

    external = tmp_path / "external_source"
    _write(external / "song.mp3", b"same-bytes")

    result = svc.import_sources(root, [str(external)], confirm=True)

    assert result["imported_count"] == 0
    assert result["duplicate_count"] == 1
    assert not (root / "Inbox" / "song (2).mp3").exists()


def test_import_rejects_symlink_source_file(tmp_path):
    root = tmp_path / "managed"
    svc.configure_workspace(root)

    real_target = tmp_path / "outside" / "secret.mp3"
    _write(real_target)
    external = tmp_path / "external_source"
    external.mkdir()
    symlink = external / "link.mp3"
    symlink.symlink_to(real_target)

    result = svc.import_sources(root, [str(external)], confirm=True)

    assert result["imported_count"] == 0
    assert not any((root / "Inbox").iterdir())


def test_import_refuses_source_inside_managed_root(tmp_path):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    inside = root / "Inbox" / "already-here.mp3"
    _write(inside)

    result = svc.import_sources(root, [str(root)], confirm=True)
    assert result["imported_count"] == 0
    assert any("Refusing to import" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# promotion
# ---------------------------------------------------------------------------

def _seed_inbox_track(root: Path, *, artist="DJ Koze", title="Pick Up", genre="House", filename="song.mp3") -> int:
    inbox_file = root / "Inbox" / filename
    _write(inbox_file)
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, ?, ?, ?, ?, 'pending', '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(inbox_file), filename, artist, title, genre),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_promotion_preview_requires_artist_title_genre(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    track_id = _seed_inbox_track(root, artist="", title="", genre="")

    preview = svc.promotion_preview(root, [track_id])
    item = preview["items"][0]
    assert item["ready"] is False
    assert any("Artist" in b for b in item["blockers"])
    assert any("Title" in b for b in item["blockers"])
    assert any("Genre" in b for b in item["blockers"])


def test_promotion_preview_bpm_key_waveform_are_warnings_not_blockers(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    track_id = _seed_inbox_track(root)
    # The fixture writes a fake (non-real-audio) file with no readable tags;
    # simulate a file whose tags already match the approved index values, as
    # a real track would after import/write-back, so only BPM/key are open.
    monkeypatch.setattr(
        tag_write_service, "_read_file_tags",
        lambda path: {"artist": "DJ Koze", "title": "Pick Up", "album": "", "genre": "House"},
    )

    preview = svc.promotion_preview(root, [track_id])
    item = preview["items"][0]
    assert item["ready"] is True
    assert "Missing BPM." in item["warnings"]
    assert "Missing key." in item["warnings"]


def test_promotion_blocks_when_metadata_write_not_yet_applied(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    track_id = _seed_inbox_track(root)

    # Simulate the file on disk having a different (stale) artist tag than
    # the approved index value -- tag_write_service.build_plan() should
    # report a pending changeable field, which must block promotion.
    monkeypatch.setattr(
        tag_write_service, "_read_file_tags",
        lambda path: {"artist": "Old Artist", "title": "", "album": "", "genre": ""},
    )

    preview = svc.promotion_preview(root, [track_id])
    item = preview["items"][0]
    assert item["ready"] is False
    assert any("not been written back" in b for b in item["blockers"])


def test_promote_tracks_moves_file_and_updates_zone(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    track_id = _seed_inbox_track(root)
    monkeypatch.setattr(
        tag_write_service, "_read_file_tags",
        lambda path: {"artist": "DJ Koze", "title": "Pick Up", "album": "", "genre": "House"},
    )
    inbox_path = root / "Inbox" / "song.mp3"

    result = svc.promote_tracks(root, [track_id], confirm=True)

    assert result["promoted_count"] == 1
    expected_dest = root / "Library" / "House" / "DJ Koze" / "DJ Koze - Pick Up.mp3"
    assert expected_dest.is_file()
    assert not inbox_path.exists()

    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        row = conn.execute("SELECT filepath, storage_zone FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row == (str(expected_dest), "LIBRARY")


def test_promote_requires_confirm(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    track_id = _seed_inbox_track(root)
    with pytest.raises(ValueError):
        svc.promote_tracks(root, [track_id], confirm=False)


def test_promote_destination_collision_blocks_and_preserves_inbox(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    track_id = _seed_inbox_track(root)
    conflicting = root / "Library" / "House" / "DJ Koze" / "DJ Koze - Pick Up.mp3"
    _write(conflicting, b"different-existing-library-file")

    result = svc.promote_tracks(root, [track_id], confirm=True)

    assert result["promoted_count"] == 0
    assert result["failed_count"] == 1
    inbox_path = root / "Inbox" / "song.mp3"
    assert inbox_path.is_file(), "failure must leave the Inbox copy in place, never lose data"
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        row = conn.execute("SELECT storage_zone FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row[0] == "INBOX"


def test_promote_path_segments_are_sanitized_against_traversal():
    seg = svc.safe_path_segment("../../etc/passwd", "fallback")
    assert "/" not in seg
    assert ".." not in seg


def test_promote_unicode_artist_preserved(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    track_id = _seed_inbox_track(root, artist="Béatrice", title="Déjà Vu", genre="Deep House")
    monkeypatch.setattr(
        tag_write_service, "_read_file_tags",
        lambda path: {"artist": "Béatrice", "title": "Déjà Vu", "album": "", "genre": "Deep House"},
    )

    result = svc.promote_tracks(root, [track_id], confirm=True)
    assert result["promoted_count"] == 1
    expected = root / "Library" / "Deep House" / "Béatrice" / "Béatrice - Déjà Vu.mp3"
    assert expected.is_file()


# ---------------------------------------------------------------------------
# Regression: fail closed on an uninitialized root, never crash
# ---------------------------------------------------------------------------

def test_promotion_preview_fails_closed_on_uninitialized_root(tmp_path):
    root = tmp_path / "not-initialized"
    root.mkdir()
    with pytest.raises(ValueError, match="initialize the managed workspace"):
        svc.promotion_preview(root)


def test_promotion_preview_fails_closed_on_missing_root(tmp_path):
    root = tmp_path / "does-not-exist-at-all"
    with pytest.raises(ValueError, match="initialize the managed workspace"):
        svc.promotion_preview(root)


def test_promote_tracks_fails_closed_on_uninitialized_root(tmp_path):
    root = tmp_path / "not-initialized"
    root.mkdir()
    with pytest.raises(ValueError, match="initialize the managed workspace"):
        svc.promote_tracks(root, [1], confirm=True)


def test_promotion_preview_empty_track_ids_means_none_not_all(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    _seed_inbox_track(root)

    preview = svc.promotion_preview(root, [])
    assert preview["track_count"] == 0, "an explicit empty list must preview nothing, not silently fall back to all tracks"

    preview_all = svc.promotion_preview(root, None)
    assert preview_all["track_count"] == 1
