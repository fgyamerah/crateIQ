"""
Filename and metadata parser for the CrateIQ pipeline.

Responsibilities:
  - Strip leading track-number prefixes (e.g. "55. Artist - Track")
  - Normalize dash variants (–, —) to ASCII hyphen
  - Parse artist / title / version from a filename stem
  - Validate artist and title values so the organizer never creates
    nonsense folders like "#", "01", "55. Bontan"
  - Provide safe fallbacks when parsing is uncertain

Used by: modules/organizer.py (fallback when file tags are absent or invalid)

Processing order inside parse_filename_stem():
  1. Normalize unicode (NFC)
  2. Normalize dash variants → ASCII hyphen
  3. Strip promo/source junk (sanitize_text)
  4. Remove leading track-number prefix
  5. Split on first " - " separator → artist / rest
  6. Extract version info from trailing brackets in the title part
  7. Validate both sides; return empty strings on failure — never raises
"""
import logging
import os as _os
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Separator normalization
# ---------------------------------------------------------------------------
# Unicode dashes we treat as equivalent to ASCII hyphen-minus U+002D
_DASH_CHARS = (
    "\u2013",  # en dash      –
    "\u2014",  # em dash      —
    "\u2015",  # horizontal bar
    "\u2012",  # figure dash
    "\u2212",  # minus sign
)


def normalize_separators(name: str) -> str:
    """
    Replace all Unicode dash variants with ASCII hyphen-minus and
    normalize pipe separators to " - ".
    Also collapses multiple consecutive spaces to one.

    >>> normalize_separators("Artist – Track")
    'Artist - Track'
    >>> normalize_separators("Artist—Track")
    'Artist-Track'
    >>> normalize_separators("1 | Artist - Track")
    '1 - Artist - Track'
    """
    for ch in _DASH_CHARS:
        name = name.replace(ch, "-")
    # Normalize pipe separator to " - " (common in DJ pool filenames)
    name = re.sub(r"\s*\|\s*", " - ", name)
    name = re.sub(r"  +", " ", name)
    return name


# ---------------------------------------------------------------------------
# Track-number prefix removal
# ---------------------------------------------------------------------------
# Matches 1-4 leading digits followed by one of:
#   • an optional run of dots/spaces/underscores + a dash (e.g. "01 - ", "03. - ")
#   • a run of dots/spaces/underscores alone    (e.g. "55. ", "003 ", "07_")
# The lookahead requires the next character to NOT be a digit/space/dash/dot/
# underscore — this prevents stripping from "2PAC" or "808 State".
_RE_TRACK_PREFIX = re.compile(
    r"^(\d{1,4})"                   # 1–4 leading digits
    r"(?:"
    r"[\s._]*[-\u2013\u2014]\s*"    # option A: optional spaces/dots then a dash
    r"|"
    r"[.\s_]+"                      # option B: one or more dots/spaces/underscores
    r")"
    r"(?=[^\d\s\-\u2013\u2014._])", # lookahead: next char is not digit/space/dash/dot/underscore
    re.UNICODE,
)


def remove_track_number_prefix(name: str) -> Tuple[str, Optional[int]]:
    """
    Strip a leading track number from a string if present.
    Returns (cleaned_name, track_number).  track_number is None if no prefix found.

    >>> remove_track_number_prefix("55. Bontan, Adam Ten - Hey")
    ('Bontan, Adam Ten - Hey', 55)
    >>> remove_track_number_prefix("01 - Black Motion - Rainbow")
    ('Black Motion - Rainbow', 1)
    >>> remove_track_number_prefix("001 &ME, Rampa - Track")
    ('&ME, Rampa - Track', 1)
    >>> remove_track_number_prefix("03_Artist - Title")
    ('Artist - Title', 3)
    >>> remove_track_number_prefix("2PAC - Track")
    ('2PAC - Track', None)
    >>> remove_track_number_prefix("Artist - Track")
    ('Artist - Track', None)
    """
    if not name:
        return name, None
    m = _RE_TRACK_PREFIX.match(name)
    if m:
        track_num = int(m.group(1))
        cleaned   = name[m.end():].strip()
        return cleaned, track_num
    return name, None


# ---------------------------------------------------------------------------
# Prefix marker removal  (Camelot keys, standalone letters A/B, hash #)
# ---------------------------------------------------------------------------
# Camelot key: 1A-12A or 1B-12B followed by a separator
_RE_CAMELOT_PREFIX = re.compile(
    r"^\s*(?:1[0-2]|[1-9])[AB]\s*[\|\-\._]+\s*",
    re.IGNORECASE,
)

# Standalone letter A or B followed by at least one space then a separator.
# The space requirement prevents matching "A-ha" or "B-side".
_RE_LETTER_PREFIX = re.compile(
    r"^\s*[AB]\s+[\|\-\._]+\s*",
    re.IGNORECASE,
)

# Hash prefix: # followed by a separator
_RE_HASH_PREFIX = re.compile(r"^\s*#\s*[\|\-\._]+\s*")


def remove_prefix_markers(name: str) -> Tuple[str, Optional[str]]:
    """
    Strip leading non-numeric prefix markers (Camelot keys, A/B, #) from a
    filename stem before artist/title parsing.

    Numeric-only prefixes (01, 55., 003) are handled separately by
    remove_track_number_prefix() so that track_number is preserved.

    Returns (cleaned_name, prefix_type) where prefix_type is one of
    'camelot', 'letter', 'symbol', or None if no prefix was removed.

    The letter prefix requires a space before the separator so that
    "A-ha - Take On Me" is not mistakenly stripped.

    >>> remove_prefix_markers("4A - Track")
    ('Track', 'camelot')
    >>> remove_prefix_markers("8B - Track")
    ('Track', 'camelot')
    >>> remove_prefix_markers("12A - Track")
    ('Track', 'camelot')
    >>> remove_prefix_markers("A - Busiswa Feat. Oskido - Ngoku")
    ('Busiswa Feat. Oskido - Ngoku', 'letter')
    >>> remove_prefix_markers("B - Track")
    ('Track', 'letter')
    >>> remove_prefix_markers("# - Track")
    ('Track', 'symbol')
    >>> remove_prefix_markers("A-ha - Take On Me")
    ('A-ha - Take On Me', None)
    >>> remove_prefix_markers("Normal Artist - Track")
    ('Normal Artist - Track', None)
    >>> remove_prefix_markers("")
    ('', None)
    """
    if not name:
        return name, None

    # Camelot key check first (most specific — must precede letter check)
    m = _RE_CAMELOT_PREFIX.match(name)
    if m:
        stripped = name[m.end():].strip()
        log.debug("Parser: stripped camelot prefix from %r", name)
        return stripped, "camelot"

    # Standalone letter (A or B) with required preceding space
    m = _RE_LETTER_PREFIX.match(name)
    if m:
        stripped = name[m.end():].strip()
        log.debug("Parser: stripped letter prefix from %r", name)
        return stripped, "letter"

    # Hash symbol
    m = _RE_HASH_PREFIX.match(name)
    if m:
        stripped = name[m.end():].strip()
        log.debug("Parser: stripped hash prefix from %r", name)
        return stripped, "symbol"

    return name, None


# ---------------------------------------------------------------------------
# Version / mix info extraction
# ---------------------------------------------------------------------------
_VERSION_KEYWORDS = frozenset({
    "mix", "remix", "edit", "version", "instrumental", "dub", "club",
    "radio", "extended", "original", "vip", "rework", "reprise",
    "bootleg", "flip", "blend", "refix", "cut", "acapella", "vocal",
    "short", "long", "intro", "outro", "remaster", "rework",
})

# Content inside brackets that indicates a source/watermark, NOT a version
_JUNK_KEYWORDS = frozenset({
    "djcity", "zipdj", "beatport", "traxsource", "beatsource",
    "promo", "fordjonly", "download", "official", "free",
})

# Matches any bracketed expression: (...) or [...]
_RE_ANY_BRACKET = re.compile(r"([\(\[])([^\)\]]*)[\)\]]")


def _extract_version(text: str) -> Tuple[str, str]:
    """
    Find the first bracketed expression that contains a version keyword
    and extract it, returning (title_without_version, version_string).

    Junk brackets are left in place — the sanitizer removes them later.
    If no version bracket is found, returns (text, '').

    >>> _extract_version("Hey (Original Mix)")
    ('Hey', 'Original Mix')
    >>> _extract_version("Track Name [Extended Mix]")
    ('Track Name', 'Extended Mix')
    >>> _extract_version("Track (fordjonly.com)")   # junk — leave it
    ('Track (fordjonly.com)', '')
    >>> _extract_version("Track feat. Someone")     # no bracket
    ('Track feat. Someone', '')
    """
    for m in _RE_ANY_BRACKET.finditer(text):
        content       = m.group(2).strip()
        content_lower = content.lower()

        has_version = any(kw in content_lower for kw in _VERSION_KEYWORDS)
        has_junk    = any(kw in content_lower for kw in _JUNK_KEYWORDS)

        if has_version and not has_junk:
            # Remove just this bracket from the title, keep the rest
            title = (text[: m.start()] + text[m.end() :]).strip()
            return title, content

    return text.strip(), ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
# Pure number with optional trailing period: "01", "55.", "003"
_RE_PURE_NUMBER = re.compile(r"^\d+\.?$")

# Only non-word chars (punctuation / symbols / spaces)
_RE_PUNCT_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)

# Separator-junk strings: "#", "-", "---", "___", whitespace-only
_RE_SEP_JUNK = re.compile(r"^[-–—\s#_.]+$", re.UNICODE)

# Starts with digits + period + space  →  "55. Bontan"  (track number leaked into tag)
_RE_STARTS_DIGIT_PERIOD = re.compile(r"^\d+\.\s")

# Starts with digits + optional-space + dash + space  →  "01 - Black Motion"
_RE_STARTS_DIGIT_DASH = re.compile(r"^\d+\s*[-–—]\s")

# Camelot key: exactly 1A–12A or 1B–12B (whole-string match)
# These are music key notations that must never become artist folder names.
_RE_CAMELOT_KEY = re.compile(r"^(?:1[0-2]|[1-9])[AB]$", re.IGNORECASE)

# URL protocol fragment anywhere in value — https, http, ftp, www.
# Catches both normal (https://) and filesystem-encoded (https___) variants.
_RE_URL_PROTOCOL_IN_VALUE = re.compile(
    r'(?:https?|ftp)[:/\s_]+|www\.',
    re.IGNORECASE,
)

# Value that is entirely a bare domain name — e.g. "electronicfresh.com"
# Conservative: only well-known TLDs, whole-string match.
_RE_DOMAIN_ONLY = re.compile(
    r'^[a-z0-9][a-z0-9\-]*'
    r'\.(?:com|net|org|info|io|dj|fm|me|biz|us|tv|cc|to|uk|de|fr|es|it|nl|pl|ru)'
    r'\s*$',
    re.IGNORECASE,
)


def is_valid_artist(value: str) -> bool:
    """
    Return True if value looks like a plausible artist name.

    Rejects obvious garbage: pure numbers, punctuation-only, separator junk,
    Camelot keys (4A, 8B), values wrapped entirely in brackets, single-char
    strings, and values with track-number prefix patterns.

    Does NOT reject artist names that start with numbers ("808 State", "&ME",
    "2 Many Artists") as long as they don't have the period/dash pattern.

    >>> is_valid_artist("Bontan, Adam Ten")
    True
    >>> is_valid_artist("&ME")
    True
    >>> is_valid_artist("808 State")
    True
    >>> is_valid_artist("2 Many Artists")
    True
    >>> is_valid_artist("4A")
    False
    >>> is_valid_artist("8B")
    False
    >>> is_valid_artist("12A")
    False
    >>> is_valid_artist("55.")
    False
    >>> is_valid_artist("01")
    False
    >>> is_valid_artist("#")
    False
    >>> is_valid_artist("A")
    False
    >>> is_valid_artist("55. Bontan")
    False
    >>> is_valid_artist("01 - Black Motion")
    False
    >>> is_valid_artist("")
    False
    """
    if not value:
        return False
    v = value.strip()
    if not v:
        return False
    # Minimum length: single-character values like "A" or "B" are junk prefixes
    if len(v) < 2:
        return False
    if _RE_PURE_NUMBER.match(v):                # "01", "55.", "003"
        return False
    if _RE_SEP_JUNK.match(v):                   # "#", "-", "___"
        return False
    if _RE_PUNCT_ONLY.match(v):                 # only non-word chars
        return False
    if not any(c.isalpha() for c in v):         # must contain at least one letter
        return False
    if _RE_CAMELOT_KEY.match(v):                # Camelot key: 4A, 8B, 12A
        return False
    # Value entirely wrapped in brackets — likely a DJ pool watermark e.g. [ßy DJ L.p.$]
    if (v.startswith("[") and v.endswith("]")) or \
       (v.startswith("(") and v.endswith(")")):
        return False
    if _RE_STARTS_DIGIT_PERIOD.match(v):        # "55. Bontan" → track number leaked
        return False
    if _RE_STARTS_DIGIT_DASH.match(v):          # "01 - Black Motion" → separator leaked
        return False
    if _RE_URL_PROTOCOL_IN_VALUE.search(v):     # https___, http://, www. etc.
        return False
    if re.match(r'^(?:https?|www)', v, re.IGNORECASE):  # http/https/www prefix — any URL variant
        return False
    if _RE_DOMAIN_ONLY.match(v):                # bare domain: electronicfresh.com
        return False
    return True


def is_valid_title(value: str) -> bool:
    """
    Return True if value looks like a plausible track title.
    Less strict than is_valid_artist — very short and numeric titles are fine.

    >>> is_valid_title("Hey")
    True
    >>> is_valid_title("7 Rings")
    True
    >>> is_valid_title("")
    False
    >>> is_valid_title("---")
    False
    """
    if not value:
        return False
    v = value.strip()
    if not v:
        return False
    if _RE_SEP_JUNK.match(v):
        return False
    return True


# ---------------------------------------------------------------------------
# Main separator pattern (after all dashes normalized to "-")
# ---------------------------------------------------------------------------
_RE_MAIN_SEP = re.compile(r"\s+-\s+")


# ---------------------------------------------------------------------------
# Full filename stem parser
# ---------------------------------------------------------------------------
def parse_filename_stem(stem: str) -> Dict[str, object]:
    """
    Parse a filename stem into artist, title, version, and track_number.

    Processing order:
      1. NFC unicode normalization
      2. Separator normalization (–/— → -, | → " - ")
      3. Promo/junk removal (sanitize_text)
      4. Remove non-numeric prefix markers (Camelot keys, A/B, #)
      5. Remove leading numeric track-number prefix
         (only if separator " - " still present after stripping)
      6. Split on first " - " → artist / title+version
      7. Extract version from trailing brackets in title
      8. Validate; fall back gracefully

    Returns a dict with keys: artist (str), title (str), version (str),
    track_number (int|None).  No key is ever absent. Never raises.

    >>> parse_filename_stem("55. Bontan, Adam Ten - Hey (Original Mix)")
    {'artist': 'Bontan, Adam Ten', 'title': 'Hey', 'version': 'Original Mix', 'track_number': 55}
    >>> parse_filename_stem("NoSeparatorHere")
    {'artist': '', 'title': 'NoSeparatorHere', 'version': '', 'track_number': None}
    """
    result: Dict[str, object] = {
        "artist":       "",
        "title":        "",
        "version":      "",
        "track_number": None,
    }

    if not stem:
        return result

    try:
        # Step 1: normalize unicode
        text = unicodedata.normalize("NFC", stem)

        # Step 2: normalize separators (dash variants + pipe → " - ")
        text = normalize_separators(text)

        # Step 3: strip obvious promo/source junk
        from modules.sanitizer import sanitize_text
        text = sanitize_text(text).strip()
        if not text:
            result["title"] = stem.strip()
            return result

        # Step 4: remove non-numeric prefix markers (Camelot, letter, hash).
        # These are unambiguous junk prefixes — strip unconditionally.
        text, prefix_type = remove_prefix_markers(text)
        if prefix_type:
            log.info(
                "PREFIX REMOVED: type=%s from %r",
                prefix_type, stem,
            )
        text = text.strip()

        # Step 5: strip leading numeric track-number prefix.
        # Safety: only strip when the remainder still contains " - " so we
        # don't turn "7 Rings" into "Rings" when the file has no separator.
        stripped, track_num = remove_track_number_prefix(text)
        if track_num is not None and _RE_MAIN_SEP.search(stripped):
            log.debug("Parser: stripped track prefix %d from %r", track_num, stem)
            text = stripped
            result["track_number"] = track_num
        # else: leave text unchanged

        text = text.strip()

        # Step 5: split on first " - "
        parts = _RE_MAIN_SEP.split(text, maxsplit=1)

        if len(parts) == 2:
            artist_raw = parts[0].strip()
            title_raw  = parts[1].strip()

            # Step 6: extract version from trailing brackets
            title_clean, version = _extract_version(title_raw)

            result["artist"]  = artist_raw
            result["title"]   = title_clean
            result["version"] = version
        else:
            # No " - " separator — everything is the title
            title_clean, version = _extract_version(text)
            result["title"]   = title_clean
            result["version"] = version

    except Exception as exc:
        log.warning("Parser: unexpected error parsing %r: %s", stem, exc)
        result["title"] = stem.strip()

    return result


# ---------------------------------------------------------------------------
# Label / artist name classification
# ---------------------------------------------------------------------------

# Default path for the optional known-labels list (project root, one label per line)
_DEFAULT_KNOWN_LABELS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "known_labels.txt",
)

# Score thresholds
# score >= _ARTIST_THRESHOLD  → "artist"
# score <= _LABEL_THRESHOLD   → "label"
# anything between            → "unknown"
_ARTIST_THRESHOLD = 1
_LABEL_THRESHOLD  = -3

# Label-positive signals: (compiled_regex, score_delta, reason)
# Strong signals (-3): almost never appear in real artist names.
# Moderate signals (-2): common in label branding, occasionally in artist names.
# Weak signals (-1): appear in both; one alone is not decisive.
_LABEL_SIGNALS: List[Tuple[re.Pattern, int, str]] = [
    # ---- Strong (-3) ----
    (re.compile(r'\brecord(?:s|ing|ings)?\b', re.IGNORECASE), -3,
     "contains 'records/recording'"),
    (re.compile(r'\bentertainment\b',           re.IGNORECASE), -3,
     "contains 'entertainment'"),
    (re.compile(r'\bpublishing\b',              re.IGNORECASE), -3,
     "contains 'publishing'"),
    (re.compile(r'\blabel(?:s)?\b',             re.IGNORECASE), -3,
     "contains 'label'"),
    (re.compile(r'\b(?:inc|ltd|llc|corp|gmbh)\b', re.IGNORECASE), -3,
     "company suffix (Inc/Ltd/Corp)"),
    # ---- Moderate (-2) ----
    (re.compile(r'\bstudio(?:s)?\b',            re.IGNORECASE), -2,
     "contains 'studio(s)'"),
    (re.compile(r'\bmanagement\b',              re.IGNORECASE), -2,
     "contains 'management'"),
    (re.compile(r'\bproduction(?:s)?\b',        re.IGNORECASE), -2,
     "contains 'production(s)'"),
    # ---- Weak (-1) ----
    (re.compile(r'\bmusic\b',                   re.IGNORECASE), -1,
     "contains 'music'"),
    (re.compile(r'\bdigital\b',                 re.IGNORECASE), -1,
     "contains 'digital'"),
    (re.compile(r'\bmedia\b',                   re.IGNORECASE), -1,
     "contains 'media'"),
    (re.compile(r'\bcollective\b',              re.IGNORECASE), -1,
     "contains 'collective'"),
    (re.compile(r'\bsound(?:s)?\b',             re.IGNORECASE), -1,
     "contains 'sound(s)'"),
    (re.compile(r'\b(?:worldwide|international|global)\b', re.IGNORECASE), -1,
     "brand geographic term"),
    (re.compile(r'\btrax\b',                    re.IGNORECASE), -1,
     "contains 'trax'"),
    (re.compile(r'\bgroup\b',                   re.IGNORECASE), -1,
     "contains 'group'"),
]

# Catalog/release code: 2–6 uppercase letters + optional separator + 2–6 digits
# e.g. "NR001", "ABC-123", "TOOL10"
_RE_CATALOG_CODE = re.compile(r'^[A-Z]{2,6}[-_]?\d{2,6}$')

# Artist-positive signals: (compiled_regex, score_delta, reason)
_ARTIST_SIGNALS: List[Tuple[re.Pattern, int, str]] = [
    (re.compile(r'\b(?:feat|ft|featuring)\b', re.IGNORECASE), +3,
     "feat/ft/featuring collaboration"),
    # Comma-separated collaborators — anchored to whole string
    (re.compile(r'^[^,]+(,[^,]+)+$'), +2,
     "comma-separated collaborators"),
    # Ampersand between names with surrounding spaces
    (re.compile(r'\s+&\s+'), +2,
     "ampersand collaboration pattern"),
    (re.compile(r'\bvs\.?\b', re.IGNORECASE), +2,
     "versus collaboration"),
]

# Module-level known-labels cache: None = not yet attempted, frozenset = loaded
_known_labels_cache: Optional[frozenset] = None
_known_labels_loaded: bool = False


def _get_known_labels_path() -> str:
    """Return the configured known-labels file path (reads config if available)."""
    try:
        import config as _cfg  # type: ignore
        return str(getattr(_cfg, "KNOWN_LABELS_FILE", _DEFAULT_KNOWN_LABELS_PATH))
    except ImportError:
        return _DEFAULT_KNOWN_LABELS_PATH


def _load_known_labels() -> frozenset:
    """
    Load known labels from known_labels.txt into a frozen lowercase set.
    Returns an empty frozenset if the file does not exist.
    Result is cached for the lifetime of the process.
    Each non-blank, non-comment line (not starting with #) is one label entry.
    """
    global _known_labels_cache, _known_labels_loaded
    if _known_labels_loaded:
        return _known_labels_cache or frozenset()

    _known_labels_loaded = True
    path = _get_known_labels_path()

    if not _os.path.isfile(path):
        _known_labels_cache = frozenset()
        return _known_labels_cache

    labels: set = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    labels.add(line.lower())
        log.info("Parser: loaded %d known labels from %s", len(labels), path)
    except OSError as exc:
        log.warning("Parser: could not read known_labels.txt at %s: %s", path, exc)

    _known_labels_cache = frozenset(labels)
    return _known_labels_cache


def classify_name_candidate(value: str) -> Dict[str, object]:
    """
    Classify whether a name candidate looks like an artist or a label.

    Uses weighted signal detection:
      - Artist signals (feat, comma-separated names, & collaborations) increase score.
      - Label signals (records, entertainment, publishing, studio, etc.) decrease score.
      - Catalog-code patterns (NR001, ABC-123) are a strong label indicator.
      - known_labels.txt entries trigger an immediate label classification.

    Score thresholds:
      score >= 1  → "artist"
      score <= -3 → "label"
      else        → "unknown"

    Returns dict with keys:
      type    — "artist", "label", or "unknown"
      score   — int (positive = artist evidence, negative = label evidence)
      reasons — list[str] describing matched signals

    Conservative by design: a single weak signal (-1) leaves the result as
    "unknown" rather than forcing a "label" classification.  A strong label
    keyword (-3) alone is enough to classify as "label".

    >>> classify_name_candidate("Bontan, Adam Ten")["type"]
    'artist'
    >>> classify_name_candidate("Busiswa Feat. Oskido")["type"]
    'artist'
    >>> classify_name_candidate("Toolroom Records")["type"]
    'label'
    >>> classify_name_candidate("Nervous Records")["type"]
    'label'
    >>> classify_name_candidate("Black Motion")["type"]
    'unknown'
    >>> classify_name_candidate("Digital Boy")["type"]
    'unknown'
    """
    result: Dict[str, object] = {"type": "unknown", "score": 0, "reasons": []}
    if not value:
        return result

    v       = value.strip()
    score   = 0
    reasons: List[str] = []

    # 1. Known-labels exact match — immediate strong classification.
    known = _load_known_labels()
    if v.lower() in known:
        reasons.append("matched known_labels.txt")
        log.debug("LABEL CLASSIFY: %r matched known_labels.txt", v)
        return {"type": "label", "score": -10, "reasons": reasons}

    # 2. Catalog/release code pattern — e.g. "NR001", "TOOL-10"
    if _RE_CATALOG_CODE.match(v):
        score -= 3
        reasons.append("catalog/release code pattern")

    # 3. Label signals
    for pattern, delta, reason in _LABEL_SIGNALS:
        if pattern.search(v):
            score += delta
            reasons.append(reason)

    # 4. Artist signals
    for pattern, delta, reason in _ARTIST_SIGNALS:
        if pattern.search(v):
            score += delta
            reasons.append(reason)

    # 5. Determine type from score
    if score >= _ARTIST_THRESHOLD:
        type_ = "artist"
    elif score <= _LABEL_THRESHOLD:
        type_ = "label"
    else:
        type_ = "unknown"

    result["type"]    = type_
    result["score"]   = score
    result["reasons"] = reasons
    return result
