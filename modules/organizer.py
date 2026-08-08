"""
File organizer — renames and moves files into the sorted library structure.

Beets CLI import is disabled by policy (beet CLI binary is forbidden in
CrateIQ application/tooling code); this module always uses the pure-Python
organizer below, which relies on existing file tags.

Naming convention:
    SORTED / first_letter / Artist Name / Artist Name - Track Title (Mix).ext

Special cases:
    - Various Artists / unknown artist → _compilations/
    - Missing title → _unsorted/
    - Characters illegal on Windows are stripped from filenames.

After organizing, the DB filepath is updated to the new location.
"""
import logging
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
import db
from modules.sanitizer import sanitize_text
from modules.textlog import log_action

log = logging.getLogger(__name__)

# Characters that are illegal in Windows filenames
_WIN_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Collapse runs of whitespace / underscores
_WHITESPACE  = re.compile(r'[\s_]+')
# Bracket content: (...) or [...] — stripped from titles in filesystem paths
# per spec: version stored in metadata only, not in the filename.
_RE_BRACKET_CONTENT = re.compile(r'\s*[\(\[][^\)\]]*[\)\]]\s*')

VA_NAMES = {"various artists", "various", "va", "v.a.", "v/a"}
MAX_COMPONENT_LEN = 80   # per directory component (Windows safety)


def _title_for_path(title: str) -> str:
    """
    Strip all parenthetical / bracket content from a title for use in the
    filesystem filename.

    Per spec: version info is stored in metadata only.  The on-disk filename
    must be clean: TrackName.mp3, not TrackName (Original Mix).mp3.

    Falls back to the original title if stripping would produce an empty string.

    >>> _title_for_path("Ngoku (Uhuru Rem)")
    'Ngoku'
    >>> _title_for_path("Hey (Original Mix)")
    'Hey'
    >>> _title_for_path("Track [Extended Mix]")
    'Track'
    >>> _title_for_path("Rainbow")
    'Rainbow'
    """
    cleaned = _RE_BRACKET_CONTENT.sub("", title).strip()
    return cleaned if cleaned else title


# ---------------------------------------------------------------------------
# Route classification
#
# Priority order (first match wins):
#   1. acapella  2. instrumental  3. dj_tools  4. edits
#   5. bootlegs  6. live          7. artists   8. unknown
#
# Fields checked per route: filename → title → album → comment
# ---------------------------------------------------------------------------

_ROUTE_PATTERNS: Dict[str, List[str]] = {
    "acapella": [
        r"\bacapella\b",
        r"\ba\s+capella\b",
        r"\ba\s+cappella\b",
    ],
    "instrumental": [
        r"\binstrumental\b",
        r"\binstr\b",
        r"\binst\s+mix\b",
    ],
    "dj_tools": [
        r"\bdj\s+tool\b",
        r"\btool\b",
        r"\bintro\s+tool\b",
        r"\btransition\s+tool\b",
        r"\bscratch\s+tool\b",
        r"\bloop\s+tool\b",
        r"\bbeat\s+tool\b",
    ],
    # edits allowlist — see _EDITS_ALLOW_PATTERNS / _EDITS_DENY_PATTERNS below
    # for the full two-stage matching logic used at runtime.
    "edits": [],
    "bootlegs": [
        r"\bbootleg\b",
        r"\bmashup\b",
        r"\bblend\b",
        r"\bunofficial\b",
    ],
    "live": [
        r"\blive\b",
        r"\blive\s+mix\b",
        r"\blive\s+version\b",
        r"\brecorded\s+live\b",
    ],
}

# ---------------------------------------------------------------------------
# Edits two-stage matching
#
# Allowlist: patterns that DO route a track to /Edits/.
#   All are compound (two-word / hyphenated) to avoid false positives.
#   Plain "\bedit\b" is intentionally excluded.
#
# Denylist: commercial release naming that should stay in /Artists/.
#   Checked only when an allowlist pattern already matched the same field.
#   A denylist hit overrides the allowlist match for that field.
# ---------------------------------------------------------------------------

_EDITS_ALLOW_PATTERNS: List[str] = [
    r"\bdj[\s-]edit\b",          # DJ Edit, DJ-Edit
    r"\bre-?edit\b",              # Re-Edit, Reedit
    r"\bextended\s+edit\b",       # Extended Edit
    r"\bclub\s+edit\b",           # Club Edit
    r"\bintro\s+edit\b",          # Intro Edit
    r"\boutro\s+edit\b",          # Outro Edit
    r"\btransition\s+edit\b",     # Transition Edit
    r"\bquick\s+edit\b",          # Quick Edit
    r"\bbootleg\s+edit\b",        # Bootleg Edit
]

_EDITS_DENY_PATTERNS: List[str] = [
    r"\bradio\s+edit\b",          # Radio Edit  — commercial single version
    r"\bclean\s+edit\b",          # Clean Edit  — radio-clean version
    r"\bdirty\s+edit\b",          # Dirty Edit  — explicit radio version
    r"\bradio\s+mix\b",           # Radio Mix
    r"\bextended\s+mix\b",        # Extended Mix
    r"\bclub\s+mix\b",            # Club Mix
    r"\boriginal\s+mix\b",        # Original Mix
]

# Priority-ordered list — checked in this exact order
_ROUTE_PRIORITY = ["acapella", "instrumental", "dj_tools", "edits", "bootlegs", "live"]

# Maps route name → destination base directory (populated lazily so config
# overrides in config_local.py are respected at runtime)
_ROUTE_BASE: Dict[str, "Path"] = {}


def _get_route_base() -> Dict[str, "Path"]:
    """Return the route→directory mapping, building it on first call."""
    if not _ROUTE_BASE:
        _ROUTE_BASE.update({
            "acapella":     config.ACAPELLA,
            "instrumental": config.INSTRUMENTAL,
            "dj_tools":     config.DJ_TOOLS,
            "edits":        config.EDITS,
            "bootlegs":     config.BOOTLEGS,
            "live":         config.LIVE,
            "unknown":      config.UNKNOWN_ROUTE,
        })
    return _ROUTE_BASE


# ---- Per-route pattern helpers (reusable externally) ----------------------

def _match_route_pattern(route: str, text: str) -> Optional[str]:
    """Return the first matching regex pattern for *route* in *text*, else None."""
    for pat in _ROUTE_PATTERNS[route]:
        if re.search(pat, text, re.IGNORECASE):
            return pat
    return None


def _matches_acapella(text: str) -> bool:
    return _match_route_pattern("acapella", text) is not None


def _matches_instrumental(text: str) -> bool:
    return _match_route_pattern("instrumental", text) is not None


def _matches_dj_tools(text: str) -> bool:
    return _match_route_pattern("dj_tools", text) is not None


def _check_edits_field(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Two-stage edits check for a single text field.

    Stage 1 — allowlist: does the text contain a DJ-oriented edit pattern?
    Stage 2 — denylist: does the same text also contain a commercial naming
               pattern that should keep the track in /Artists/?

    Returns:
        (allow_pattern, deny_pattern)
        - (None,  None)  → no allowlist match; skip edits routing for this field
        - (allow, None)  → allowlist matched, no deny override → route to /Edits/
        - (allow, deny)  → both matched; deny wins → keep in /Artists/
    """
    allow_pat = next(
        (p for p in _EDITS_ALLOW_PATTERNS if re.search(p, text, re.IGNORECASE)), None
    )
    if not allow_pat:
        return None, None
    deny_pat = next(
        (p for p in _EDITS_DENY_PATTERNS if re.search(p, text, re.IGNORECASE)), None
    )
    return allow_pat, deny_pat


def _matches_edits(text: str) -> bool:
    """Return True only when text passes the edits allowlist and is not blocked by the denylist."""
    allow_pat, deny_pat = _check_edits_field(text)
    return allow_pat is not None and deny_pat is None


def _matches_bootlegs(text: str) -> bool:
    return _match_route_pattern("bootlegs", text) is not None


def _matches_live(text: str) -> bool:
    return _match_route_pattern("live", text) is not None


# ---- Extra tag reader for classification fields ---------------------------

def _read_classify_fields(path: Path) -> Dict[str, str]:
    """
    Read title, album, and comment from file tags for route classification.
    Returns a dict with those three keys (all str, possibly empty).
    Comment requires non-easy mutagen access for ID3 COMM frames.
    """
    result: Dict[str, str] = {"title": "", "album": "", "comment": ""}
    try:
        from mutagen import File as MFile

        # easy=True gives reliable title and album for all supported formats
        easy = MFile(str(path), easy=True)
        if easy is not None:
            get = lambda key: (easy.get(key) or [""])[0]
            result["title"]   = get("title")
            result["album"]   = get("album")
            result["comment"] = get("comment")  # works for Vorbis; may be empty for ID3

        # If comment is still empty, try non-easy access for ID3 COMM frames
        if not result["comment"]:
            full = MFile(str(path))
            if full is not None and full.tags is not None:
                for key in list(full.tags.keys()):
                    if key.startswith("COMM"):
                        val = full.tags[key]
                        if hasattr(val, "text") and val.text:
                            result["comment"] = str(val.text[0])
                        break
    except Exception as exc:
        log.debug("Could not read classify fields from %s: %s", path.name, exc)
    return result


# ---- Primary classification helper ----------------------------------------

def classify_track_route(track_info: Dict[str, str], filename: str) -> str:
    """
    Classify a track into a route using metadata and filename.

    Checks these fields in order for each route: filename → title → album → comment.
    Route priority (first match wins):
        acapella > instrumental > dj_tools > edits > bootlegs > live > artists > unknown

    Args:
        track_info: dict with optional keys: title, album, comment, artist
        filename:   original filename (stem or full name) used as the first search field

    Returns:
        Route name string — one of:
        "acapella", "instrumental", "dj_tools", "edits", "bootlegs",
        "live", "artists", "unknown"

    Side-effects:
        Logs selected route, trigger field, and matched pattern at INFO level.
    """
    search_fields = [
        ("filename", filename or ""),
        ("title",    track_info.get("title",   "") or ""),
        ("album",    track_info.get("album",   "") or ""),
        ("comment",  track_info.get("comment", "") or ""),
    ]

    for route in _ROUTE_PRIORITY:
        for field_name, field_value in search_fields:
            if not field_value:
                continue

            # Edits uses two-stage allow/deny logic; all other routes are simple.
            if route == "edits":
                allow_pat, deny_pat = _check_edits_field(field_value)
                if deny_pat:
                    log.info(
                        "ROUTE: Edits DENIED (trigger=%s, overridden by deny pattern) [%s]",
                        field_name, filename,
                    )
                    log_action(
                        f"ROUTE: Edits DENIED (trigger={field_name},"
                        f" allow={allow_pat!r} overridden by deny={deny_pat!r}) [{filename}]"
                    )
                    continue  # deny blocks this field; check remaining fields
                if allow_pat:
                    log.info(
                        "ROUTE: %s (trigger=%s) [%s]",
                        "Edits", field_name, filename,
                    )
                    log_action(
                        f"ROUTE: Edits (trigger={field_name}) [{filename}]"
                    )
                    return "edits"
                continue  # no allowlist match in this field

            matched = _match_route_pattern(route, field_value)
            if matched:
                route_label = route.capitalize()
                log.info(
                    "ROUTE: %s (trigger=%s) [%s]",
                    route_label, field_name, filename,
                )
                log_action(
                    f"ROUTE: {route_label} (trigger={field_name}) [{filename}]"
                )
                return route

    # No special pattern matched — fall back based on metadata quality
    artist = track_info.get("artist", "") or ""
    title  = track_info.get("title",  "") or ""
    if not artist and not title:
        log.info("ROUTE UNKNOWN | no artist/title metadata | file=%s", filename)
        log_action(f"ROUTE UNKNOWN | no artist/title metadata | file={filename}")
        return "unknown"

    log.debug("ROUTE ARTISTS | no special pattern matched | file=%s", filename)
    return "artists"


# ---- Route-aware destination builder --------------------------------------

def _build_route_dest(route: str, artist: str, title: str, suffix: str) -> Path:
    """
    Return destination Path for a track given its *route*.

    - "artists" uses the existing letter-indexed SORTED structure (unchanged).
    - All other routes use: <ROUTE_BASE>/<Artist>/<Artist> - <Title><ext>
      (no letter prefix — simpler flat artist folders per route directory).
    - Various-Artists / empty artist → <ROUTE_BASE>/_compilations/<Title><ext>
    """
    if route == "artists":
        return _build_dest(artist, title, suffix)

    base_dir = _get_route_base().get(route, config.UNKNOWN_ROUTE)

    safe_artist = sanitize(artist) if artist else ""
    safe_title  = sanitize(title)  if title  else ""

    if not safe_artist or safe_artist.lower() in VA_NAMES:
        comp_dir = base_dir / "_compilations"
        filename = safe_title + suffix if safe_title else "unknown" + suffix
        return comp_dir / filename

    path_title = _title_for_path(safe_title) if safe_title else ""

    if not path_title:
        return base_dir / safe_artist / ("unknown" + suffix)

    filename = f"{path_title}{suffix}"
    return base_dir / safe_artist / filename


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------
def sanitize(name: str, max_len: int = MAX_COMPONENT_LEN) -> str:
    """
    Return a cross-platform-safe filename component (not the full path).

    Processing order:
      1. Remove URL/promo junk (sanitize_text) — last-resort defence against
         URLs or DJ-pool watermarks reaching the filesystem.
      2. NFC unicode normalisation.
      3. Strip Windows-illegal characters.
      4. Collapse whitespace / underscores.
      5. Strip leading dots and trailing spaces/dots.
      6. Truncate to max_len.
    """
    # Step 1 — remove URL/promo junk before touching the filesystem
    name = sanitize_text(name)
    # Step 2 — normalize unicode (NFC) — keeps accented chars, collapses oddities
    name = unicodedata.normalize("NFC", name)
    # Step 3 — strip Windows-illegal characters
    name = _WIN_ILLEGAL.sub("", name)
    # Step 4 — collapse whitespace
    name = _WHITESPACE.sub(" ", name).strip()
    # Step 5 — strip leading dots and trailing spaces/dots (Windows quirks)
    name = name.strip(". ")
    # Step 6 — truncate
    return name[:max_len]


def _first_letter(artist: str) -> str:
    """Return the index letter for the artist (A-Z or #)."""
    a = artist.strip().upper()
    if not a:
        return "#"
    # Strip "The ", "A " prefixes for sorting purposes
    for prefix in ("THE ", "A "):
        if a.startswith(prefix):
            a = a[len(prefix):]
            break
    first = a[0]
    return first if first.isalpha() else "#"


def _build_dest(artist: str, title: str, suffix: str) -> Path:
    """Return the destination Path for a track (does not create dirs).

    Final structure: SORTED/<letter>/<Artist>/<Track>.ext
    Filename is the track title only (no artist prefix) per spec.
    """
    safe_artist = sanitize(artist) if artist else ""
    safe_title  = sanitize(title)  if title  else ""

    if not safe_artist or safe_artist.lower() in VA_NAMES:
        base_dir = config.COMPILATIONS
        filename = safe_title + suffix if safe_title else "unknown" + suffix
    elif not safe_title:
        base_dir = config.UNSORTED
        filename = Path(safe_artist + suffix).name  # last resort
    else:
        letter      = _first_letter(safe_artist)
        base_dir    = config.SORTED / letter / safe_artist
        path_title  = _title_for_path(safe_title)
        filename    = f"{path_title}{suffix}"

    return base_dir / filename


def _unique_dest(dest: Path) -> Path:
    """If dest already exists, append a counter to avoid overwrites."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    parent = dest.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# Tag reading (fallback, when beets is not used or fails)
# ---------------------------------------------------------------------------
def _read_tags(path: Path) -> Tuple[str, str, str, str]:
    """
    Read artist, title, album_artist, genre from file tags.
    Returns ("", "", "", "") on any failure.
    """
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return "", "", "", ""
        get = lambda key: (audio.get(key) or [""])[0]
        artist       = get("artist")
        title        = get("title")
        album_artist = get("albumartist") or get("album_artist")
        genre        = get("genre")
        return artist, title, album_artist, genre
    except Exception as exc:
        log.debug("Could not read tags from %s: %s", path.name, exc)
        return "", "", "", ""


# ---------------------------------------------------------------------------
# beets-based organizer (primary)
# ---------------------------------------------------------------------------
def _run_beets(files: List[Path], dry_run: bool) -> Tuple[List[Path], List[Path]]:
    """
    Historically shelled out to the `beet` CLI. CrateIQ's invariant is now
    Beets Python API = allowed, `beet` CLI binary = forbidden anywhere in
    application/tooling code, so this always defers to the Python fallback
    organizer below (`_organize_file`). `--skip-beets` remains accepted for
    CLI compatibility but has no additional effect since beets CLI import was
    removed.
    """
    log.info("Beets CLI import is disabled by policy — using Python fallback organizer")
    return [], files


# ---------------------------------------------------------------------------
# Pure-Python fallback organizer
# ---------------------------------------------------------------------------
def _organize_file(path: Path, dry_run: bool) -> Optional[Path]:
    """
    Move a single file into the sorted library.

    Parse / validate stage (before any file move):
      1. Read existing ID3/Vorbis tags.
      2. Snapshot raw tags for rollback history.
      3. Validate artist with is_valid_artist() — catches garbage like "55. Bontan"
         or "01 - Black Motion" that crept into tags from badly-named files.
      4. If tags are invalid/absent, fall back to filename parsing via
         parse_filename_stem(), which strips track-number prefixes and handles
         all common separator styles.
      5. Final safety net: if nothing produces a valid artist, use "Unknown Artist".

    Returns new Path on success, None on failure.
    """
    from modules.parser import (
        is_valid_artist, is_valid_title, parse_filename_stem,
        classify_name_candidate,
    )

    artist, title, album_artist, genre = _read_tags(path)
    effective_artist = album_artist or artist
    # Save the pure 'artist' tag separately — used as label-detection fallback.
    # When album_artist is a label name, _raw_artist_tag may hold the real artist.
    _raw_artist_tag = artist

    # Snapshot RAW tags before any correction — preserved for rollback
    original_meta = {
        "artist":   artist,
        "title":    title,
        "album":    "",
        "genre":    genre,
        "filepath": str(path),
    }

    # -----------------------------------------------------------------------
    # PARSE STAGE
    # -----------------------------------------------------------------------
    artist_ok = is_valid_artist(effective_artist)
    title_ok  = is_valid_title(title)

    if not artist_ok or not title_ok:
        parsed = parse_filename_stem(path.stem)
        parsed_artist = parsed.get("artist", "")
        parsed_title  = parsed.get("title",  "")
        parsed_tnum   = parsed.get("track_number")

        if not artist_ok:
            if is_valid_artist(parsed_artist):
                log.info(
                    "Parser: artist from filename %r (tag %r rejected) [%s]",
                    parsed_artist, effective_artist, path.name,
                )
                log_action(
                    f"PARSE: artist from filename {parsed_artist!r}"
                    f" (tag {effective_artist!r} rejected) [{path.name}]"
                )
                effective_artist = parsed_artist
                artist           = parsed_artist
            else:
                log.debug("Parser: filename also gave no valid artist for %s", path.name)

        if not title_ok:
            if is_valid_title(parsed_title):
                log.info("Parser: title from filename %r [%s]", parsed_title, path.name)
                log_action(f"PARSE: title from filename {parsed_title!r} [{path.name}]")
                title = parsed_title

        if parsed_tnum is not None:
            log.debug("Parser: track number %d stripped from filename [%s]", parsed_tnum, path.name)
            log_action(f"PARSE: removed track number {parsed_tnum} from filename [{path.name}]")

    # Final safety net — never create a folder named after a number or symbol
    if not is_valid_artist(effective_artist):
        log.warning(
            'REJECTED ARTIST: %r → replaced with Unknown Artist [%s]',
            effective_artist, path.name,
        )
        log_action(
            f"REJECTED ARTIST: {effective_artist!r} → replaced with Unknown Artist"
            f" [{path.name}]"
        )
        effective_artist = "Unknown Artist"
        artist           = "Unknown Artist"

    if not is_valid_title(title):
        title = path.stem  # last resort: raw filename stem

    # -----------------------------------------------------------------------
    # PRE-PATH SANITIZATION
    # The tag-sanitizer pipeline step runs *after* organizer (step 6 vs 5),
    # so we scrub artist and title here to stop URL/promo junk from leaking
    # into folder names.  The same sanitize_text() logic runs again later
    # on the file's ID3 tags — this pass only affects path construction.
    # -----------------------------------------------------------------------
    _dirty_artist = effective_artist
    effective_artist = sanitize_text(effective_artist).strip()
    if effective_artist != _dirty_artist:
        log.info(
            "PATH SANITIZE: artist %r → %r [%s]",
            _dirty_artist, effective_artist or "(empty)", path.name,
        )
        log_action(
            f"PATH SANITIZE: artist {_dirty_artist!r}"
            f" → {effective_artist!r} [{path.name}]"
        )
        if not is_valid_artist(effective_artist):
            log.warning(
                'REJECTED ARTIST: %r → replaced with Unknown Artist'
                ' (after URL/symbol cleaning) [%s]',
                _dirty_artist, path.name,
            )
            log_action(
                f"REJECTED ARTIST: {_dirty_artist!r} → replaced with Unknown Artist"
                f" (after URL/symbol cleaning) [{path.name}]"
            )
            effective_artist = "Unknown Artist"
        artist = effective_artist

    _dirty_title = title
    title = sanitize_text(title).strip() if title else title
    if title != _dirty_title:
        log.info(
            "PATH SANITIZE: title %r → %r [%s]",
            _dirty_title, title or "(empty)", path.name,
        )
        log_action(
            f"PATH SANITIZE: title {_dirty_title!r}"
            f" → {title!r} [{path.name}]"
        )
    # If sanitization emptied the title entirely, fall back to sanitized stem
    if not title:
        title = sanitize(path.stem)

    # -----------------------------------------------------------------------
    # LABEL DETECTION
    # Classify the effective artist to prevent label names from becoming
    # artist folder names.  Runs after sanitization so URLs/symbols are gone.
    # Logic:
    #   label   → reject; try _raw_artist_tag as fallback, else Unknown Artist
    #   unknown → prefer _raw_artist_tag if it has a stronger artist signal
    #   artist  → use normally
    # -----------------------------------------------------------------------
    if effective_artist not in ("Unknown Artist", ""):
        _cls         = classify_name_candidate(effective_artist)
        _cls_type    = _cls["type"]
        _cls_score   = int(_cls["score"])
        _cls_reasons = (
            ", ".join(_cls["reasons"]) if _cls["reasons"] else "no signals"
        )

        if _cls_type == "label":
            log.warning(
                "LABEL DETECTED: %r (score=%d, reasons=[%s]) → fallback [%s]",
                effective_artist, _cls_score, _cls_reasons, path.name,
            )
            log_action(
                f"LABEL DETECTED: {effective_artist!r} (score={_cls_score},"
                f" reasons=[{_cls_reasons}]) → fallback [{path.name}]"
            )
            # Attempt to fall back to the raw artist tag (tag artist ≠ album_artist).
            # This handles the common case where album_artist holds the label name
            # and the 'artist' tag holds the actual performing artist.
            _fallback: Optional[str] = None
            if (
                _raw_artist_tag
                and _raw_artist_tag != effective_artist
                and is_valid_artist(_raw_artist_tag)
            ):
                _fallback_cls = classify_name_candidate(_raw_artist_tag)
                if _fallback_cls["type"] != "label":
                    _fallback = _raw_artist_tag

            if _fallback:
                log.info(
                    "LABEL FALLBACK: using original tag artist %r [%s]",
                    _fallback, path.name,
                )
                log_action(
                    f"LABEL FALLBACK: {_fallback!r} [{path.name}]"
                )
                effective_artist = _fallback
                artist           = _fallback
            else:
                log.info(
                    "LABEL FALLBACK: no valid non-label artist → Unknown Artist [%s]",
                    path.name,
                )
                log_action(
                    f"LABEL FALLBACK: Unknown Artist [{path.name}]"
                )
                effective_artist = "Unknown Artist"
                artist           = "Unknown Artist"

        elif _cls_type == "unknown":
            log.debug(
                "LABEL CLASSIFY: %r → unknown (score=%d, reasons=[%s]) [%s]",
                effective_artist, _cls_score, _cls_reasons, path.name,
            )
            # When the candidate is uncertain, prefer the raw tag artist if it
            # carries a clear artist signal (e.g. contains "feat.") and is
            # different from the current effective_artist.
            if (
                _raw_artist_tag
                and _raw_artist_tag != effective_artist
                and is_valid_artist(_raw_artist_tag)
            ):
                _raw_cls = classify_name_candidate(_raw_artist_tag)
                if _raw_cls["type"] == "artist":
                    log.info(
                        "LABEL CLASSIFY: unknown %r, preferring metadata artist %r [%s]",
                        effective_artist, _raw_artist_tag, path.name,
                    )
                    log_action(
                        f"LABEL CLASSIFY: unknown {effective_artist!r},"
                        f" using metadata artist {_raw_artist_tag!r} [{path.name}]"
                    )
                    effective_artist = _raw_artist_tag
                    artist           = _raw_artist_tag

        else:  # "artist"
            log.debug(
                "LABEL CLASSIFY: %r → artist (score=%d) [%s]",
                effective_artist, _cls_score, path.name,
            )

    # -----------------------------------------------------------------------
    # ROUTE CLASSIFICATION
    # -----------------------------------------------------------------------
    classify_fields = _read_classify_fields(path)
    classify_fields["artist"] = effective_artist  # needed for unknown detection
    route = classify_track_route(classify_fields, path.name)

    # -----------------------------------------------------------------------
    # ORGANIZE
    # -----------------------------------------------------------------------
    dest = _build_route_dest(route, effective_artist, title, path.suffix.lower())
    dest = _unique_dest(dest)

    try:
        rel_display = dest.relative_to(config.MUSIC_ROOT)
    except ValueError:
        rel_display = dest
    log.info("ORGANIZE [%s] %s → %s", route.upper(), path.name, rel_display)

    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))

    old_path_str = str(path)
    new_path_str = str(dest)

    if not dry_run:
        row = db.get_track(old_path_str)
        if row:
            db.upsert_track(
                new_path_str,
                artist=artist or row["artist"],
                title=title   or row["title"],
                genre=genre   or row["genre"],
                status=row["status"],
            )
            with db.get_conn() as conn:
                conn.execute("DELETE FROM tracks WHERE filepath=?", (old_path_str,))

        db.save_track_history(
            filepath=new_path_str,
            original_path=old_path_str,
            original_meta=original_meta,
            actions=["organized"],
        )
        log_action(f"ORGANIZED: {new_path_str}")

    return dest if not dry_run else path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------
def run(files: List[Path], run_id: int, dry_run: bool = False, use_beets: bool = True) -> List[Path]:
    """
    Organize files into the sorted library.
    Returns list of new file paths (in SORTED / UNSORTED / COMPILATIONS).
    """
    organized: List[Path] = []
    failed:    List[Path] = []

    # Try beets first (unless disabled)
    if use_beets:
        _, beets_failed = _run_beets(files, dry_run)
    else:
        log.info("Beets disabled — using Python fallback organizer for all files")
        beets_failed = files

    # Beets moved its files — use Python fallback for any it couldn't handle
    to_organize = beets_failed if beets_failed else files

    for path in to_organize:
        if not path.exists():
            log.warning("File missing before organize: %s", path)
            continue
        new_path = _organize_file(path, dry_run)
        if new_path:
            organized.append(new_path)
        else:
            failed.append(path)
            dest = config.UNSORTED / path.name
            log.warning("Organize failed for %s — moving to _unsorted", path.name)
            if not dry_run:
                config.UNSORTED.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
            db.mark_status(str(path), "needs_review", "organize failed")

    # After beets run (not fallback), scan SORTED for newly added files
    # and register them in the DB if beets handled them
    if not beets_failed and not dry_run:
        _register_beets_imports(files)
        organized = _collect_new_sorted_files()

    log.info("Organizer: %d organized, %d failed/unsorted", len(organized), len(failed))
    return organized


def _register_beets_imports(original_files: List[Path]) -> None:
    """
    After beets runs, scan SORTED for files and register any that aren't in DB.
    beets moves + renames files so we can't track old→new path directly.
    """
    for path in config.SORTED.rglob("*"):
        if path.suffix.lower() not in config.AUDIO_EXTENSIONS:
            continue
        if db.get_track(str(path)) is None:
            artist, title, _, genre = _read_tags(path)
            db.upsert_track(
                str(path),
                artist=artist,
                title=title,
                genre=genre,
                status="pending",
            )


def _collect_new_sorted_files() -> List[Path]:
    """Return all audio files in SORTED with status='pending' or 'ok'."""
    result = []
    for path in config.SORTED.rglob("*"):
        if path.suffix.lower() not in config.AUDIO_EXTENSIONS:
            continue
        row = db.get_track(str(path))
        if row and row["status"] in ("pending", "ok"):
            result.append(path)
    return result
