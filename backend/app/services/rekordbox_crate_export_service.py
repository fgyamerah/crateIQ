"""Preview-first, staged Rekordbox XML exports for Manual Crates only."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

from ..core.library_root import assert_path_under_root, selected_library_root
from ..schemas.export import (
    RekordboxExportPreview,
    RekordboxExportRequest,
    RekordboxExportResult,
)
from . import crate_export_service, crate_service

_INVALID_XML_CHARS = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]"
)


def _xml_text(value: object | None) -> str:
    """Drop XML 1.0-invalid control characters before ElementTree escapes text."""
    return _INVALID_XML_CHARS.sub("", str(value or ""))


def _location_uri(path: str) -> str:
    if not path:
        return ""
    return "file://localhost/" + quote(path.replace("\\", "/"), safe="/:")


def _candidate_path(directory: Path, crate_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = f"{crate_export_service._safe_name(crate_name)}_{stamp}"
    candidate = directory / f"{stem}.xml"
    suffix = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{suffix}.xml"
        suffix += 1
    return candidate


def _render(crate_id: int, request: RekordboxExportRequest) -> RekordboxExportPreview | None:
    crate = crate_service.get_crate(crate_id)
    if crate is None:
        return None

    root = selected_library_root()
    output_dir = assert_path_under_root(root / "exports" / "rekordbox", root)
    output_path = _candidate_path(output_dir, crate.name)
    track_paths = crate_export_service._track_paths([track.track_id for track in crate.tracks])
    warnings = [
        "Staged Rekordbox XML only: this does not write to a live Rekordbox database, device, USB, or application folder."
    ]

    document = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(document, "PRODUCT", {"Name": "rekordbox", "Version": "6.0.0", "Company": "Pioneer DJ"})
    collection = ET.SubElement(document, "COLLECTION", {"Entries": str(len(crate.tracks))})
    playlists = ET.SubElement(document, "PLAYLISTS")
    root_node = ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "1"})
    playlist = ET.SubElement(
        root_node,
        "NODE",
        {"Name": _xml_text(crate.name), "Type": "1", "KeyType": "0", "Entries": str(len(crate.tracks))},
    )

    for index, track in enumerate(crate.tracks, start=1):
        display_path, warning = crate_export_service._display_path(
            track_paths.get(track.track_id, ""), request.path_mode, root
        )
        if warning:
            label = track.title or track.filename or "Track"
            warnings.append(f"#{track.position} {_xml_text(label)}: {warning}")
        attributes = {
            "TrackID": str(index),
            "Name": _xml_text(track.title or track.filename),
            "Artist": _xml_text(track.artist),
            "Composer": "",
            "Album": "",
            "Grouping": "",
            "Genre": _xml_text(track.genre),
            "Kind": Path(display_path).suffix.lstrip(".").upper(),
            "Size": "0",
            "TotalTime": str(int(track.duration_sec)) if track.duration_sec is not None else "0",
            "DiscNumber": "0",
            "TrackNumber": "0",
            "Year": "",
            "AverageBpm": f"{track.bpm:.2f}" if track.bpm is not None else "",
            "DateAdded": "",
            "BitRate": "0",
            "SampleRate": "0",
            "Comments": "",
            "PlayCount": "0",
            "Rating": "0",
            "Location": _location_uri(display_path),
            "Remixer": "",
            "Tonality": _xml_text(track.key_camelot),
            "Label": "",
            "Mix": "",
        }
        if not request.include_metadata:
            for key in ("Artist", "Genre", "AverageBpm", "Tonality"):
                attributes[key] = ""
        track_node = ET.SubElement(collection, "TRACK", attributes)
        if request.include_metadata and track.bpm is not None:
            ET.SubElement(track_node, "TEMPO", {"Inizio": "0.000", "Bpm": f"{track.bpm:.2f}", "Metro": "4/4", "Battito": "1"})
        ET.SubElement(playlist, "TRACK", {"Key": str(index)})

    if not crate.tracks:
        warnings.append("This crate is empty; the staged XML contains an empty playlist node.")
    if request.path_mode != "absolute":
        warnings.append("Filename and relative locations may need path mapping when imported into Rekordbox.")

    ET.indent(document, space="  ")
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        document, encoding="unicode", short_empty_elements=True
    ) + "\n"
    return RekordboxExportPreview(
        crate_id=crate.id,
        crate_name=crate.name,
        track_count=len(crate.tracks),
        destination_mode="staged",
        output_path=str(output_path),
        warnings=warnings,
        safety_notes=[
            "Creates an importable staged Rekordbox XML file only.",
            "Does not write to your live Rekordbox database or mutate music files, tags, BPM, key, cue points, or Mixed In Key data.",
        ],
        xml_content=xml_content,
    )


def preview(crate_id: int, request: RekordboxExportRequest) -> RekordboxExportPreview | None:
    return _render(crate_id, request)


def write(crate_id: int, request: RekordboxExportRequest) -> RekordboxExportResult | None:
    preview_result = _render(crate_id, request)
    if preview_result is None:
        return None
    if request.dry_run:
        return RekordboxExportResult(**preview_result.model_dump(), written=False)

    output_path = Path(preview_result.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(preview_result.xml_content, encoding="utf-8", newline="")
    return RekordboxExportResult(**preview_result.model_dump(), written=True)
