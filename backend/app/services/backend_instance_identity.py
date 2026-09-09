"""Supervisor-provided backend identity used by local candidate verification."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from ..core.library_key import library_key_for_root


def current_backend_identity() -> dict[str, object] | None:
    instance_id = os.environ.get("CRATEIQ_BACKEND_INSTANCE_ID")
    supervisor_id = os.environ.get("CRATEIQ_SUPERVISOR_INSTANCE_ID")
    role = os.environ.get("CRATEIQ_BACKEND_START_ROLE")
    port = os.environ.get("CRATEIQ_BACKEND_BOUND_PORT")
    verification_token = os.environ.get("CRATEIQ_BACKEND_VERIFY_TOKEN")
    if not all((instance_id, supervisor_id, role, port, verification_token)):
        return None
    try:
        bound_port = int(port)
    except ValueError:
        return None
    root_text = os.environ.get("CRATEIQ_LIBRARY_ROOT")
    root = Path(root_text).resolve(strict=False) if root_text else None
    return {
        "instance_id": instance_id,
        "supervisor_instance_id": supervisor_id,
        "role": role,
        "port": bound_port,
        "library_root": str(root) if root else None,
        "library_key": library_key_for_root(root),
        "verification_token": verification_token,
        # Uvicorn does not serve this route until application lifespan startup
        # has completed, so this is an explicit assertion of that readiness
        # boundary rather than a TCP-only liveness signal.
        "ready": True,
    }


def verify_supervisor_token(provided_token: str | None) -> dict[str, object] | None:
    """Return private identity only for the owning supervisor's loopback call."""
    expected = os.environ.get("CRATEIQ_BACKEND_VERIFY_TOKEN")
    identity = current_backend_identity()
    if not expected or identity is None or not provided_token:
        return None
    return identity if secrets.compare_digest(expected, provided_token) else None
