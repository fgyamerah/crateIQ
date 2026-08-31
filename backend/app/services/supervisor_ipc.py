"""Fixed-path client for the local CrateIQ supervisor socket.

No HTTP route calls this in 1B.2A.  It is the constrained backend-side seam
that a later activation endpoint can use after it has applied admission and
blocker checks.  The client deliberately accepts only the supervisor's small
operation allowlist; it cannot execute commands or inject process arguments.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from ..supervisor import DEFAULT_SOCKET_PATH, IPC_SCHEMA_VERSION, MAX_IPC_MESSAGE_BYTES

_ALLOWED = frozenset({"ping", "status", "start_candidate", "stop_candidate", "inspect_child", "restore_previous"})


class SupervisorIPCError(RuntimeError):
    pass


def request(operation: str, payload: dict[str, object] | None = None, *, socket_path: Path = DEFAULT_SOCKET_PATH) -> dict[str, Any]:
    if operation not in _ALLOWED:
        raise SupervisorIPCError("unknown supervisor operation")
    message = json.dumps({"schema_version": IPC_SCHEMA_VERSION, "operation": operation, "payload": payload or {}}).encode("utf-8") + b"\n"
    if len(message) > MAX_IPC_MESSAGE_BYTES:
        raise SupervisorIPCError("supervisor message is too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2.0)
        client.connect(str(socket_path))
        client.sendall(message)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = client.recv(min(4096, MAX_IPC_MESSAGE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_IPC_MESSAGE_BYTES or b"\n" in chunk:
                break
        response = b"".join(chunks)
    if len(response) > MAX_IPC_MESSAGE_BYTES or not response.endswith(b"\n") or response.count(b"\n") != 1:
        raise SupervisorIPCError("invalid supervisor response")
    try:
        document = json.loads(response)
    except json.JSONDecodeError as exc:
        raise SupervisorIPCError("invalid supervisor response") from exc
    if not isinstance(document, dict) or not isinstance(document.get("ok"), bool):
        raise SupervisorIPCError("invalid supervisor response")
    if not document["ok"]:
        raise SupervisorIPCError(str(document.get("error", "supervisor request failed")))
    result = document.get("result")
    if not isinstance(result, dict):
        raise SupervisorIPCError("invalid supervisor result")
    return result
