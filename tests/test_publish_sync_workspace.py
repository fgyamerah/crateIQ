"""
Publish/SSD Sync workspace-aware source resolution and destination
configuration (Legacy Architecture Cleanup Phase 2).

Covers: source always derives from the active workspace (never a hardcoded
personal path, never Inbox/Quarantine), destination is explicit/configurable
with no silent default, rsync safety is preserved, and Legacy Direct Library
compatibility is maintained.
"""
from __future__ import annotations

import inspect
import os
import stat

import pytest

from backend.app.core.library_root import _REPO_ROOT
from backend.app.schemas.sync import SyncPreviewRequest
from backend.app.services import (
    publish_sync_service,
    rsync_runner,
    sync_destination_service as svc,
    workspace_service,
)
from tests.conftest import async_test


def _isolate_destination_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "DESTINATION_SETTINGS_PATH", tmp_path / "publish_sync_settings.json")


def _managed_root(tmp_path, monkeypatch, name="workspace"):
    root = tmp_path / name
    root.mkdir()
    workspace_service.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    return root


# ---------------------------------------------------------------------------
# SOURCE
# ---------------------------------------------------------------------------

def test_managed_workspace_source_resolves_to_library(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    assert svc.get_sync_source() == root / "Library"


def test_inbox_is_never_source(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    source = svc.get_sync_source()
    assert source != root / "Inbox"
    assert source.name != "Inbox"


def test_quarantine_is_never_source(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    source = svc.get_sync_source()
    assert source != root / "Quarantine"
    assert source.name != "Quarantine"


def test_external_import_original_is_never_source(tmp_path, monkeypatch):
    """Importing copies files into Inbox; the external original tree must
    never appear as, or inside, the resolved sync source."""
    root = _managed_root(tmp_path, monkeypatch)
    external = tmp_path / "external_originals"
    external.mkdir()
    (external / "track.mp3").write_bytes(b"original")
    workspace_service.import_sources(root, [str(external)], confirm=True)

    source = svc.get_sync_source()
    assert source == root / "Library"
    with pytest.raises(ValueError):
        source.relative_to(external)


def test_switching_workspace_changes_source(tmp_path, monkeypatch):
    root_a = _managed_root(tmp_path, monkeypatch, name="workspace_a")
    assert svc.get_sync_source() == root_a / "Library"

    root_b = _managed_root(tmp_path, monkeypatch, name="workspace_b")
    assert svc.get_sync_source() == root_b / "Library"
    assert svc.get_sync_source() != root_a / "Library"


def test_pending_root_does_not_change_active_source(tmp_path, monkeypatch):
    """A saved-but-not-yet-active library root (Settings' restart-required
    pending change lives only in .run/local/crateiq.env, never in the
    process environment) must never affect the current sync source."""
    active_root = _managed_root(tmp_path, monkeypatch, name="active")
    pending_root = tmp_path / "pending"
    pending_root.mkdir()
    workspace_service.configure_workspace(pending_root)

    assert svc.get_sync_source() == active_root / "Library"


def test_config_module_has_no_hardcoded_sync_paths():
    """Regression guard: the personal hardcoded paths this phase removed
    must never come back onto backend.app.core.config."""
    from backend.app.core import config

    for name in ("SYNC_SOURCE_LIBRARY", "SYNC_SOURCE_INBOX", "SYNC_SOURCE_MAP", "SYNC_DEST_SSD"):
        assert not hasattr(config, name)


# ---------------------------------------------------------------------------
# DESTINATION
# ---------------------------------------------------------------------------

def test_no_configured_destination_is_needs_setup(tmp_path, monkeypatch):
    _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)

    status, blockers, _warnings = svc.describe_destination_status()
    assert status == "needs_setup"
    assert blockers


def test_configured_safe_destination_is_ready(tmp_path, monkeypatch):
    _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    dest = tmp_path / "external_ssd"
    dest.mkdir()

    svc.set_destination(str(dest))
    status, blockers, _warnings = svc.describe_destination_status()
    assert status == "ready"
    assert blockers == []


def test_relative_destination_rejected(tmp_path, monkeypatch):
    _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        svc.validate_destination_path("relative/dir")


def test_filesystem_root_destination_rejected(tmp_path, monkeypatch):
    _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        svc.validate_destination_path("/")


def test_repo_runtime_destination_rejected(tmp_path, monkeypatch):
    _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        svc.validate_destination_path(str(_REPO_ROOT))


def test_source_equals_destination_rejected(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="same path as the source"):
        svc.validate_destination_path(str(root / "Library"))


def test_inbox_destination_rejected(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="managed Inbox"):
        svc.validate_destination_path(str(root / "Inbox"))


def test_library_destination_rejected(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        svc.validate_destination_path(str(root / "Library"))


def test_quarantine_destination_rejected(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="managed Quarantine"):
        svc.validate_destination_path(str(root / "Quarantine"))


def test_destination_inside_source_rejected(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    nested = root / "Library" / "nested_dest"
    nested.mkdir()
    with pytest.raises(ValueError, match="nested inside the source"):
        svc.validate_destination_path(str(nested))


def test_source_inside_destination_rejected(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="ancestor of the source"):
        svc.validate_destination_path(str(root))


def test_symlink_resolved_unsafe_destination_rejected(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    link = tmp_path / "looks_safe"
    link.symlink_to(root / "Inbox")
    with pytest.raises(ValueError):
        svc.validate_destination_path(str(link))


def test_missing_or_not_mounted_destination_reported_truthfully(tmp_path, monkeypatch):
    _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    dest = tmp_path / "not_mounted_yet"
    # Save while it exists (structurally safe), then remove it to simulate
    # the drive being unplugged after being configured once.
    dest.mkdir()
    svc.set_destination(str(dest))
    dest.rmdir()

    status, blockers, _warnings = svc.describe_destination_status()
    assert status == "not_mounted"
    assert any("not mounted" in b.lower() or "does not exist" in b.lower() for b in blockers)


def test_non_writable_destination_reported_truthfully(tmp_path, monkeypatch):
    _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    dest = tmp_path / "readonly_dest"
    dest.mkdir()
    svc.set_destination(str(dest))
    os.chmod(dest, stat.S_IREAD | stat.S_IEXEC)
    try:
        if os.access(dest, os.W_OK):
            pytest.skip("running as a user that bypasses directory permission bits (e.g. root)")
        status, _blockers, warnings = svc.describe_destination_status()
        assert status == "ready"
        assert any("not writable" in w.lower() for w in warnings)
    finally:
        os.chmod(dest, stat.S_IRWXU)


# ---------------------------------------------------------------------------
# RSYNC SAFETY
# ---------------------------------------------------------------------------

def test_rsync_command_omits_delete_by_default(tmp_path):
    cmd = rsync_runner._build_rsync_cmd(tmp_path / "src", tmp_path / "dst", allow_delete=False)
    assert "--delete" not in cmd


def test_start_sync_job_allow_delete_defaults_false():
    sig = inspect.signature(rsync_runner.start_sync_job)
    assert sig.parameters["allow_delete"].default is False


def test_no_remove_source_files_flag_in_rsync_runner():
    source = inspect.getsource(rsync_runner)
    assert "--remove-source-files" not in source


def test_publish_sync_confirm_request_rejects_missing_confirm():
    with pytest.raises(publish_sync_service.PublishSyncBlocked, match="confirm"):
        publish_sync_service.confirm("library", False)


@async_test
async def test_preview_is_read_only_and_uses_configured_source_and_destination(tmp_path, monkeypatch):
    root = _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    (root / "Library" / "track.mp3").write_bytes(b"fixture-audio")
    dest = tmp_path / "dest"
    dest.mkdir()
    svc.set_destination(str(dest))

    result = await rsync_runner.preview_sync(SyncPreviewRequest(source="library"))

    assert result.source_path == str(root / "Library")
    assert result.dest_path == str(dest)
    assert list(dest.iterdir()) == []
    assert (root / "Library" / "track.mp3").read_bytes() == b"fixture-audio"


@async_test
async def test_preview_surfaces_existing_differing_destination_file(tmp_path, monkeypatch):
    """A destination file that already exists but differs from the source
    must show up in the preview's file list rather than being silently
    treated as already in sync."""
    root = _managed_root(tmp_path, monkeypatch)
    _isolate_destination_settings(monkeypatch, tmp_path)
    (root / "Library" / "track.mp3").write_bytes(b"new-content")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "track.mp3").write_bytes(b"stale-content")
    svc.set_destination(str(dest))

    result = await rsync_runner.preview_sync(SyncPreviewRequest(source="library"))

    assert any(f.path == "track.mp3" for f in result.files)
    # Preview never writes -- the stale destination content is untouched.
    assert (dest / "track.mp3").read_bytes() == b"stale-content"


# ---------------------------------------------------------------------------
# LEGACY DIRECT LIBRARY COMPATIBILITY
# ---------------------------------------------------------------------------

def test_legacy_direct_library_source_is_the_root_itself(tmp_path, monkeypatch):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "track.mp3").write_bytes(b"legacy-audio")
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))

    assert workspace_service.workspace_state(root)["state"] == "legacy_direct_library"
    assert svc.get_sync_source() == root


def test_not_configured_root_blocks_with_clear_message(tmp_path, monkeypatch):
    root = tmp_path / "empty"
    root.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))

    with pytest.raises(ValueError, match="not configured as a managed workspace"):
        svc.get_sync_source()
