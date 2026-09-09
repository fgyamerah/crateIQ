"""Canonical, privacy-safe identity for one selected CrateIQ library root."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .library_root import selected_library_root


def library_key_for_root(root: Path | str | None) -> str | None:
    """Return a stable key for ``root``; rootless instances have no key.

    The digest deliberately retains the established waveform ``library_id``
    algorithm. ``library_id`` is consequently a compatibility column name
    whose value is the canonical library key.
    """
    if root is None:
        return None
    canonical = Path(root).expanduser().resolve(strict=False)
    return hashlib.sha256(b"crateiq-library-v1\0" + os.fsencode(canonical)).hexdigest()


def current_library_key() -> str:
    """Return the immutable root-bound key; rootless use fails closed."""
    key = library_key_for_root(selected_library_root())
    if key is None:  # defensive; selected_library_root currently raises first
        raise RuntimeError("No active library key is available.")
    expected = os.environ.get("CRATEIQ_BACKEND_LIBRARY_KEY")
    if expected and expected != key:
        raise RuntimeError("Backend library identity does not match its selected root.")
    return key
