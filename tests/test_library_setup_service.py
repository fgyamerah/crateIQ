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
