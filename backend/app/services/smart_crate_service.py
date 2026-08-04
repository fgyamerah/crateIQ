"""Deterministic local Smart Crate previews; never writes until explicitly saved."""
from __future__ import annotations

from typing import Optional

from modules.harmonic import camelot_distance, camelot_score

from ..core.pipeline_db import get_pipeline_conn, pipeline_db_exists
from ..models.track import Track
from ..schemas.smart_crate import SmartCratePreset, SmartCratePreview, SmartCrateRequest, SmartCrateTrack

PRESETS = [
    SmartCratePreset(id="afro-house-warmup", label="Afro House Warmup", description="Issue-free Afro House around a relaxed warmup BPM.", criteria=SmartCrateRequest(name="Afro House Warmup", bpm_min=118, bpm_max=123, genres=["Afro House"], issue_free_only=True, limit=25)),
    SmartCratePreset(id="peak-amapiano", label="Peak Time Amapiano", description="Issue-free Amapiano picks from the current library.", criteria=SmartCrateRequest(name="Peak Time Amapiano", bpm_min=108, bpm_max=116, genres=["Amapiano"], issue_free_only=True, limit=25)),
    SmartCratePreset(id="deep-house-builder", label="Deep House Builder", description="A steady Deep House building block.", criteria=SmartCrateRequest(name="Deep House Builder", bpm_min=120, bpm_max=124, genres=["Deep House"], issue_free_only=True, limit=25)),
    SmartCratePreset(id="highlife-hiplife", label="Highlife / Hiplife Set", description="A genre-led Ghanaian set suggestion.", criteria=SmartCrateRequest(name="Highlife / Hiplife Set", genres=["Highlife", "Hiplife"], issue_free_only=True, limit=25)),
]


def list_presets() -> list[SmartCratePreset]:
    return PRESETS


def _harmonic_match(mode: str, from_key: str, candidate_key: str) -> bool:
    if not candidate_key:
        return False
    distance, switched = camelot_distance(from_key, candidate_key)
    if mode == "exact":
        return distance == 0 and not switched
    if mode == "compatible":
        return (distance == 0) or (distance == 1 and not switched)
    from_number, candidate_number = int(from_key[:-1]), int(candidate_key[:-1])
    if from_key[-1] != candidate_key[-1]:
        return False
    if mode == "energy_up":
        return candidate_number == (from_number % 12) + 1
    return candidate_number == ((from_number - 2) % 12) + 1


def preview(criteria: SmartCrateRequest) -> SmartCratePreview:
    warnings: list[str] = []
    if not pipeline_db_exists():
        return SmartCratePreview(criteria=criteria, generated_name=criteria.name or "Smart Crate", explanation="No local library database is available.", warnings=["Run the library pipeline before generating suggestions."], track_count=0, tracks=[])
    with get_pipeline_conn() as conn:
        rows = conn.execute("SELECT * FROM tracks").fetchall()
    candidates: list[tuple[float, Track, list[str]]] = []
    center = ((criteria.bpm_min or 0) + (criteria.bpm_max or 0)) / 2 if criteria.bpm_min is not None and criteria.bpm_max is not None else None
    normalized_genres = {genre.lower() for genre in criteria.genres}
    for row in rows:
        track = Track.from_row(row)
        if criteria.bpm_min is not None and (track.bpm is None or track.bpm < criteria.bpm_min): continue
        if criteria.bpm_max is not None and (track.bpm is None or track.bpm > criteria.bpm_max): continue
        if normalized_genres and (track.genre or "").lower() not in normalized_genres: continue
        if criteria.issue_free_only and track.issues: continue
        if criteria.harmonic_mode and not _harmonic_match(criteria.harmonic_mode, criteria.camelot_key or "", track.key_camelot or ""): continue
        score, reasons = 0.0, []
        if criteria.issue_free_only: score += 30; reasons.append("Issue-free")
        if normalized_genres: score += 20; reasons.append("Genre match")
        if center is not None and track.bpm is not None:
            score += max(0, 20 - abs(track.bpm - center) * 4); reasons.append("BPM range match")
        if criteria.harmonic_mode and track.key_camelot:
            score += 25 * camelot_score(criteria.camelot_key or "", track.key_camelot); reasons.append(f"{criteria.harmonic_mode.replace('_', ' ')} key match")
        candidates.append((score, track, reasons or ["Library match"]))
    candidates.sort(key=lambda item: (-item[0], (item[1].artist or "").lower(), (item[1].title or item[1].filename).lower(), item[1].id))
    items = [SmartCrateTrack(track_id=track.id, position=index, artist=track.artist, title=track.title, filename=track.filename, genre=track.genre, bpm=track.bpm, key_camelot=track.key_camelot, duration_sec=track.duration_sec, reasons=reasons) for index, (_score, track, reasons) in enumerate(candidates[:criteria.limit], start=1)]
    if not items: warnings.append("No tracks matched these criteria. Broaden BPM, genre, key, or issue-free filters.")
    if criteria.limit < len(candidates): warnings.append(f"Showing the first {criteria.limit} matching tracks.")
    return SmartCratePreview(criteria=criteria, generated_name=criteria.name or "Smart Crate", explanation="Deterministic local ranking uses issue state, BPM proximity, Camelot compatibility, and genre matches when selected.", warnings=warnings, track_count=len(items), tracks=items)
