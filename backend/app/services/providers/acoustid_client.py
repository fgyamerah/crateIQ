"""
AcoustID provider adapter (Cycle 11).

Two distinct local/remote halves, per the product spec's hard rule:

  1. Fingerprinting (`fingerprint_file`) is 100% local -- it shells out to
     the `fpcalc` (Chromaprint) CLI against the managed Inbox COPY only,
     never the external original, and never submits anything anywhere.
     Bounded, read-only, no network.
  2. Lookup (`lookup`) sends only the fingerprint + duration to AcoustID's
     official https://api.acoustid.org/v2/lookup endpoint with a
     self-serve application client key (register at acoustid.org).
     Lookup only -- CrateIQ never submits a fingerprint *to* AcoustID
     (that would associate this fingerprint with new metadata in their
     database, which is out of scope and not requested).

Rate-limited to AcoustID's own published 3 requests/second limit via a
minimum inter-call spacing enforced by the caller (the batch enrichment
orchestrator), not by this module holding process-wide state.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import requests

from .base import ProviderCandidate, ProviderCapability, ProviderResult, missing_credentials, timeout

log = logging.getLogger(__name__)

_LOOKUP_URL = "https://api.acoustid.org/v2/lookup"
_REQUIRED = ("client_key",)
_FPCALC_TIMEOUT_SECONDS = 20


def capability(credentials: dict[str, str]) -> ProviderCapability:
    missing = missing_credentials(_REQUIRED, credentials)
    if missing:
        return ProviderCapability(
            status="needs_setup",
            message="Add a free AcoustID application client key (acoustid.org/new-application) in Settings to enable fingerprint lookups.",
            required_credentials=_REQUIRED,
        )
    if not _resolve_fpcalc_binary():
        return ProviderCapability(
            status="unavailable",
            message="fpcalc (Chromaprint) is not installed or not on PATH; fingerprinting cannot run locally.",
            required_credentials=_REQUIRED,
        )
    return ProviderCapability(status="ready", message="Client key configured and fpcalc is available.", required_credentials=_REQUIRED)


def _resolve_fpcalc_binary() -> str | None:
    override = os.environ.get("FPCALC_BIN", "").strip()
    if override:
        resolved = shutil.which(override)
        return resolved if resolved and os.access(resolved, os.X_OK) else None
    resolved = shutil.which("fpcalc")
    return resolved if resolved and os.access(resolved, os.X_OK) else None


def fpcalc_available() -> bool:
    """Local, credential-free check: is the fpcalc (Chromaprint) tool usable?

    Callers that only need local fingerprinting (no AcoustID lookup) should
    use this instead of `capability()`, which also requires an AcoustID
    client key.
    """
    return _resolve_fpcalc_binary() is not None


def fingerprint_file(path: Path) -> tuple[str | None, float | None, str | None]:
    """
    Compute a Chromaprint fingerprint for one managed Inbox copy.

    Returns (fingerprint, duration_sec, error). Local-only, read-only,
    bounded -- never touches the network, never writes to the file.
    """
    binary = _resolve_fpcalc_binary()
    if binary is None:
        return None, None, "fpcalc is not installed or not on PATH."
    try:
        proc = subprocess.run(
            [binary, "-json", str(path)],
            capture_output=True, text=True, timeout=_FPCALC_TIMEOUT_SECONDS, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, None, f"fpcalc failed: {exc}"
    if proc.returncode != 0:
        return None, None, f"fpcalc exited {proc.returncode}: {proc.stderr.strip()[:200]}"
    try:
        import json
        payload = json.loads(proc.stdout)
        return payload.get("fingerprint"), payload.get("duration"), None
    except (ValueError, AttributeError):
        return None, None, "fpcalc returned an unexpected response shape."


def lookup(fingerprint: str, duration_sec: float, *, credentials: dict[str, str]) -> ProviderResult:
    cap = capability(credentials)
    if cap.status not in ("ready",):
        return ProviderResult(error=cap.message)

    params = {
        "client": credentials["client_key"],
        "meta": "recordings+releasegroups",
        "fingerprint": fingerprint,
        "duration": int(round(duration_sec)),
    }
    try:
        resp = requests.get(_LOOKUP_URL, params=params, timeout=timeout())
    except requests.RequestException as exc:
        return ProviderResult(error=f"AcoustID request failed: {exc}", network_used=True)

    if resp.status_code == 429:
        return ProviderResult(error="AcoustID rate limit exceeded (3 req/sec).", status_code=429, network_used=True)
    if resp.status_code >= 400:
        return ProviderResult(error=f"AcoustID returned HTTP {resp.status_code}.", status_code=resp.status_code, network_used=True)

    try:
        payload = resp.json()
    except ValueError:
        return ProviderResult(error="AcoustID returned an unexpected response shape.", network_used=True)

    if payload.get("status") != "ok":
        return ProviderResult(error=str(payload.get("error", {}).get("message", "AcoustID lookup failed.")), network_used=True)

    candidates: list[ProviderCandidate] = []
    for result in payload.get("results", []):
        score = result.get("score")
        for recording in result.get("recordings", []) or []:
            artists = recording.get("artists") or []
            candidates.append(ProviderCandidate(
                provider="acoustid",
                artist=", ".join(a.get("name", "") for a in artists if a.get("name")) or None,
                title=recording.get("title"),
                provider_id=recording.get("id"),
                provider_url=f"https://musicbrainz.org/recording/{recording.get('id')}" if recording.get("id") else None,
                raw_confidence=f"acoustid_score={score:.2f}" if isinstance(score, (int, float)) else None,
            ))
    return ProviderResult(candidates=candidates, network_used=True)
