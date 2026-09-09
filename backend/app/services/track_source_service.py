"""Shared DB-backed source-path validation for preview and waveform stat use."""
from __future__ import annotations

import stat
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from ..core.library_key import library_key_for_root
from ..models.waveform import SourceStatSnapshot
from . import track_service


class TrackSourceNotFound(LookupError):
    pass


class TrackSourcePathRejected(ValueError):
    pass


class TrackSourceUnavailable(FileNotFoundError):
    pass


@dataclass(frozen=True)
class ValidatedTrackSource:
    track_id: int
    path: Path
    library_root: Path


def library_identity(root: Path | str | None = None) -> str:
    """Compatibility alias for the canonical ``library_key`` value."""
    return library_key_for_root(root or selected_library_root())  # type: ignore[return-value]


def validated_track_source(
    track_id: int, *, library_root: Path | None = None
) -> ValidatedTrackSource:
    """Resolve a track ID through processed.db and enforce the preview boundary."""
    root = (library_root or selected_library_root()).resolve(strict=False)
    if library_root is None:
        track = track_service.get_track_by_id(track_id)
        filepath = track.filepath if track is not None else None
    else:
        db_path = library_db_path(root)
        if not db_path.is_file():
            raise TrackSourceNotFound("track not found")
        with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT filepath FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
        filepath = row[0] if row is not None else None
    if filepath is None:
        raise TrackSourceNotFound("track not found")
    if not filepath:
        raise TrackSourceUnavailable("track source unavailable")
    try:
        path = assert_path_under_root(Path(filepath), root)
    except ValueError as exc:
        raise TrackSourcePathRejected("track source is outside the selected library") from exc
    if not path.is_file():
        raise TrackSourceUnavailable("track source unavailable")
    return ValidatedTrackSource(track_id=track_id, path=path, library_root=root)


def source_stat_snapshot(
    track_id: int, *, library_root: Path | None = None
) -> SourceStatSnapshot:
    """Capture only cheap stat identity; never open or hash source contents."""
    source = validated_track_source(track_id, library_root=library_root)
    details = source.path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise TrackSourceUnavailable("track source is not a regular file")
    return SourceStatSnapshot(
        library_id=library_identity(source.library_root),
        track_id=track_id,
        source_size_bytes=details.st_size,
        source_mtime_ns=details.st_mtime_ns,
        source_ctime_ns=details.st_ctime_ns,
        source_device=details.st_dev,
        source_inode=details.st_ino,
    )
