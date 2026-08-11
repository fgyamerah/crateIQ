"""Deterministic genre taxonomy/mapping resolver (Genre Intelligence Phase 3).

Repository-controlled defaults live in version-controlled
`config/genre_taxonomy.json` and `config/genre_mappings.json` (schema
validated, loaded once, deterministic file order). User customizations live
in the selected library's local index (`genre_taxonomy` / `genre_mappings`
tables, the same tables the Genre Taxonomy page has always used) and
override the repository defaults for the same key. This module never writes
to the config files -- user edits always land in the local index.

Resolution precedence for one raw genre string, per the product's safe
precedence model:

  1. an explicit enabled user mapping (a `genre_mappings` DB row for the
     normalized raw key);
  2. an exact match against an enabled preferred canonical genre name
     (identity match, not a guess);
  3. an enabled repository default mapping (`config/genre_mappings.json`);
  4. no hit -> Needs Review. Ambiguous/unmapped raw genres are never
     guessed.

Generic/ambiguous raw labels (e.g. "afro", "dance") intentionally have no
repository default mapping to a specific preferred genre -- their config
entries mark `needs_review: true` instead, and only an explicit user mapping
may collapse them.

Read paths (`preview`) never modify track data. `apply()` is explicit,
selected-track scoped, requires confirmation, and records provenance via
`field_provenance_service` with `origin="system"` (deterministic app logic,
never masquerading as provider confidence). Consensus uses the same
resolver contract through a small adapter in this module, so provider
normalization and Genre Taxonomy preview stay aligned.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import field_provenance_service

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TAXONOMY_CONFIG_PATH = _REPO_ROOT / "config" / "genre_taxonomy.json"
_MAPPINGS_CONFIG_PATH = _REPO_ROOT / "config" / "genre_mappings.json"

VALID_CONFIDENCES = {"high", "medium", "low", "manual"}
VALID_SOURCES = {"default_taxonomy", "user_mapping", "imported_metadata", "filename_hint"}
NEEDS_REVIEW = "Needs Review"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_key(raw: str) -> str:
    """Deterministic casefold + whitespace/hyphen/underscore + punctuation normalization.

    Pinned by tests -- do not change without updating fixtures. `&` is kept
    (so "R&B" stays distinct) but other punctuation is stripped after
    hyphens/underscores/slashes are collapsed to a single space.
    """
    s = (raw or "").strip().casefold()
    s = re.sub(r"[\-_/]+", " ", s)
    s = re.sub(r"[^\w\s&]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _rows_by_normalized_key(rows: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        grouped.setdefault(normalize_key(value), []).append(row)
    return grouped


def taxonomy_rows_for_key(
    conn: sqlite3.Connection, name: str | None, *, exclude_id: int | None = None,
) -> list[dict[str, Any]]:
    key = normalize_key(name or "")
    if not key:
        return []
    rows = [row for row in _user_taxonomy_rows(conn) if normalize_key(row["name"]) == key]
    if exclude_id is not None:
        rows = [row for row in rows if row.get("id") != exclude_id]
    return rows


def mapping_rows_for_key(
    conn: sqlite3.Connection, raw_genre: str | None, *, exclude_id: int | None = None,
) -> list[dict[str, Any]]:
    key = normalize_key(raw_genre or "")
    if not key:
        return []
    rows = [row for row in _user_mapping_rows(conn) if normalize_key(row["raw_genre"]) == key]
    if exclude_id is not None:
        rows = [row for row in rows if row.get("id") != exclude_id]
    return rows


@dataclass(frozen=True)
class TaxonomyResolution:
    name: str | None
    source: str
    reason: str
    needs_review: bool


# ---------------------------------------------------------------------------
# Repository config: schema-validated, cached, deterministic order.
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_taxonomy_config(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("genres"), list):
        raise ValueError("genre_taxonomy.json must contain a 'genres' list.")
    genres: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for i, entry in enumerate(payload["genres"]):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError(f"genre_taxonomy.json entry {i} requires a non-empty 'name' string.")
        name = entry["name"].strip()
        key = normalize_key(name)
        if key in seen_keys:
            raise ValueError(f"genre_taxonomy.json has a duplicate genre name: {name!r}.")
        seen_keys.add(key)
        genres.append({
            "name": name,
            "parent_name": entry.get("parent_name") or None,
            "description": entry.get("description") or None,
            "sort_order": i,
        })
    return genres


def validate_mappings_config(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("mappings"), list):
        raise ValueError("genre_mappings.json must contain a 'mappings' list.")
    mappings: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for i, entry in enumerate(payload["mappings"]):
        if not isinstance(entry, dict) or not isinstance(entry.get("raw_genre"), str) or not entry["raw_genre"].strip():
            raise ValueError(f"genre_mappings.json entry {i} requires a non-empty 'raw_genre' string.")
        needs_review = bool(entry.get("needs_review", False))
        normalized_genre = entry.get("normalized_genre")
        if not needs_review and (not isinstance(normalized_genre, str) or not normalized_genre.strip()):
            raise ValueError(
                f"genre_mappings.json entry {i} ({entry['raw_genre']!r}) requires a non-empty "
                "'normalized_genre' unless 'needs_review' is true."
            )
        confidence = entry.get("confidence", "medium")
        if confidence not in VALID_CONFIDENCES:
            raise ValueError(f"genre_mappings.json entry {i} has an invalid confidence: {confidence!r}.")
        key = normalize_key(entry["raw_genre"])
        if key in seen_keys:
            raise ValueError(f"genre_mappings.json has a duplicate raw_genre: {entry['raw_genre']!r}.")
        seen_keys.add(key)
        mappings.append({
            "raw_genre": entry["raw_genre"].strip(),
            "normalized_genre": normalized_genre.strip() if isinstance(normalized_genre, str) else None,
            "confidence": confidence,
            "needs_review": needs_review or normalized_genre is None,
        })
    return mappings


@lru_cache(maxsize=1)
def repo_taxonomy() -> tuple[dict[str, Any], ...]:
    """Repository default preferred genres, in deterministic config-file order."""
    return tuple(validate_taxonomy_config(_load_json(_TAXONOMY_CONFIG_PATH)))


@lru_cache(maxsize=1)
def repo_mappings() -> tuple[dict[str, Any], ...]:
    """Repository default raw-genre mappings, in deterministic config-file order."""
    return tuple(validate_mappings_config(_load_json(_MAPPINGS_CONFIG_PATH)))


def _repo_mappings_by_key() -> dict[str, dict[str, Any]]:
    return {normalize_key(m["raw_genre"]): m for m in repo_mappings()}


# ---------------------------------------------------------------------------
# Local index (DB) overrides -- user customizations only.
# ---------------------------------------------------------------------------


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS genre_taxonomy("
        "id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,parent_name TEXT,description TEXT,"
        "enabled INTEGER NOT NULL DEFAULT 1,sort_order INTEGER NOT NULL,"
        "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS genre_mappings("
        "id INTEGER PRIMARY KEY,raw_genre TEXT UNIQUE NOT NULL,normalized_genre TEXT NOT NULL,"
        "confidence TEXT NOT NULL,source TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,"
        "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS genre_review_snapshots("
        "id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,items_json TEXT NOT NULL)"
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    for col in ("normalized_genre", "genre_source", "genre_reviewed_at", "genre_updated_at"):
        if col not in cols:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} TEXT")


def _user_taxonomy_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='genre_taxonomy'"
    ).fetchone():
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM genre_taxonomy")]


def _user_mapping_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='genre_mappings'"
    ).fetchone():
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM genre_mappings")]


def effective_taxonomy(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Repository defaults merged with local-index overrides, DB row wins per name."""
    user_groups = _rows_by_normalized_key(_user_taxonomy_rows(conn), "name")
    collisions = sorted(key for key, rows in user_groups.items() if len(rows) > 1)
    if collisions:
        raise ValueError(
            f"Ambiguous preferred-genre rows collapse to the same normalized key: {', '.join(collisions)}."
        )
    user_by_key = {key: rows[0] for key, rows in user_groups.items()}
    merged: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for repo_entry in repo_taxonomy():
        key = normalize_key(repo_entry["name"])
        seen_keys.add(key)
        override = user_by_key.get(key)
        if override is not None:
            merged.append({**override, "source": "user"})
        else:
            merged.append({"id": None, **repo_entry, "enabled": 1, "source": "repository"})
    for key, row in user_by_key.items():
        if key not in seen_keys:
            merged.append({**row, "source": "user"})
    merged.sort(key=lambda g: (g.get("sort_order", 100), g["name"]))
    return merged


def _effective_taxonomy_names_enabled(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        normalize_key(g["name"]): g["name"]
        for g in effective_taxonomy(conn)
        if g.get("enabled")
    }


def effective_mappings(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Repository defaults merged with local-index overrides, for display."""
    user_groups = _rows_by_normalized_key(_user_mapping_rows(conn), "raw_genre")
    collisions = sorted(key for key, rows in user_groups.items() if len(rows) > 1)
    if collisions:
        raise ValueError(
            f"Ambiguous raw-genre mappings collapse to the same normalized key: {', '.join(collisions)}."
        )
    user_by_key = {key: rows[0] for key, rows in user_groups.items()}
    merged: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for repo_entry in repo_mappings():
        key = normalize_key(repo_entry["raw_genre"])
        seen_keys.add(key)
        override = user_by_key.get(key)
        if override is not None:
            merged.append({**override, "source": override.get("source") or "user_mapping"})
        else:
            merged.append({
                "id": None,
                "raw_genre": repo_entry["raw_genre"],
                "normalized_genre": NEEDS_REVIEW if repo_entry["needs_review"] else repo_entry["normalized_genre"],
                "confidence": repo_entry["confidence"],
                "source": "default_taxonomy",
                "enabled": 1,
            })
    for key, row in user_by_key.items():
        if key not in seen_keys:
            merged.append({**row, "source": row.get("source") or "user_mapping"})
    merged.sort(key=lambda m: m["raw_genre"])
    return merged


def lookup_db_mapping(conn: sqlite3.Connection, raw_genre: str | None) -> str | None:
    """Local-index-only mapping lookup (no repository fallback).

    Preserves the pre-existing DB-only contract for older callers while
    still using the same normalized-key identity as the deterministic
    resolver. Duplicate normalized keys fail closed by returning None.
    """
    if not raw_genre or not raw_genre.strip():
        return None
    key = normalize_key(raw_genre)
    try:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM genre_mappings")]
    except sqlite3.OperationalError:
        return None
    matches = [row for row in rows if normalize_key(str(row.get("raw_genre") or "")) == key and row.get("enabled", 1)]
    if len(matches) != 1:
        return None
    normalized = matches[0].get("normalized_genre")
    return normalized.strip() if isinstance(normalized, str) and normalized.strip() else None


# ---------------------------------------------------------------------------
# Deterministic resolver.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenreResolution:
    raw_genre: str
    normalized_genre: str | None
    confidence: str | None
    source: str
    reason: str
    needs_review: bool


def resolve_taxonomy_name(conn: sqlite3.Connection, name: str | None) -> TaxonomyResolution:
    raw = (name or "").strip()
    if not raw:
        return TaxonomyResolution(None, "missing", "No preferred genre name present.", True)

    key = normalize_key(raw)
    user_groups = _rows_by_normalized_key(_user_taxonomy_rows(conn), "name")
    user_matches = user_groups.get(key, [])
    if len(user_matches) > 1:
        return TaxonomyResolution(
            None,
            "taxonomy_ambiguous",
            "Multiple preferred-genre rows collapse to the same normalized key.",
            True,
        )
    if len(user_matches) == 1:
        row = user_matches[0]
        if not row.get("enabled", 1):
            return TaxonomyResolution(
                None,
                "user_taxonomy_disabled",
                "A preferred genre override exists but is disabled.",
                True,
            )
        return TaxonomyResolution(
            row["name"].strip(),
            "user_taxonomy",
            "Matched an enabled user preferred-genre row.",
            False,
        )

    repo_match = next((g for g in repo_taxonomy() if normalize_key(g["name"]) == key), None)
    if repo_match is not None:
        return TaxonomyResolution(
            repo_match["name"],
            "preferred_taxonomy_exact",
            "Raw genre matches an enabled preferred genre exactly.",
            False,
        )
    return TaxonomyResolution(None, "unmapped", "No enabled preferred genre matches this name.", True)


def resolve_genre(conn: sqlite3.Connection, raw_genre: str | None) -> GenreResolution:
    raw = (raw_genre or "").strip()
    if not raw:
        return GenreResolution(raw, None, None, "missing", "No raw genre present.", False)

    key = normalize_key(raw)
    user_groups = _rows_by_normalized_key(_user_mapping_rows(conn), "raw_genre")
    user_matches = user_groups.get(key, [])
    if len(user_matches) > 1:
        return GenreResolution(
            raw,
            None,
            "low",
            "user_mapping_ambiguous",
            "Multiple user mappings collapse to the same normalized key.",
            True,
        )
    if len(user_matches) == 1:
        user_row = user_matches[0]
        if not user_row.get("enabled", 1):
            return GenreResolution(
                raw,
                None,
                "low",
                "user_mapping_disabled",
                "A user mapping for this raw genre exists but is disabled.",
                True,
            )
        target_resolution = resolve_taxonomy_name(conn, user_row["normalized_genre"])
        if target_resolution.name is None:
            reason = "User mapping target is not an enabled preferred genre."
            if target_resolution.source == "taxonomy_ambiguous":
                reason = "User mapping target is ambiguous because multiple preferred genres share the same normalized key."
            elif target_resolution.source == "user_taxonomy_disabled":
                reason = "User mapping target is disabled in the preferred taxonomy."
            return GenreResolution(raw, None, "low", "user_mapping_invalid_target", reason, True)
        return GenreResolution(
            raw,
            target_resolution.name,
            user_row.get("confidence") or "manual",
            "user_mapping",
            "Matched an explicit user mapping.",
            False,
        )

    canonical = resolve_taxonomy_name(conn, raw)
    if canonical.name is not None:
        return GenreResolution(raw, canonical.name, "high", canonical.source, canonical.reason, False)
    if canonical.source != "unmapped":
        return GenreResolution(raw, None, "low", canonical.source, canonical.reason, True)

    repo_map = _repo_mappings_by_key().get(key)
    if repo_map is not None:
        if repo_map["needs_review"]:
            return GenreResolution(raw, None, "low", "repository_default_needs_review",
                                    "Repository default marks this label ambiguous; needs manual review.", True)
        target_resolution = resolve_taxonomy_name(conn, repo_map["normalized_genre"])
        if target_resolution.name is None:
            reason = "Repository default target genre is disabled or ambiguous."
            if target_resolution.source == "taxonomy_ambiguous":
                reason = "Repository default target genre is ambiguous because multiple preferred genres share the same normalized key."
            elif target_resolution.source == "user_taxonomy_disabled":
                reason = "Repository default target genre is disabled in the preferred taxonomy."
            return GenreResolution(raw, None, "low", "repository_default_invalid_target", reason, True)
        return GenreResolution(raw, target_resolution.name, repo_map["confidence"],
                                "repository_default_mapping", "Matched a repository default mapping.", False)

    return GenreResolution(raw, None, "low", "unmapped",
                            "No mapping found for this raw genre; needs manual review.", True)


def resolve_consensus_genre(conn: sqlite3.Connection, raw_genre: str | None) -> str | None:
    """Small adapter for consensus_service: same deterministic resolver, value only."""
    resolution = resolve_genre(conn, raw_genre)
    if resolution.needs_review or not resolution.normalized_genre:
        return None
    return resolution.normalized_genre


# ---------------------------------------------------------------------------
# Preview / apply -- explicit, read-only-vs-write contract.
# ---------------------------------------------------------------------------


def build_preview_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Read-only with respect to track metadata; only inserts a review snapshot row."""
    ensure_tables(conn)
    conn.row_factory = sqlite3.Row
    items: list[dict[str, Any]] = []
    for row in conn.execute("SELECT id,title,artist,genre FROM tracks ORDER BY id"):
        raw = (row["genre"] or "").strip()
        if not raw:
            continue
        resolution = resolve_genre(conn, raw)
        suggested = NEEDS_REVIEW if resolution.needs_review or not resolution.normalized_genre else resolution.normalized_genre
        items.append({
            "track_id": row["id"],
            "title": row["title"],
            "artist": row["artist"],
            "raw_genre": raw,
            "current_genre": raw,
            "suggested_genre": suggested,
            "confidence": resolution.confidence or "low",
            "source": resolution.source,
            "reason": resolution.reason,
            "needs_review": suggested == NEEDS_REVIEW,
        })
    return items


def save_preview_snapshot(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> None:
    conn.execute(
        "INSERT INTO genre_review_snapshots(created_at,items_json) VALUES(?,?)",
        (now(), json.dumps(items)),
    )


def latest_snapshot_items(conn: sqlite3.Connection) -> list[dict[str, Any]] | None:
    conn.row_factory = sqlite3.Row
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='genre_review_snapshots'"
    ).fetchone():
        return None
    snap = conn.execute("SELECT * FROM genre_review_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if not snap:
        return None
    return json.loads(snap["items_json"])


def summarize(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pending": len(items),
        "high_confidence": sum(1 for x in items if x["confidence"] == "high"),
        "needs_review": sum(1 for x in items if x["suggested_genre"] == NEEDS_REVIEW),
        "missing_genre": sum(1 for x in items if not x["raw_genre"]),
        "applied": 0,
    }


def apply_selected(conn: sqlite3.Connection, track_ids: list[int]) -> dict[str, Any]:
    """Explicit, selected-track-scoped apply. Preserves raw genre; writes only
    `normalized_genre`/provenance columns. Never touches BPM/key/cues/tags/audio.
    """
    ensure_tables(conn)
    items = latest_snapshot_items(conn)
    if items is None:
        raise ValueError("Refresh preview first.")
    by_id = {x["track_id"]: x for x in items}
    applied = skipped = 0
    for track_id in track_ids:
        item = by_id.get(track_id)
        if not item or item["suggested_genre"] == NEEDS_REVIEW or item.get("needs_review"):
            skipped += 1
            continue
        ts = now()
        conn.execute(
            "UPDATE tracks SET normalized_genre=?,genre_source=?,genre_reviewed_at=?,genre_updated_at=? WHERE id=?",
            (item["suggested_genre"], "genre_taxonomy", ts, ts, track_id),
        )
        field_provenance_service.record(
            track_id, "normalized_genre", item["suggested_genre"],
            origin="system", source=f"genre_taxonomy:{item['source']}",
            reason=item["reason"],
            evidence={
                "raw_genre": item["raw_genre"],
                "resolver_confidence": item["confidence"],
                "resolver_source": item["source"],
            },
            conn=conn,
        )
        applied += 1
    return {
        "applied": applied,
        "skipped": skipped,
        "failed": 0,
        "warnings": ["Raw genre is preserved; only normalized_genre was written."],
    }
