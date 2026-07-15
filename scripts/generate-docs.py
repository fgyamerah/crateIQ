"""
generate-docs.py — Standalone doc generator for CrateIQ.

Generates COMMANDS.txt, COMMANDS.md, COMMANDS.html, and optionally
splices the README.md Core Commands section from the registry defined
in this file. Run from the project root:

    python3 scripts/generate-docs.py
    python3 scripts/generate-docs.py --dry-run
    python3 scripts/generate-docs.py --format md,html
    python3 scripts/generate-docs.py --format readme   # splice README only
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

COMMANDS: list[dict] = [
    {
        "name": "metadata-sanitize",
        "description": (
            "Deterministic offline cleaning of all metadata fields. "
            "Removes URL watermarks, promo artifacts, DJ pool tags, "
            "malformed ISRCs, and BPM/key comment noise."
        ),
        "purpose": [
            "Strips URL watermarks, promo tags, and DJ pool artifacts from every metadata field",
            "Removes malformed ISRCs and BPM/key noise embedded in comment fields",
            "Runs fully offline — no network, no AI, no external dependencies",
            "Safe to repeat — already-clean files produce no further changes",
        ],
        "common_usage": (
            "python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply"
        ),
        "notes": (
            "Idempotent — re-running a clean file produces no further changes.\n"
            "Safe to run before any AI or enrichment step."
        ),
        "flags": [
            {"flag": "--input DIR", "description": "Directory of audio files to process."},
            {"flag": "--apply",     "description": "Write changes to files. Without this flag, preview only."},
            {"flag": "--verbose",   "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py metadata-sanitize --input ~/Music/inbox",
            "python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply",
        ],
        "readme_snippet": [
            "# Preview (no writes)",
            "python3 pipeline.py metadata-sanitize --input ~/Music/inbox",
            "",
            "# Apply",
            "python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply",
        ],
    },
    {
        "name": "ai-normalize",
        "description": (
            "Local AI (Ollama) metadata proposals for artist, title, version, "
            "label, remixers, and featured artists. Preview by default; "
            "--apply to write. BPM, key, and cues are never touched."
        ),
        "purpose": [
            "Proposes improved artist, title, version, label, and remixer values via a local LLM",
            "Uses Ollama — all inference runs on your machine, no data sent externally",
            "Skips proposals below 0.75 confidence; BPM, key, and cues are never touched",
            "Use `--pre-sanitize` to clean fields before inference in a single pass",
        ],
        "common_usage": (
            "python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize --apply"
        ),
        "notes": (
            "Min confidence: 0.75 — proposals below threshold are skipped, not applied.\n"
            "--pre-sanitize: runs metadata-sanitize before inference (recommended)."
        ),
        "flags": [
            {"flag": "--input DIR",           "description": "Directory of audio files to process."},
            {"flag": "--apply",               "description": "Write accepted proposals to files."},
            {"flag": "--pre-sanitize",        "description": "Run metadata-sanitize before AI inference."},
            {"flag": "--min-confidence 0.75", "description": "Minimum confidence to accept a proposal."},
            {"flag": "--model MODEL",         "description": "Ollama model to use. Default: OLLAMA_DEFAULT_MODEL env."},
            {"flag": "--verbose",             "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py ai-normalize --input ~/Music/inbox",
            "python3 pipeline.py ai-normalize --input ~/Music/inbox --apply",
            "python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize --apply",
            "python3 pipeline.py ai-normalize --input ~/Music/inbox --min-confidence 0.80 --apply",
        ],
        "readme_snippet": [
            "# Preview",
            "python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize",
            "",
            "# Apply",
            "python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize --apply",
        ],
    },
    {
        "name": "artist-intelligence",
        "description": (
            "Deterministic artist normalization, alias resolution, and identity "
            "consistency across the library. Builds an alias store for consistent "
            "downstream processing."
        ),
        "purpose": [
            "Resolves artist name variants to a single canonical form across the library",
            "Stores aliases persistently for consistent cross-run identity resolution",
            "Handles collab/feat suffixes without corrupting the primary artist name",
            "Deterministic — same input always produces the same output",
        ],
        "common_usage": (
            "python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply"
        ),
        "notes": "Package: intelligence/artist/",
        "flags": [
            {"flag": "--input DIR", "description": "Directory or library path to process."},
            {"flag": "--apply",     "description": "Write normalized artist tags to files."},
            {"flag": "--verbose",   "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py artist-intelligence --input ~/Music/inbox",
            "python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply",
        ],
        "readme_snippet": [
            "python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply",
        ],
    },
    {
        "name": "metadata-enrich-online",
        "description": (
            "Fill missing album, label, and ISRC via Spotify + Deezer matching "
            "with confidence scoring. Preview by default; --apply to write. "
            "Artist field is never proposed."
        ),
        "purpose": [
            "Queries Spotify, Deezer, and Traxsource to fill missing album, label, and ISRC",
            "Routes each result to APPLY, REVIEW, or SKIP based on confidence and safety rules",
            "Artist field is never proposed; version mismatches block auto-apply",
            "Use `--move-ignored` to quarantine unresolvable files automatically",
        ],
        "common_usage": (
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply --move-ignored"
        ),
        "notes": (
            "Operational states per track:\n"
            "  APPLY   conf >= 0.80; all safety rules pass -> written with --apply\n"
            "  REVIEW  0.70 <= conf < 0.80 -> added to review queue\n"
            "  SKIP    hard safety block fires -> moved to IGNORED with --move-ignored\n"
            "\n"
            "IGNORED path: /home/koolkatdj/Music/music/IGNORED/"
        ),
        "flags": [
            {"flag": "--input DIR",           "description": "Directory of audio files to enrich."},
            {"flag": "--apply",               "description": "Write APPLY-state changes to files."},
            {"flag": "--min-confidence 0.80", "description": "Minimum confidence to apply. Default: 0.80."},
            {"flag": "--move-ignored",        "description": "Move all hard-rejected files to the IGNORED quarantine directory."},
            {"flag": "--verbose",             "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox",
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply",
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply --move-ignored",
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --min-confidence 0.85",
        ],
        "readme_snippet": [
            "# Preview",
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox",
            "",
            "# Apply (with IGNORED quarantine for unresolvable files)",
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply --move-ignored",
        ],
    },
    {
        "name": "review-queue",
        "description": (
            "Review and resolve medium-confidence enrichment results interactively. "
            "Reads entries populated by metadata-enrich-online (REVIEW state: "
            "0.70 <= conf < 0.80)."
        ),
        "purpose": [
            "Opens an interactive session to resolve REVIEW-state enrichment results",
            "Each entry shows proposed changes with before/after field values",
            "Accepted entries are written immediately; skipped entries stay in the queue",
            "Use `--list-only` to audit the queue without making any changes",
        ],
        "common_usage": "python3 pipeline.py review-queue",
        "notes": (
            "Queue file: data/intelligence/enrichment_review_queue.json\n"
            "Actions: a=apply  s=skip  d=delete  n=next  q=quit"
        ),
        "flags": [
            {"flag": "--list-only", "description": "Print all pending entries without entering interactive mode."},
        ],
        "examples": [
            "python3 pipeline.py review-queue",
            "python3 pipeline.py review-queue --list-only",
        ],
        "readme_snippet": [
            "python3 pipeline.py review-queue",
            "python3 pipeline.py review-queue --list-only",
        ],
    },
]

# Shared preamble data used in generate_md and generate_html.
_PIPELINE_STAGES = [
    "metadata-sanitize",
    "ai-normalize",
    "artist-intelligence",
    "metadata-enrich-online",
]

_OPERATIONAL_STATES = [
    ("APPLY",  "conf ≥ 0.80, all safety rules pass", "Written with `--apply`"),
    ("REVIEW", "0.70 ≤ conf < 0.80",                 "Added to review queue"),
    ("SKIP",   "Hard safety block fires",             "Moved to IGNORED with `--move-ignored`"),
]

_SAFETY_GUARANTEES = [
    "Artist field: **never proposed or modified**",
    "BPM, key, and cues: **never modified** — Mixed In Key owns these",
    "Version mismatch: conflicting version tokens → confidence capped at 0.74",
    "Low artist similarity (< 0.90, no ISRC anchor): confidence capped at 0.74",
    "ISRC exact match: overrides all formula limits → confidence 0.98",
    "Preview by default on every command — nothing writes without `--apply`",
]


# ---------------------------------------------------------------------------
# COMMANDS.txt  (unchanged from original)
# ---------------------------------------------------------------------------

def _divider(char: str = "=", width: int = 70) -> str:
    return char * width


def generate_txt(commands: list[dict], version: str) -> str:
    today = datetime.date.today().isoformat()
    lines: list[str] = [
        _divider("="),
        "CrateIQ — COMMAND REFERENCE (Intelligence Pipeline)",
        _divider("="),
        "",
        f"Version : {version}",
        "Platform: Ubuntu Studio 24 (Linux)",
        f"Updated : {today}",
        "",
        "Commands:",
    ]
    for cmd in commands:
        lines.append(f"  {cmd['name']}")
    lines += ["", _divider("="), ""]

    for cmd in commands:
        name = cmd["name"]
        lines += [
            _divider("-"),
            f"{name} — {cmd['description']}",
            _divider("-"),
            "",
        ]

        notes = cmd.get("notes", "")
        if notes:
            for note_line in notes.splitlines():
                lines.append(f"  {note_line}")
            lines.append("")

        flags = cmd.get("flags", [])
        if flags:
            lines.append("FLAGS")
            lines.append("")
            for f in flags:
                lines.append(f"  {f['flag']}")
                lines.append(f"        {f['description']}")
                lines.append("")

        examples = cmd.get("examples", [])
        if examples:
            lines.append("EXAMPLES")
            lines.append("")
            for ex in examples:
                lines.append(f"  {ex}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMMANDS.md
# ---------------------------------------------------------------------------

def generate_md(commands: list[dict], version: str) -> str:
    today = datetime.date.today().isoformat()

    # Table of contents
    toc_lines = [
        "- [Core Pipeline](#core-pipeline)",
        "- [Operational States](#operational-states)",
        "- [Safety Guarantees](#safety-guarantees)",
    ]
    for cmd in commands:
        anchor = cmd["name"].lower().replace(" ", "-")
        toc_lines.append(f"- [{cmd['name']}](#{anchor})")

    # Operational states table
    states_rows = "\n".join(
        f"| **{s}** | {c} | {a} |"
        for s, c, a in _OPERATIONAL_STATES
    )

    # Safety guarantees list
    safety_items = "\n".join(f"- {g}" for g in _SAFETY_GUARANTEES)

    # Pipeline string
    pipeline_str = " → ".join(f"`{s}`" for s in _PIPELINE_STAGES)

    lines: list[str] = [
        "# CrateIQ Commands",
        "",
        "A reference for the CrateIQ intelligence pipeline CLI.",
        "",
        f"Version {version} &nbsp;·&nbsp; Updated {today}",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
        "\n".join(toc_lines),
        "",
        "---",
        "",
        "## Core Pipeline",
        "",
        pipeline_str,
        "",
        "Each stage is standalone. Run one, or compose the full pipeline.  ",
        "Preview by default — nothing writes without `--apply`.",
        "",
        "---",
        "",
        "## Operational States",
        "",
        "Applies to `metadata-enrich-online` results:",
        "",
        "| State | Condition | Action |",
        "|---|---|---|",
        states_rows,
        "",
        "---",
        "",
        "## Safety Guarantees",
        "",
        safety_items,
        "",
        "---",
        "",
    ]

    for cmd in commands:
        name = cmd["name"]
        purpose = cmd.get("purpose", [])
        common_usage = cmd.get("common_usage", "")
        flags = cmd.get("flags", [])
        examples = cmd.get("examples", [])
        notes = cmd.get("notes", "")

        lines += [f"## {name}", "", cmd["description"], ""]

        if notes:
            for note_line in notes.splitlines():
                lines.append(f"> {note_line}")
            lines.append("")

        if purpose:
            lines += ["### Purpose", ""]
            for bullet in purpose:
                lines.append(f"- {bullet}")
            lines.append("")

        if common_usage:
            lines += [
                "### Common usage",
                "",
                "```bash",
                common_usage,
                "```",
                "",
            ]

        if flags:
            lines += [
                "### Flags",
                "",
                "| Flag | Description |",
                "|---|---|",
            ]
            for f in flags:
                lines.append(f"| `{f['flag']}` | {f['description']} |")
            lines.append("")

        if examples:
            lines += [
                "### Examples",
                "",
                "```bash",
            ]
            for ex in examples:
                lines.append(ex)
            lines += ["```", ""]

        lines += ["---", ""]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMMANDS.html  (Bootstrap 5)
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    import html
    return html.escape(s)


def _md_inline(s: str) -> str:
    """Convert **bold** and `code` in a known-safe string to HTML."""
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def generate_html(commands: list[dict], version: str) -> str:
    today = datetime.date.today().isoformat()

    # Sidebar nav
    nav_items = "\n".join(
        f'      <li class="nav-item">'
        f'<a class="nav-link py-1 px-3 small" href="#{_esc(cmd["name"])}">'
        f'{_esc(cmd["name"])}</a></li>'
        for cmd in commands
    )

    # Preamble: operational states table
    states_rows = "\n".join(
        f"<tr>"
        f'<td><span class="badge bg-secondary">{_esc(s)}</span></td>'
        f"<td><small>{_esc(c)}</small></td>"
        f"<td><small>{_md_inline(a)}</small></td>"
        f"</tr>"
        for s, c, a in _OPERATIONAL_STATES
    )

    # Preamble: safety guarantees
    safety_items = "\n".join(
        f"<li>{_md_inline(g)}</li>"
        for g in _SAFETY_GUARANTEES
    )

    # Pipeline badge row
    pipeline_badges = " <span class='text-muted'>→</span> ".join(
        f'<code class="bg-light border rounded px-2 py-1">{_esc(s)}</code>'
        for s in _PIPELINE_STAGES
    )

    # Command sections
    sections: list[str] = []
    for cmd in commands:
        name = cmd["name"]
        purpose = cmd.get("purpose", [])
        common_usage = cmd.get("common_usage", "")
        flags = cmd.get("flags", [])
        examples = cmd.get("examples", [])
        notes = cmd.get("notes", "")

        notes_html = ""
        if notes:
            notes_html = (
                f'<div class="alert alert-warning py-2 px-3 mt-3" role="alert">'
                f'<pre class="mb-0 small">{_esc(notes)}</pre>'
                f"</div>"
            )

        purpose_html = ""
        if purpose:
            items = "\n".join(f"<li>{_md_inline(b)}</li>" for b in purpose)
            purpose_html = (
                f'<h6 class="text-uppercase text-muted small fw-bold mt-3 mb-1">Purpose</h6>'
                f"<ul class=\"small mb-3\">{items}</ul>"
            )

        common_usage_html = ""
        if common_usage:
            common_usage_html = (
                f'<h6 class="text-uppercase text-muted small fw-bold mt-3 mb-1">Common usage</h6>'
                f'<pre class="bg-light border rounded p-3 small"><code>{_esc(common_usage)}</code></pre>'
            )

        flags_html = ""
        if flags:
            rows = "\n".join(
                f"<tr>"
                f'<td class="text-nowrap"><code>{_esc(f["flag"])}</code></td>'
                f'<td class="small text-muted">{_esc(f["description"])}</td>'
                f"</tr>"
                for f in flags
            )
            flags_html = (
                f'<h6 class="text-uppercase text-muted small fw-bold mt-3 mb-1">Flags</h6>'
                f'<table class="table table-sm table-bordered small mb-3">'
                f"<thead class=\"table-light\">"
                f"<tr><th>Flag</th><th>Description</th></tr>"
                f"</thead>"
                f"<tbody>{rows}</tbody>"
                f"</table>"
            )

        examples_html = ""
        if examples:
            inner = _esc("\n".join(examples))
            examples_html = (
                f'<h6 class="text-uppercase text-muted small fw-bold mt-3 mb-1">Examples</h6>'
                f'<pre class="bg-light border rounded p-3 small"><code>{inner}</code></pre>'
            )

        sections.append(
            f'<section id="{_esc(name)}" class="mb-5 pb-3 border-bottom">\n'
            f'  <h2 class="h4 mb-1"><code>{_esc(name)}</code></h2>\n'
            f'  <p class="text-muted">{_esc(cmd["description"])}</p>\n'
            f"  {notes_html}\n"
            f"  {purpose_html}\n"
            f"  {common_usage_html}\n"
            f"  {flags_html}\n"
            f"  {examples_html}\n"
            f"</section>"
        )

    body = "\n\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CrateIQ — Command Reference</title>
<link
  rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css"
  integrity="sha384-T3c6CoIi6uLrA9TneNEoa7RxnatzjcDSCmG1MXxSR1GAsXEV/Dwwykc2MPK8M2HN"
  crossorigin="anonymous"
>
<style>
  body {{ font-size: 14px; }}
  .sidebar {{
    position: sticky;
    top: 0;
    height: 100vh;
    overflow-y: auto;
    border-right: 1px solid #dee2e6;
    background: #f8f9fa;
  }}
  .sidebar .nav-link {{ color: #495057; }}
  .sidebar .nav-link:hover,
  .sidebar .nav-link.active {{ color: #0d6efd; background: transparent; }}
  pre code {{ font-size: 12px; }}
  section {{ scroll-margin-top: 1rem; }}
</style>
</head>
<body data-bs-spy="scroll" data-bs-target="#sidebar-nav" data-bs-offset="20">

<div class="container-fluid">
  <div class="row">

    <!-- Sidebar -->
    <nav id="sidebar-nav" class="col-md-3 col-lg-2 d-none d-md-flex flex-column sidebar py-3 ps-3 pe-0">
      <div class="mb-3">
        <strong class="d-block">CrateIQ</strong>
        <span class="text-muted" style="font-size:11px">Command Reference &nbsp;·&nbsp; v{_esc(version)}</span>
      </div>
      <div class="mb-1" style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#6c757d;font-weight:700;">
        Preamble
      </div>
      <ul class="nav flex-column mb-3 small">
        <li class="nav-item"><a class="nav-link py-1 px-3" href="#core-pipeline">Core Pipeline</a></li>
        <li class="nav-item"><a class="nav-link py-1 px-3" href="#operational-states">Operational States</a></li>
        <li class="nav-item"><a class="nav-link py-1 px-3" href="#safety-guarantees">Safety Guarantees</a></li>
      </ul>
      <div class="mb-1" style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#6c757d;font-weight:700;">
        Commands
      </div>
      <ul class="nav flex-column small">
{nav_items}
      </ul>
    </nav>

    <!-- Main content -->
    <main class="col-md-9 ms-sm-auto col-lg-10 px-md-5 py-4">

      <div class="mb-4 pb-3 border-bottom">
        <h1 class="h3 mb-1">CrateIQ Command Reference</h1>
        <span class="text-muted small">Version {_esc(version)} &nbsp;·&nbsp; Updated {_esc(today)}</span>
      </div>

      <!-- Core Pipeline -->
      <section id="core-pipeline" class="mb-5 pb-3 border-bottom">
        <h2 class="h5 mb-3">Core Pipeline</h2>
        <p class="mb-2">{pipeline_badges}</p>
        <p class="text-muted small mb-0">
          Each stage is standalone — run one, or compose the full pipeline.<br>
          Preview by default. Nothing writes without <code>--apply</code>.
        </p>
      </section>

      <!-- Operational States -->
      <section id="operational-states" class="mb-5 pb-3 border-bottom">
        <h2 class="h5 mb-3">Operational States</h2>
        <p class="text-muted small">Applies to <code>metadata-enrich-online</code> results.</p>
        <table class="table table-sm table-bordered small">
          <thead class="table-light">
            <tr><th>State</th><th>Condition</th><th>Action</th></tr>
          </thead>
          <tbody>
            {states_rows}
          </tbody>
        </table>
      </section>

      <!-- Safety Guarantees -->
      <section id="safety-guarantees" class="mb-5 pb-3 border-bottom">
        <h2 class="h5 mb-3">Safety Guarantees</h2>
        <ul class="small mb-0">
          {safety_items}
        </ul>
      </section>

      <!-- Command sections -->
      {body}

    </main>
  </div>
</div>

<script
  src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"
  integrity="sha384-C6RzsynM9kWDrMNeT87bh95OGNyZPhcTNXj1NW7RuBCsyN/o0jlpcV8Qyq46cDfL"
  crossorigin="anonymous"
></script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# README.md splice
# ---------------------------------------------------------------------------

README_START = "<!-- COMMANDS:START -->"
README_END   = "<!-- COMMANDS:END -->"


def generate_readme_section(commands: list[dict]) -> str:
    """
    Generate the content block that lives between README_START and README_END.
    Numbered subsections with a compact bash snippet per command.
    """
    lines: list[str] = [""]
    for i, cmd in enumerate(commands, 1):
        name = cmd["name"]
        desc_short = cmd["description"].split(".")[0]   # first sentence only
        snippet = cmd.get("readme_snippet", cmd["examples"][:2])
        lines += [
            f"### {i}. {name}",
            "",
            desc_short + ".",
            "",
            "```bash",
        ]
        lines += snippet
        lines += ["```", ""]

    lines.append(
        "> Full reference: [COMMANDS.md](COMMANDS.md) | [COMMANDS.html](COMMANDS.html)"
    )
    lines.append("")
    return "\n".join(lines)


def splice_readme(readme_path: Path, new_section: str) -> str:
    """
    Replace content between README_START / README_END markers.

    Falls back to inserting markers after the '## Core Commands' heading
    if not present. Appends to end of file if the heading is also absent.
    """
    text = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

    start_idx = text.find(README_START)
    end_idx   = text.find(README_END)

    if start_idx != -1 and end_idx != -1:
        block = README_START + "\n" + new_section + "\n" + README_END
        return text[:start_idx] + block + text[end_idx + len(README_END):]

    # No markers — find '## Core Commands' heading and insert after it
    heading_match = re.search(r"^## Core Commands\s*$", text, re.MULTILINE)
    if heading_match:
        insert_at = heading_match.end()
        # Skip any blank line immediately after the heading
        rest = text[insert_at:]
        leading = len(rest) - len(rest.lstrip("\n"))
        insert_at += leading
        block = (
            "\n" + README_START + "\n"
            + new_section + "\n"
            + README_END + "\n"
        )
        # Find next H2 or '---' to know where the old section ends
        end_match = re.search(r"^(?:## |\-\-\-)", text[insert_at:], re.MULTILINE)
        if end_match:
            old_end = insert_at + end_match.start()
            return text[:insert_at] + block + "\n" + text[old_end:]
        return text[:insert_at] + block

    # No heading found — append
    return text.rstrip() + "\n\n## Core Commands\n\n" + README_START + "\n" + new_section + "\n" + README_END + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

VERSION = "2.0.0"

OUTPUT_FILES = {
    "txt":    "COMMANDS.txt",
    "md":     "COMMANDS.md",
    "html":   "COMMANDS.html",
    "readme": "README.md",
}

GENERATORS = {
    "txt":  generate_txt,
    "md":   generate_md,
    "html": generate_html,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate CrateIQ command docs from the in-file registry.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print generated content to stdout — write no files.",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Write output here instead of the project root (default: parent of scripts/).",
    )
    parser.add_argument(
        "--format", default="txt,md,html",
        help=(
            "Comma-separated formats: txt, md, html, readme. "
            "Default: txt,md,html. "
            "'readme' splices the Core Commands section into README.md."
        ),
    )
    args = parser.parse_args()

    formats = {f.strip() for f in args.format.split(",") if f.strip()}
    unknown = formats - set(OUTPUT_FILES)
    if unknown:
        print(f"ERROR: unknown format(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1

    script_dir = Path(__file__).resolve().parent
    out_root = Path(args.output_dir).resolve() if args.output_dir else script_dir.parent

    generated: list[tuple[str, str]] = []

    for fmt in ("txt", "md", "html"):
        if fmt not in formats:
            continue
        content = GENERATORS[fmt](COMMANDS, VERSION)
        generated.append((OUTPUT_FILES[fmt], content))

    if "readme" in formats:
        readme_path = out_root / "README.md"
        section = generate_readme_section(COMMANDS)
        content = splice_readme(readme_path, section)
        generated.append(("README.md", content))

    if args.dry_run:
        for filename, content in generated:
            print(f"\n{'=' * 60}")
            print(f"  {filename}")
            print(f"{'=' * 60}\n")
            print(content[:2000])
            if len(content) > 2000:
                print(f"  ... [{len(content) - 2000} more chars] ...")
        print(f"\n--dry-run: {len(generated)} file(s) would be written to {out_root}")
        return 0

    out_root.mkdir(parents=True, exist_ok=True)
    for filename, content in generated:
        dest = out_root / filename
        dest.write_text(content, encoding="utf-8")
        print(f"  WROTE  {dest}")

    print(f"\ngenerate-docs: {len(generated)} file(s) written to {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
