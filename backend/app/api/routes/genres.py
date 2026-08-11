"""Thin typed adapter over `genre_taxonomy_service` (Genre Intelligence Phase 3).

Business logic (repository config loading, precedence resolution, preview,
apply, provenance) lives in the service module; this file only handles HTTP
concerns: request/response models, path lookup, and confirm/validation
errors.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from ...core.library_root import library_db_path, selected_library_root
from ...services import genre_taxonomy_service as svc

router = APIRouter(tags=["genres"])


def db():
    p = library_db_path(selected_library_root())
    if not p.is_file():
        raise ValueError("Configured library is not initialized.")
    return p


class GenreIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_name: str | None = None
    description: str | None = None
    enabled: bool = True
    sort_order: int = 100


class MappingIn(BaseModel):
    raw_genre: str = Field(min_length=1, max_length=200)
    normalized_genre: str = Field(min_length=1, max_length=100)
    confidence: str = "manual"
    source: str = "user_mapping"
    enabled: bool = True


class ApplyIn(BaseModel):
    confirm: bool = False
    track_ids: list[int] = Field(default_factory=list)


def _repo_fallback_taxonomy() -> dict:
    return {"genres": [{"id": None, **g, "enabled": 1, "source": "repository"} for g in svc.repo_taxonomy()]}


@router.get("/genres/taxonomy")
def get_taxonomy():
    try:
        path = db()
    except ValueError:
        return _repo_fallback_taxonomy()
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        try:
            return {"genres": svc.effective_taxonomy(c)}
        except ValueError as exc:
            raise HTTPException(422, str(exc))


@router.post("/genres/taxonomy")
def add_taxonomy(body: GenreIn):
    with sqlite3.connect(db()) as c:
        svc.ensure_tables(c)
        if svc.taxonomy_rows_for_key(c, body.name):
            raise HTTPException(422, "Preferred genre already exists.")
        now = svc.now()
        try:
            c.execute(
                "INSERT INTO genre_taxonomy(name,parent_name,description,enabled,sort_order,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (body.name.strip(), body.parent_name, body.description, int(body.enabled), body.sort_order, now, now),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(422, "Preferred genre already exists.")
    return get_taxonomy()


@router.patch("/genres/taxonomy/{genre_id}")
def patch_taxonomy(body: GenreIn, genre_id: int = Path(ge=1)):
    with sqlite3.connect(db()) as c:
        c.row_factory = sqlite3.Row
        svc.ensure_tables(c)
        existing = c.execute("SELECT * FROM genre_taxonomy WHERE id=?", (genre_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Preferred genre not found.")
        if svc.taxonomy_rows_for_key(c, body.name, exclude_id=genre_id):
            raise HTTPException(422, "Preferred genre already exists.")
        c.execute(
            "UPDATE genre_taxonomy SET name=?,parent_name=?,description=?,enabled=?,sort_order=?,updated_at=? WHERE id=?",
            (body.name.strip(), body.parent_name, body.description, int(body.enabled), body.sort_order, svc.now(), genre_id),
        )
    return get_taxonomy()


@router.delete("/genres/taxonomy/{genre_id}")
def disable_taxonomy(genre_id: int = Path(ge=1)):
    with sqlite3.connect(db()) as c:
        svc.ensure_tables(c)
        changed = c.execute(
            "UPDATE genre_taxonomy SET enabled=0,updated_at=? WHERE id=?", (svc.now(), genre_id)
        ).rowcount
        if not changed:
            raise HTTPException(404, "Preferred genre not found.")
    return get_taxonomy()


@router.get("/genres/mappings")
def get_mappings():
    try:
        path = db()
    except ValueError:
        return {"mappings": [
            {"id": None, "raw_genre": m["raw_genre"],
             "normalized_genre": svc.NEEDS_REVIEW if m["needs_review"] else m["normalized_genre"],
             "confidence": m["confidence"], "source": "default_taxonomy", "enabled": 1}
            for m in svc.repo_mappings()
        ]}
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        try:
            return {"mappings": svc.effective_mappings(c)}
        except ValueError as exc:
            raise HTTPException(422, str(exc))


@router.post("/genres/mappings")
def add_mapping(body: MappingIn):
    with sqlite3.connect(db()) as c:
        c.row_factory = sqlite3.Row
        svc.ensure_tables(c)
        target = svc.resolve_taxonomy_name(c, body.normalized_genre)
        if target.name is None:
            raise HTTPException(422, "Normalized genre must be in the preferred taxonomy.")
        if body.confidence not in svc.VALID_CONFIDENCES or body.source not in svc.VALID_SOURCES:
            raise HTTPException(422, "Invalid mapping confidence or source.")
        if svc.mapping_rows_for_key(c, body.raw_genre):
            raise HTTPException(422, "Raw genre mapping already exists.")
        try:
            c.execute(
                "INSERT INTO genre_mappings(raw_genre,normalized_genre,confidence,source,enabled,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (body.raw_genre.strip(), target.name, body.confidence, body.source,
                 int(body.enabled), svc.now(), svc.now()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(422, "Raw genre mapping already exists.")
    return get_mappings()


@router.patch("/genres/mappings/{mapping_id}")
def patch_mapping(body: MappingIn, mapping_id: int = Path(ge=1)):
    with sqlite3.connect(db()) as c:
        c.row_factory = sqlite3.Row
        svc.ensure_tables(c)
        if not c.execute("SELECT id FROM genre_mappings WHERE id=?", (mapping_id,)).fetchone():
            raise HTTPException(404, "Genre mapping not found.")
        target = svc.resolve_taxonomy_name(c, body.normalized_genre)
        if target.name is None or body.confidence not in svc.VALID_CONFIDENCES or body.source not in svc.VALID_SOURCES:
            raise HTTPException(422, "Invalid normalized genre, confidence, or source.")
        if svc.mapping_rows_for_key(c, body.raw_genre, exclude_id=mapping_id):
            raise HTTPException(422, "Raw genre mapping already exists.")
        c.execute(
            "UPDATE genre_mappings SET raw_genre=?,normalized_genre=?,confidence=?,source=?,enabled=?,updated_at=? WHERE id=?",
            (body.raw_genre.strip(), target.name, body.confidence, body.source,
             int(body.enabled), svc.now(), mapping_id),
        )
    return get_mappings()


@router.delete("/genres/mappings/{mapping_id}")
def disable_mapping(mapping_id: int = Path(ge=1)):
    with sqlite3.connect(db()) as c:
        svc.ensure_tables(c)
        changed = c.execute(
            "UPDATE genre_mappings SET enabled=0,updated_at=? WHERE id=?", (svc.now(), mapping_id)
        ).rowcount
        if not changed:
            raise HTTPException(404, "Genre mapping not found.")
    return get_mappings()


_SAFETY = ["db_only", "review_first", "no_tag_writes", "no_file_writes"]


@router.get("/genres/review")
def review():
    try:
        with sqlite3.connect(db()) as c:
            items = svc.latest_snapshot_items(c)
    except ValueError:
        return {"summary": svc.summarize([]), "items": [], "safety": _SAFETY,
                "message": "Initialize the local index before genre review."}
    if items is None:
        return {"summary": svc.summarize([]), "items": [], "safety": _SAFETY,
                "message": "Refresh preview to review local genre values."}
    return {"summary": svc.summarize(items), "items": items, "safety": _SAFETY}


@router.post("/genres/review/preview-refresh")
def refresh():
    with sqlite3.connect(db()) as c:
        c.row_factory = sqlite3.Row
        items = svc.build_preview_items(c)
        svc.save_preview_snapshot(c, items)
    return {"summary": svc.summarize(items), "items": items, "safety": _SAFETY}


@router.post("/genres/review/apply")
def apply(body: ApplyIn):
    if not body.confirm:
        raise HTTPException(422, "Apply requires confirm=true.")
    with sqlite3.connect(db()) as c:
        c.row_factory = sqlite3.Row
        try:
            return svc.apply_selected(c, body.track_ids)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
