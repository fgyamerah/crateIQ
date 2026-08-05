"""Shared DB-backed source-path validation for preview and waveform stat use."""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from ..core.library_root import assert_path_under_root, selected_library_root
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
    """Return a stable path-identity digest without exposing the root."""
    canonical = Path(root or selected_library_root()).expanduser().resolve(strict=False)
    return hashlib.sha256(b"crateiq-library-v1\0" + os.fsencode(canonical)).hexdigest()


def validated_track_source(track_id: int) -> ValidatedTrackSource:
    """Resolve a track ID through processed.db and enforce the preview boundary."""
    track = track_service.get_track_by_id(track_id)
    if track is None:
        raise TrackSourceNotFound("track not found")
    if not track.filepath:
        raise TrackSourceUnavailable("track source unavailable")
    root = selected_library_root()
    try:
        path = assert_path_under_root(Path(track.filepath), root)
    except ValueError as exc:
        raise TrackSourcePathRejected("track source is outside the selected library") from exc
    if not path.is_file():
        raise TrackSourceUnavailable("track source unavailable")
    return ValidatedTrackSource(track_id=track_id, path=path, library_root=root)


def source_stat_snapshot(track_id: int) -> SourceStatSnapshot:
    """Capture only cheap stat identity; never open or hash source contents."""
    source = validated_track_source(track_id)
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
