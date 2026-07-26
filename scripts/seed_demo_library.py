#!/usr/bin/env python3
"""CrateIQ demo library seeder.

Populates a small, clearly-fake SQLite database with realistic-looking demo
tracks so the frontend Library view (and other dashboards) can be exercised
and screenshotted with populated data, without touching any real music
library.

Safety:
  - Always writes under <repo>/.run/demo-library/logs/processed.db. This
    path is NOT configurable via CLI flags — that's deliberate, so this
    script can never accidentally be pointed at a real DJ_MUSIC_ROOT.
  - Never scans, reads, or writes real audio files. Every "filepath" in the
    seeded rows is a fabricated string; no filesystem paths outside
    .run/demo-library are touched.
  - `.run/` is already gitignored (see .gitignore), so the demo DB is never
    committed.
  - Idempotent: re-running upserts the same fixed set of rows (keyed by
    filepath). Pass --reset to wipe the tracks table first for a clean slate.

Usage:
  .venv/bin/python scripts/seed_demo_library.py            # seed/update
  .venv/bin/python scripts/seed_demo_library.py --reset     # wipe + reseed
  .venv/bin/python scripts/seed_demo_library.py --count 60  # more tracks

Then point the backend at the demo library for a local run:
  export DJ_MUSIC_ROOT="$(pwd)/.run/demo-library"
  bash scripts/crateiq-local-services.sh restart
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_ROOT = REPO_ROOT / ".run" / "demo-library"

# Fixed seed → deterministic, idempotent output across runs.
_RNG = random.Random(1337)

# genre -> (bpm_min, bpm_max)
GENRE_BPM = {
    "House": (118, 126),
    "Tech House": (122, 128),
    "Deep House": (118, 124),
    "Melodic House": (118, 124),
    "Afro House": (112, 121),
    "Amapiano": (108, 116),
    "Afrobeats": (100, 112),
    "Highlife": (100, 118),
    "Hiplife": (90, 106),
    "Gospel": (70, 140),
    "Techno": (125, 135),
    "Progressive House": (120, 126),
}

ARTISTS = [
    "MK", "RUFUS DU SOL", "John Summit", "ARTBAT", "Meduza", "Anyma",
    "Calvin Harris", "Drumcomplex", "Mark Knight", "Chris Lake", "Alice Deejay",
    "Yotto", "Black Coffee", "Culoe De Song", "Kabza De Small", "DJ Maphorisa",
    "Burna Boy", "Wizkid", "Davido", "Daddy Lumba", "Amakye Dede",
    "Sarkodie", "Sonnie Badu", "Joe Mettle", "Diamond Platnumz",
    "Adam Beyer", "Charlotte de Witte", "Eric Prydz", "Lane 8", "Dom Dolla",
    "Fisher", "Disclosure", "Camelphat", "Solardo", "Franky Wah",
]

TITLE_WORDS_A = [
    "Solar", "Deep", "Midnight", "Golden", "Silent", "Electric", "Sunset",
    "Broken", "Higher", "Distant", "Velvet", "Neon", "Wild", "Rising",
    "Lost", "Sacred", "Endless", "Hidden", "Crystal", "Amber",
]
TITLE_WORDS_B = [
    "Flare", "End", "Move", "Skies", "Control", "Horizon", "Echo", "Ground",
    "Fire", "Signal", "Dreams", "Faith", "Rhythm", "Motion", "Light",
    "Waves", "Groove", "Spirit", "Drift", "Glow",
]
VERSIONS = [
    "Original Mix", "Extended Mix", "Radio Edit", "Club Mix", "VIP Mix",
    "Extended", None, None,
]

CAMELOT_CODES = [f"{n}{letter}" for n in range(1, 13) for letter in ("A", "B")]
MUSICAL_KEYS = {
    "A": ["A Minor", "E Minor", "B Minor", "F# Minor", "C# Minor", "G# Minor",
          "D# Minor", "A# Minor", "F Minor", "C Minor", "G Minor", "D Minor"],
    "B": ["C Major", "G Major", "D Major", "A Major", "E Major", "B Major",
          "F# Major", "C# Major", "G# Major", "D# Major", "A# Major", "F Major"],
}

QUALITY_BY_BITRATE = {320: "HIGH", 256: "HIGH", 192: "MEDIUM", 128: "LOW"}


def make_track(i: int, genre: str) -> dict:
    bpm_lo, bpm_hi = GENRE_BPM[genre]
    bpm = float(_RNG.randint(bpm_lo, bpm_hi))
    artist = _RNG.choice(ARTISTS)
    title = f"{_RNG.choice(TITLE_WORDS_A)} {_RNG.choice(TITLE_WORDS_B)}"
    version = _RNG.choice(VERSIONS)
    filename_title = f"{title} ({version})" if version else title
    bitrate = _RNG.choice([320, 320, 256, 192, 128])
    camelot = _RNG.choice(CAMELOT_CODES)
    letter = camelot[-1]
    number = int(camelot[:-1])
    musical = MUSICAL_KEYS[letter][(number - 1) % 12]

    # A handful of deliberately imperfect rows so issue states are visible.
    issue_roll = i % 9
    missing_artist = issue_roll == 0
    missing_title = issue_roll == 1
    missing_key = issue_roll == 2
    low_quality = issue_roll == 3
    needs_review = issue_roll in (2, 3, 4)

    if low_quality:
        bitrate = 128
    if missing_key:
        camelot = None
        musical = None

    row = dict(
        filepath=f"/music/{genre}/{artist} - {filename_title} [{i:03d}].mp3",
        artist=None if missing_artist else artist,
        title=None if missing_title else title,
        genre=genre,
        bpm=bpm,
        key_musical=musical,
        key_camelot=camelot,
        bitrate_kbps=bitrate,
        status="needs_review" if needs_review else "ok",
        parse_confidence="LOW" if (missing_artist or missing_title) else (
            "MEDIUM" if needs_review else "HIGH"
        ),
        quality_tier=QUALITY_BY_BITRATE.get(bitrate, "MEDIUM"),
    )
    return row


def build_rows(count: int) -> list[dict]:
    genres = list(GENRE_BPM.keys())
    rows = []
    for i in range(count):
        genre = genres[i % len(genres)]
        rows.append(make_track(i, genre))
    return rows


def _musical_for_camelot(camelot: str) -> str:
    letter = camelot[-1]
    number = int(camelot[:-1])
    return MUSICAL_KEYS[letter][(number - 1) % 12]


def _cluster_row(
    *,
    slug: str,
    artist: str,
    title: str,
    genre: str,
    bpm: float,
    camelot: str,
    bitrate: int = 320,
) -> dict:
    """Build one fixed-filepath demo row for the compatible-tracks clusters below."""
    return dict(
        filepath=f"/music/Compatibility Demo/{artist} - {title} [{slug}].mp3",
        artist=artist,
        title=title,
        genre=genre,
        bpm=bpm,
        key_musical=_musical_for_camelot(camelot),
        key_camelot=camelot,
        bitrate_kbps=bitrate,
        status="ok",
        parse_confidence="HIGH",
        quality_tier=QUALITY_BY_BITRATE.get(bitrate, "MEDIUM"),
    )


def build_compatibility_cluster_rows() -> list[dict]:
    """
    Fixed, deterministic demo tracks guaranteeing that the compatible-tracks
    API/UI always has interesting real results to show, regardless of what
    the random --count rows happen to roll. Two anchors:

      - "Coastal Anchor" (8A, Afro House, 122 BPM) — same-key, adjacent-key,
        relative-major/minor, and close-BPM clusters.
      - "Motherland Anchor" (3A, Amapiano, 110 BPM) — a same-key cluster that
        spans the required Ghana/Africa genre set (Amapiano, Afrobeats,
        Highlife, Hiplife, Gospel), demonstrating the genre-boost signal.
    """
    return [
        # --- Anchor 1: 8A / Afro House / 122 BPM -------------------------------
        _cluster_row(slug="c01", artist="Coastal Collective", title="Coastal Anchor",
                     genre="Afro House", bpm=122.0, camelot="8A"),
        # Same-key group (8A)
        _cluster_row(slug="c02", artist="Coastal Collective", title="Coastal Sunrise",
                     genre="Afro House", bpm=121.0, camelot="8A"),
        _cluster_row(slug="c03", artist="Coastal Collective", title="Coastal Drift",
                     genre="Amapiano", bpm=120.0, camelot="8A"),
        _cluster_row(slug="c04", artist="Coastal Collective", title="Coastal Horizon",
                     genre="Tech House", bpm=123.0, camelot="8A"),
        # Adjacent-key group (7A / 9A) — includes a close-BPM and a farther-BPM
        # candidate at the same adjacent key so BPM tolerance visibly affects rank.
        _cluster_row(slug="c05", artist="Coastal Collective", title="Adjacent Tide",
                     genre="Deep House", bpm=119.0, camelot="7A"),
        _cluster_row(slug="c06", artist="Coastal Collective", title="Adjacent Pulse",
                     genre="Melodic House", bpm=122.5, camelot="9A"),
        _cluster_row(slug="c07", artist="Coastal Collective", title="Adjacent Runout",
                     genre="Melodic House", bpm=127.0, camelot="9A"),
        # Relative major/minor group (8B)
        _cluster_row(slug="c08", artist="Coastal Collective", title="Relative Bloom",
                     genre="Progressive House", bpm=122.0, camelot="8B"),
        # --- Anchor 2: 3A / Amapiano / 110 BPM — mixed-genre same-key cluster --
        _cluster_row(slug="c09", artist="Motherland Sound", title="Motherland Anchor",
                     genre="Amapiano", bpm=110.0, camelot="3A"),
        _cluster_row(slug="c10", artist="Motherland Sound", title="Motherland Pulse",
                     genre="Afrobeats", bpm=108.0, camelot="3A"),
        _cluster_row(slug="c11", artist="Motherland Sound", title="Motherland Highlife",
                     genre="Highlife", bpm=104.0, camelot="3A"),
        _cluster_row(slug="c12", artist="Motherland Sound", title="Motherland Praise",
                     genre="Gospel", bpm=100.0, camelot="3A"),
        # Adjacent + relative coverage for anchor 2
        _cluster_row(slug="c13", artist="Motherland Sound", title="Motherland Echo",
                     genre="Hiplife", bpm=96.0, camelot="2A"),
        _cluster_row(slug="c14", artist="Motherland Sound", title="Motherland Uplift",
                     genre="Afro House", bpm=114.0, camelot="3B"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=52, help="number of demo tracks (default 52)")
    parser.add_argument("--reset", action="store_true", help="wipe existing rows before seeding")
    args = parser.parse_args()

    if args.count < 1 or args.count > 500:
        print("refusing: --count must be between 1 and 500", file=sys.stderr)
        return 1

    DEMO_ROOT.mkdir(parents=True, exist_ok=True)
    (DEMO_ROOT / "logs").mkdir(parents=True, exist_ok=True)

    # Import db.py/config.py only after pointing DJ_MUSIC_ROOT at the demo
    # root, so init_db()/DB_PATH resolve inside .run/demo-library — never
    # the real library root.
    import os
    os.environ["DJ_MUSIC_ROOT"] = str(DEMO_ROOT)
    sys.path.insert(0, str(REPO_ROOT))
    import db  # noqa: E402  (repo-root module, see AGENTS.md db.py)
    import config  # noqa: E402

    assert Path(config.DB_PATH).is_relative_to(DEMO_ROOT), (
        f"refusing to seed outside demo root: {config.DB_PATH}"
    )

    db.init_db()

    if args.reset:
        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute("DELETE FROM tracks")
            conn.commit()
        print(f"Reset tracks table at {config.DB_PATH}")

    rows = build_rows(args.count) + build_compatibility_cluster_rows()
    for row in rows:
        db.upsert_track(row.pop("filepath"), **row)

    with sqlite3.connect(config.DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]

    print(f"Seeded {len(rows)} demo tracks ({total} total rows) at {config.DB_PATH}")
    print(f"Genres: {', '.join(GENRE_BPM.keys())}")
    print(
        "Includes 14 fixed 'Compatibility Demo' tracks (Coastal Collective / "
        "Motherland Sound) covering same-key, adjacent-key, relative major/"
        "minor, close-BPM, and mixed-genre clusters for GET /api/tracks/{id}/compatible."
    )
    print()
    print("To run the app against this demo library:")
    print(f'  export DJ_MUSIC_ROOT="{DEMO_ROOT}"')
    print("  bash scripts/crateiq-local-services.sh restart")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
