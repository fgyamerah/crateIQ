"""Focused coverage for Checkpoint 1B.1's rootless launcher foundation."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.core.db as backend_core_db
import backend.app.main as backend_main
from backend.app.services import library_registry_service as registry


def _configure_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    registry_path = tmp_path / ".run" / "local" / "library_registry.json"
    local_env_path = tmp_path / ".run" / "local" / "crateiq.env"
    monkeypatch.setattr(registry, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(registry, "LOCAL_ENV_PATH", local_env_path)
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CRATEMINDAI_LIBRARY_ROOT", raising=False)
    return registry_path, local_env_path


def _legacy_db(root: Path) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        # This is the stable schema core present in every supported historical
        # CrateIQ/DJ Toolkit processed.db version (see db.py history).
        conn.executescript("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT,
                status TEXT, pipeline_ver TEXT, key_musical TEXT, key_camelot TEXT
            );
            CREATE TABLE track_history (
                id INTEGER PRIMARY KEY, filepath TEXT, original_meta TEXT,
                cleaned_meta TEXT, actions TEXT, rolled_back INTEGER
            );
            CREATE TABLE pipeline_runs (
                id INTEGER PRIMARY KEY, run_at TEXT, dry_run INTEGER,
                inbox_count INTEGER, processed INTEGER, rejected INTEGER,
                duplicates INTEGER, unsorted INTEGER, errors INTEGER
            );
            CREATE TABLE duplicate_groups (
                id INTEGER PRIMARY KEY, run_id INTEGER, original TEXT,
                duplicate TEXT, reason TEXT, resolved INTEGER
            );
        """)
    return db_path


def _managed_workspace(root: Path) -> None:
    root.mkdir()
    for zone in registry.ZONE_NAMES:
        (root / zone).mkdir()
    (root / registry.WORKSPACE_MARKER_NAME).write_text(
        json.dumps({"version": registry.WORKSPACE_MARKER_VERSION, "created_at": "2026-08-30T00:00:00Z"}),
        encoding="utf-8",
    )


def test_empty_registry_and_first_insert(monkeypatch, tmp_path):
    registry_path, _ = _configure_registry(monkeypatch, tmp_path)
    assert registry.get_launcher_registry()["recent_libraries"] == []
    assert not registry_path.exists(), "registry GET must not create state"

    library = tmp_path / "DJ Coco"
    library.mkdir()
    entry = registry.record_recent_library(str(library), opened_at="2026-08-30T12:00:00Z")

    assert entry["path"] == str(library.resolve())
    stored = json.loads(registry_path.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 1
    assert stored["recent_libraries"] == [entry]


def test_registry_deduplicates_canonical_paths_and_orders_newest_first(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    first = tmp_path / "first"; first.mkdir()
    second = tmp_path / "second"; second.mkdir()
    alias = tmp_path / "first-alias"; alias.symlink_to(first, target_is_directory=True)

    registry.record_recent_library(str(first), opened_at="2026-08-30T10:00:00Z")
    registry.record_recent_library(str(second), opened_at="2026-08-30T11:00:00Z")
    registry.record_recent_library(str(alias), opened_at="2026-08-30T12:00:00Z")

    items = registry.get_launcher_registry()["recent_libraries"]
    assert [item["path"] for item in items] == [str(first.resolve()), str(second.resolve())]
    assert items[0]["last_opened_at"] == "2026-08-30T12:00:00Z"


def test_registry_retains_sixteen_and_launcher_returns_latest_four(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    for index in range(17):
        root = tmp_path / f"library-{index}"
        root.mkdir()
        registry.record_recent_library(str(root), opened_at=f"2026-08-30T12:{index:02d}:00Z")

    stored = registry._read_registry()
    response = registry.get_launcher_registry()
    assert len(stored) == registry.RECENT_LIBRARY_LIMIT
    assert len(response["recent_libraries"]) == registry.LAUNCHER_RECENT_LIMIT
    assert response["recent_libraries"][0]["path"].endswith("library-16")
    assert all(not item["path"].endswith("library-0") for item in stored)


def test_missing_recent_and_dangling_symlink_are_unavailable(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    missing = tmp_path / "gone"
    registry.record_recent_library(str(missing), opened_at="2026-08-30T12:00:00Z")
    assert registry.get_launcher_registry()["recent_libraries"][0]["classification"] == "missing"
    dangling = tmp_path / "dangling"
    dangling.symlink_to(missing, target_is_directory=True)
    result = registry.classify_library_candidate(str(dangling))
    assert result["classification"] == "missing"
    assert result["available"] is False


def test_malformed_registry_is_not_overwritten_by_read(monkeypatch, tmp_path):
    registry_path, _ = _configure_registry(monkeypatch, tmp_path)
    registry_path.parent.mkdir(parents=True)
    original = "{ definitely not json"
    registry_path.write_text(original, encoding="utf-8")

    response = registry.get_launcher_registry()

    assert response["registry_status"] == "malformed"
    assert registry_path.read_text(encoding="utf-8") == original


def test_registry_atomic_write_uses_replace_and_restrictive_permissions(monkeypatch, tmp_path):
    registry_path, _ = _configure_registry(monkeypatch, tmp_path)
    root = tmp_path / "library"; root.mkdir()
    replaced: list[Path] = []
    original_replace = registry.os.replace

    def checked_replace(source, destination):
        source_path = Path(source)
        assert source_path.parent == registry_path.parent
        assert source_path.stat().st_mode & 0o777 == 0o600
        replaced.append(source_path)
        return original_replace(source, destination)

    monkeypatch.setattr(registry.os, "replace", checked_replace)
    registry.record_recent_library(str(root), opened_at="2026-08-30T12:00:00Z")
    assert len(replaced) == 1
    assert registry_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("name", "prepare", "expected"),
    [
        ("managed", _managed_workspace, "managed_workspace"),
        ("external", lambda root: (root.mkdir(), (root / "song.mp3").write_bytes(b"audio")), "external_music_folder"),
        ("empty", lambda root: root.mkdir(), "empty_folder"),
    ],
)
def test_read_only_classifier_basics(tmp_path, name, prepare, expected):
    root = tmp_path / name
    prepare(root)
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    result = registry.classify_library_candidate(str(root))
    after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    assert result["classification"] == expected
    assert before == after


def test_classifier_rejects_bad_marker_version_and_unsafe_zone(tmp_path):
    root = tmp_path / "bad-marker"; root.mkdir()
    (root / registry.WORKSPACE_MARKER_NAME).write_text('{"version": 2}', encoding="utf-8")
    assert registry.classify_library_candidate(str(root))["classification"] == "malformed_or_unsafe"

    unsafe = tmp_path / "unsafe-zone"; _managed_workspace(unsafe)
    (unsafe / "Inbox").rmdir()
    (unsafe / "Inbox").symlink_to(tmp_path, target_is_directory=True)
    assert registry.classify_library_candidate(str(unsafe))["classification"] == "malformed_or_unsafe"


def test_classifier_rejects_invalid_managed_marker_json(tmp_path):
    root = tmp_path / "invalid-marker"; root.mkdir()
    (root / registry.WORKSPACE_MARKER_NAME).write_text("{not-json", encoding="utf-8")
    assert registry.classify_library_candidate(str(root))["classification"] == "malformed_or_unsafe"


def test_classifier_uses_conservative_legacy_fingerprint(tmp_path):
    legacy = tmp_path / "legacy"; legacy.mkdir(); _legacy_db(legacy)
    assert registry.classify_library_candidate(str(legacy))["classification"] == "legacy_direct_library"

    tracks_only = tmp_path / "tracks-only"; tracks_only.mkdir()
    db_path = tracks_only / "logs" / "processed.db"; db_path.parent.mkdir()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT)")
    assert registry.classify_library_candidate(str(tracks_only))["classification"] == "malformed_or_unsafe"

    external = tmp_path / "external"; external.mkdir()
    db_path = external / "logs" / "processed.db"; db_path.parent.mkdir()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT)")
    (external / "music.mp3").write_bytes(b"audio")
    assert registry.classify_library_candidate(str(external))["classification"] == "external_music_folder"


def test_valid_managed_workspace_does_not_depend_on_legacy_detection(monkeypatch, tmp_path):
    root = tmp_path / "managed"; _managed_workspace(root)
    db_path = root / "logs" / "processed.db"; db_path.parent.mkdir()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT)")
    monkeypatch.setattr(
        registry,
        "_legacy_db_is_crateiq",
        lambda *_args: (_ for _ in ()).throw(AssertionError("managed marker must win")),
    )

    assert registry.classify_library_candidate(str(root))["classification"] == "managed_workspace"


def test_classifier_fails_closed_for_malformed_sqlite_without_writing(tmp_path):
    root = tmp_path / "malformed-db"; root.mkdir()
    db_path = root / "logs" / "processed.db"; db_path.parent.mkdir()
    db_path.write_bytes(b"not a sqlite database")
    before = db_path.read_bytes(), db_path.stat().st_mtime_ns

    result = registry.classify_library_candidate(str(root))

    assert result["classification"] == "malformed_or_unsafe"
    assert (db_path.read_bytes(), db_path.stat().st_mtime_ns) == before


def test_legacy_wal_inspection_is_immutable_and_registry_get_is_side_effect_free(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    root = tmp_path / "wal-legacy"; root.mkdir()
    db_path = _legacy_db(root)
    writer = sqlite3.connect(db_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO tracks (id, filepath, filename) VALUES (1, 'track.mp3', 'track.mp3')")
        writer.commit()
        wal_path = Path(f"{db_path}-wal")
        shm_path = Path(f"{db_path}-shm")
        assert wal_path.exists()
        shm_path.unlink()
        assert not shm_path.exists()
        before = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (db_path, wal_path)
        }

        assert registry.classify_library_candidate(str(root))["classification"] == "legacy_direct_library"
        assert not shm_path.exists(), "immutable inspection must not create a SQLite shared-memory file"
        assert {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (db_path, wal_path)
        } == before

        registry.record_recent_library(str(root), opened_at="2026-08-30T12:00:00Z")
        assert registry.get_launcher_registry()["recent_libraries"][0]["classification"] == "legacy_direct_library"
        assert not shm_path.exists(), "registry GET must not create a SQLite shared-memory file"
        assert {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (db_path, wal_path)
        } == before
    finally:
        writer.close()


def test_legacy_inspection_does_not_require_a_writable_database(tmp_path):
    root = tmp_path / "readonly-legacy"; root.mkdir()
    db_path = _legacy_db(root)
    original_mode = db_path.stat().st_mode
    db_path.chmod(0o444)
    try:
        assert registry.classify_library_candidate(str(root))["classification"] == "legacy_direct_library"
    finally:
        db_path.chmod(original_mode)


def test_legacy_inspection_does_not_create_wal_or_shared_memory_files(tmp_path):
    root = tmp_path / "legacy-no-sidecars"; root.mkdir()
    db_path = _legacy_db(root)
    wal_path = Path(f"{db_path}-wal")
    shm_path = Path(f"{db_path}-shm")
    assert not wal_path.exists()
    assert not shm_path.exists()

    assert registry.classify_library_candidate(str(root))["classification"] == "legacy_direct_library"

    assert not wal_path.exists()
    assert not shm_path.exists()


def test_classifier_missing_and_unsafe_path(tmp_path):
    assert registry.classify_library_candidate(str(tmp_path / "missing"))["classification"] == "missing"
    with pytest.raises(ValueError):
        registry.classify_library_candidate("relative/path")


def test_compatibility_saved_root_is_recorded_without_library_changes(monkeypatch, tmp_path):
    _, local_env = _configure_registry(monkeypatch, tmp_path)
    saved = tmp_path / "saved"; saved.mkdir(); db_path = _legacy_db(saved)
    db_before = db_path.read_bytes()
    local_env.parent.mkdir(parents=True)
    local_env.write_text(f"CRATEIQ_LIBRARY_ROOT={saved}\n", encoding="utf-8")

    registry.bootstrap_compatibility_registry()
    response = registry.get_launcher_registry()

    assert response["recent_libraries"][0]["path"] == str(saved.resolve())
    assert db_path.read_bytes() == db_before
    assert not (saved / registry.WORKSPACE_MARKER_NAME).exists()


def test_rootless_compatibility_seed_uses_only_the_saved_settings_root(monkeypatch, tmp_path):
    registry_path, local_env = _configure_registry(monkeypatch, tmp_path)
    saved = tmp_path / "saved"; saved.mkdir()
    inherited = tmp_path / "inherited"; inherited.mkdir()
    local_env.parent.mkdir(parents=True)
    local_env.write_text(f"CRATEIQ_LIBRARY_ROOT={saved}\n", encoding="utf-8")
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(inherited))

    registry.bootstrap_compatibility_registry()
    response = registry.get_launcher_registry()
    assert [item["path"] for item in response["recent_libraries"]] == [str(saved.resolve())]
    assert str(inherited.resolve()) not in registry_path.read_text(encoding="utf-8")


def test_rootless_compatibility_seed_leaves_missing_saved_root_and_inherited_root_unseeded(monkeypatch, tmp_path):
    registry_path, _ = _configure_registry(monkeypatch, tmp_path)
    inherited = tmp_path / "inherited"; inherited.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(inherited))

    registry.bootstrap_compatibility_registry()

    assert not registry_path.exists()


def test_compatibility_seed_is_idempotent_and_preserves_malformed_registry(monkeypatch, tmp_path):
    registry_path, local_env = _configure_registry(monkeypatch, tmp_path)
    saved = tmp_path / "saved"; saved.mkdir()
    local_env.parent.mkdir(parents=True)
    local_env.write_text(f"CRATEIQ_LIBRARY_ROOT={saved}\n", encoding="utf-8")
    registry.bootstrap_compatibility_registry()
    first = registry_path.read_bytes()
    registry.bootstrap_compatibility_registry()
    assert registry_path.read_bytes() == first

    malformed = b"{broken"
    registry_path.write_bytes(malformed)
    with pytest.raises(registry.MalformedRegistryError):
        registry.bootstrap_compatibility_registry()
    assert registry_path.read_bytes() == malformed


def test_rootless_api_isolated_from_library_routes_and_startup_recovery(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CRATEMINDAI_LIBRARY_ROOT", raising=False)
    monkeypatch.setenv("CRATEIQ_LAUNCH_ACCESS_MODE", "local")
    jobs_db = tmp_path / "backend-data" / "jobs.db"
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", jobs_db)
    monkeypatch.setattr(backend_main.waveform_job_service, "recover_interrupted_jobs", lambda: (_ for _ in ()).throw(AssertionError("no recovery")))
    monkeypatch.setattr(backend_main.analysis_operations_service, "recover_interrupted_operations", lambda: (_ for _ in ()).throw(AssertionError("no recovery")))
    monkeypatch.setattr(backend_main.publish_operations_service, "recover_interrupted_operations", lambda: (_ for _ in ()).throw(AssertionError("no recovery")))
    monkeypatch.setattr(backend_main.waveform_operations_service, "recover_interrupted_operations", lambda: (_ for _ in ()).throw(AssertionError("no recovery")))
    monkeypatch.setattr(backend_main.tag_write_service, "recover_interrupted_operations", lambda: (_ for _ in ()).throw(AssertionError("no recovery")))
    monkeypatch.setattr(backend_main.preparation_operations_service, "recover_interrupted_operations", lambda: (_ for _ in ()).throw(AssertionError("no recovery")))
    monkeypatch.setattr(backend_main, "get_scheduler", lambda: (_ for _ in ()).throw(AssertionError("no scheduler")))
    candidate = tmp_path / "uninitialized"; candidate.mkdir()

    with TestClient(backend_main.app) as client:
        registry_response = client.get("/api/launcher/library-registry")
        current_response = client.get("/api/launcher/current-library")
        classification_response = client.post("/api/launcher/library-classification", json={"library_root": str(candidate)})
        library_response = client.get("/api/tracks")
        health_response = client.get("/api/health")
        readiness_response = client.get("/api/runtime/readiness")

    assert registry_response.status_code == 200
    assert current_response.json() == {
        "rootless": True,
        "library_root": None,
        "library_id": None,
        "display_name": None,
        "launcher_status": "supervisor_unavailable",
        "activation_status": "idle",
    }
    assert classification_response.status_code == 200
    assert library_response.status_code == 409
    assert health_response.json() == {"ok": True, "db_exists": False}
    assert readiness_response.status_code == 200
    assert readiness_response.json()["status"] == "not_ready"
    assert not (candidate / "logs" / "processed.db").exists()
    assert not jobs_db.exists(), "rootless startup must not create backend operational state"


def test_rootless_lifespan_seeds_saved_settings_root_only(monkeypatch, tmp_path):
    registry_path, local_env = _configure_registry(monkeypatch, tmp_path)
    saved = tmp_path / "saved"; saved.mkdir()
    inherited = tmp_path / "inherited"; inherited.mkdir()
    local_env.parent.mkdir(parents=True)
    local_env.write_text(f"CRATEIQ_LIBRARY_ROOT={saved}\n", encoding="utf-8")
    # This simulates the environment after the launcher command has cleared
    # inherited active-root state before it starts the rootless backend.
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CRATEMINDAI_LIBRARY_ROOT", raising=False)
    monkeypatch.setenv("CRATEIQ_LAUNCH_ACCESS_MODE", "local")
    jobs_db = tmp_path / "backend-data" / "jobs.db"
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", jobs_db)

    with TestClient(backend_main.app) as client:
        assert client.get("/api/launcher/library-registry").status_code == 200

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert [entry["path"] for entry in payload["recent_libraries"]] == [str(saved.resolve())]
    assert str(inherited.resolve()) not in registry_path.read_text(encoding="utf-8")
    assert not jobs_db.exists()


def test_remote_candidate_path_administration_is_blocked(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CRATEMINDAI_LIBRARY_ROOT", raising=False)
    monkeypatch.setenv("CRATEIQ_LAUNCH_ACCESS_MODE", "local")
    candidate = tmp_path / "candidate"; candidate.mkdir()
    with TestClient(backend_main.app, client=("192.168.1.50", 50000)) as client:
        response = client.post("/api/launcher/library-classification", json={"library_root": str(candidate)})
    assert response.status_code == 403


def test_local_mode_allows_loopback_proxy_candidate_path_inspection(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("CRATEIQ_LAUNCH_ACCESS_MODE", "local")
    candidate = tmp_path / "candidate"; candidate.mkdir()

    with TestClient(backend_main.app, client=("127.0.0.1", 5175)) as client:
        response = client.post("/api/launcher/library-classification", json={"library_root": str(candidate)})

    assert response.status_code == 200
    assert response.json()["classification"] == "empty_folder"


def test_lan_mode_blocks_proxy_equivalent_candidate_path_inspection(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("CRATEIQ_LAUNCH_ACCESS_MODE", "lan")
    candidate = tmp_path / "candidate"; candidate.mkdir()

    # The Vite proxy reaches FastAPI over loopback. A forwarded client header
    # must not alter the server-startup LAN decision.
    with TestClient(backend_main.app, client=("127.0.0.1", 5175)) as client:
        response = client.post(
            "/api/launcher/library-classification",
            json={"library_root": str(candidate)},
            headers={"X-Forwarded-For": "192.168.1.50", "Forwarded": "for=192.168.1.50"},
        )
    assert response.status_code == 403
