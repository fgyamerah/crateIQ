"""
Neutral path-audit / path-reconcile core.

This module holds the path-audit detection engine and the path-reconcile
plan/validate logic that both the legacy ``pipeline.py`` CLI (`path-audit`,
`path-reconcile` subcommands) and the current FastAPI backend (reconciliation
route/services) need. It was extracted from ``pipeline.py`` so the backend no
longer has to import private helpers from that 300+KB legacy module.

Design constraints (do not violate without re-auditing both callers):
  * No FastAPI import.
  * No frontend concepts.
  * No import-time side effects: importing this module must never create
    directories, open/mutate a database, or require a music root to exist.
  * Deterministic and read-only. Nothing in this module writes to the music
    library, tags, or the pipeline database. Callers decide whether/where to
    persist any returned report/plan/result as a JSON artifact.
  * ``path_audit_report`` accepts ``audio_extensions``/``skip_dirs`` so the
    legacy CLI can keep honoring its ``config.py``/``config_local.py``
    overrides (via a thin pipeline.py wrapper) while the backend can call
    this module directly using the built-in defaults below, with no
    dependency on the legacy root config module at all.
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults (mirror config.py's AUDIO_EXTENSIONS / MAINTENANCE_SKIP_DIRS).
# Kept as plain literals so this module never has to import config.py to get
# them; pipeline.py's wrapper passes config.AUDIO_EXTENSIONS /
# config.MAINTENANCE_SKIP_DIRS explicitly to preserve config_local.py
# override behavior for the CLI.
# ---------------------------------------------------------------------------
DEFAULT_AUDIO_EXTENSIONS: frozenset = frozenset(
    {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg", ".opus"}
)
DEFAULT_MAINTENANCE_SKIP_DIRS: frozenset = frozenset({
    ".BIN",
    "QUARANTINE", "IGNORED", "CORRUPT", "DUPLICATES", "REJECTED",
    "_duplicates", "_corrupt",
    "__pycache__",
})


def assert_path_under_root(path: Path | str, root: Path | str) -> Path:
    """
    Resolve path and verify it stays under root.

    Relative paths are interpreted relative to root so benign relative DB paths
    can be audited, while '../' traversal resolves outside root and is rejected.
    """
    root_path = Path(root).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root_path / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"path outside selected root: {resolved} not under {root_path}") from exc
    return resolved


def _path_audit_audio_files(
    root: Path,
    *,
    audio_extensions=DEFAULT_AUDIO_EXTENSIONS,
    skip_dirs=DEFAULT_MAINTENANCE_SKIP_DIRS,
) -> list[Path]:
    skip = skip_dirs
    files: list[Path] = []
    seen: set[str] = set()
    for ext in audio_extensions:
        for pattern in (f"*{ext}", f"*{ext.upper()}"):
            for path in root.rglob(pattern):
                if any(part in skip for part in path.parts):
                    continue
                if any(part.startswith(".") for part in path.parts):
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                files.append(path.resolve())
    return sorted(files)


def _path_audit_db_path(root: Path) -> Path:
    return assert_path_under_root(root / "logs" / "processed.db", root)


def _path_audit_table_columns(conn, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


_PATH_AUDIT_STAGE_PRIORITY = {
    "metadata-sanitize": 0,
    "artist-intelligence": 1,
    "metadata-enrich-online": 2,
    "filename-normalize": 3,
    "library-organize": 4,
}


def _path_audit_current_processed_rows(rows: list[dict]) -> list[dict]:
    known_rows = [
        row for row in rows
        if row.get("stage") in _PATH_AUDIT_STAGE_PRIORITY
    ]
    if known_rows:
        final_priority = max(
            _PATH_AUDIT_STAGE_PRIORITY[row["stage"]]
            for row in known_rows
        )
        return [
            row for row in known_rows
            if _PATH_AUDIT_STAGE_PRIORITY[row["stage"]] == final_priority
        ]

    latest_by_path: dict[str, dict] = {}
    for row in rows:
        fp = str(row.get("filepath") or "")
        if not fp:
            continue
        current = latest_by_path.get(fp)
        if current is None or str(row.get("processed_at") or "") > str(current.get("processed_at") or ""):
            latest_by_path[fp] = row
    return list(latest_by_path.values())


def _path_audit_normalized_filename(path: Path) -> str:
    import re

    stem = path.stem.lower()
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    stem = re.sub(r"\s*-\s*[0-9]{1,2}[ab]\s*-\s*\d{2,3}\s*$", "", stem)
    stem = re.sub(r"\s*-\s*\d{2,3}\s*-\s*[0-9]{1,2}[ab]\s*$", "", stem)
    stem = re.sub(r"[^a-z0-9]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def _path_audit_size_diff_pct(old_size, new_size: int) -> float | None:
    if old_size in (None, "", 0):
        return None
    try:
        old = float(old_size)
    except (TypeError, ValueError):
        return None
    if old <= 0:
        return None
    return abs(old - float(new_size)) / old


def _path_audit_fuzzy_similarity(old_path: Path, new_path: Path) -> float:
    from difflib import SequenceMatcher

    old_name = _path_audit_normalized_filename(old_path)
    new_name = _path_audit_normalized_filename(new_path)
    if not old_name or not new_name:
        return 0.0
    return SequenceMatcher(None, old_name, new_name).ratio()


_PATH_AUDIT_RELOCATION_IGNORE_TOKENS = {
    "remix", "mix", "original", "feat", "ft", "featuring", "extended",
}


def _path_audit_filename_tokens(path: Path) -> set[str]:
    return {
        token for token in _path_audit_normalized_filename(path).split()
        if len(token) > 1 and token not in _PATH_AUDIT_RELOCATION_IGNORE_TOKENS
    }


def _path_audit_token_overlap(old_path: Path, new_path: Path) -> float:
    old_tokens = _path_audit_filename_tokens(old_path)
    if not old_tokens:
        return 0.0
    new_tokens = _path_audit_filename_tokens(new_path)
    return len(old_tokens & new_tokens) / len(old_tokens)


_PATH_AUDIT_VERSION_TOKENS = {
    "original",
    "remix",
    "extended",
    "dub",
    "vocal",
    "instrumental",
    "bootleg",
    "edit",
    "radio",
    "club",
    "amapiano",
    "re-edit",
    "journey",
}


def _path_audit_version_tokens(path: Path) -> set[str]:
    import re

    raw_stem = path.stem.lower()
    tokens = set(_path_audit_normalized_filename(path).split())
    version_tokens = {
        token for token in tokens
        if token in _PATH_AUDIT_VERSION_TOKENS
    }
    if re.search(r"\bre[\s-]*edit\b", raw_stem):
        version_tokens.add("re-edit")
        version_tokens.discard("edit")
    return version_tokens


def _path_audit_numeric_title_risk(old_path: Path, new_path: Path) -> bool:
    old_title = old_path.stem.split(" - ", 1)[-1]
    new_title = new_path.stem.split(" - ", 1)[-1]
    old_tokens = _path_audit_normalized_filename(Path(old_title)).split()
    new_tokens = _path_audit_normalized_filename(Path(new_title)).split()
    if not old_tokens or not new_tokens:
        return False
    old_has_number = old_tokens[0].isdigit()
    new_has_number = new_tokens[0].isdigit()
    if old_has_number == new_has_number:
        return False
    old_without_number = old_tokens[1:] if old_has_number else old_tokens
    new_without_number = new_tokens[1:] if new_has_number else new_tokens
    return old_without_number == new_without_number


_PATH_AUDIT_ARTIST_CONNECTOR_TOKENS = {
    "and",
    "feat",
    "featuring",
    "ft",
    "pres",
    "presents",
    "vs",
    "with",
    "x",
}


def _path_audit_artist_tokens(path: Path) -> set[str]:
    artist_part = path.stem.split(" - ", 1)[0]
    normalized = _path_audit_normalized_filename(Path(artist_part))
    return {
        token for token in normalized.split()
        if len(token) > 1 and token not in _PATH_AUDIT_ARTIST_CONNECTOR_TOKENS
    }


def _path_audit_artist_expansion_risk(old_path: Path, new_path: Path) -> bool:
    old_artist_tokens = _path_audit_artist_tokens(old_path)
    if not old_artist_tokens:
        return False
    new_artist_tokens = _path_audit_artist_tokens(new_path)
    return len(new_artist_tokens - old_artist_tokens) >= 2


def _path_audit_auto_safe_downgrade_risk(old_path: Path, new_path: Path) -> bool:
    old_version_tokens = _path_audit_version_tokens(old_path)
    new_version_tokens = _path_audit_version_tokens(new_path)
    if old_version_tokens != new_version_tokens:
        return True
    if _path_audit_numeric_title_risk(old_path, new_path):
        return True
    return _path_audit_artist_expansion_risk(old_path, new_path)


def _path_audit_top_folder(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "(outside_root)"
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "library" and parts[1] == "sorted":
        return "sorted"
    return parts[0] if parts else "(root)"


def _path_audit_orphan_analysis(orphan_rows: list[dict], disk_files: list[Path], root: Path) -> dict:
    from collections import Counter

    by_top_folder: Counter = Counter()
    by_stage_status: Counter = Counter()
    by_parent_folder: Counter = Counter()
    exact_size = 0
    near_size = 0
    token_50 = 0
    token_60 = 0
    token_70 = 0

    disk_sizes: list[int] = []
    disk_token_rows: list[tuple[str, set[str]]] = []
    for path in disk_files:
        try:
            disk_sizes.append(path.stat().st_size)
        except OSError:
            pass
        tokens = _path_audit_filename_tokens(path)
        if tokens:
            disk_token_rows.append((path.suffix.lower(), tokens))

    for orphan in orphan_rows:
        old_path = Path(orphan["filepath"])
        by_top_folder[_path_audit_top_folder(old_path, root)] += 1
        source_rows = orphan.get("source_rows") or []
        if source_rows:
            for source in source_rows:
                stage = source.get("stage") or source.get("table") or "unknown"
                status = orphan.get("status") or "unknown"
                by_stage_status[f"{stage}/{status}"] += 1
        else:
            status = orphan.get("status") or "unknown"
            by_stage_status[f"unknown/{status}"] += 1
        by_parent_folder[str(old_path.parent)] += 1

        old_size = orphan.get("filesize_bytes")
        try:
            old_size_int = int(old_size)
        except (TypeError, ValueError):
            old_size_int = None
        if old_size_int and any(size == old_size_int for size in disk_sizes):
            exact_size += 1
        if old_size_int and any(abs(size - old_size_int) / old_size_int < 0.10 for size in disk_sizes):
            near_size += 1

        old_tokens = _path_audit_filename_tokens(old_path)
        best_overlap = 0.0
        if old_tokens:
            old_suffix = old_path.suffix.lower()
            for candidate_suffix, candidate_tokens in disk_token_rows:
                if candidate_suffix != old_suffix:
                    continue
                overlap = len(old_tokens & candidate_tokens) / len(old_tokens)
                if overlap > best_overlap:
                    best_overlap = overlap
        if best_overlap >= 0.50:
            token_50 += 1
        if best_overlap >= 0.60:
            token_60 += 1
        if best_overlap >= 0.70:
            token_70 += 1

    return {
        "orphan_by_top_folder": dict(sorted(by_top_folder.items())),
        "orphan_by_stage_status": dict(sorted(by_stage_status.items())),
        "orphan_by_parent_folder_sample": [
            {"parent_folder": folder, "count": count}
            for folder, count in by_parent_folder.most_common(30)
        ],
        "orphan_size_match_stats": {
            "exact_file_size_exists_elsewhere": exact_size,
            "near_file_size_within_10pct_exists_elsewhere": near_size,
        },
        "orphan_filename_token_match_stats": {
            "token_overlap_gte_50pct": token_50,
            "token_overlap_gte_60pct": token_60,
            "token_overlap_gte_70pct": token_70,
        },
    }


def _path_audit_orphan_candidates(orphan_rows: list[dict], disk_files: list[Path]) -> list[dict]:
    candidates: list[dict] = []
    for orphan in orphan_rows:
        old_path = Path(orphan["filepath"])
        old_size = orphan.get("filesize_bytes")
        scored: list[dict] = []
        for disk_path in disk_files:
            try:
                candidate_size = disk_path.stat().st_size
            except OSError:
                continue
            token_overlap = _path_audit_token_overlap(old_path, disk_path)
            size_diff_pct = _path_audit_size_diff_pct(old_size, candidate_size)
            if size_diff_pct is None:
                size_similarity = 0.0
            else:
                size_similarity = max(0.0, 1.0 - min(size_diff_pct, 1.0))
            same_extension = old_path.suffix.lower() == disk_path.suffix.lower()
            score = (token_overlap * 0.60) + (size_similarity * 0.30) + (0.10 if same_extension else 0.0)
            if score <= 0:
                continue
            rounded_score = round(score, 6)
            rounded_size_diff = round(size_diff_pct, 6) if size_diff_pct is not None else ""
            review_tier = _path_audit_orphan_candidate_tier(
                rounded_score,
                token_overlap,
                size_diff_pct,
                same_extension,
                old_path,
                disk_path,
            )
            reason_bits = []
            if token_overlap:
                reason_bits.append("token_overlap")
            if size_similarity:
                reason_bits.append("size_similarity")
            if same_extension:
                reason_bits.append("same_extension")
            scored.append({
                "old_path": orphan["filepath"],
                "candidate_path": str(disk_path),
                "score": rounded_score,
                "token_overlap": round(token_overlap, 4),
                "size_diff_pct": rounded_size_diff,
                "same_extension": same_extension,
                "review_tier": review_tier,
                "old_filename": old_path.name,
                "candidate_filename": disk_path.name,
                "old_size": old_size,
                "candidate_size": candidate_size,
                "reason": "+".join(reason_bits) if reason_bits else "weak",
            })
        scored.sort(key=lambda row: (-row["score"], row["candidate_path"]))
        top_candidates = scored[:5]
        auto_safe_candidates = [
            row for row in top_candidates
            if row["review_tier"] == "AUTO_SAFE_CANDIDATE"
        ]
        if len(auto_safe_candidates) > 1:
            for row in auto_safe_candidates:
                row["review_tier"] = "REVIEW_CAREFULLY"
        candidates.extend(top_candidates)
    candidates.sort(key=lambda row: (row["old_path"], -row["score"]))
    return candidates


def _path_audit_orphan_candidate_tier(
    score: float,
    token_overlap: float,
    size_diff_pct,
    same_extension: bool,
    old_path: Path | None = None,
    new_path: Path | None = None,
) -> str:
    if (
        score >= 0.95
        and token_overlap >= 0.90
        and size_diff_pct is not None
        and size_diff_pct < 0.01
        and same_extension
    ):
        if old_path is not None and new_path is not None:
            if _path_audit_auto_safe_downgrade_risk(old_path, new_path):
                return "REVIEW_CAREFULLY"
        return "AUTO_SAFE_CANDIDATE"
    if score >= 0.80:
        return "REVIEW_CAREFULLY"
    return "WEAK_MATCH"


def _path_audit_orphan_candidate_tier_counts(candidates: list[dict]) -> dict:
    counts = {
        "AUTO_SAFE_CANDIDATE": 0,
        "REVIEW_CAREFULLY": 0,
        "WEAK_MATCH": 0,
    }
    for candidate in candidates:
        tier = candidate.get("review_tier", "WEAK_MATCH")
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def _path_audit_best_rename_match(matches: list[dict]) -> dict | None:
    if not matches:
        return None
    rank = {
        "same_basename": 0,
        "fuzzy_filename": 1,
        "same_size_and_extension": 2,
    }
    return sorted(
        matches,
        key=lambda m: (
            rank.get(m.get("reason", ""), 99),
            -(m.get("similarity") or 0),
            m.get("size_diff_pct") if m.get("size_diff_pct") is not None else 999,
        ),
    )[0]


def _path_audit_db_rows(db_path: Path) -> tuple[list[dict], list[dict], dict, str | None]:
    import sqlite3

    if not db_path.exists():
        return [], [], {
            "tracks_rows": 0,
            "processed_state_rows": 0,
            "combined_db_paths": 0,
            "processed_state_path_column": None,
            "repeated_processed_state_paths": 0,
            "cross_source_overlap_count": 0,
            "historical_paths_count": 0,
            "stale_processed_state_rows_total": 0,
            "active_processed_state_rows": 0,
            "canonical_source": "processed_state",
            "current_processed_state_stage": None,
        }, f"database not found: {db_path}"

    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            combined: dict[str, dict] = {}
            duplicates: list[dict] = []
            source_counts = {
                "tracks_rows": 0,
                "processed_state_rows": 0,
                "combined_db_paths": 0,
                "processed_state_path_column": None,
                "repeated_processed_state_paths": 0,
                "cross_source_overlap_count": 0,
                "historical_paths_count": 0,
                "stale_processed_state_rows_total": 0,
                "active_processed_state_rows": 0,
                "canonical_source": "processed_state",
                "current_processed_state_stage": None,
            }
            track_rows: list[dict] = []

            if "tracks" in tables:
                track_rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, filepath, filename, status, filesize_bytes FROM tracks"
                    ).fetchall()
                ]
                source_counts["tracks_rows"] = len(track_rows)
                for row in track_rows:
                    fp = str(row.get("filepath") or "")
                    if not fp:
                        continue
                    item = combined.setdefault(fp, {
                        "filepath": fp,
                        "filename": row.get("filename") or Path(fp).name,
                        "status": row.get("status"),
                        "filesize_bytes": row.get("filesize_bytes"),
                        "sources": [],
                        "source_rows": [],
                    })
                    if "tracks" not in item["sources"]:
                        item["sources"].append("tracks")
                    if item.get("filesize_bytes") is None:
                        item["filesize_bytes"] = row.get("filesize_bytes")
                    item["source_rows"].append({"table": "tracks", "id": row.get("id")})

                duplicates.extend([
                    {
                        "filepath": row["filepath"],
                        "count": row["n"],
                        "sources": ["tracks"],
                        "duplicate_type": "within_table",
                        "table": "tracks",
                        "row_ids": [
                            r["id"] for r in track_rows
                            if str(r.get("filepath", "")) == str(row["filepath"])
                        ],
                    }
                    for row in conn.execute(
                        "SELECT filepath, COUNT(*) AS n FROM tracks "
                        "GROUP BY filepath HAVING COUNT(*) > 1"
                    ).fetchall()
                ])

            has_track_rows = len(track_rows) > 0
            source_counts["canonical_source"] = "tracks" if has_track_rows else "processed_state"

            if "processed_state" in tables:
                columns = _path_audit_table_columns(conn, "processed_state")
                path_col = "filepath" if "filepath" in columns else "path" if "path" in columns else None
                source_counts["processed_state_path_column"] = path_col
                if path_col:
                    processed_rows = [
                        dict(row)
                        for row in conn.execute(
                            f"SELECT id, stage, {path_col} AS filepath, file_size, "
                            "file_mtime, status, processed_at, reason "
                            "FROM processed_state"
                        ).fetchall()
                    ]
                    source_counts["historical_paths_count"] = len(processed_rows)
                    stale_processed_rows = [
                        row for row in processed_rows
                        if str(row.get("status") or "").lower() == "stale"
                    ]
                    active_processed_rows = [
                        row for row in processed_rows
                        if str(row.get("status") or "").lower() != "stale"
                    ]
                    source_counts["stale_processed_state_rows_total"] = len(stale_processed_rows)
                    current_processed_rows = _path_audit_current_processed_rows(active_processed_rows)
                    source_counts["processed_state_rows"] = len(current_processed_rows)
                    source_counts["active_processed_state_rows"] = len(current_processed_rows)
                    current_stages = sorted({
                        str(row.get("stage") or "")
                        for row in current_processed_rows
                        if row.get("stage")
                    })
                    source_counts["current_processed_state_stage"] = (
                        current_stages[0] if len(current_stages) == 1 else current_stages
                    )
                    if not has_track_rows:
                        for row in current_processed_rows:
                            fp = str(row.get("filepath") or "")
                            if not fp:
                                continue
                            item = combined.setdefault(fp, {
                                "filepath": fp,
                                "filename": Path(fp).name,
                                "status": row.get("status"),
                                "filesize_bytes": row.get("file_size"),
                                "sources": [],
                                "source_rows": [],
                            })
                            if "processed_state" not in item["sources"]:
                                item["sources"].append("processed_state")
                            if item.get("filesize_bytes") is None:
                                item["filesize_bytes"] = row.get("file_size")
                            item["source_rows"].append({
                                "table": "processed_state",
                                "id": row.get("id"),
                                "stage": row.get("stage"),
                            })

                    repeated_rows = conn.execute(
                        f"SELECT {path_col} AS filepath, COUNT(*) AS n "
                        "FROM processed_state "
                        f"GROUP BY {path_col} HAVING COUNT(*) > 1"
                    ).fetchall()
                    source_counts["repeated_processed_state_paths"] = len(repeated_rows)

                    if not has_track_rows:
                        current_stage_path_rows: dict[tuple[str, str], list[dict]] = {}
                        for row in current_processed_rows:
                            key = (str(row.get("stage") or ""), str(row.get("filepath") or ""))
                            current_stage_path_rows.setdefault(key, []).append(row)
                        for (stage, filepath), grouped_rows in current_stage_path_rows.items():
                            if len(grouped_rows) <= 1:
                                continue
                            duplicates.append({
                                "filepath": filepath,
                                "count": len(grouped_rows),
                                "sources": ["processed_state"],
                                "duplicate_type": "within_stage",
                                "table": "processed_state",
                                "stage": stage,
                                "row_ids": [r["id"] for r in grouped_rows],
                            })

                    if has_track_rows:
                        track_paths = {str(row.get("filepath") or "") for row in track_rows if row.get("filepath")}
                        processed_paths = {
                            str(row.get("filepath") or "")
                            for row in current_processed_rows
                            if row.get("filepath")
                        }
                        source_counts["cross_source_overlap_count"] = len(track_paths & processed_paths)
                    else:
                        for item in combined.values():
                            if "tracks" in item["sources"] and "processed_state" in item["sources"]:
                                source_counts["cross_source_overlap_count"] += 1

            rows = sorted(combined.values(), key=lambda r: r["filepath"])
            source_counts["combined_db_paths"] = len(rows)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [], [], {
            "tracks_rows": 0,
            "processed_state_rows": 0,
            "combined_db_paths": 0,
                "processed_state_path_column": None,
                "repeated_processed_state_paths": 0,
                "cross_source_overlap_count": 0,
                "historical_paths_count": 0,
                "stale_processed_state_rows_total": 0,
                "active_processed_state_rows": 0,
                "canonical_source": "processed_state",
                "current_processed_state_stage": None,
            }, f"could not read database {db_path}: {exc}"

    return rows, duplicates, source_counts, None


def _path_audit_all_processed_state_rows(db_path: Path) -> list[dict]:
    import sqlite3

    if not db_path.exists():
        return []
    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_state'"
            ).fetchone()
            if table is None:
                return []
            columns = _path_audit_table_columns(conn, "processed_state")
            path_col = "filepath" if "filepath" in columns else "path" if "path" in columns else None
            if path_col is None:
                return []
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT id, stage, {path_col} AS filepath, file_size, "
                    "file_mtime, status, processed_at, reason "
                    "FROM processed_state"
                ).fetchall()
            ]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def _path_audit_stale_processed_state_rows(root: Path, db_path: Path) -> list[dict]:
    rows = _path_audit_all_processed_state_rows(db_path)
    candidate_paths: set[Path] = set()
    for row in rows:
        if str(row.get("status") or "").lower() == "stale":
            continue
        raw_path = str(row.get("filepath") or "")
        if not raw_path:
            continue
        try:
            path = assert_path_under_root(raw_path, root)
        except ValueError:
            continue
        if path.exists():
            candidate_paths.add(path)
    candidate_paths_by_name: dict[str, list[Path]] = {}
    for path in candidate_paths:
        key = _path_audit_normalized_filename(path)
        if key:
            candidate_paths_by_name.setdefault(key, []).append(path)

    stale_rows: list[dict] = []
    for row in rows:
        if str(row.get("status") or "").lower() == "stale":
            continue
        raw_path = str(row.get("filepath") or "")
        if not raw_path:
            continue
        try:
            old_path = assert_path_under_root(raw_path, root)
        except ValueError:
            continue
        if old_path.exists():
            continue
        candidate_subset = candidate_paths_by_name.get(_path_audit_normalized_filename(old_path), [])
        if not candidate_subset:
            continue
        orphan = {
            "filepath": str(old_path),
            "filesize_bytes": row.get("file_size"),
        }
        candidates = _path_audit_orphan_candidates([orphan], sorted(candidate_subset))
        auto_safe = [
            candidate for candidate in candidates
            if candidate.get("review_tier") == "AUTO_SAFE_CANDIDATE"
        ]
        if not auto_safe:
            continue
        best = auto_safe[0]
        stale_rows.append({
            "old_path": str(old_path),
            "replacement_path": best["candidate_path"],
            "stage": row.get("stage"),
            "reason": "superseded_by_existing_path",
            "source_rows": [
                {
                    "table": "processed_state",
                    "id": row.get("id"),
                    "stage": row.get("stage"),
                }
            ],
        })
    stale_rows.sort(key=lambda item: (item["old_path"], item["stage"] or ""))
    return stale_rows


def _path_audit_queue_files(root: Path) -> list[Path]:
    queue_files: list[Path] = []
    base = root / "data"
    if not base.exists():
        return []
    for suffix in ("*.json", "*.jsonl"):
        for path in base.rglob(suffix):
            if "queue" in path.name.lower():
                queue_files.append(path.resolve())
    return sorted(set(queue_files))


def _path_audit_iter_paths(value, *, field: str = "", location: str = ""):
    path_keys = {
        "file",
        "filepath",
        "path",
        "track_path",
        "original_path",
        "current_path",
        "target_path",
        "old_path",
        "new_path",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}" if location else str(key)
            if isinstance(child, str) and key in path_keys:
                yield key, child, child_location
            else:
                yield from _path_audit_iter_paths(
                    child, field=str(key), location=child_location
                )
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            child_location = f"{location}[{idx}]"
            yield from _path_audit_iter_paths(
                child, field=field, location=child_location
            )


def _path_audit_stale_queue_entries(root: Path) -> list[dict]:
    import json

    stale: list[dict] = []
    for queue_file in _path_audit_queue_files(root):
        try:
            if queue_file.suffix.lower() == ".jsonl":
                records = []
                for line_no, line in enumerate(
                    queue_file.read_text(encoding="utf-8").splitlines(),
                    start=1,
                ):
                    if not line.strip():
                        continue
                    try:
                        records.append((f"line {line_no}", json.loads(line)))
                    except json.JSONDecodeError:
                        stale.append({
                            "queue_file": str(queue_file),
                            "location": f"line {line_no}",
                            "field": "",
                            "path": "",
                            "reason": "invalid_json",
                        })
            else:
                records = [("json", json.loads(queue_file.read_text(encoding="utf-8")))]
        except (OSError, json.JSONDecodeError) as exc:
            stale.append({
                "queue_file": str(queue_file),
                "location": "",
                "field": "",
                "path": "",
                "reason": f"unreadable_queue: {exc}",
            })
            continue

        for record_location, record in records:
            for field, raw_path, location in _path_audit_iter_paths(record):
                candidate = Path(raw_path).expanduser()
                checked = candidate if candidate.is_absolute() else root / candidate
                if not checked.exists():
                    stale.append({
                        "queue_file": str(queue_file),
                        "location": f"{record_location}:{location}",
                        "field": field,
                        "path": raw_path,
                        "reason": "path_not_found",
                    })
    return stale


def path_audit_report(
    root: Path,
    db_path: Path,
    *,
    include_orphan_candidates: bool = False,
    audio_extensions=DEFAULT_AUDIO_EXTENSIONS,
    skip_dirs=DEFAULT_MAINTENANCE_SKIP_DIRS,
) -> dict:
    from collections import defaultdict
    from datetime import datetime, timezone

    db_rows, duplicate_db_entries, source_counts, db_error = _path_audit_db_rows(db_path)
    disk_files = _path_audit_audio_files(root, audio_extensions=audio_extensions, skip_dirs=skip_dirs)
    mixed_root_db_paths: list[dict] = []
    scoped_db_rows: list[dict] = []
    for row in db_rows:
        raw_fp = str(row.get("filepath") or "")
        try:
            scoped_path = assert_path_under_root(raw_fp, root)
        except ValueError as exc:
            mixed_root_db_paths.append({
                "filepath": raw_fp,
                "sources": row.get("sources", []),
                "source_rows": row.get("source_rows", []),
                "reason": str(exc),
            })
            continue
        scoped = dict(row)
        scoped["filepath"] = str(scoped_path)
        scoped_db_rows.append(scoped)
    db_rows = scoped_db_rows
    scoped_duplicates: list[dict] = []
    for duplicate in duplicate_db_entries:
        try:
            scoped_dup_path = assert_path_under_root(duplicate.get("filepath", ""), root)
        except ValueError:
            continue
        scoped_duplicate = dict(duplicate)
        scoped_duplicate["filepath"] = str(scoped_dup_path)
        scoped_duplicates.append(scoped_duplicate)
    duplicate_db_entries = scoped_duplicates

    db_paths_exact = {str(Path(row["filepath"]).expanduser()) for row in db_rows}
    db_paths_resolved = set()
    for row in db_rows:
        try:
            db_paths_resolved.add(str(Path(row["filepath"]).expanduser().resolve()))
        except OSError:
            pass

    untracked = [
        path for path in disk_files
        if str(path) not in db_paths_exact and str(path.resolve()) not in db_paths_resolved
    ]

    by_basename: dict[str, list[Path]] = defaultdict(list)
    by_size_ext: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for path in untracked:
        by_basename[path.name.lower()].append(path)
        try:
            by_size_ext[(path.stat().st_size, path.suffix.lower())].append(path)
        except OSError:
            pass

    missing_files: list[dict] = []
    possible_renames: list[dict] = []
    relocation_candidates: list[dict] = []
    orphan_db_rows: list[dict] = []

    for row in db_rows:
        fp = str(row["filepath"])
        path = Path(fp).expanduser()
        if path.exists():
            continue

        missing = {
            "id": row.get("id"),
            "filepath": fp,
            "filename": row.get("filename") or path.name,
            "status": row.get("status"),
            "filesize_bytes": row.get("filesize_bytes"),
            "sources": row.get("sources", []),
            "source_rows": row.get("source_rows", []),
        }
        missing_files.append(missing)

        matches: list[dict] = []
        for candidate in by_basename.get(path.name.lower(), []):
            candidate_size = candidate.stat().st_size
            size_diff_pct = _path_audit_size_diff_pct(row.get("filesize_bytes"), candidate_size)
            matches.append({
                "path": str(candidate),
                "reason": "same_basename",
                "size": candidate_size,
                "similarity": 1.0,
                "size_diff_pct": size_diff_pct,
            })

        if matches:
            matched_paths = {match["path"] for match in matches}
        else:
            matched_paths = set()

        for candidate in untracked:
            if str(candidate) in matched_paths:
                continue
            if candidate.suffix.lower() != path.suffix.lower():
                continue
            try:
                candidate_size = candidate.stat().st_size
            except OSError:
                continue
            size_diff_pct = _path_audit_size_diff_pct(row.get("filesize_bytes"), candidate_size)
            if size_diff_pct is None or size_diff_pct >= 0.05:
                continue
            similarity = _path_audit_fuzzy_similarity(path, candidate)
            if similarity <= 0.85:
                continue
            matches.append({
                "path": str(candidate),
                "reason": "fuzzy_filename",
                "size": candidate_size,
                "similarity": round(similarity, 4),
                "size_diff_pct": round(size_diff_pct, 6),
            })
            matched_paths.add(str(candidate))

        if matches:
            best_match = _path_audit_best_rename_match(matches)
            possible_renames.append({
                "old_path": missing["filepath"],
                "new_path": best_match.get("path") if best_match else None,
                "similarity": best_match.get("similarity") if best_match else None,
                "size_diff_pct": best_match.get("size_diff_pct") if best_match else None,
                "reason": best_match.get("reason") if best_match else None,
                "db_row": missing,
                "matches": matches,
            })
        else:
            orphan_db_rows.append(missing)

    remaining_orphans: list[dict] = []
    for orphan in orphan_db_rows:
        old_path = Path(orphan["filepath"])
        best_candidate: dict | None = None
        for candidate in untracked:
            if candidate.suffix.lower() != old_path.suffix.lower():
                continue
            try:
                candidate_size = candidate.stat().st_size
            except OSError:
                continue
            size_diff_pct = _path_audit_size_diff_pct(orphan.get("filesize_bytes"), candidate_size)
            if size_diff_pct is None or size_diff_pct >= 0.10:
                continue
            token_overlap = _path_audit_token_overlap(old_path, candidate)
            if token_overlap < 0.70:
                continue
            match = {
                "old_path": orphan["filepath"],
                "new_path": str(candidate),
                "match_type": "relocation",
                "token_overlap": round(token_overlap, 4),
                "size_diff_pct": round(size_diff_pct, 6),
                "db_row": orphan,
            }
            if best_candidate is None:
                best_candidate = match
                continue
            if (
                match["token_overlap"] > best_candidate["token_overlap"]
                or (
                    match["token_overlap"] == best_candidate["token_overlap"]
                    and match["size_diff_pct"] < best_candidate["size_diff_pct"]
                )
            ):
                best_candidate = match
        if best_candidate:
            relocation_candidates.append(best_candidate)
        else:
            remaining_orphans.append(orphan)
    orphan_db_rows = remaining_orphans
    orphan_analysis = _path_audit_orphan_analysis(orphan_db_rows, disk_files, root)
    orphan_candidates = (
        _path_audit_orphan_candidates(orphan_db_rows, disk_files)
        if include_orphan_candidates else []
    )
    orphan_candidate_tiers = _path_audit_orphan_candidate_tier_counts(orphan_candidates)
    stale_processed_state_rows = _path_audit_stale_processed_state_rows(root, db_path)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "database": str(db_path),
        "read_only": True,
        "db_error": db_error,
        "summary": {
            "db_rows": len(db_rows),
            "tracks_rows": source_counts["tracks_rows"],
            "processed_state_rows": source_counts["processed_state_rows"],
            "canonical_source": source_counts["canonical_source"],
            "combined_db_paths": len(db_rows),
            "mixed_root_db_paths": len(mixed_root_db_paths),
            "repeated_processed_state_paths": source_counts["repeated_processed_state_paths"],
            "cross_source_overlap_count": source_counts["cross_source_overlap_count"],
            "historical_paths_count": source_counts["historical_paths_count"],
            "stale_processed_state_rows_total": source_counts["stale_processed_state_rows_total"],
            "active_processed_state_rows": source_counts["active_processed_state_rows"],
            "disk_audio_files": len(disk_files),
            "missing_files": len(missing_files),
            "untracked_files": len(untracked),
            "possible_renames": len(possible_renames),
            "relocation_candidates": len(relocation_candidates),
            "duplicate_db_entries": len(duplicate_db_entries),
            "stale_queue_entries": 0,
            "stale_processed_state_count": len(stale_processed_state_rows),
            "orphan_db_rows": len(orphan_db_rows),
            "orphan_candidate_scoring_enabled": include_orphan_candidates,
        },
        "path_sources": {
            "tracks_rows": source_counts["tracks_rows"],
            "processed_state_rows": source_counts["processed_state_rows"],
            "canonical_source": source_counts["canonical_source"],
            "combined_db_paths": len(db_rows),
            "mixed_root_db_paths": len(mixed_root_db_paths),
            "repeated_processed_state_paths": source_counts["repeated_processed_state_paths"],
            "cross_source_overlap_count": source_counts["cross_source_overlap_count"],
            "historical_paths_count": source_counts["historical_paths_count"],
            "stale_processed_state_rows_total": source_counts["stale_processed_state_rows_total"],
            "active_processed_state_rows": source_counts["active_processed_state_rows"],
            "current_processed_state_stage": source_counts["current_processed_state_stage"],
            "processed_state_path_column": source_counts["processed_state_path_column"],
        },
        "missing_files": missing_files,
        "mixed_root_db_paths": mixed_root_db_paths,
        **orphan_analysis,
        "orphan_candidate_tiers": orphan_candidate_tiers,
        "orphan_candidates": orphan_candidates,
        "untracked_files": [str(path) for path in untracked],
        "possible_renames": possible_renames,
        "relocation_candidates": relocation_candidates,
        "duplicate_db_entries": duplicate_db_entries,
        "stale_queue_entries": _path_audit_stale_queue_entries(root),
        "stale_processed_state_rows": stale_processed_state_rows,
        "orphan_db_rows": orphan_db_rows,
        "limitations": [
            "rename matching is heuristic only: same basename or same filesize plus extension",
            "filesize rename matching requires tracks.filesize_bytes to be populated",
            "queue auditing checks JSON/JSONL files with 'queue' in the filename under data/",
        ],
    }


def _path_reconcile_best_match(matches: list[dict]) -> dict | None:
    if not matches:
        return None
    reason_rank = {
        "same_basename": 0,
        "fuzzy_filename": 1,
        "same_size_and_extension": 2,
    }
    return sorted(matches, key=lambda m: reason_rank.get(m.get("reason", ""), 99))[0]


def _path_reconcile_confidence(reason: str) -> float:
    if reason == "same_basename":
        return 0.90
    if reason == "same_size_and_extension":
        return 0.70
    if reason == "fuzzy_filename":
        return 0.80
    return 0.50


def _path_reconcile_candidate_tier(old_path: Path, new_path: Path, old_size, new_size) -> str:
    token_overlap = _path_audit_token_overlap(old_path, new_path)
    size_diff_pct = _path_audit_size_diff_pct(old_size, new_size)
    if size_diff_pct is None:
        size_similarity = 0.0
    else:
        size_similarity = max(0.0, 1.0 - min(size_diff_pct, 1.0))
    same_extension = old_path.suffix.lower() == new_path.suffix.lower()
    score = (token_overlap * 0.60) + (size_similarity * 0.30) + (0.10 if same_extension else 0.0)
    return _path_audit_orphan_candidate_tier(
        round(score, 6),
        token_overlap,
        size_diff_pct,
        same_extension,
        old_path,
        new_path,
    )


def path_reconcile_plan(root: Path, audit: dict) -> dict:
    from datetime import datetime, timezone

    actions: list[dict] = []
    rename_by_old_path: dict[str, dict] = {}

    for item in audit.get("possible_renames", []):
        old_path = item.get("db_row", {}).get("filepath", "")
        match = _path_reconcile_best_match(item.get("matches", []))
        if not old_path or match is None:
            continue
        reason = match.get("reason", "unknown")
        new_path = match.get("path")
        review_tier = _path_reconcile_candidate_tier(
            Path(old_path),
            Path(new_path),
            item.get("db_row", {}).get("filesize_bytes"),
            match.get("size"),
        ) if new_path else "REVIEW_CAREFULLY"
        action = {
            "action": "update_path_reference",
            "old_path": old_path,
            "new_path": new_path,
            "confidence": _path_reconcile_confidence(reason),
            "reason": reason,
            "risk": "LOW" if review_tier == "AUTO_SAFE_CANDIDATE" else "REVIEW_REQUIRED",
            "review_tier": review_tier,
        }
        actions.append(action)
        rename_by_old_path[old_path] = action

    for item in audit.get("relocation_candidates", []):
        old_path = item.get("old_path", "")
        new_path = item.get("new_path")
        if not old_path or not new_path:
            continue
        action = {
            "action": "update_path_reference",
            "old_path": old_path,
            "new_path": new_path,
            "confidence": 0.65,
            "reason": "relocation",
            "risk": "REVIEW_REQUIRED",
            "review_tier": "REVIEW_CAREFULLY",
            "token_overlap": item.get("token_overlap"),
            "size_diff_pct": item.get("size_diff_pct"),
        }
        actions.append(action)
        rename_by_old_path[old_path] = action

    for item in audit.get("orphan_candidates", []):
        if item.get("review_tier") != "AUTO_SAFE_CANDIDATE":
            continue
        old_path = item.get("old_path", "")
        new_path = item.get("candidate_path")
        if not old_path or not new_path:
            continue
        action = {
            "action": "update_path_reference",
            "old_path": old_path,
            "new_path": new_path,
            "confidence": item.get("score", 0.95),
            "reason": "orphan_auto_safe_candidate",
            "risk": "LOW",
            "review_tier": "AUTO_SAFE_CANDIDATE",
            "token_overlap": item.get("token_overlap"),
            "size_diff_pct": item.get("size_diff_pct"),
        }
        actions.append(action)
        rename_by_old_path[old_path] = action

    for entry in audit.get("stale_queue_entries", []):
        old_path = entry.get("path", "")
        candidate = rename_by_old_path.get(old_path)
        if candidate:
            actions.append({
                "action": "update_queue_reference",
                "queue_file": entry.get("queue_file"),
                "old_path": old_path,
                "new_path": candidate["new_path"],
                "confidence": candidate["confidence"],
                "reason": "candidate_found_from_path_audit",
                "risk": candidate.get("risk", "LOW"),
                "unresolved": False,
            })
        else:
            actions.append({
                "action": "update_queue_reference",
                "queue_file": entry.get("queue_file"),
                "old_path": old_path,
                "new_path": None,
                "confidence": 0.0,
                "reason": "unresolved_no_candidate",
                "risk": "REVIEW_REQUIRED",
                "unresolved": True,
            })

    for row in audit.get("orphan_db_rows", []):
        actions.append({
            "action": "mark_orphan_candidate",
            "old_path": row.get("filepath"),
            "reason": "missing_file_no_rename_candidate",
            "risk": "REVIEW_REQUIRED",
        })

    for duplicate in audit.get("duplicate_db_entries", []):
        actions.append({
            "action": "investigate_duplicate_path",
            "filepath": duplicate.get("filepath"),
            "count": duplicate.get("count"),
            "row_ids": duplicate.get("row_ids", []),
            "risk": "REVIEW_REQUIRED",
        })

    for row in audit.get("stale_processed_state_rows", []):
        actions.append({
            "action": "mark_stale_processed_state_path",
            "old_path": row.get("old_path"),
            "replacement_path": row.get("replacement_path"),
            "stage": row.get("stage"),
            "reason": row.get("reason", "superseded_by_existing_path"),
            "source_rows": row.get("source_rows", []),
            "risk": "LOW",
            "report_only": True,
        })

    summary: dict[str, int] = {}
    for action in actions:
        key = action["action"]
        summary[key] = summary.get(key, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "database": audit.get("database"),
        "dry_run": True,
        "apply_supported": False,
        "audit_summary": audit.get("summary", {}),
        "audit_findings": {
            "missing_files": audit.get("missing_files", []),
            "mixed_root_db_paths": audit.get("mixed_root_db_paths", []),
            "possible_renames": audit.get("possible_renames", []),
            "relocation_candidates": audit.get("relocation_candidates", []),
            "duplicate_db_entries": audit.get("duplicate_db_entries", []),
            "stale_queue_entries": audit.get("stale_queue_entries", []),
            "stale_processed_state_rows": audit.get("stale_processed_state_rows", []),
            "orphan_db_rows": audit.get("orphan_db_rows", []),
        },
        "planned_action_summary": summary,
        "planned_actions": actions,
        "limitations": [
            "plan only; no database, queue, file, or tag updates are implemented",
            "rename candidates come from path-audit heuristics only",
            "queue updates are unresolved unless the queue path exactly matches a rename old_path",
        ],
    }


def _path_reconcile_plan_review_state_candidates(plan_path: Path) -> list[Path]:
    candidates = [
        plan_path.with_name(f"{plan_path.stem}_review_state.json"),
    ]
    try:
        root = plan_path.parent.parent.parent
        candidates.append(root / "data" / "intelligence" / "path_reconcile_review_state.json")
    except Exception:
        pass
    return candidates


def _path_reconcile_load_review_state(plan_path: Path) -> dict:
    import json

    for candidate in _path_reconcile_plan_review_state_candidates(plan_path):
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _path_reconcile_action_is_approved(action: dict, plan_path: Path) -> bool:
    review_state = _path_reconcile_load_review_state(plan_path)

    for field in ("approved", "is_approved"):
        if action.get(field) is True:
            return True
    if str(action.get("review_status") or "").lower() == "approved":
        return True
    if str(action.get("approval_status") or "").lower() == "approved":
        return True

    approvals = review_state.get("approved_actions")
    if isinstance(approvals, list):
        for entry in approvals:
            if isinstance(entry, str):
                if entry == action.get("action_id") or entry == action.get("ledger_id"):
                    return True
            elif isinstance(entry, dict):
                keys = (
                    ("action_id", "action_id"),
                    ("ledger_id", "ledger_id"),
                    ("action", "action"),
                    ("old_path", "old_path"),
                    ("new_path", "new_path"),
                )
                if all(
                    entry.get(entry_key) == action.get(action_key)
                    for action_key, entry_key in keys
                    if action.get(action_key) not in (None, "")
                ):
                    return True

    items = review_state.get("items")
    if isinstance(items, dict):
        for key, value in items.items():
            if not isinstance(value, dict):
                continue
            if str(value.get("review_status") or "").lower() != "approved":
                continue
            signature = _path_reconcile_action_signature(action)
            if key == signature:
                return True
            if str(value.get("action_id") or "") == str(action.get("action_id") or ""):
                return True
            if value.get("old_path") == action.get("old_path") and value.get("new_path") == action.get("new_path"):
                return True
    return False


def _path_reconcile_action_signature(action: dict) -> str:
    return "|".join(
        [
            str(action.get("action") or ""),
            str(action.get("old_path") or ""),
            str(action.get("new_path") or ""),
            str(action.get("queue_file") or ""),
        ]
    )


def _path_reconcile_canonical_paths(root: Path) -> set[str]:
    db_path = _path_audit_db_path(root)
    if not db_path.exists():
        return set()
    try:
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            paths: set[str] = set()
            for table in ("tracks", "processed_state"):
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if exists is None:
                    continue
                for row in conn.execute(f"SELECT filepath FROM {table} WHERE filepath IS NOT NULL"):
                    raw = str(row["filepath"] or "")
                    if raw:
                        paths.add(str(Path(raw).expanduser().resolve(strict=False)))
                        paths.add(raw)
            return paths
        finally:
            conn.close()
    except Exception:
        return set()


def _path_reconcile_validate_action(
    action: dict,
    *,
    plan_path: Path,
    root: Path,
    canonical_paths: set[str],
) -> dict:
    issues: list[str] = []
    warnings: list[str] = []
    action_type = str(action.get("action") or "").strip()
    old_path = str(action.get("old_path") or "").strip()
    new_path = str(action.get("new_path") or "").strip()
    review_tier = str(action.get("review_tier") or "").strip()
    risk = str(action.get("risk") or "").strip()
    confidence = action.get("confidence")

    allowed_actions = {
        "update_path_reference",
        "update_queue_reference",
        "mark_orphan_candidate",
        "investigate_duplicate_path",
        "mark_stale_processed_state_path",
    }
    report_only_actions = {
        "mark_orphan_candidate",
        "investigate_duplicate_path",
        "mark_stale_processed_state_path",
    }

    if not action_type:
        issues.append("missing_action_type")
    elif action_type not in allowed_actions:
        issues.append(f"unsupported_action_type:{action_type}")

    if action_type in report_only_actions or action.get("report_only") is True:
        return {
            "action": action,
            "action_type": action_type,
            "status": "skipped",
            "reason": "report_only",
            "issues": [],
            "warnings": warnings,
        }

    if review_tier == "WEAK_MATCH":
        issues.append("weak_match_rejected")

    if review_tier == "REVIEW_CAREFULLY" and risk not in {"REVIEW_REQUIRED", "LOW"}:
        issues.append(f"invalid_risk_for_review_tier:{risk or 'missing'}")

    if risk == "REVIEW_REQUIRED" and not _path_reconcile_action_is_approved(action, plan_path):
        issues.append("review_required_not_approved")

    if action_type == "update_path_reference":
        if not old_path:
            issues.append("missing_old_path")
        if not new_path:
            issues.append("missing_new_path")
        # old_path is expected to be missing on disk: this action type exists
        # specifically to relink a DB row whose file is gone to a candidate
        # found elsewhere (see path_reconcile_plan). Only the candidate
        # (new_path) must actually exist for the action to be applicable.
        if new_path and not Path(new_path).expanduser().exists():
            issues.append("new_path_missing_on_disk")
    elif action_type == "update_queue_reference":
        if not old_path:
            issues.append("missing_old_path")
        if action.get("unresolved") is True or not new_path:
            warnings.append("queue_reference_unresolved")
        elif not Path(new_path).expanduser().exists():
            issues.append("new_path_missing_on_disk")
    elif action_type == "mark_stale_processed_state_path":
        if not old_path:
            issues.append("missing_old_path")
    elif action_type in {"mark_orphan_candidate", "investigate_duplicate_path"}:
        if not old_path and not str(action.get("filepath") or "").strip():
            issues.append("missing_reference_path")

    if old_path:
        resolved_old = str(Path(old_path).expanduser().resolve(strict=False))
        if resolved_old not in canonical_paths and old_path not in canonical_paths:
            issues.append("old_path_not_in_canonical_db")
        try:
            resolved_root = Path(old_path).expanduser().resolve(strict=False)
            resolved_root.relative_to(root)
        except Exception:
            issues.append("old_path_outside_root")

    if new_path:
        try:
            resolved_new = Path(new_path).expanduser().resolve(strict=False)
            resolved_new.relative_to(root)
        except Exception:
            issues.append("new_path_outside_root")

    if confidence is not None:
        try:
            conf = float(confidence)
            if not 0.0 <= conf <= 1.0:
                issues.append("confidence_out_of_range")
        except Exception:
            issues.append("confidence_not_numeric")

    if review_tier and review_tier not in {"AUTO_SAFE_CANDIDATE", "REVIEW_CAREFULLY", "WEAK_MATCH"}:
        warnings.append(f"unexpected_review_tier:{review_tier}")
    if risk and risk not in {"LOW", "REVIEW_REQUIRED"}:
        warnings.append(f"unexpected_risk:{risk}")

    status = "valid" if not issues else "invalid"
    return {
        "action": action,
        "action_type": action_type,
        "status": status,
        "reason": None if status == "valid" else issues[0],
        "issues": issues,
        "warnings": warnings,
    }


def path_reconcile_validate_plan(plan_path: Path) -> dict:
    import json
    from datetime import datetime, timezone

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("plan json must be an object")

    plan_root_raw = str(plan.get("root") or "").strip()
    if plan_root_raw:
        root = Path(plan_root_raw).expanduser().resolve()
    else:
        root = plan_path.parent.parent.parent.resolve()
    if not root.exists():
        raise ValueError(f"plan root does not exist: {root}")

    planned_actions = plan.get("planned_actions")
    if not isinstance(planned_actions, list):
        raise ValueError("plan json missing planned_actions list")

    canonical_paths = _path_reconcile_canonical_paths(root)
    validation_records: list[dict] = []
    reasons: dict[str, int] = {}
    totals = {"valid": 0, "invalid": 0, "skipped": 0}

    for action in planned_actions:
        if not isinstance(action, dict):
            record = {
                "action": action,
                "status": "invalid",
                "reason": "action_not_object",
                "issues": ["action_not_object"],
                "warnings": [],
            }
        else:
            record = _path_reconcile_validate_action(
                action,
                plan_path=plan_path,
                root=root,
                canonical_paths=canonical_paths,
            )
        status = record["status"]
        totals[status] = totals.get(status, 0) + 1
        if status != "valid":
            for issue in record.get("issues", []):
                reasons[issue] = reasons.get(issue, 0) + 1
        validation_records.append(record)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "plan_path": str(plan_path),
        "root": str(root),
        "total_actions": len(planned_actions),
        "valid_actions": totals.get("valid", 0),
        "invalid_actions": totals.get("invalid", 0),
        "skipped_actions": totals.get("skipped", 0),
        "reasons": dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
        "validation_records": validation_records,
    }
    return result


def path_reconcile_latest_plan_path(root: Path) -> Path | None:
    log_dir = root / "logs" / "path_reconcile"
    if not log_dir.exists():
        return None
    candidates = sorted(log_dir.glob("*_path_reconcile_plan.json"))
    return candidates[-1] if candidates else None
