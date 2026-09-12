"""
Tests for single-track editing plus safe Genre/Comment/Label bulk editing on
managed Inbox tracks (backend.app.services.workspace_service.edit_inbox_track_metadata,
bulk_edit_preview, bulk_edit_apply) and their routes.

These verify the DB-first edit contract against real audio fixtures generated
by ffmpeg -- skipped if ffmpeg is unavailable, matching
tests/test_tag_write_service.py's convention. Every fixture lives
under pytest's tmp_path; nothing under /home/paak/Music/crateiq-test-library
is ever touched.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI

import backend.app.core.db as backend_core_db
from backend.app.api.routes import workspace as workspace_routes
from backend.app.services import preparation_service, workspace_service

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")


def _make_audio(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "1", "-c:a", "libmp3lame", "-b:a", "32k", str(path)],
        check=True,
    )


def _read_tags(path: Path) -> dict[str, str]:
    from mutagen import File as MFile
    audio = MFile(str(path), easy=True)
    get = lambda k: (audio.get(k) or [""])[0]
    return {k: get(k) for k in ("artist", "title", "album", "genre")}


def _write_tags(path: Path, **fields: str | None) -> None:
    from mutagen import File as MFile
    audio = MFile(str(path), easy=True)
    for field, value in fields.items():
        if value:
            audio[field] = [value]
    audio.save()


@pytest.fixture()
def managed_root(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    workspace_service.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_core_db.init_db()
    return root


def _seed_track(
    root: Path, filename: str, *, zone: str = "INBOX",
    artist: str = "Old Artist", title: str = "Some Title", album: str | None = None,
    genre: str = "Dance",
) -> int:
    path = root / ("Inbox" if zone == "INBOX" else "Library") / filename
    _make_audio(path)
    _write_tags(path, artist=artist, title=title, album=album, genre=genre)
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, album, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', '2026-01-01T00:00:00Z', 'test', ?)""",
            (str(path), filename, artist, title, album, genre, zone),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _row(root: Path, track_id: int) -> sqlite3.Row:
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def _api_request(method: str, path: str, payload: dict) -> tuple[int, dict]:
    """Exercise the production router without the broken local TestClient stack.

    Starlette's installed TestClient currently hangs because this environment
    has the deprecated httpx integration rather than httpx2. Sending a real
    ASGI request still covers routing, Pydantic request validation, and the
    production endpoint handler without weakening tests to direct calls.
    """
    app = FastAPI()
    app.include_router(workspace_routes.router, prefix="/api")
    request_body = json.dumps(payload).encode("utf-8")
    messages: list[dict] = []

    async def invoke() -> None:
        received = False

        async def receive() -> dict:
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": request_body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "headers": [
                    (b"host", b"testserver"),
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(request_body)).encode("ascii")),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
                "root_path": "",
            },
            receive,
            send,
        )

    asyncio.run(invoke())
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return status, json.loads(response_body or b"{}")


# ---------------------------------------------------------------------------
# Single Artist edit
# ---------------------------------------------------------------------------

def test_single_artist_edit_updates_db_only_and_becomes_unsaved(managed_root):
    track_id = _seed_track(managed_root, "song.mp3", artist="Old Artist")
    path = managed_root / "Inbox" / "song.mp3"
    tags_before = _read_tags(path)

    result = workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="Black Coffee")

    assert result["status"] == "updated"
    assert result["fields_changed"] == ["artist"]
    assert _row(managed_root, track_id)["artist"] == "Black Coffee"
    assert _read_tags(path) == tags_before
    assert result["tag_write"] is None
    assert result["preparation_state"]["status"] == "UNSAVED"
    assert result["preparation_state"]["pending_fields"] == ["artist"]


def test_single_metadata_edit_does_not_call_writer_or_create_backup(managed_root, monkeypatch):
    track_id = _seed_track(managed_root, "song.mp3", artist="Old Artist")
    monkeypatch.setattr(preparation_service, "write_tracks", lambda *_: pytest.fail("tag writer must not be called"))

    from backend.app.services import tag_write_service
    before = tag_write_service.list_operations(limit=5)
    workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="Black Coffee")
    assert tag_write_service.list_operations(limit=5) == before


def test_single_genre_edit_updates_db_only(managed_root):
    track_id = _seed_track(managed_root, "song.mp3", genre="Dance")
    path = managed_root / "Inbox" / "song.mp3"

    result = workspace_service.edit_inbox_track_metadata(managed_root, track_id, genre="Afro House")

    assert result["status"] == "updated"
    assert _row(managed_root, track_id)["genre"] == "Afro House"
    assert _read_tags(path)["genre"] == "Dance"


def test_single_title_edit_does_not_rename_filename(managed_root):
    track_id = _seed_track(managed_root, "song.mp3", title="Old Title")
    result = workspace_service.edit_inbox_track_metadata(managed_root, track_id, title="New Title")

    assert result["fields_changed"] == ["title"]
    assert _row(managed_root, track_id)["title"] == "New Title"
    assert _row(managed_root, track_id)["filename"] == "song.mp3"
    assert (managed_root / "Inbox" / "song.mp3").is_file()
    assert _read_tags(managed_root / "Inbox" / "song.mp3")["title"] == "Old Title"
    assert result["preparation_state"]["pending_fields"] == ["title"]


def test_single_album_edit_updates_db_only(managed_root):
    track_id = _seed_track(managed_root, "song.mp3", album="Old Album")
    result = workspace_service.edit_inbox_track_metadata(managed_root, track_id, album="New Album")

    assert result["fields_changed"] == ["album"]
    assert _row(managed_root, track_id)["album"] == "New Album"
    assert _read_tags(managed_root / "Inbox" / "song.mp3")["album"] == "Old Album"
    assert result["preparation_state"]["status"] == "UNSAVED"
    assert result["preparation_state"]["pending_fields"] == ["album"]


def test_missing_album_does_not_block_promotion(managed_root):
    track_id = _seed_track(managed_root, "song.mp3", album=None)
    preview = workspace_service.promotion_preview(managed_root, [track_id])
    assert preview["items"][0]["ready"] is True


def test_single_edit_records_user_provenance_for_each_changed_field(managed_root):
    track_id = _seed_track(managed_root, "song.mp3")
    workspace_service.edit_inbox_track_metadata(
        managed_root, track_id, artist="New Artist", title="New Title", genre="New Genre", album="New Album",
    )

    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT field_name, value, origin, source FROM field_provenance WHERE track_id = ? AND is_current = 1",
            (track_id,),
        ).fetchall()
    assert {tuple(row) for row in rows} == {
        ("artist", "New Artist", "user", "manual_edit"),
        ("title", "New Title", "user", "manual_edit"),
        ("genre", "New Genre", "user", "manual_edit"),
        ("album", "New Album", "user", "manual_edit"),
    }


def test_explicit_tag_write_sync_clears_unsaved_state(managed_root):
    track_id = _seed_track(managed_root, "song.mp3")
    workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="New Artist")
    assert workspace_service.inbox_preparation_states(managed_root, [track_id])[track_id]["status"] == "UNSAVED"

    from backend.app.services import tag_write_service
    plan = tag_write_service.build_plan([track_id])
    expected = {
        item["track_id"]: {
            "expected_size": item["expected_size"],
            "expected_mtime_ns": item["expected_mtime_ns"],
        }
        for item in plan["items"] if item["fields"]
    }
    tag_write_service.apply_plan([track_id], expected, confirm=True)
    state = workspace_service.inbox_preparation_states(managed_root, [track_id])[track_id]
    assert state["status"] == "READY"
    assert state["pending_fields"] == []


def test_edit_rejects_empty_value(managed_root):
    track_id = _seed_track(managed_root, "song.mp3")
    with pytest.raises(ValueError, match="empty"):
        workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="   ")


def test_validation_failure_leaves_all_metadata_unchanged(managed_root):
    track_id = _seed_track(managed_root, "song.mp3", artist="Old Artist", title="Old Title")
    with pytest.raises(ValueError, match="control character"):
        workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="New\x01Artist", title="New Title")
    row = _row(managed_root, track_id)
    assert row["artist"] == "Old Artist"
    assert row["title"] == "Old Title"


def test_edit_rejects_non_inbox_track(managed_root):
    track_id = _seed_track(managed_root, "promoted.mp3", zone="LIBRARY")
    with pytest.raises(ValueError, match="Inbox"):
        workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="New")


def test_edit_no_change_when_value_identical(managed_root):
    track_id = _seed_track(managed_root, "song.mp3", artist="Same Artist")
    result = workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="Same Artist")
    assert result["status"] == "no_change"
    assert result["tag_write"] is None
    assert result["preparation_state"]["status"] == "READY"
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='field_provenance'").fetchone() is None


def test_edit_preserves_unicode(managed_root):
    track_id = _seed_track(managed_root, "song.mp3")
    result = workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="Björk")
    assert result["artist"] == "Björk"
    assert _row(managed_root, track_id)["artist"] == "Björk"


def test_edit_external_original_unchanged(tmp_path, managed_root):
    source_file = tmp_path / "downloads" / "Track.mp3"
    _make_audio(source_file)
    original_bytes = source_file.read_bytes()

    workspace_service.import_sources(managed_root, [str(source_file)], confirm=True)
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        track_id = conn.execute("SELECT id FROM tracks WHERE storage_zone = 'INBOX'").fetchone()["id"]

    workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="New Artist")

    assert source_file.read_bytes() == original_bytes


def test_edit_refreshes_readiness(managed_root):
    # Use non-suspicious metadata so this regression isolates the missing
    # Genre transition under the preparation-state contract.
    track_id = _seed_track(managed_root, "song.mp3", artist="Artist", title="Title", genre="")
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute("UPDATE tracks SET genre = NULL WHERE id = ?", (track_id,))
        conn.commit()

    before = workspace_service.promotion_preview(managed_root, [track_id])["items"][0]
    assert before["ready"] is False
    assert any("Genre" in b for b in before["blockers"])

    workspace_service.edit_inbox_track_metadata(managed_root, track_id, genre="Afro House")

    after = workspace_service.promotion_preview(managed_root, [track_id])["items"][0]
    assert after["ready"] is False
    assert after["preparation_state"]["status"] == "UNSAVED"
    assert after["preparation_state"]["pending_fields"] == ["genre"]


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------

def test_patch_route_edits_all_supported_metadata_fields_together(managed_root):
    track_id = _seed_track(managed_root, "song.mp3")

    status, body = _api_request(
        "PATCH", f"/api/workspace/inbox/tracks/{track_id}",
        {"artist": "Black Coffee", "title": "New Title", "genre": "Afro House", "album": "New Album"},
    )
    assert status == 200
    assert body["metadata"]["status"] == "updated"
    assert set(body["metadata"]["fields_changed"]) == {"artist", "title", "genre", "album"}
    assert _read_tags(managed_root / "Inbox" / "song.mp3") == {"artist": "Old Artist", "title": "Some Title", "album": "", "genre": "Dance"}
    assert body["metadata"]["preparation_state"]["status"] == "UNSAVED"


def test_patch_route_rejects_blank_artist_with_422(managed_root):
    track_id = _seed_track(managed_root, "song.mp3")
    status, body = _api_request("PATCH", f"/api/workspace/inbox/tracks/{track_id}", {"artist": "   "})
    assert status == 422
    assert "empty" in body["detail"]


# ---------------------------------------------------------------------------
# Bulk edit
# ---------------------------------------------------------------------------

def _set(field: str, value: str) -> dict:
    return {field: {"operation": "set", "value": value}}


def test_bulk_edit_preview_shows_mixed_values_and_field_counts(managed_root):
    ids = [
        _seed_track(managed_root, "a.mp3", genre="Dance"),
        _seed_track(managed_root, "b.mp3", genre="Electro"),
        _seed_track(managed_root, "c.mp3", genre="Afro House"),
    ]
    preview = workspace_service.bulk_edit_preview(managed_root, ids, operations=_set("genre", "Afro House"))

    assert preview["selected_count"] == 3
    assert preview["eligible_count"] == 3
    assert preview["changeable_count"] == 2
    assert preview["fields"]["genre"]["value"] == "Afro House"
    assert preview["fields"]["genre"]["mixed"] is True
    assert preview["fields"]["genre"]["affected_count"] == 2
    assert preview["fields"]["genre"]["already_matching_count"] == 1
    assert set(preview["fields"]["genre"]["current_values"]) == {"Dance", "Electro", "Afro House"}


def test_bulk_edit_preview_requires_at_least_one_field(managed_root):
    ids = [_seed_track(managed_root, "a.mp3")]
    with pytest.raises(ValueError, match="at least one metadata change"):
        workspace_service.bulk_edit_preview(managed_root, ids, operations={"genre": {"operation": "leave"}})


def test_bulk_edit_preview_requires_selection(managed_root):
    with pytest.raises(ValueError, match="at least one track"):
        workspace_service.bulk_edit_preview(managed_root, [], operations=_set("genre", "Afro House"))


def test_bulk_edit_service_rejects_more_than_maximum_selection(managed_root):
    with pytest.raises(ValueError, match="limited to 200"):
        workspace_service.bulk_edit_preview(
            managed_root, list(range(1, 202)), operations=_set("genre", "Afro House"),
        )


def test_bulk_edit_apply_sets_genre_and_writes_through_verified_writer(managed_root):
    ids = [_seed_track(managed_root, f"t{i}.mp3", genre="Dance") for i in range(3)]

    result = workspace_service.bulk_edit_apply(managed_root, ids, operations=_set("genre", "Afro House"), confirm=True)

    assert result["selected_count"] == 3
    assert result["changed_count"] == 3
    assert result["succeeded_count"] == 3
    assert result["failed_count"] == 0
    assert result["tag_write"]["used_verified_writer"] is True
    assert result["tag_write"]["operation_ids"]
    for track_id, filename in zip(ids, ["t0.mp3", "t1.mp3", "t2.mp3"]):
        assert _row(managed_root, track_id)["genre"] == "Afro House"
        assert _row(managed_root, track_id)["artist"] == "Old Artist"
        assert _row(managed_root, track_id)["title"] == "Some Title"
        assert _read_tags(managed_root / "Inbox" / filename)["genre"] == "Afro House"
        item = next(item for item in result["results"] if item["track_id"] == track_id)
        assert item["preparation_state"]["pending_fields"] == []


def test_bulk_genre_write_does_not_flush_unreviewed_single_track_title_change(managed_root):
    track_id = _seed_track(managed_root, "song.mp3", title="File Title", genre="Dance")
    path = managed_root / "Inbox" / "song.mp3"
    workspace_service.edit_inbox_track_metadata(managed_root, track_id, title="Working Title")

    result = workspace_service.bulk_edit_apply(
        managed_root, [track_id], operations=_set("genre", "Afro House"), confirm=True,
    )

    assert result["succeeded_count"] == 1
    assert _row(managed_root, track_id)["title"] == "Working Title"
    tags = _read_tags(path)
    assert tags["genre"] == "Afro House"
    assert tags["title"] == "File Title", "bulk Genre preview must not authorize a pending Title write"
    assert result["results"][0]["preparation_state"]["pending_fields"] == ["title"]


def test_bulk_edit_apply_rejects_artist(managed_root):
    ids = [_seed_track(managed_root, f"t{i}.mp3", artist="Unknown") for i in range(3)]

    with pytest.raises(ValueError, match="Bulk Artist editing is prohibited"):
        workspace_service.bulk_edit_apply(managed_root, ids, operations=_set("artist", "Black Coffee"), confirm=True)
    for track_id in ids:
        assert _row(managed_root, track_id)["artist"] == "Unknown"


@pytest.mark.parametrize("field", ["title", "filename"])
def test_bulk_edit_apply_rejects_identity_fields(managed_root, field):
    ids = [_seed_track(managed_root, f"t{i}.mp3") for i in range(2)]

    with pytest.raises(ValueError, match="prohibited"):
        workspace_service.bulk_edit_apply(managed_root, ids, operations=_set(field, "Shared identity"), confirm=True)
    assert all(_row(managed_root, track_id)["title"] == "Some Title" for track_id in ids)


def test_bulk_edit_rejects_unsupported_field_and_operation(managed_root):
    ids = [_seed_track(managed_root, "a.mp3")]
    with pytest.raises(ValueError, match="Unsupported bulk metadata field"):
        workspace_service.bulk_edit_preview(managed_root, ids, operations=_set("album", "Compilation"))
    with pytest.raises(ValueError, match="not valid for Label"):
        workspace_service.bulk_edit_preview(
            managed_root, ids, operations={"label": {"operation": "append", "value": "Label"}},
        )


def test_bulk_edit_apply_requires_confirm(managed_root):
    ids = [_seed_track(managed_root, "a.mp3")]
    with pytest.raises(ValueError, match="confirm=true"):
        workspace_service.bulk_edit_apply(managed_root, ids, operations=_set("genre", "Afro House"), confirm=False)


def test_bulk_edit_apply_rejects_no_field_selected(managed_root):
    ids = [_seed_track(managed_root, "a.mp3")]
    with pytest.raises(ValueError, match="at least one metadata change"):
        workspace_service.bulk_edit_apply(managed_root, ids, operations={"genre": {"operation": "leave"}}, confirm=True)


def test_bulk_edit_apply_rejects_empty_selection(managed_root):
    with pytest.raises(ValueError, match="at least one track"):
        workspace_service.bulk_edit_apply(managed_root, [], operations=_set("genre", "Afro House"), confirm=True)


def test_bulk_edit_apply_skips_non_inbox_tracks_truthfully(managed_root):
    inbox_id = _seed_track(managed_root, "inbox.mp3", genre="Dance")
    library_id = _seed_track(managed_root, "promoted.mp3", zone="LIBRARY", genre="Dance")

    result = workspace_service.bulk_edit_apply(
        managed_root, [inbox_id, library_id], operations=_set("genre", "Afro House"), confirm=True,
    )

    assert result["succeeded_count"] == 1
    assert result["skipped_count"] == 1
    by_id = {r["track_id"]: r for r in result["results"]}
    assert by_id[library_id]["status"] == "skipped"
    assert _row(managed_root, library_id)["genre"] == "Dance", "non-Inbox track must never be modified"


def test_bulk_edit_apply_counts_true_no_ops(managed_root):
    already = _seed_track(managed_root, "already.mp3", genre="Afro House")
    changed = _seed_track(managed_root, "changed.mp3", genre="Dance")

    result = workspace_service.bulk_edit_apply(
        managed_root, [already, changed], operations=_set("genre", "Afro House"), confirm=True,
    )

    assert result["unchanged_count"] == 1
    assert result["succeeded_count"] == 1
    by_id = {r["track_id"]: r for r in result["results"]}
    assert by_id[already]["status"] == "unchanged"


def test_bulk_edit_apply_rejects_unknown_track_without_changing_selected_track(managed_root):
    inbox_id = _seed_track(managed_root, "inbox.mp3", genre="Dance")
    with pytest.raises(ValueError, match="active library"):
        workspace_service.bulk_edit_apply(
            managed_root, [inbox_id, 999999], operations=_set("genre", "Afro House"), confirm=True,
        )
    assert _row(managed_root, inbox_id)["genre"] == "Dance"


def test_bulk_edit_apply_skips_unsupported_without_affecting_supported_track(managed_root):
    good_id = _seed_track(managed_root, "good.mp3", genre="Dance")
    bad_path = managed_root / "Inbox" / "bad.wav"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_bytes(b"not real audio")
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, 'bad.wav', 'A', 'T', 'Dance', 'pending',
                       '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(bad_path),),
        )
        bad_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    result = workspace_service.bulk_edit_apply(
        managed_root, [good_id, bad_id], operations=_set("genre", "Afro House"), confirm=True,
    )

    by_id = {r["track_id"]: r for r in result["results"]}
    assert by_id[good_id]["status"] == "succeeded"
    assert by_id[bad_id]["status"] == "skipped"
    assert by_id[bad_id]["preparation_state"]["status"] == "WRITE_BLOCKED"
    assert result["succeeded_count"] == 1
    assert result["skipped_count"] == 1
    assert result["failed_count"] == 0
    assert _row(managed_root, good_id)["genre"] == "Afro House"


def test_bulk_edit_apply_external_originals_unchanged(tmp_path, managed_root):
    source_files = []
    for i in range(3):
        f = tmp_path / "downloads" / f"track{i}.mp3"
        _make_audio(f)
        source_files.append(f)
    originals = {f: f.read_bytes() for f in source_files}

    workspace_service.import_sources(managed_root, [str(f) for f in source_files], confirm=True)
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        ids = [r["id"] for r in conn.execute("SELECT id FROM tracks WHERE storage_zone = 'INBOX'")]

    workspace_service.bulk_edit_apply(managed_root, ids, operations=_set("genre", "Afro House"), confirm=True)

    for f, original in originals.items():
        assert f.read_bytes() == original


def test_bulk_comment_set_append_label_set_and_clear_semantics(managed_root):
    ids = [_seed_track(managed_root, f"t{i}.mp3") for i in range(2)]
    result = workspace_service.bulk_edit_apply(
        managed_root, ids,
        operations={
            "comment": {"operation": "set", "value": "Warm-up"},
            "label": {"operation": "set", "value": "Soulistic"},
        }, confirm=True,
    )
    assert result["succeeded_count"] == 2
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        assert all(row["comment"] == "Warm-up" and row["label"] == "Soulistic" for row in conn.execute("SELECT comment, label FROM tracks"))

    workspace_service.bulk_edit_apply(
        managed_root, ids,
        operations={"comment": {"operation": "append", "value": "Peak"}, "label": {"operation": "clear"}},
        confirm=True,
    )
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT comment, label FROM tracks ORDER BY id").fetchall()
    assert all(row["comment"] == "Warm-up\nPeak" and row["label"] is None for row in rows)

    workspace_service.bulk_edit_apply(
        managed_root, ids,
        operations={
            "genre": {"operation": "clear"},
            "comment": {"operation": "clear"},
            "label": {"operation": "clear"},
        }, confirm=True,
    )
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        cleared = conn.execute("SELECT genre, comment, label FROM tracks ORDER BY id").fetchall()
    assert all(row["genre"] is None and row["comment"] is None and row["label"] is None for row in cleared)


def test_bulk_edit_rejects_track_path_outside_active_workspace(tmp_path, managed_root):
    inside_id = _seed_track(managed_root, "inside.mp3", genre="Dance")
    outside_path = tmp_path / "outside-library" / "outside.mp3"
    _make_audio(outside_path)
    _write_tags(outside_path, artist="Outside Artist", title="Outside Title", genre="Dance")
    original_bytes = outside_path.read_bytes()
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, 'outside.mp3', 'Outside Artist', 'Outside Title', 'Dance', 'pending',
                       '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(outside_path),),
        )
        outside_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    result = workspace_service.bulk_edit_apply(
        managed_root, [inside_id, outside_id], operations=_set("genre", "Afro House"), confirm=True,
    )

    by_id = {item["track_id"]: item for item in result["results"]}
    assert by_id[inside_id]["status"] == "succeeded"
    assert by_id[outside_id]["status"] == "skipped"
    assert _row(managed_root, outside_id)["genre"] == "Dance"
    assert outside_path.read_bytes() == original_bytes


def test_bulk_leave_unchanged_is_ignored_when_another_field_changes(managed_root):
    track_id = _seed_track(managed_root, "a.mp3", genre="House")
    workspace_service.bulk_edit_apply(
        managed_root, [track_id],
        operations={
            "genre": {"operation": "leave"},
            "comment": {"operation": "set", "value": "Warm-up"},
        }, confirm=True,
    )
    row = _row(managed_root, track_id)
    assert row["genre"] == "House"
    assert row["comment"] == "Warm-up"


def test_bulk_edit_reports_per_track_writer_failure_without_stopping_others(managed_root, monkeypatch):
    first = _seed_track(managed_root, "first.mp3", genre="House")
    second = _seed_track(managed_root, "second.mp3", genre="House")

    def fake_write(track_ids, *, allowed_fields=None):
        assert allowed_fields == {"genre"}
        return {
            "operation_ids": ["op-1"],
            "results": [
                {"track_id": first, "status": "applied"},
                {"track_id": second, "status": "failed", "reason": "Verification mismatch; backup preserved."},
            ],
        }

    monkeypatch.setattr(preparation_service, "write_tracks", fake_write)
    result = workspace_service.bulk_edit_apply(
        managed_root, [first, second], operations=_set("genre", "Afro House"), confirm=True,
    )

    by_id = {item["track_id"]: item for item in result["results"]}
    assert result["succeeded_count"] == 1
    assert result["failed_count"] == 1
    assert by_id[first]["status"] == "succeeded"
    assert by_id[second]["status"] == "failed"
    assert "backup preserved" in by_id[second]["reason"]
    assert by_id[second]["preparation_state"]["status"] == "UNSAVED"


def test_bulk_edit_reports_raised_writer_failure_for_every_changed_track(managed_root, monkeypatch):
    ids = [_seed_track(managed_root, f"track-{index}.mp3", genre="House") for index in range(2)]
    from backend.app.services import tag_write_service

    def failed_write(*args, **kwargs):
        raise RuntimeError("operation store unavailable")

    monkeypatch.setattr(tag_write_service, "apply_plan", failed_write)
    result = workspace_service.bulk_edit_apply(
        managed_root, ids, operations=_set("genre", "Afro House"), confirm=True,
    )

    assert result["failed_count"] == 2
    assert result["succeeded_count"] == 0
    assert all(item["status"] == "failed" for item in result["results"])
    assert all("Working metadata remains unsaved" in item["reason"] for item in result["results"])
    assert all(item["preparation_state"]["status"] == "UNSAVED" for item in result["results"])


# ---------------------------------------------------------------------------
# HTTP routes for bulk edit
# ---------------------------------------------------------------------------

def test_bulk_edit_preview_route(managed_root):
    ids = [_seed_track(managed_root, f"t{i}.mp3", genre="Dance") for i in range(3)]
    status, body = _api_request(
        "POST", "/api/workspace/inbox/bulk-edit/preview",
        {"track_ids": ids, "operations": _set("genre", "Afro House")},
    )
    assert status == 200
    assert body["selected_count"] == 3


def test_bulk_edit_apply_route_requires_confirm(managed_root):
    ids = [_seed_track(managed_root, "a.mp3")]
    status, body = _api_request(
        "POST", "/api/workspace/inbox/bulk-edit/apply",
        {"track_ids": ids, "operations": _set("genre", "Afro House")},
    )
    assert status == 422
    assert "confirm=true" in body["detail"]


def test_bulk_edit_apply_route_rejects_bulk_identity_fields(managed_root):
    ids = [_seed_track(managed_root, f"t{i}.mp3", artist="Old") for i in range(3)]

    for field, value in (
        ("title", "Duplicate title"),
        ("filename", "same.mp3"),
        ("artist", "Shared artist"),
    ):
        status, body = _api_request(
            "POST", "/api/workspace/inbox/bulk-edit/apply",
            {"track_ids": ids, "operations": _set(field, value), "confirm": True},
        )
        assert status == 422
        assert "prohibited" in body["detail"]
    assert all(_row(managed_root, track_id)["title"] == "Some Title" for track_id in ids)


def test_bulk_edit_route_rejects_more_than_maximum_selection():
    status, body = _api_request(
        "POST", "/api/workspace/inbox/bulk-edit/apply",
        {"track_ids": list(range(1, 202)), "operations": _set("genre", "Afro House"), "confirm": True},
    )
    assert status == 422
    assert body["detail"][0]["type"] == "too_long"


@pytest.mark.parametrize(
    "payload,error_type",
    [
        ({"track_ids": [0], "operations": _set("genre", "Afro House")}, "greater_than"),
        ({"track_ids": [1], "operations": _set("genre", "Afro House"), "unexpected": True}, "extra_forbidden"),
        ({"track_ids": [1], "operations": {"genre": {"operation": "set", "value": "Afro House", "unexpected": True}}}, "extra_forbidden"),
        ({"track_ids": [1], "operations": {"genre": {"operation": "merge", "value": "Afro House"}}}, "literal_error"),
    ],
)
def test_bulk_edit_route_rejects_invalid_request_shapes(payload, error_type):
    status, body = _api_request("POST", "/api/workspace/inbox/bulk-edit/preview", payload)
    assert status == 422
    assert body["detail"][0]["type"] == error_type


# ---------------------------------------------------------------------------
# Manual-edit precedence regression (section 9)
# ---------------------------------------------------------------------------

def test_manual_artist_survives_process_all_enrich(managed_root, monkeypatch):
    track_id = _seed_track(managed_root, "song.mp3", artist="Old Artist", title="A Title", genre="")
    workspace_service.edit_inbox_track_metadata(managed_root, track_id, artist="My Deliberate Artist")

    def _fake_gather_evidence(*args, **kwargs):
        raise AssertionError("must not be called when nothing is junk/empty for this track")

    # Genre is still empty so the track remains eligible for enrichment
    # consideration, but the manually-set Artist is non-empty and non-junk,
    # so the existing "never overwrite existing non-empty data" rule in
    # preparation_service.enrich_tracks must leave it exactly as-is even if
    # a provider strongly disagrees.
    from backend.app.services import provider_routing_service, consensus_service

    class _FieldConsensus:
        def __init__(self, value, confidence):
            self.value = value
            self.confidence = confidence
            self.reason_code = "matched"
            self.evidence = []

    class _Consensus:
        fields = {
            "artist": _FieldConsensus("Someone Else Entirely", "HIGH"),
            "title": _FieldConsensus(None, "LOW"),
            "genre": _FieldConsensus("Afro House", "HIGH"),
        }

    monkeypatch.setattr(provider_routing_service, "gather_evidence", lambda *a, **k: {})
    monkeypatch.setattr(consensus_service, "build_track_consensus", lambda *a, **k: _Consensus())

    preparation_service.enrich_tracks(managed_root, [track_id])

    assert _row(managed_root, track_id)["artist"] == "My Deliberate Artist", (
        "a manually-entered non-empty, non-junk Artist must survive Process All's enrichment stage"
    )


def test_manual_genre_survives_process_all_enrich(managed_root, monkeypatch):
    track_id = _seed_track(managed_root, "song.mp3", artist="Some Artist", title="A Title", genre="")
    workspace_service.edit_inbox_track_metadata(managed_root, track_id, genre="My Deliberate Genre")

    from backend.app.services import provider_routing_service, consensus_service

    class _FieldConsensus:
        def __init__(self, value, confidence):
            self.value = value
            self.confidence = confidence
            self.reason_code = "matched"
            self.evidence = []

    class _Consensus:
        fields = {
            "artist": _FieldConsensus(None, "LOW"),
            "title": _FieldConsensus(None, "LOW"),
            "genre": _FieldConsensus("Some Provider Genre", "HIGH"),
        }

    monkeypatch.setattr(provider_routing_service, "gather_evidence", lambda *a, **k: {})
    monkeypatch.setattr(consensus_service, "build_track_consensus", lambda *a, **k: _Consensus())

    preparation_service.enrich_tracks(managed_root, [track_id])

    assert _row(managed_root, track_id)["genre"] == "My Deliberate Genre", (
        "a manually-entered non-empty, non-junk Genre must survive Process All's enrichment stage"
    )
