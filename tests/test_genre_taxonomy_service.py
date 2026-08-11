"""
Genre Intelligence Phase 3: config schema/load, deterministic resolver
precedence, preview/apply contract, provenance, and route wiring.

Section A: repository config schema validation (genre_taxonomy_service).
Section B: deterministic resolver precedence -- canonical exact match,
           repository default mapping, user mapping override, disabled
           mapping/taxonomy, ambiguous/unmapped -> Needs Review.
Section C: normalize_key -- casefold/whitespace/hyphen/punctuation, pinned.
Section D: preview/apply contract via the service directly (read-only
           preview, explicit scoped apply, raw preserved, provenance
           written, idempotent re-apply, no BPM/key/tag changes).
Section E: /api/genres/* route wiring (confirm required, API compatibility).
Section F: Needs Review lifecycle -- applying a normalized genre clears its
           own pending metadata_repair_queue_service/needs_review_service entry.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.services import (
    field_provenance_service,
    genre_taxonomy_service as svc,
    metadata_repair_queue_service,
    needs_review_service,
)


def _create_tracks_db(root: Path) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            artist TEXT,
            title TEXT,
            genre TEXT,
            bpm REAL,
            key_musical TEXT,
            key_camelot TEXT,
            status TEXT NOT NULL DEFAULT 'ok'
        )
        """
    )
    conn.executemany(
        "INSERT INTO tracks (filepath, filename, artist, title, genre, bpm, key_musical, key_camelot) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("/x/a.mp3", "a.mp3", "Artist A", "Title A", "afrohouse", 124.0, "Am", "8A"),
            ("/x/b.mp3", "b.mp3", "Artist B", "Title B", "afro", 120.0, "Cm", "5A"),
            ("/x/c.mp3", "c.mp3", "Artist C", "Title C", "House", 122.0, "Gm", "6A"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    db_path = _create_tracks_db(root)
    return root, db_path


@pytest.fixture()
def conn(db_env):
    _, db_path = db_env
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# Section A: config schema/load
# ---------------------------------------------------------------------------


def test_repo_config_loads_and_validates():
    taxonomy = svc.repo_taxonomy()
    mappings = svc.repo_mappings()
    assert any(g["name"] == "Afro House" for g in taxonomy)
    assert any(m["raw_genre"] == "afrohouse" and m["normalized_genre"] == "Afro House" for m in mappings)
    # Deterministic order: same as file order every call (cached).
    assert svc.repo_taxonomy() == taxonomy


def test_taxonomy_schema_rejects_missing_name():
    with pytest.raises(ValueError):
        svc.validate_taxonomy_config({"genres": [{"parent_name": None}]})


def test_taxonomy_schema_rejects_duplicate_name():
    with pytest.raises(ValueError):
        svc.validate_taxonomy_config({"genres": [{"name": "House"}, {"name": "house"}]})


def test_mappings_schema_requires_target_unless_needs_review():
    with pytest.raises(ValueError):
        svc.validate_mappings_config({"mappings": [{"raw_genre": "dance"}]})
    # OK when needs_review is explicit.
    result = svc.validate_mappings_config({"mappings": [{"raw_genre": "dance", "needs_review": True}]})
    assert result[0]["needs_review"] is True
    assert result[0]["normalized_genre"] is None


def test_mappings_schema_rejects_invalid_confidence():
    with pytest.raises(ValueError):
        svc.validate_mappings_config(
            {"mappings": [{"raw_genre": "x", "normalized_genre": "House", "confidence": "extreme"}]}
        )


def test_repository_config_rejects_duplicate_normalized_keys():
    with pytest.raises(ValueError):
        svc.validate_taxonomy_config({"genres": [{"name": "My House"}, {"name": "my_house"}]})
    with pytest.raises(ValueError):
        svc.validate_mappings_config(
            {
                "mappings": [
                    {"raw_genre": "Afro-House", "normalized_genre": "Afro Tech"},
                    {"raw_genre": "Afro House", "normalized_genre": "Afro Tech"},
                ]
            }
        )


# ---------------------------------------------------------------------------
# Section B: deterministic resolver precedence
# ---------------------------------------------------------------------------


def test_resolve_canonical_exact_match(conn):
    svc.ensure_tables(conn)
    result = svc.resolve_genre(conn, "house")
    assert result.normalized_genre == "House"
    assert result.confidence == "high"
    assert result.source == "preferred_taxonomy_exact"
    assert result.needs_review is False


def test_resolve_repository_default_mapping(conn):
    svc.ensure_tables(conn)
    result = svc.resolve_genre(conn, "afrohouse")
    assert result.normalized_genre == "Afro House"
    assert result.source == "repository_default_mapping"
    assert result.needs_review is False


def test_resolve_ambiguous_repo_default_needs_review_no_guess(conn):
    svc.ensure_tables(conn)
    result = svc.resolve_genre(conn, "afro")
    assert result.normalized_genre is None
    assert result.needs_review is True
    assert result.source == "repository_default_needs_review"


def test_resolve_unmapped_needs_review_no_guess(conn):
    svc.ensure_tables(conn)
    result = svc.resolve_genre(conn, "some-totally-unknown-tag-xyz")
    assert result.normalized_genre is None
    assert result.needs_review is True
    assert result.source == "unmapped"


def test_user_mapping_overrides_repository_default(conn):
    svc.ensure_tables(conn)
    now = svc.now()
    conn.execute(
        "INSERT INTO genre_mappings(raw_genre,normalized_genre,confidence,source,enabled,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("afrohouse", "Afro Tech", "manual", "user_mapping", 1, now, now),
    )
    result = svc.resolve_genre(conn, "AfroHouse")
    assert result.normalized_genre == "Afro Tech"
    assert result.source == "user_mapping"


@pytest.mark.parametrize(
    "variant",
    ["Afro-House", "Afro House", "afro_house", "  AFRO   HOUSE  ", "aFrO hOuSe"],
)
def test_user_mapping_normalization_variants_share_one_contract(conn, variant):
    svc.ensure_tables(conn)
    now = svc.now()
    conn.execute(
        "INSERT INTO genre_mappings(raw_genre,normalized_genre,confidence,source,enabled,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("Afro-House", "Afro Tech", "manual", "user_mapping", 1, now, now),
    )
    conn.execute("UPDATE tracks SET genre = ? WHERE id = 1", (variant,))

    resolution = svc.resolve_genre(conn, variant)
    preview = {item["track_id"]: item for item in svc.build_preview_items(conn)}

    assert resolution.normalized_genre == "Afro Tech"
    assert resolution.source == "user_mapping"
    assert resolution.needs_review is False
    assert preview[1]["raw_genre"] == variant.strip()
    assert preview[1]["suggested_genre"] == "Afro Tech"
    assert preview[1]["source"] == "user_mapping"


def test_user_mapping_can_explicitly_override_ambiguous_label(conn):
    """An explicit user mapping may collapse a repo-ambiguous label; the repo
    default alone never does (see test_resolve_ambiguous_repo_default_needs_review_no_guess)."""
    svc.ensure_tables(conn)
    now = svc.now()
    conn.execute(
        "INSERT INTO genre_mappings(raw_genre,normalized_genre,confidence,source,enabled,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("afro", "Afro House", "manual", "user_mapping", 1, now, now),
    )
    result = svc.resolve_genre(conn, "afro")
    assert result.normalized_genre == "Afro House"
    assert result.source == "user_mapping"
    assert result.needs_review is False


def test_disabled_user_mapping_falls_back_to_needs_review(conn):
    svc.ensure_tables(conn)
    now = svc.now()
    conn.execute(
        "INSERT INTO genre_mappings(raw_genre,normalized_genre,confidence,source,enabled,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("afrohouse", "Afro Tech", "manual", "user_mapping", 0, now, now),
    )
    result = svc.resolve_genre(conn, "afrohouse")
    assert result.normalized_genre is None
    assert result.needs_review is True
    assert result.source == "user_mapping_disabled"


def test_disabling_taxonomy_entry_removes_it_as_a_valid_target(conn):
    svc.ensure_tables(conn)
    now = svc.now()
    # Disable "House" via a local override row.
    conn.execute(
        "INSERT INTO genre_taxonomy(name,parent_name,description,enabled,sort_order,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("House", None, None, 0, 100, now, now),
    )
    result = svc.resolve_genre(conn, "house")
    assert result.normalized_genre is None
    assert result.needs_review is True


def test_legacy_duplicate_user_mapping_rows_fail_closed(conn):
    svc.ensure_tables(conn)
    now = svc.now()
    conn.execute(
        "INSERT INTO genre_mappings(raw_genre,normalized_genre,confidence,source,enabled,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("Afro-House", "Afro Tech", "manual", "user_mapping", 1, now, now),
    )
    conn.execute(
        "INSERT INTO genre_mappings(raw_genre,normalized_genre,confidence,source,enabled,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("Afro House", "Afro House", "manual", "user_mapping", 1, now, now),
    )
    result = svc.resolve_genre(conn, "afro house")
    repeat = svc.resolve_genre(conn, "AFRO-HOUSE")
    assert result.normalized_genre is None
    assert result.needs_review is True
    assert result.source == "user_mapping_ambiguous"
    assert repeat.source == result.source
    assert repeat.normalized_genre == result.normalized_genre


def test_legacy_duplicate_taxonomy_rows_fail_closed(conn):
    svc.ensure_tables(conn)
    now = svc.now()
    conn.execute(
        "INSERT INTO genre_taxonomy(name,parent_name,description,enabled,sort_order,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("My House", None, None, 1, 100, now, now),
    )
    conn.execute(
        "INSERT INTO genre_taxonomy(name,parent_name,description,enabled,sort_order,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("my-house", None, None, 1, 101, now, now),
    )
    result = svc.resolve_genre(conn, "My House")
    repeat = svc.resolve_genre(conn, "my_house")
    assert result.normalized_genre is None
    assert result.needs_review is True
    assert result.source == "taxonomy_ambiguous"
    assert repeat.source == result.source


def test_disabling_does_not_rewrite_existing_track_data(conn):
    """Disabling a taxonomy/mapping entry only affects future resolution -- it
    must never touch already-stored track columns."""
    svc.ensure_tables(conn)
    row_before = conn.execute("SELECT genre, normalized_genre FROM tracks WHERE id=1").fetchone()
    now = svc.now()
    conn.execute(
        "INSERT INTO genre_taxonomy(name,parent_name,description,enabled,sort_order,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("Afro House", None, None, 0, 100, now, now),
    )
    row_after = conn.execute("SELECT genre, normalized_genre FROM tracks WHERE id=1").fetchone()
    assert tuple(row_before) == tuple(row_after)


# ---------------------------------------------------------------------------
# Section C: normalize_key -- pinned casefold/whitespace/hyphen/punctuation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("House", "house"),
        ("  House  ", "house"),
        ("Deep-House", "deep house"),
        ("Deep_House", "deep house"),
        ("Deep   House", "deep house"),
        ("Hip-Hop!", "hip hop"),
        ("R&B", "r&b"),
        ("Afro/House", "afro house"),
    ],
)
def test_normalize_key_pinned(raw, expected):
    assert svc.normalize_key(raw) == expected


# ---------------------------------------------------------------------------
# Section D: preview/apply contract
# ---------------------------------------------------------------------------


def test_preview_is_read_only_with_respect_to_track_values(conn):
    cols = "id, filepath, filename, artist, title, genre, bpm, key_musical, key_camelot, status"
    before = [dict(r) for r in conn.execute(f"SELECT {cols} FROM tracks ORDER BY id")]
    items = svc.build_preview_items(conn)
    after = [dict(r) for r in conn.execute(f"SELECT {cols} FROM tracks ORDER BY id")]
    assert before == after
    assert len(items) == 3
    ids = {i["track_id"] for i in items}
    assert ids == {1, 2, 3}


def test_preview_items_carry_source_confidence_reason_no_guess(conn):
    items = {i["track_id"]: i for i in svc.build_preview_items(conn)}
    assert items[1]["suggested_genre"] == "Afro House"
    assert items[1]["source"] == "repository_default_mapping"
    assert items[1]["needs_review"] is False
    assert items[2]["suggested_genre"] == svc.NEEDS_REVIEW
    assert items[2]["needs_review"] is True
    assert items[3]["suggested_genre"] == "House"
    assert items[3]["source"] == "preferred_taxonomy_exact"


def test_apply_writes_normalized_genre_preserves_raw_and_records_provenance(conn):
    items = svc.build_preview_items(conn)
    svc.save_preview_snapshot(conn, items)
    result = svc.apply_selected(conn, [1, 3])
    assert result["applied"] == 2
    assert result["skipped"] == 0

    row1 = conn.execute("SELECT genre, normalized_genre, bpm, key_musical FROM tracks WHERE id=1").fetchone()
    assert row1["genre"] == "afrohouse"  # raw preserved
    assert row1["normalized_genre"] == "Afro House"
    assert row1["bpm"] == 124.0  # BPM untouched
    assert row1["key_musical"] == "Am"  # key untouched

    prov = field_provenance_service.current_for_track(1, conn=conn)
    assert prov["normalized_genre"]["value"] == "Afro House"
    assert prov["normalized_genre"]["origin"] == "system"
    assert prov["normalized_genre"]["confidence"] is None  # never masquerades as provider confidence


def test_apply_skips_needs_review_items_and_does_not_write(conn):
    items = svc.build_preview_items(conn)
    svc.save_preview_snapshot(conn, items)
    result = svc.apply_selected(conn, [2])
    assert result["applied"] == 0
    assert result["skipped"] == 1
    row2 = conn.execute("SELECT normalized_genre FROM tracks WHERE id=2").fetchone()
    assert row2["normalized_genre"] is None


def test_apply_scope_is_exactly_the_selected_ids(conn):
    """Selecting track 1 must never widen to touch track 3, even though both
    are eligible in the snapshot."""
    items = svc.build_preview_items(conn)
    svc.save_preview_snapshot(conn, items)
    svc.apply_selected(conn, [1])
    row3 = conn.execute("SELECT normalized_genre FROM tracks WHERE id=3").fetchone()
    assert row3["normalized_genre"] is None


def test_repeated_apply_is_idempotent_no_provenance_duplication(conn):
    items = svc.build_preview_items(conn)
    svc.save_preview_snapshot(conn, items)
    svc.apply_selected(conn, [1])
    svc.apply_selected(conn, [1])
    history = field_provenance_service.list_for_track(1, include_history=True, conn=conn)
    normalized_rows = [r for r in history if r["field_name"] == "normalized_genre"]
    assert len(normalized_rows) == 1
    assert normalized_rows[0]["is_current"] is True


def test_apply_without_snapshot_raises():
    conn2 = sqlite3.connect(":memory:")
    conn2.execute(
        "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, artist TEXT, title TEXT, "
        "genre TEXT, normalized_genre TEXT, genre_source TEXT, genre_reviewed_at TEXT, genre_updated_at TEXT)"
    )
    with pytest.raises(ValueError):
        svc.apply_selected(conn2, [1])
    conn2.close()


# ---------------------------------------------------------------------------
# Section E: route wiring
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(db_env):
    import backend.app.main as backend_main
    return TestClient(backend_main.app)


def test_route_taxonomy_and_review_contract(client, db_env):
    r = client.get("/api/genres/taxonomy")
    assert r.status_code == 200
    assert "genres" in r.json()
    assert any(g["name"] == "House" for g in r.json()["genres"])

    r = client.get("/api/genres/review")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"summary", "items", "safety"}


def test_route_apply_requires_confirm(client, db_env):
    client.post("/api/genres/review/preview-refresh")
    r = client.post("/api/genres/review/apply", json={"confirm": False, "track_ids": [1]})
    assert r.status_code == 422


def test_route_preview_then_apply_end_to_end(client, db_env):
    preview = client.post("/api/genres/review/preview-refresh").json()
    ids = [i["track_id"] for i in preview["items"] if i["suggested_genre"] != svc.NEEDS_REVIEW]
    r = client.post("/api/genres/review/apply", json={"confirm": True, "track_ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] == len(ids)
    assert "Raw genre is preserved" in body["warnings"][0]


def test_route_mapping_allows_explicit_ambiguous_override(client, db_env):
    r = client.post(
        "/api/genres/mappings",
        json={"raw_genre": "dance", "normalized_genre": "House", "confidence": "manual", "source": "user_mapping"},
    )
    assert r.status_code == 200
    mappings = r.json()["mappings"]
    dance = next(m for m in mappings if m["raw_genre"] == "dance")
    assert dance["normalized_genre"] == "House"


def test_route_rejects_duplicate_mapping_normalized_key(client, db_env):
    first = client.post(
        "/api/genres/mappings",
        json={"raw_genre": "Afro-House", "normalized_genre": "Afro Tech", "confidence": "manual", "source": "user_mapping"},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/genres/mappings",
        json={"raw_genre": "Afro House", "normalized_genre": "Afro Tech", "confidence": "manual", "source": "user_mapping"},
    )
    assert second.status_code == 422


def test_route_rejects_duplicate_taxonomy_normalized_key(client, db_env):
    first = client.post("/api/genres/taxonomy", json={"name": "My House"})
    assert first.status_code == 200
    second = client.post("/api/genres/taxonomy", json={"name": "my-house"})
    assert second.status_code == 422


def test_route_mapping_update_allows_self_update_but_rejects_colliding_update(client, db_env):
    _, db_path = db_env
    assert (
        client.post(
            "/api/genres/mappings",
            json={"raw_genre": "Afro-House", "normalized_genre": "Afro Tech", "confidence": "manual", "source": "user_mapping"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/genres/mappings",
            json={"raw_genre": "Deep House", "normalized_genre": "House", "confidence": "manual", "source": "user_mapping"},
        ).status_code
        == 200
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        a_id = conn.execute("SELECT id FROM genre_mappings WHERE raw_genre = ?", ("Afro-House",)).fetchone()["id"]
        b_id = conn.execute("SELECT id FROM genre_mappings WHERE raw_genre = ?", ("Deep House",)).fetchone()["id"]

    allowed_body = {
        "raw_genre": "afro-house",
        "normalized_genre": "Afro Tech",
        "confidence": "manual",
        "source": "user_mapping",
        "enabled": True,
    }
    allowed = client.patch(f"/api/genres/mappings/{a_id}", json=allowed_body)
    assert allowed.status_code == 200
    repeat_allowed = client.patch(f"/api/genres/mappings/{a_id}", json=allowed_body)
    assert repeat_allowed.status_code == 200

    collision_body = {
        "raw_genre": "afro house",
        "normalized_genre": "House",
        "confidence": "manual",
        "source": "user_mapping",
        "enabled": True,
    }
    rejected = client.patch(f"/api/genres/mappings/{b_id}", json=collision_body)
    assert rejected.status_code == 422
    repeat_rejected = client.patch(f"/api/genres/mappings/{b_id}", json=collision_body)
    assert repeat_rejected.status_code == 422

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        a_row = conn.execute("SELECT raw_genre, normalized_genre FROM genre_mappings WHERE id = ?", (a_id,)).fetchone()
        b_row = conn.execute("SELECT raw_genre, normalized_genre FROM genre_mappings WHERE id = ?", (b_id,)).fetchone()

    assert a_row["raw_genre"] == "afro-house"
    assert a_row["normalized_genre"] == "Afro Tech"
    assert b_row["raw_genre"] == "Deep House"
    assert b_row["normalized_genre"] == "House"


def test_route_taxonomy_update_allows_self_update_but_rejects_colliding_update(client, db_env):
    _, db_path = db_env
    assert client.post("/api/genres/taxonomy", json={"name": "Tribal House"}).status_code == 200
    assert client.post("/api/genres/taxonomy", json={"name": "Minimal House"}).status_code == 200

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        a_id = conn.execute("SELECT id FROM genre_taxonomy WHERE name = ?", ("Tribal House",)).fetchone()["id"]
        b_id = conn.execute("SELECT id FROM genre_taxonomy WHERE name = ?", ("Minimal House",)).fetchone()["id"]

    allowed_body = {
        "name": "tribal-house",
        "parent_name": None,
        "description": None,
        "enabled": True,
        "sort_order": 100,
    }
    allowed = client.patch(f"/api/genres/taxonomy/{a_id}", json=allowed_body)
    assert allowed.status_code == 200
    repeat_allowed = client.patch(f"/api/genres/taxonomy/{a_id}", json=allowed_body)
    assert repeat_allowed.status_code == 200

    collision_body = {
        "name": "tribal house",
        "parent_name": None,
        "description": None,
        "enabled": True,
        "sort_order": 101,
    }
    rejected = client.patch(f"/api/genres/taxonomy/{b_id}", json=collision_body)
    assert rejected.status_code == 422
    repeat_rejected = client.patch(f"/api/genres/taxonomy/{b_id}", json=collision_body)
    assert repeat_rejected.status_code == 422

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        a_row = conn.execute("SELECT name, enabled, sort_order FROM genre_taxonomy WHERE id = ?", (a_id,)).fetchone()
        b_row = conn.execute("SELECT name, enabled, sort_order FROM genre_taxonomy WHERE id = ?", (b_id,)).fetchone()

    assert a_row["name"] == "tribal-house"
    assert bool(a_row["enabled"]) is True
    assert a_row["sort_order"] == 100
    assert b_row["name"] == "Minimal House"
    assert bool(b_row["enabled"]) is True
    assert b_row["sort_order"] == 100


# ---------------------------------------------------------------------------
# Section F: Needs Review lifecycle -- applying a normalized genre clears its
# own pending queue entry, without a second/duplicate review store.
# ---------------------------------------------------------------------------


@pytest.fixture()
def needs_review_env(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    dbconn = sqlite3.connect(db_path)
    dbconn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            artist TEXT,
            title TEXT,
            genre TEXT,
            bpm REAL,
            bpm_trusted INTEGER NOT NULL DEFAULT 0,
            key_musical TEXT,
            key_camelot TEXT,
            key_trusted INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ok'
        )
        """
    )
    dbconn.execute(
        "INSERT INTO tracks (filepath, filename, artist, title, genre, bpm, bpm_trusted, key_musical, "
        "key_camelot, key_trusted) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 1)",
        ("/x/a.mp3", "a.mp3", "Artist A", "Title A", "afrohouse", 124.0, "Am", "8A"),
    )
    dbconn.commit()
    dbconn.close()
    return root, db_path


def test_applying_normalized_genre_clears_needs_review_entry(needs_review_env):
    setup_conn = sqlite3.connect(needs_review_env[1])
    svc.ensure_tables(setup_conn)  # adds the normalized_genre column the repair queue reads
    setup_conn.commit()
    setup_conn.close()

    metadata_repair_queue_service.refresh()
    genre_items_before = needs_review_service.list_items("GENRE")
    assert genre_items_before["counts"]["GENRE"] == 1

    conn = sqlite3.connect(needs_review_env[1])
    conn.row_factory = sqlite3.Row
    items = svc.build_preview_items(conn)
    svc.save_preview_snapshot(conn, items)
    result = svc.apply_selected(conn, [items[0]["track_id"]])
    conn.commit()
    conn.close()
    assert result["applied"] == 1

    metadata_repair_queue_service.refresh()
    genre_items_after = needs_review_service.list_items("GENRE")
    assert genre_items_after["counts"]["GENRE"] == 0
