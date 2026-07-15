"""
modules/junk_patterns.py

Centralized junk/promo-source pattern loader for CrateIQ.

Loads config/junk_patterns.json once (cached at module level) and exposes
structured data for use by sanitizer, metadata-clean, artist-folder-clean,
and label-intel modules.

Fall-back: if the JSON file is missing or malformed, the module returns a
minimal hard-coded JunkPatterns instance so that all callers continue to work
without the config file.

Public API:
    load_junk_patterns()             → JunkPatterns (cached singleton)
    normalize_for_junk_match(value)  → lowercase, stripped, collapsed string
    is_junk_metadata(value, patterns) → bool
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# Path to the JSON config relative to this file's package root
_JSON_PATH = Path(__file__).parent.parent / "config" / "junk_patterns.json"

# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass
class JunkPatterns:
    """
    Compiled junk-detection data loaded from config/junk_patterns.json.

    Attributes:
        phrase_patterns     Ordered list of (compiled_re, replacement) tuples;
                            order matters — applied in sequence by sanitizer.
        exact_source_junk   frozenset of lowercase known-junk exact names.
        source_junk_substrings  tuple of lowercase substrings to check via 'in'.
        exact_bad_labels    frozenset of lowercase label values that mean nothing.
        genre_words         frozenset of lowercase genre terms (sometimes leak
                            into label fields).
        domain_tlds         List of TLD strings for plain-domain regex builds.
    """
    phrase_patterns:        List[Tuple[re.Pattern, str]] = field(default_factory=list)
    exact_source_junk:      frozenset = field(default_factory=frozenset)
    source_junk_substrings: tuple     = field(default_factory=tuple)
    exact_bad_labels:       frozenset = field(default_factory=frozenset)
    genre_words:            frozenset = field(default_factory=frozenset)
    domain_tlds:            List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Minimal fallback (used when JSON is missing or unreadable)
# ---------------------------------------------------------------------------

def _fallback_patterns() -> JunkPatterns:
    """
    Minimal hard-coded fallback so callers never crash if the JSON is absent.
    Covers the most common watermarks only.
    """
    phrase_patterns = [
        (re.compile(r'\bdjcity\b', re.IGNORECASE), ''),
        (re.compile(r'\btraxcrate\b', re.IGNORECASE), ''),
        (re.compile(r'\bzipdj\b', re.IGNORECASE), ''),
        (re.compile(r'\bmusicafresca\b', re.IGNORECASE), ''),
        (re.compile(r'\bbeatsource\b', re.IGNORECASE), ''),
        (re.compile(r'\btraxsource\b', re.IGNORECASE), ''),
        (re.compile(r'\bbeatport\b', re.IGNORECASE), ''),
        (re.compile(r'\bpromo\s+only\b', re.IGNORECASE), ''),
        (re.compile(r'\bfree\s+download\b', re.IGNORECASE), ''),
    ]
    return JunkPatterns(
        phrase_patterns=phrase_patterns,
        exact_source_junk=frozenset({
            "traxcrate", "djcity", "zipdj", "musicafresca",
            "beatsource", "traxsource", "beatport", "fordjonly",
        }),
        source_junk_substrings=(
            "traxcrate", "djcity", "zipdj", "musicafresca",
            "beatsource", "traxsource", "beatport", "fordjonly",
            "downloaded from", "promo only", "free download",
        ),
        exact_bad_labels=frozenset({
            "", "unknown", "n/a", "na", "none", "null",
            "promo", "various", "various artists", "va",
            "-", "--", "?", "tbc", "tba", "untitled",
        }),
        genre_words=frozenset({
            "house", "techno", "trance", "amapiano", "afrobeats",
            "electronic", "dance", "edm", "dnb",
        }),
        domain_tlds=["com", "net", "org", "info", "io", "dj", "fm", "me", "biz", "us"],
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_FLAG_MAP = {
    "IGNORECASE": re.IGNORECASE,
    "MULTILINE":  re.MULTILINE,
    "DOTALL":     re.DOTALL,
    "UNICODE":    re.UNICODE,
    "VERBOSE":    re.VERBOSE,
}

def _parse_flags(flags_str: str) -> int:
    """Convert a comma-separated flag string like 'IGNORECASE' to re flags int."""
    result = 0
    for part in flags_str.split(","):
        part = part.strip()
        if part in _FLAG_MAP:
            result |= _FLAG_MAP[part]
    return result


def _load_from_json(path: Path) -> JunkPatterns:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    # phrase_patterns — order-sensitive, compiled from JSON array
    phrase_patterns: List[Tuple[re.Pattern, str]] = []
    for entry in data.get("phrase_patterns", []):
        flags = _parse_flags(entry.get("flags", "IGNORECASE"))
        try:
            compiled = re.compile(entry["regex"], flags)
            phrase_patterns.append((compiled, entry.get("replacement", "")))
        except re.error as exc:
            log.warning("junk_patterns.json: bad regex %r — %s", entry.get("regex"), exc)

    exact_source_junk = frozenset(
        v.lower() for v in data.get("exact_source_junk", [])
    )
    source_junk_substrings = tuple(
        v.lower() for v in data.get("source_junk_substrings", [])
    )
    exact_bad_labels = frozenset(
        v.lower() for v in data.get("exact_bad_labels", [])
    )
    genre_words = frozenset(
        v.lower() for v in data.get("genre_words", [])
    )
    domain_tlds = [v.lower() for v in data.get("domain_tlds", [])]

    return JunkPatterns(
        phrase_patterns=phrase_patterns,
        exact_source_junk=exact_source_junk,
        source_junk_substrings=source_junk_substrings,
        exact_bad_labels=exact_bad_labels,
        genre_words=genre_words,
        domain_tlds=domain_tlds,
    )


# Module-level cache
_CACHE: Optional[JunkPatterns] = None


def load_junk_patterns() -> JunkPatterns:
    """
    Return the JunkPatterns singleton, loading from JSON on first call.

    Thread safety: in CPython the GIL makes this safe for typical pipeline use.
    If the JSON file is absent or malformed, logs a warning and returns the
    built-in fallback so callers never need to handle None.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    if _JSON_PATH.exists():
        try:
            _CACHE = _load_from_json(_JSON_PATH)
            log.debug("junk_patterns: loaded %d phrase patterns from %s",
                      len(_CACHE.phrase_patterns), _JSON_PATH)
            return _CACHE
        except Exception as exc:
            log.warning("junk_patterns.json: failed to load (%s) — using fallback", exc)
    else:
        log.warning("junk_patterns.json not found at %s — using fallback", _JSON_PATH)

    _CACHE = _fallback_patterns()
    return _CACHE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RE_MULTI_SPACE = re.compile(r'\s+')


def normalize_for_junk_match(value: str) -> str:
    """
    Normalize a metadata value for junk comparison.

    Steps:
      - Unicode NFC normalization
      - Strip leading/trailing whitespace
      - Lowercase
      - Collapse internal whitespace to single space

    >>> normalize_for_junk_match("  DJCity  ")
    'djcity'
    >>> normalize_for_junk_match("Promo\\tOnly")
    'promo only'
    """
    if not value:
        return ""
    v = unicodedata.normalize("NFC", value)
    v = v.strip().lower()
    v = _RE_MULTI_SPACE.sub(" ", v)
    return v


def is_junk_metadata(value: str, patterns: Optional[JunkPatterns] = None) -> bool:
    """
    Return True if *value* is recognized as junk metadata.

    Checks (in order):
      1. Exact match against exact_source_junk
      2. Exact match against exact_bad_labels
      3. Any source_junk_substring found in the normalized value
      4. Any genre word is the entire normalized value

    Args:
        value:    Raw metadata string to test.
        patterns: JunkPatterns instance. If None, load_junk_patterns() is used.

    >>> is_junk_metadata("DJCity")
    True
    >>> is_junk_metadata("Deep House")
    True
    >>> is_junk_metadata("Defected Records")
    False
    """
    if patterns is None:
        patterns = load_junk_patterns()

    norm = normalize_for_junk_match(value)

    if norm in patterns.exact_source_junk:
        return True

    if norm in patterns.exact_bad_labels:
        return True

    for sub in patterns.source_junk_substrings:
        if sub in norm:
            return True

    if norm in patterns.genre_words:
        return True

    return False
