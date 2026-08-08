"""
Targeted tests for backend.app.services.library_setup_service.

Covers: initialization, scan preview (read-only), import (embedded-tag
reading with filename fallback, idempotent rescans, unsupported/skip
counts, path containment), and confirms no source audio bytes are ever
touched by any of these operations.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app.services import library_setup_service as svc


def _write(path: Path, content: bytes = b"fake-audio-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_initialize_library_creates_schema_without_scanning(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    _write(root / "track.mp3")

    result = svc.initialize_library(str(root))

    assert result["initialized"] is True
    db_path = root / "logs" / "processed.db"
    assert db_path.is_file()
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    assert count == 0, "initialize must not scan or index any files"


def test_scan_preview_requires_initialization(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    with pytest.raises(ValueError):
        svc.scan_preview(str(root))


def test_scan_preview_is_read_only(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    svc.initialize_library(str(root))
    _write(root / "a.mp3")
    _write(root / "b.txt")  # unsupported

    preview = svc.scan_preview(str(root))

    assert preview["supported_audio_files"] == 1
    assert preview["unsupported_file_count"] == 1
    assert preview["importable"] is True
    assert "track_paths" not in preview

    db_path = root / "logs" / "processed.db"
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    assert count == 0, "scan preview must not write to the local index"


def test_import_reads_embedded_tags_over_filename(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    svc.initialize_library(str(root))
    track = root / "Untitled Export.mp3"
    _write(track)

    def fake_tags(path: Path) -> dict[str, str]:
        if path == track:
            return {"artist": "Real Artist", "title": "Real Title", "album": "Real Album", "genre": "House"}
        return {}

    monkeypatch.setattr(svc, "_embedded_tags", fake_tags)

    result = svc.import_previewed_library(str(root))

    assert result["imported_count"] == 1
    assert result["tags_read_count"] == 1
    db_path = root / "logs" / "processed.db"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT artist, title, album, genre, parse_confidence FROM tracks").fetchone()
    assert row == ("Real Artist", "Real Title", "Real Album", "House", "HIGH")


def test_import_falls_back_to_filename_when_no_tags(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    svc.initialize_library(str(root))
    track = root / "DJ Koze - Pick Up.mp3"
    _write(track)

    monkeypatch.setattr(svc, "_embedded_tags", lambda path: {})

    result = svc.import_previewed_library(str(root))

    assert result["tags_read_count"] == 0
    db_path = root / "logs" / "processed.db"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT artist, title, parse_confidence FROM tracks").fetchone()
    assert row == ("DJ Koze", "Pick Up", "MEDIUM")


def test_rescan_import_is_idempotent_and_reports_existing_count(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    svc.initialize_library(str(root))
    _write(root / "one.mp3")
    monkeypatch.setattr(svc, "_embedded_tags", lambda path: {})

    first = svc.import_previewed_library(str(root))
    assert first["imported_count"] == 1
    assert first["existing_count"] == 0

    second = svc.import_previewed_library(str(root))
    assert second["imported_count"] == 0
    assert second["existing_count"] == 1
    assert second["total_indexed_count"] == 1


def test_import_never_modifies_source_audio_bytes(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    svc.initialize_library(str(root))
    track = root / "keep-me.mp3"
    original = b"original-audio-bytes-untouched"
    _write(track, original)
    monkeypatch.setattr(svc, "_embedded_tags", lambda path: {"artist": "X", "title": "Y", "album": "", "genre": ""})

    svc.import_previewed_library(str(root))

    assert track.read_bytes() == original


def test_omitted_root_never_uses_pending_root_over_selected_root(tmp_path, monkeypatch):
    """
    Regression for the Cycle 7 near-miss: a pending library root (library A,
    staged via Settings but not yet active because the process hasn't
    restarted) must never be silently substituted for the actual selected
    root (library B) when a mutating call omits an explicit library_root.
    """
    library_a = tmp_path / "library-a-pending"
    library_b = tmp_path / "library-b-selected"
    library_a.mkdir()
    library_b.mkdir()
    _write(library_a / "a-track.mp3")
    _write(library_b / "b-track.mp3")

    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library_b))
    monkeypatch.setattr(
        "backend.app.services.settings_service._pending_library_root",
        lambda: library_a,
    )

    result = svc.initialize_library()
    assert Path(result["library_root"]) == library_b.resolve()
    assert (library_b / "logs" / "processed.db").is_file()
    assert not (library_a / "logs" / "processed.db").exists()

    import_result = svc.import_previewed_library()
    assert import_result["imported_count"] == 1
    with sqlite3.connect(library_b / "logs" / "processed.db") as conn:
        rows = conn.execute("SELECT filepath FROM tracks").fetchall()
    assert rows == [(str((library_b / "b-track.mp3").resolve()),)]
    assert not (library_a / "logs" / "processed.db").exists()


def test_target_root_fails_closed_when_no_root_is_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CRATEMINDAI_LIBRARY_ROOT", raising=False)
    monkeypatch.setattr(
        "backend.app.core.library_root._load_toolkit_music_root", lambda: None
    )
    monkeypatch.setattr(
        "backend.app.services.settings_service._pending_library_root",
        lambda: tmp_path / "some-pending-library",
    )

    with pytest.raises(RuntimeError):
        svc.initialize_library()


def test_scan_skips_unreadable_and_reports_sample(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    svc.initialize_library(str(root))
    _write(root / "good.flac")
    unreadable = root / "locked.mp3"
    _write(unreadable)
    unreadable.chmod(0o000)
    try:
        preview = svc.scan_preview(str(root))
    finally:
        unreadable.chmod(0o644)

    assert preview["supported_audio_files"] == 1
    assert preview["skipped_file_count"] == 1
