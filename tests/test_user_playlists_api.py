from __future__ import annotations

import asyncio
import sqlite3

import httpx

import backend.app.main as backend_main


def _tracks_db(root):
    path = root / "logs" / "processed.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, artist TEXT, title TEXT, genre TEXT, bpm REAL, key_musical TEXT, key_camelot TEXT, duration_sec REAL, bitrate_kbps INTEGER, status TEXT, quality_tier TEXT, parse_confidence TEXT, storage_zone TEXT)"
        )
        conn.executemany(
            "INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, str(root / "Library" / "a.mp3"), "a.mp3", "Alpha", "First", "House", 120, "A minor", "8A", 180, 320, "ok", "HIGH", "HIGH", "LIBRARY"),
                (2, str(root / "Library" / "b.mp3"), "b.mp3", "Beta", "Second", "House", 124, "B minor", "10A", 190, 320, "ok", "HIGH", "HIGH", "LIBRARY"),
            ],
        )


def test_user_playlist_http_contract_without_startup_lifespan(tmp_path, monkeypatch):
    root = tmp_path / "library"
    _tracks_db(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))

    async def exercise():
        transport = httpx.ASGITransport(app=backend_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/user-playlists", json={"name": "Warm Up", "description": "First hour"})
            assert created.status_code == 201
            playlist_id = created.json()["id"]
            assert (await client.post("/api/user-playlists", json={"name": " warm up "})).status_code == 409
            assert (await client.post(f"/api/user-playlists/{playlist_id}/tracks/preview", json={"track_ids": [1, 2]})).json()["will_add_count"] == 2
            assert (await client.post(f"/api/user-playlists/{playlist_id}/tracks", json={"track_ids": [1, 2]})).status_code == 409
            applied = await client.post(f"/api/user-playlists/{playlist_id}/tracks", json={"track_ids": [1, 2], "confirm": True})
            assert applied.status_code == 201
            assert applied.json()["added_count"] == 2
            detail = await client.get(f"/api/user-playlists/{playlist_id}", params={"sort": "title"})
            assert [track["track_id"] for track in detail.json()["tracks"]] == [1, 2]
            assert (await client.delete(f"/api/user-playlists/{playlist_id}/tracks/1")).status_code == 200
            assert (await client.get("/api/tracks/1")).status_code == 200

    asyncio.run(exercise())
