"""
modules/harmonic.py

AI-assisted harmonic mixing suggestions.

Scoring architecture (rule-based + weighted ranking):

  1. Camelot compatibility (Camelot wheel rules)
     - Same key:              1.00
     - ±1 position:           0.90  (energy boost / drop, very mixable)
     - Mode switch (A↔B):     0.85  (same root, major ↔ minor)
     - ±1 + mode switch:      0.80  (diagonal — still good)
     - ±2 same mode:          0.55  (workable with pitch correction)
     - ±3+:                   0.15  (clash; avoid)

  2. BPM compatibility
     - ≤2% delta:  0.95  (pitchable in any CDJ/controller)
     - ≤5%:        0.82
     - ≤8%:        0.65  (stretching it)
     - ≤12%:       0.45  (halftime/doubletime only)
     - >12%:       0.15  (usually avoid)

  3. Energy compatibility
     - Same tier (Peak/Mid/Chill):    1.00
     - Adjacent tier:                 0.70
     - Two tiers apart:               0.35

  4. Genre compatibility
     - Same genre:            1.00
     - Closely related:       0.80  (see _GENRE_RELATIONS)
     - Different:             0.50  (DJs often cross genres deliberately)

  5. Transition direction bonuses
     - energy_lift:  incoming BPM slightly higher, incoming energy higher
     - smooth_blend: very close BPM + same/adjacent Camelot
     - safe:         high Camelot + BPM compatibility combined

Ranking strategies:
  "safest"        — highest Camelot × BPM composite score
  "energy_lift"   — incoming energy tier higher or BPM notably faster
  "smooth_blend"  — very tight BPM + Camelot scores
  "best_warmup"   — Chill/Mid energy, relaxed BPM, harmonic
  "best_late_set" — Peak energy, high BPM, strong Camelot

Usage:
  python pipeline.py harmonic-suggest --track "/music/.../track.mp3"
  python pipeline.py harmonic-suggest --key 8A --bpm 128
  python pipeline.py harmonic-suggest --track "..." --strategy energy_lift
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
import db
from modules.textlog import log_action

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Route exclusivity guard
# Acapella and Tool tracks must not enter the harmonic candidate pool.
# ---------------------------------------------------------------------------
def _is_exclusive_route(row) -> bool:
    """Return True for Acapella and Tool tracks."""
    fp = str(row["filepath"] or "")
    if fp.startswith(str(config.ACAPELLA)) or fp.startswith(str(config.DJ_TOOLS)):
        return True
    combined = f"{row['genre'] or ''} {row['title'] or ''}".lower()
    if "acapella" in combined or "a cappella" in combined:
        return True
    if any(kw in combined for kw in ("dj tool", "drum tool", "fx tool", "percussion tool")):
        return True
    return False


# ---------------------------------------------------------------------------
# Camelot wheel helpers
# ---------------------------------------------------------------------------

_RE_CAMELOT = re.compile(r'^(1[0-2]|[1-9])([AB])$', re.IGNORECASE)


def _parse_camelot(key: str) -> Optional[Tuple[int, str]]:
    """Parse a Camelot key like '8A' into (8, 'A') or None."""
    if not key:
        return None
    m = _RE_CAMELOT.match(key.strip().upper())
    if not m:
        return None
    return int(m.group(1)), m.group(2).upper()


def camelot_distance(key_a: str, key_b: str) -> Tuple[int, bool]:
    """
    Return (distance, mode_switched) between two Camelot keys.
    distance = min clockwise/counter-clockwise steps ignoring mode.
    mode_switched = True if A↔B at same or adjacent position.

    Public helper — safe for other modules (e.g. backend services) to import
    directly instead of reaching into module-private functions.
    """
    a = _parse_camelot(key_a)
    b = _parse_camelot(key_b)
    if a is None or b is None:
        return 99, False

    num_a, letter_a = a
    num_b, letter_b = b

    # Circular distance on 12-position wheel
    diff = abs(num_a - num_b)
    circ_dist = min(diff, 12 - diff)
    mode_switched = letter_a != letter_b

    return circ_dist, mode_switched


def _camelot_distance(key_a: str, key_b: str) -> Tuple[int, bool]:
    """Backward-compatible internal alias for camelot_distance()."""
    return camelot_distance(key_a, key_b)


def camelot_score(key_a: str, key_b: str) -> float:
    """
    Return a [0, 1] compatibility score between two Camelot keys.
    Higher = more harmonically compatible.
    """
    dist, switched = _camelot_distance(key_a, key_b)

    if dist == 0 and not switched:
        return 1.00   # identical key
    if dist == 0 and switched:
        return 0.85   # mode switch, same root (e.g. 8A → 8B)
    if dist == 1 and not switched:
        return 0.90   # adjacent on wheel (dominant / subdominant)
    if dist == 1 and switched:
        return 0.80   # adjacent + mode switch (diagonal)
    if dist == 2 and not switched:
        return 0.55   # 2 steps, workable with pitch
    if dist == 2 and switched:
        return 0.45
    if dist == 3:
        return 0.25
    return 0.10       # 4+ positions apart: clash


# ---------------------------------------------------------------------------
# BPM scoring
# ---------------------------------------------------------------------------

def bpm_score(bpm_a: float, bpm_b: float) -> float:
    """
    Return a [0, 1] BPM compatibility score.
    Handles halftime / doubletime automatically.
    """
    if not bpm_a or not bpm_b:
        return 0.5  # unknown — neutral

    # Allow doubletime / halftime matching
    ratios = [bpm_b / bpm_a, (bpm_b * 2) / bpm_a, bpm_b / (bpm_a * 2)]
    best   = min(abs(r - 1.0) for r in ratios)   # smallest deviation from 1.0

    if best <= 0.02:   return 0.95
    if best <= 0.05:   return 0.82
    if best <= 0.08:   return 0.65
    if best <= 0.12:   return 0.45
    if best <= 0.20:   return 0.25
    return 0.10


def bpm_delta_pct(bpm_a: float, bpm_b: float) -> float:
    """Signed BPM change percentage (positive = b is faster)."""
    if not bpm_a:
        return 0.0
    return (bpm_b - bpm_a) / bpm_a * 100.0


# ---------------------------------------------------------------------------
# Energy scoring
# ---------------------------------------------------------------------------

_ENERGY_RANK: Dict[str, int] = {"Chill": 0, "Mid": 1, "Peak": 2}


def energy_score(energy_a: str, energy_b: str) -> float:
    """Return a [0, 1] energy compatibility score."""
    r_a = _ENERGY_RANK.get(energy_a, 1)
    r_b = _ENERGY_RANK.get(energy_b, 1)
    diff = abs(r_a - r_b)
    return [1.00, 0.70, 0.35][diff]


def _classify_energy(bpm: float, genre: str) -> str:
    """Mirror of playlists._classify_energy — avoid circular import."""
    genre_l = (genre or "").strip().lower()
    if any(g in genre_l for g in ("afro tech", "techno", "hard techno", "rave")):
        return "Peak"
    if any(g in genre_l for g in ("deep house", "organic house", "melodic", "downtempo")):
        return "Chill"
    bpm_f = bpm or 0.0
    if bpm_f >= 126:   return "Peak"
    if bpm_f >= 118:   return "Mid"
    if bpm_f > 0:      return "Chill"
    return "Mid"


# ---------------------------------------------------------------------------
# Unknown-artist detection
# ---------------------------------------------------------------------------

_UNKNOWN_ARTISTS: frozenset = frozenset({
    "", "unknown", "unknown artist", "va", "various artists", "various",
    "n/a", "none", "-", "--",
})

_JUNK_MARKER_RE = re.compile(r'\b(unknown|untitled)\b', re.IGNORECASE)


def _is_unknown_artist(artist: str) -> bool:
    """Return True if the artist value is a blank/fallback placeholder."""
    return (artist or "").strip().lower() in _UNKNOWN_ARTISTS


# ---------------------------------------------------------------------------
# Artist normalization (primary artist extraction)
# ---------------------------------------------------------------------------

# Strips feat./ft./featuring/vs./x and everything after
_FEAT_RE = re.compile(
    r'\s+(feat\.?|ft\.?|featuring|vs\.?)\s+.*$',
    re.IGNORECASE,
)
# Splits multi-artist strings on comma, ampersand, or slash
_MULTI_ARTIST_SPLIT_RE = re.compile(r'[,/&]|\s+x\s+', re.IGNORECASE)


def _normalize_artist(artist: str) -> str:
    """
    Return normalized primary artist only.

    - Strips feat./ft./featuring/vs. and everything after
    - Splits on comma / & / x, keeps first token
    - Lowercases and strips whitespace
    - Keeps original for display; only used for identity/ranking logic

    Examples:
      "ATFC, Lisa Millet"  → "atfc"
      "Bob & Alice ft. Eve" → "bob"
      "&Me"                → "&me"
    """
    s = (artist or "").strip()
    s = _FEAT_RE.sub("", s)
    parts = _MULTI_ARTIST_SPLIT_RE.split(s)
    return parts[0].strip().lower()


# ---------------------------------------------------------------------------
# Candidate deduplication
# ---------------------------------------------------------------------------

def _normalize_for_dedupe(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, drop leading articles."""
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(the|a|an)\s+", "", s)
    return s


# Strips common version/mix suffixes from titles before dedupe keying
_VERSION_JUNK_RE = re.compile(
    r'\s*[\(\[](original|extended|radio|club|instrumental|vip|'
    r'acapella|dub|mix|edit|version|rmx|remix|rework|bootleg|'
    r'remaster(?:ed)?)[^\)\]]*[\)\]]\s*$',
    re.IGNORECASE,
)


def _normalize_title_for_dedupe(title: str) -> str:
    """Strip version/remix suffixes, then apply standard normalization."""
    s = _VERSION_JUNK_RE.sub("", (title or "").strip())
    return _normalize_for_dedupe(s)


def _dedupe_candidates(rows: list, verbose: bool = False) -> list:
    """
    Collapse duplicate / near-duplicate tracks into one canonical candidate.

    Identity key: (normalized_primary_artist, normalized_title_no_version).
    Normalized artist uses _normalize_artist() — primary artist only, no feat.
    Normalized title strips "(Original Mix)", "(Extended)" etc. before keying.

    When duplicates exist, keep the highest-quality entry:
      1. Lossless format (FLAC / WAV / AIFF) preferred over lossy
      2. More complete metadata (non-empty artist + title)
      3. Fewer junk markers in tags
      4. Shorter filename (cleaner library entry)
    """
    from typing import Tuple as _Tuple
    groups: Dict[_Tuple[str, str], list] = {}
    for row in rows:
        key = (
            _normalize_artist(row["artist"] or ""),
            _normalize_title_for_dedupe(row["title"] or Path(row["filepath"]).stem),
        )
        groups.setdefault(key, []).append(row)

    result = []
    for _key, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
            continue

        def _quality(r) -> tuple:
            fp   = str(r["filepath"]).lower()
            fmt  = 2 if any(fp.endswith(e) for e in (".flac", ".wav", ".aiff")) else 1
            meta = (1 if r["artist"] and r["artist"].strip() else 0) + \
                   (1 if r["title"]  and r["title"].strip()  else 0)
            junk = len(_JUNK_MARKER_RE.findall(r["artist"] or "")) + \
                   len(_JUNK_MARKER_RE.findall(r["title"]  or ""))
            fname_len = -len(Path(r["filepath"]).name)   # shorter is cleaner
            return (fmt, meta, -junk, fname_len)

        group.sort(key=_quality, reverse=True)
        best = group[0]
        if verbose:
            dupes = [Path(r["filepath"]).name for r in group[1:]]
            log.info(
                "dedupe: keeping '%s', collapsing %d duplicate(s): %s",
                Path(best["filepath"]).name, len(dupes), ", ".join(dupes),
            )
        result.append(best)

    return result


# ---------------------------------------------------------------------------
# BPM step constraint
# ---------------------------------------------------------------------------

# Preferred max absolute BPM step between consecutive tracks
_BPM_STEP_SOFT: float = 3.0
# Hard cap — transitions beyond this are heavily penalised
_BPM_STEP_HARD: float = 6.0


def _bpm_step_multiplier(
    from_bpm: float,
    to_bpm:   float,
    soft:     float = _BPM_STEP_SOFT,
    hard:     float = _BPM_STEP_HARD,
) -> float:
    """
    Return a [0.10, 1.0] penalty multiplier for absolute BPM step size.

    ≤ soft BPM delta  →  1.0  (no penalty)
    soft … hard       →  linear 1.0 → 0.40
    > hard BPM delta  →  0.10  (hard cap penalty)
    """
    if not from_bpm or not to_bpm:
        return 1.0
    delta = abs(to_bpm - from_bpm)
    if delta <= soft:
        return 1.0
    if delta <= hard:
        span = max(hard - soft, 0.01)
        return round(1.0 - 0.60 * (delta - soft) / span, 3)
    return 0.10


# ---------------------------------------------------------------------------
# Genre compatibility
# ---------------------------------------------------------------------------

# Closely related genre pairs (bidirectional)
_GENRE_RELATIONS: List[Tuple[str, str]] = [
    ("afro house",    "afrotech"),
    ("afro house",    "afro tech"),
    ("afro house",    "deep house"),
    ("afro house",    "organic house"),
    ("deep house",    "organic house"),
    ("deep house",    "melodic house"),
    ("tech house",    "techno"),
    ("tech house",    "afro tech"),
    ("afro tech",     "techno"),
    ("amapiano",      "afro house"),
    ("amapiano",      "afrobeats"),
    ("progressive",   "melodic house"),
    ("progressive",   "melodic techno"),
]


def genre_score(genre_a: str, genre_b: str) -> float:
    """Return a [0, 1] genre compatibility score."""
    if not genre_a or not genre_b:
        return 0.5  # unknown — neutral

    a = genre_a.strip().lower()
    b = genre_b.strip().lower()

    if a == b:
        return 1.00

    for g1, g2 in _GENRE_RELATIONS:
        if (a == g1 and b == g2) or (a == g2 and b == g1):
            return 0.80

    return 0.50   # different genres — DJ may cross intentionally


# ---------------------------------------------------------------------------
# Composite transition score
# ---------------------------------------------------------------------------

# Default weights — must sum to 1.0
_DEFAULT_WEIGHTS = {
    "camelot": 0.35,
    "bpm":     0.30,
    "energy":  0.20,
    "genre":   0.15,
}


@dataclass
class TransitionScore:
    from_filepath: str
    to_filepath:   str
    to_title:      str
    to_artist:     str
    to_bpm:        float
    to_key:        str
    to_energy:     str
    to_genre:      str

    camelot_score: float = 0.0
    bpm_score:     float = 0.0
    energy_score:  float = 0.0
    genre_score:   float = 0.0
    total_score:   float = 0.0
    bpm_delta_pct: float = 0.0

    strategies:    Dict[str, float] = field(default_factory=dict)
    explanation:   str = ""


def score_transition(
    from_row,
    to_row,
    weights: Optional[Dict[str, float]] = None,
) -> TransitionScore:
    """
    Score a single track-to-track transition.
    Both rows are sqlite3.Row objects from the tracks table.
    """
    w = weights or _DEFAULT_WEIGHTS

    from_bpm    = float(from_row["bpm"] or 0)
    from_key    = from_row["key_camelot"] or ""
    from_genre  = from_row["genre"] or ""
    from_energy = _classify_energy(from_bpm, from_genre)

    to_bpm    = float(to_row["bpm"] or 0)
    to_key    = to_row["key_camelot"] or ""
    to_genre  = to_row["genre"] or ""
    to_energy = _classify_energy(to_bpm, to_genre)

    c_score = camelot_score(from_key, to_key)
    b_score = bpm_score(from_bpm, to_bpm)
    e_score = energy_score(from_energy, to_energy)
    g_score = genre_score(from_genre, to_genre)

    total = (
        w.get("camelot", 0.35) * c_score
        + w.get("bpm",     0.30) * b_score
        + w.get("energy",  0.20) * e_score
        + w.get("genre",   0.15) * g_score
    )

    bpm_d = bpm_delta_pct(from_bpm, to_bpm)

    ts = TransitionScore(
        from_filepath = str(from_row["filepath"]),
        to_filepath   = str(to_row["filepath"]),
        to_title      = to_row["title"] or Path(to_row["filepath"]).stem,
        to_artist     = to_row["artist"] or "Unknown",
        to_bpm        = to_bpm,
        to_key        = to_key,
        to_energy     = to_energy,
        to_genre      = to_genre,
        camelot_score = round(c_score, 3),
        bpm_score     = round(b_score, 3),
        energy_score  = round(e_score, 3),
        genre_score   = round(g_score, 3),
        total_score   = round(total, 3),
        bpm_delta_pct = round(bpm_d, 1),
    )

    # Strategy sub-scores
    ts.strategies = {
        "safest":       round((c_score * 0.55 + b_score * 0.45), 3),
        "energy_lift":  round(_energy_lift_score(b_score, bpm_d, e_score, from_energy, to_energy, c_score), 3),
        "smooth_blend": round((c_score * 0.50 + b_score * 0.50), 3),
        "best_warmup":  round(_warmup_score(to_energy, b_score, c_score), 3),
        "best_late_set":round(_late_set_score(to_energy, b_score, c_score, to_bpm), 3),
    }

    ts.explanation = _explain(ts, from_key, to_key, from_energy, from_bpm)
    return ts


def _energy_lift_score(
    b_score: float,
    bpm_d: float,
    e_score: float,
    from_energy: str,
    to_energy: str,
    c_score: float = 1.0,
) -> float:
    """
    Score for energy_lift strategy.
    Camelot compatibility is a primary constraint — energy/BPM bonuses only
    boost already harmonically-acceptable candidates.

    Hard caps prevent key clashes from ranking highly:
      c_score <= 0.15  (4+ Camelot steps): capped at 0.25
      c_score <= 0.25  (3 Camelot steps):  capped at 0.45
    """
    lift_bonus = 0.0
    if bpm_d > 1.0:
        lift_bonus += min(bpm_d / 10.0, 0.15)   # faster BPM: up to +0.15
    if _ENERGY_RANK.get(to_energy, 1) > _ENERGY_RANK.get(from_energy, 1):
        lift_bonus += 0.15                        # energy tier rises: +0.15
    raw = c_score * 0.35 + b_score * 0.30 + e_score * 0.20 + lift_bonus
    # Hard harmonic floor — clashes cannot reach the top of the list
    if c_score <= 0.15:
        return min(raw, 0.25)
    if c_score <= 0.25:
        return min(raw, 0.45)
    return min(1.0, raw)


def _warmup_score(to_energy: str, b_score: float, c_score: float) -> float:
    energy_bonus = 0.3 if to_energy in ("Chill", "Mid") else 0.0
    return min(1.0, c_score * 0.40 + b_score * 0.30 + energy_bonus)


def _late_set_score(to_energy: str, b_score: float, c_score: float, to_bpm: float) -> float:
    energy_bonus = 0.25 if to_energy == "Peak" else 0.0
    bpm_bonus    = 0.10 if to_bpm >= 126 else 0.0
    return min(1.0, c_score * 0.35 + b_score * 0.30 + energy_bonus + bpm_bonus)


def _explain(ts: TransitionScore, from_key: str, to_key: str,
             from_energy: str, from_bpm: float) -> str:
    parts: List[str] = []

    # Key explanation
    dist, switched = _camelot_distance(from_key, to_key)
    if dist == 0 and not switched:
        parts.append(f"same key ({to_key}) — perfect harmonic match")
    elif dist == 0 and switched:
        parts.append(f"{from_key}→{to_key} mode switch — same notes, different feel")
    elif dist == 1 and not switched:
        parts.append(f"{from_key}→{to_key} adjacent key — standard harmonic move")
    elif dist == 1 and switched:
        parts.append(f"{from_key}→{to_key} diagonal — slightly adventurous, still works")
    elif dist == 2:
        parts.append(f"{from_key}→{to_key} 2 steps apart — use pitch correction")
    else:
        parts.append(f"{from_key}→{to_key} {dist} positions — key clash, risky mix")

    # BPM explanation
    d = abs(ts.bpm_delta_pct)
    if d <= 2:
        parts.append(f"BPM {ts.to_bpm:.1f} (+{ts.bpm_delta_pct:+.1f}%) — instant beatmatch")
    elif d <= 5:
        parts.append(f"BPM {ts.to_bpm:.1f} ({ts.bpm_delta_pct:+.1f}%) — easy pitch adjust")
    elif d <= 10:
        parts.append(f"BPM {ts.to_bpm:.1f} ({ts.bpm_delta_pct:+.1f}%) — noticeable shift")
    else:
        parts.append(f"BPM {ts.to_bpm:.1f} ({ts.bpm_delta_pct:+.1f}%) — major tempo change")

    # Energy explanation
    if ts.energy_score >= 0.95:
        parts.append(f"energy stays {ts.to_energy}")
    elif _ENERGY_RANK.get(ts.to_energy, 1) > _ENERGY_RANK.get(from_energy, 1):
        parts.append(f"energy lifts to {ts.to_energy}")
    else:
        parts.append(f"energy drops to {ts.to_energy}")

    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Main suggestion engine
# ---------------------------------------------------------------------------

def suggest_next(
    from_filepath: str,
    candidate_rows: Optional[list]          = None,
    strategy:       str                     = "safest",
    top_n:          int                     = 10,
    weights:        Optional[Dict[str, float]] = None,
    energy_direction: Optional[str]         = None,  # "up" | "down" | "maintain"
    exclude_paths:  Optional[List[str]]     = None,
) -> List[TransitionScore]:
    """
    Return top_n suggested next tracks for mixing after from_filepath.

    Args:
        from_filepath:    Path of the currently playing track.
        candidate_rows:   Pre-loaded DB rows to rank. If None, loads all OK tracks.
        strategy:         Ranking strategy: safest | energy_lift | smooth_blend |
                          best_warmup | best_late_set
        top_n:            Number of results to return.
        weights:          Custom factor weights dict (camelot/bpm/energy/genre).
        energy_direction: Bias toward energy going up/down/same.
        exclude_paths:    Paths to exclude (already-played tracks, the current track).

    Returns:
        List of TransitionScore objects ordered by strategy score descending.
    """
    from_row = db.get_track(from_filepath)
    if from_row is None:
        log.warning("harmonic: from track not found in DB: %s", from_filepath)
        return []

    if candidate_rows is None:
        candidate_rows = db.get_all_ok_tracks()

    exclude = set(exclude_paths or [])
    exclude.add(from_filepath)

    # Filter: exclude Acapella/Tool routes, unknown artists, already-used paths
    before = len(candidate_rows)
    candidates = [
        r for r in candidate_rows
        if r["filepath"] not in exclude
        and not _is_unknown_artist(r["artist"] or "")
        and not _is_exclusive_route(r)
    ]
    exclusive_excluded = before - len(candidate_rows) - len(exclude)
    if exclusive_excluded > 0:
        log.info(
            "harmonic-suggest: excluded %d Acapella/Tool track(s) from candidate pool",
            exclusive_excluded,
        )
    candidates = _dedupe_candidates(candidates)

    scores: List[TransitionScore] = []
    for row in candidates:
        ts = score_transition(from_row, row, weights)

        # Apply energy direction bias
        if energy_direction:
            from_energy = _classify_energy(float(from_row["bpm"] or 0), from_row["genre"] or "")
            to_energy   = ts.to_energy
            from_rank   = _ENERGY_RANK.get(from_energy, 1)
            to_rank     = _ENERGY_RANK.get(to_energy, 1)
            if energy_direction == "up"       and to_rank <= from_rank:
                ts.total_score *= 0.6
            elif energy_direction == "down"   and to_rank >= from_rank:
                ts.total_score *= 0.6
            elif energy_direction == "maintain" and to_rank != from_rank:
                ts.total_score *= 0.7

        scores.append(ts)

    # Apply absolute BPM step penalty — prevents 122→150 type jumps regardless
    # of how good harmonic/energy scores are.
    from_bpm_val = float(from_row["bpm"] or 0)
    if from_bpm_val > 0:
        for ts in scores:
            m = _bpm_step_multiplier(from_bpm_val, ts.to_bpm)
            if m < 1.0:
                for k in list(ts.strategies.keys()):
                    ts.strategies[k] = round(ts.strategies[k] * m, 3)
                ts.total_score = round(ts.total_score * m, 3)

    # Sort by chosen strategy
    valid_strategies = {"safest", "energy_lift", "smooth_blend", "best_warmup", "best_late_set"}
    sort_key_name = strategy if strategy in valid_strategies else "safest"
    scores.sort(key=lambda s: s.strategies.get(sort_key_name, s.total_score), reverse=True)

    return scores[:top_n]


# ---------------------------------------------------------------------------
# Context-aware suggestion (playlist tail awareness)
# ---------------------------------------------------------------------------

def suggest_from_playlist_context(
    played_filepaths: List[str],
    top_n: int = 10,
    strategy: str = "safest",
) -> List[TransitionScore]:
    """
    Suggest next tracks given an already-played sequence.
    Uses the last track as the current and all played tracks as exclusions.
    """
    if not played_filepaths:
        return []
    current = played_filepaths[-1]
    return suggest_next(
        from_filepath  = current,
        strategy       = strategy,
        top_n          = top_n,
        exclude_paths  = list(played_filepaths),
    )


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def format_suggestions_table(
    suggestions: List[TransitionScore],
    strategy: str,
    from_title: str,
    from_key: str,
    from_bpm: float,
) -> str:
    """Format suggestions as a human-readable table string."""
    lines: List[str] = []
    lines.append(f"\n=== Harmonic Suggestions for: {from_title} ===")
    lines.append(f"    Current key: {from_key}   BPM: {from_bpm:.1f}   Strategy: {strategy}\n")
    lines.append(f"  {'#':>2}  {'Score':>6}  {'Key':>4}  {'BPM':>7}  {'Energy':>6}  {'Artist — Title'}")
    lines.append(f"  {'-'*2}  {'-'*6}  {'-'*4}  {'-'*7}  {'-'*6}  {'-'*40}")
    for i, ts in enumerate(suggestions, 1):
        strat_score = ts.strategies.get(strategy, ts.total_score)
        title = f"{ts.to_artist} — {ts.to_title}"
        if len(title) > 45:
            title = title[:42] + "…"
        lines.append(
            f"  {i:>2}  {strat_score:.3f}  {ts.to_key:>4}  {ts.to_bpm:>7.1f}  "
            f"{ts.to_energy:>6}  {title}"
        )
        lines.append(f"      ↳ {ts.explanation}")
    return "\n".join(lines)


def write_suggestions_json(
    suggestions: List[TransitionScore],
    from_filepath: str,
    strategy: str,
    output_dir: Path,
) -> Path:
    """Write suggestions to a JSON file and return the path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem   = Path(from_filepath).stem[:30].replace(" ", "_")
    path   = output_dir / f"harmonic_{stem}_{ts_str}.json"

    data = {
        "from_filepath": from_filepath,
        "strategy": strategy,
        "suggestions": [
            {
                "rank":           i,
                "filepath":       ts.to_filepath,
                "artist":         ts.to_artist,
                "title":          ts.to_title,
                "bpm":            ts.to_bpm,
                "key":            ts.to_key,
                "energy":         ts.to_energy,
                "genre":          ts.to_genre,
                "total_score":    ts.total_score,
                "strategy_score": ts.strategies.get(strategy, ts.total_score),
                "camelot_score":  ts.camelot_score,
                "bpm_score":      ts.bpm_score,
                "energy_score":   ts.energy_score,
                "genre_score":    ts.genre_score,
                "bpm_delta_pct":  ts.bpm_delta_pct,
                "explanation":    ts.explanation,
            }
            for i, ts in enumerate(suggestions, 1)
        ],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Key/BPM lookup mode (no specific track required)
# ---------------------------------------------------------------------------

def suggest_by_key_bpm(
    key:    str,
    bpm:    float,
    energy: Optional[str]   = None,
    genre:  Optional[str]   = None,
    strategy: str           = "safest",
    top_n:  int             = 10,
) -> List[TransitionScore]:
    """
    Suggest tracks compatible with a given key + BPM without needing a track filepath.
    Constructs a synthetic 'from' row for scoring.
    """
    import sqlite3
    # Build a synthetic sqlite3.Row-like dict and use score_transition
    class _FakeRow(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)
        def __contains__(self, k):
            return super().__contains__(k)

    fake_row = _FakeRow({
        "filepath":     "__query__",
        "bpm":          bpm,
        "key_camelot":  key,
        "key_musical":  "",
        "genre":        genre or "",
        "artist":       "",
        "title":        f"Query: {key} @ {bpm:.0f}",
    })

    all_rows = db.get_all_ok_tracks()
    # Filter unknown artists, Acapella/Tool routes, then dedupe
    candidates = [
        r for r in all_rows
        if not _is_unknown_artist(r["artist"] or "")
        and not _is_exclusive_route(r)
    ]
    exclusive_excluded = len(all_rows) - len(candidates)
    if exclusive_excluded > 0:
        log.info(
            "harmonic-suggest: excluded %d Acapella/Tool track(s) from candidate pool",
            exclusive_excluded,
        )
    candidates = _dedupe_candidates(candidates)

    scores: List[TransitionScore] = []
    for row in candidates:
        ts = score_transition(fake_row, row)
        if energy:
            ts_energy = _ENERGY_RANK.get(ts.to_energy, 1)
            target    = _ENERGY_RANK.get(energy, 1)
            if abs(ts_energy - target) > 1:
                ts.total_score *= 0.6
        scores.append(ts)

    # Apply absolute BPM step penalty
    if bpm and bpm > 0:
        for ts in scores:
            m = _bpm_step_multiplier(bpm, ts.to_bpm)
            if m < 1.0:
                for k in list(ts.strategies.keys()):
                    ts.strategies[k] = round(ts.strategies[k] * m, 3)
                ts.total_score = round(ts.total_score * m, 3)

    valid = {"safest", "energy_lift", "smooth_blend", "best_warmup", "best_late_set"}
    sk    = strategy if strategy in valid else "safest"
    scores.sort(key=lambda s: s.strategies.get(sk, s.total_score), reverse=True)
    return scores[:top_n]
