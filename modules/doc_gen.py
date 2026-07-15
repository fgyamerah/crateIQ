"""
CrateIQ — Documentation generator.

Reads the command registry (modules/doc_registry.py) and generates:
  - COMMANDS.txt   — plain-text command reference
  - README.md      — the "Subcommands" section only (full replacement of
                     the command table between the sentinel comments)
  - COMMANDS.html  — dark-themed, sidebar-navigation HTML reference

All three outputs are complete, self-contained files safe to overwrite.
"""
from __future__ import annotations

import datetime
import html as _html
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules.doc_registry import REGISTRY


# ---------------------------------------------------------------------------
# COMMANDS.txt generator
# ---------------------------------------------------------------------------

def _divider(char: str = "=", width: int = 70) -> str:
    return char * width


def _fmt_flags_txt(flags: list[dict], indent: int = 2) -> str:
    """Render a flag list for plain-text output."""
    if not flags:
        return ""
    pad = " " * indent
    lines = ["FLAGS", ""]
    for f in flags:
        flag_str = f["flag"]
        meta = f.get("meta")
        if meta:
            flag_str = f"{flag_str} {meta}"
        lines.append(f"{pad}{flag_str}")
        desc = f.get("description", "")
        default = f.get("default")
        if default:
            desc = f"{desc} Default: {default}."
        # Wrap description at ~66 chars with deeper indent
        words = desc.split()
        line = ""
        sub_pad = " " * (indent + 6)
        wrapped: list[str] = []
        for word in words:
            if len(line) + len(word) + 1 > 60:
                wrapped.append(sub_pad + line.strip())
                line = word + " "
            else:
                line += word + " "
        if line.strip():
            wrapped.append(sub_pad + line.strip())
        lines.append("\n".join(wrapped))
        lines.append("")
    return "\n".join(lines)


def _fmt_examples_txt(examples: list[str], indent: int = 2) -> str:
    if not examples:
        return ""
    pad = " " * indent
    lines = ["EXAMPLES", ""]
    for ex in examples:
        for line in ex.splitlines():
            lines.append(f"{pad}{line}")
    return "\n".join(lines)


def generate_commands_txt(registry: list[dict], version: str = "1.5.0") -> str:
    """Generate the full COMMANDS.txt content from the registry."""
    today = datetime.date.today().isoformat()
    lines: list[str] = []

    lines += [
        _divider("="),
        "DJTOOLKIT (CrateIQ) — COMMAND REFERENCE",
        _divider("="),
        "",
        f"Version : {version}",
        f"Platform: Ubuntu Studio 24 (Linux)",
        f"Updated : {today}",
        "",
        "This file is generated from modules/doc_registry.py.",
        "To regenerate: python3 pipeline.py generate-docs",
        "To validate  : python3 pipeline.py validate-docs",
        "",
    ]

    # Table of contents
    lines += ["Contents:"]
    from modules.doc_registry import commands_by_category
    for cat, entries in commands_by_category().items():
        lines.append(f"  {cat}")
        for e in entries:
            lines.append(f"    {e['name']}")
    lines += ["", _divider("="), ""]

    # Body: one section per category
    for cat, entries in commands_by_category().items():
        lines += [
            _divider("="),
            cat,
            _divider("="),
            "",
        ]
        for entry in entries:
            name = entry["name"]
            lines += [
                _divider("-"),
                f"{name} — {entry['description']}",
                _divider("-"),
                "",
                entry["usage"],
                "",
            ]

            notes = entry.get("notes")
            if notes:
                for note_line in notes.splitlines():
                    lines.append(f"  {note_line}")
                lines.append("")

            flags_txt = _fmt_flags_txt(entry.get("flags", []))
            if flags_txt:
                lines.append(flags_txt)

            examples_txt = _fmt_examples_txt(entry.get("examples", []))
            if examples_txt:
                lines.append(examples_txt)

            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# README.md command section generator
# ---------------------------------------------------------------------------

def generate_readme_commands_section(registry: list[dict]) -> str:
    """
    Generate the ## Subcommands section for README.md.

    Returns just the section content (not the full README) so the caller
    can splice it in.
    """
    from modules.doc_registry import commands_by_category
    lines: list[str] = [
        "## Subcommands",
        "",
        "> Auto-generated from `modules/doc_registry.py`.  "
        "Run `python3 pipeline.py generate-docs` to refresh.",
        "",
    ]

    skip_cats = {"MAIN PIPELINE", "DOCS"}
    for cat, entries in commands_by_category().items():
        if cat in skip_cats:
            continue

        # Category heading
        cat_title = cat.replace("_", " ").title()
        lines += [f"### {cat_title}", ""]
        lines.append("```bash")
        for entry in entries:
            name = entry["name"]
            lines.append(f"# {entry['description']}")
            # Show the first non-multiline example (or usage)
            examples = [e for e in entry.get("examples", []) if "\\\n" not in e]
            if examples:
                lines.append(examples[0])
            else:
                lines.append(entry["usage"])
            lines.append("")
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def splice_readme_commands(readme_path: Path, new_section: str) -> str:
    """
    Return a new README.md string where the Subcommands section is replaced
    with new_section.

    Markers used:
        <!-- COMMANDS:START -->
        <!-- COMMANDS:END -->

    If the markers are present, the content between them is replaced.
    If the markers are absent but a '## Subcommands' heading exists, the
    section from that heading to the next '---' separator (or end-of-file)
    is replaced and wrapped with markers.
    If neither is found, the new section is appended at the end.
    """
    import re as _re

    text = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    start_marker = "<!-- COMMANDS:START -->"
    end_marker   = "<!-- COMMANDS:END -->"

    start_idx = text.find(start_marker)
    end_idx   = text.find(end_marker)

    if start_idx != -1 and end_idx != -1:
        # Markers already present — replace content between them
        block = start_marker + "\n" + new_section + "\n" + end_marker
        return text[:start_idx] + block + text[end_idx + len(end_marker):]

    # No markers — look for an existing '## Subcommands' heading
    heading_match = _re.search(r"^## Subcommands\s*$", text, _re.MULTILINE)
    if heading_match:
        # Find the end of this section: next H2 heading or '---' separator
        rest_start = heading_match.end()
        rest = text[rest_start:]
        end_match = _re.search(r"^(?:## |\-\-\-)", rest, _re.MULTILINE)
        if end_match:
            section_end = rest_start + end_match.start()
            block = start_marker + "\n" + new_section + "\n" + end_marker + "\n\n"
            return text[:heading_match.start()] + block + text[section_end:]
        else:
            # Section runs to end of file
            block = start_marker + "\n" + new_section + "\n" + end_marker + "\n"
            return text[:heading_match.start()] + block

    # No heading found — append at end
    return (
        text.rstrip()
        + "\n\n"
        + start_marker + "\n"
        + new_section + "\n"
        + end_marker + "\n"
    )


# ---------------------------------------------------------------------------
# COMMANDS.md generator
# ---------------------------------------------------------------------------

def _fmt_flags_md(flags: list[dict]) -> str:
    """Render a flag list as a Markdown table."""
    if not flags:
        return ""
    lines = [
        "| Flag | Description |",
        "|---|---|",
    ]
    for f in flags:
        flag_str = f["flag"]
        meta = f.get("meta")
        if meta:
            flag_str = f"{flag_str} {meta}"
        desc = f.get("description", "")
        default = f.get("default")
        if default:
            desc = f"{desc} Default: `{default}`."
        lines.append(f"| `{flag_str}` | {desc} |")
    return "\n".join(lines)


def generate_commands_md(registry: list[dict], version: str = "1.5.0") -> str:
    """Generate COMMANDS.md — a GitHub-friendly Markdown command reference."""
    today = datetime.date.today().isoformat()

    from modules.doc_registry import commands_by_category

    lines: list[str] = [
        "# CrateIQ Commands",
        "",
        f"Version {version} &nbsp;·&nbsp; Updated {today}",
        "",
        "> Generated from `modules/doc_registry.py`.  ",
        "> Run `python3 pipeline.py generate-docs` to refresh.",
        "",
        "---",
        "",
    ]

    for cat, entries in commands_by_category().items():
        lines += [f"## {cat.title()}", ""]
        for entry in entries:
            name = entry["name"]
            desc = entry["description"]
            usage = entry["usage"]
            flags = entry.get("flags", [])
            examples = entry.get("examples", [])
            notes = entry.get("notes", "")

            lines += [f"### `{name}`", "", desc, "", f"```bash", usage, "```", ""]

            if notes:
                for note_line in notes.splitlines():
                    lines.append(f"> {note_line}")
                lines.append("")

            flags_md = _fmt_flags_md(flags)
            if flags_md:
                lines += [flags_md, ""]

            if examples:
                lines += ["**Examples**", "", "```bash"]
                for ex in examples:
                    lines.append(ex)
                lines += ["```", ""]

        lines += ["---", ""]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMMANDS.html generator
# ---------------------------------------------------------------------------

def _html_id(name: str) -> str:
    return name.lower().replace(" ", "-").replace("/", "-")


def _esc(s: str) -> str:
    return _html.escape(s)


def _flag_rows_html(flags: list[dict]) -> str:
    if not flags:
        return ""
    rows = []
    for f in flags:
        flag_str = _esc(f["flag"])
        meta = f.get("meta")
        if meta:
            flag_str += f' <span class="meta">{_esc(meta)}</span>'
        desc = _esc(f.get("description", ""))
        default = f.get("default")
        if default:
            desc += f' <span class="default">(default: {_esc(default)})</span>'
        rows.append(
            f'<tr><td class="flag-cell"><code>{flag_str}</code></td>'
            f'<td class="desc-cell">{desc}</td></tr>'
        )
    return (
        '<table class="flag-table">'
        "<thead><tr><th>Flag</th><th>Description</th></tr></thead>"
        "<tbody>" + "\n".join(rows) + "</tbody></table>"
    )


def _example_block_html(examples: list[str]) -> str:
    if not examples:
        return ""
    inner = "\n".join(_esc(ex) for ex in examples)
    return f'<pre><code>{inner}</code></pre>'


def _nav_html(registry: list[dict]) -> str:
    from modules.doc_registry import commands_by_category
    cats = commands_by_category()
    parts = []
    for cat, entries in cats.items():
        cat_id = _html_id(cat)
        parts.append(
            f'<div class="nav-section">'
            f'<div class="nav-section-title">{_esc(cat)}</div>'
        )
        for e in entries:
            cmd_id = _html_id(e["name"])
            parts.append(
                f'<a href="#{cmd_id}">{_esc(e["name"])}</a>'
            )
        parts.append("</div>")
    return "\n".join(parts)


def generate_commands_html(registry: list[dict], version: str = "1.5.0") -> str:
    """Generate a complete COMMANDS.html from the registry."""
    today = datetime.date.today().isoformat()

    from modules.doc_registry import commands_by_category

    # Build body sections
    body_parts: list[str] = []
    for cat, entries in commands_by_category().items():
        cat_id = _html_id(cat)
        body_parts.append(
            f'<section id="{cat_id}" class="section">'
            f'<h2 class="section-title">{_esc(cat)}</h2>'
        )
        for entry in entries:
            cmd_id = _html_id(entry["name"])
            name = entry["name"]
            desc = entry["description"]
            usage = entry["usage"]
            flags = entry.get("flags", [])
            examples = entry.get("examples", [])
            notes = entry.get("notes", "")

            notes_html = ""
            if notes:
                notes_html = (
                    f'<div class="notes"><pre>{_esc(notes)}</pre></div>'
                )

            flags_html = _flag_rows_html(flags)
            examples_html = _example_block_html(examples)

            body_parts.append(f"""
<div id="{cmd_id}" class="command-block">
  <h3 class="command-name">{_esc(name)}</h3>
  <p class="command-desc">{_esc(desc)}</p>
  <div class="usage-line"><strong>Usage:</strong> <code>{_esc(usage)}</code></div>
  {notes_html}
  {"<h4>Flags</h4>" + flags_html if flags_html else ""}
  {"<h4>Examples</h4>" + examples_html if examples_html else ""}
</div>
""")
        body_parts.append("</section>")

    nav = _nav_html(registry)
    body = "\n".join(body_parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CrateIQ — Command Reference</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:        #0e1117;
    --bg2:       #161b22;
    --bg3:       #1c2330;
    --border:    #30363d;
    --text:      #e6edf3;
    --text-dim:  #8b949e;
    --accent:    #58a6ff;
    --accent2:   #3fb950;
    --warn:      #d29922;
    --code-bg:   #161b22;
    --nav-w:     260px;
    --font-mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }}

  html {{ scroll-behavior: smooth; }}
  body {{
    background: var(--bg); color: var(--text); font-family: var(--font-sans);
    font-size: 14px; line-height: 1.6; display: flex; min-height: 100vh;
  }}

  /* ── Navigation ── */
  #nav {{
    position: fixed; top: 0; left: 0; width: var(--nav-w); height: 100vh;
    background: var(--bg2); border-right: 1px solid var(--border);
    overflow-y: auto; padding: 0 0 24px 0; z-index: 100;
    scrollbar-width: thin; scrollbar-color: var(--border) transparent;
  }}
  .nav-header {{
    padding: 16px 16px 12px; border-bottom: 1px solid var(--border);
    position: sticky; top: 0; background: var(--bg2); z-index: 1;
  }}
  .nav-header h1 {{ font-size: 13px; font-weight: 700; color: var(--accent);
    letter-spacing: 0.5px; text-transform: uppercase; }}
  .nav-header p {{ font-size: 11px; color: var(--text-dim); margin-top: 2px; }}
  .nav-section {{ padding: 10px 0 4px 0; }}
  .nav-section-title {{
    font-size: 10px; font-weight: 700; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: 1px; padding: 0 16px 4px;
  }}
  #nav a {{
    display: block; padding: 5px 16px 5px 20px; color: var(--text-dim);
    text-decoration: none; font-size: 13px; border-left: 2px solid transparent;
    transition: color .15s, border-color .15s, background .15s;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  #nav a:hover {{ color: var(--text); background: var(--bg3); border-left-color: var(--accent); }}
  #nav a.active {{ color: var(--accent); border-left-color: var(--accent);
    background: rgba(88,166,255,0.08); }}

  /* ── Content ── */
  #content {{ margin-left: var(--nav-w); flex: 1; max-width: 900px; padding: 40px 48px 80px; }}
  .page-header {{ margin-bottom: 40px; padding-bottom: 24px; border-bottom: 1px solid var(--border); }}
  .page-header h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
  .page-header p {{ color: var(--text-dim); font-size: 15px; max-width: 600px; }}
  .meta-line {{ font-size: 12px; color: var(--text-dim); margin-top: 8px; }}

  /* ── Sections ── */
  .section {{ margin-bottom: 56px; }}
  .section-title {{
    font-size: 13px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; color: var(--text-dim);
    border-bottom: 1px solid var(--border); padding-bottom: 8px; margin-bottom: 24px;
  }}

  /* ── Command blocks ── */
  .command-block {{
    background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
    padding: 24px 28px; margin-bottom: 20px;
  }}
  .command-name {{ font-size: 18px; font-weight: 700; color: var(--accent); margin-bottom: 6px; }}
  .command-desc {{ color: var(--text-dim); margin-bottom: 14px; }}
  .usage-line {{ margin-bottom: 16px; }}
  .usage-line code {{ background: var(--code-bg); padding: 4px 8px; border-radius: 4px;
    font-family: var(--font-mono); font-size: 13px; color: var(--accent2); }}

  /* ── Notes ── */
  .notes {{ margin: 12px 0; padding: 12px 16px; background: var(--bg3);
    border-left: 3px solid var(--warn); border-radius: 0 4px 4px 0; }}
  .notes pre {{ font-family: var(--font-mono); font-size: 12px; color: var(--text-dim);
    white-space: pre-wrap; }}

  /* ── Flags table ── */
  h4 {{ font-size: 12px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.5px; color: var(--text-dim); margin: 16px 0 8px; }}
  .flag-table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 16px; }}
  .flag-table th {{ text-align: left; color: var(--text-dim); font-size: 11px;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
    padding: 4px 8px; border-bottom: 1px solid var(--border); }}
  .flag-table td {{ padding: 6px 8px; border-bottom: 1px solid var(--bg3); vertical-align: top; }}
  .flag-cell {{ width: 35%; white-space: nowrap; }}
  .flag-cell code {{ font-family: var(--font-mono); font-size: 12px; color: var(--accent2); }}
  .meta {{ color: var(--warn); font-style: italic; }}
  .default {{ color: var(--text-dim); font-size: 11px; }}

  /* ── Examples ── */
  pre {{ background: var(--code-bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 14px 16px; overflow-x: auto; }}
  pre code {{ font-family: var(--font-mono); font-size: 12px; color: var(--text);
    white-space: pre; }}
</style>
</head>
<body>

<nav id="nav">
  <div class="nav-header">
    <h1>CrateIQ</h1>
    <p>Command Reference</p>
  </div>
  {nav}
</nav>

<main id="content">
  <div class="page-header">
    <h1>CrateIQ — Command Reference</h1>
    <p>Local-first DJ library automation toolkit. Ubuntu Studio 24 → Rekordbox on Windows.</p>
    <div class="meta-line">Version {_esc(version)} &nbsp;·&nbsp; Generated {_esc(today)}</div>
  </div>

  {body}
</main>

<script>
  // Highlight active nav link on scroll
  const sections = document.querySelectorAll('[id]');
  const navLinks = document.querySelectorAll('#nav a');
  const observer = new IntersectionObserver(entries => {{
    entries.forEach(e => {{
      if (e.isIntersecting) {{
        navLinks.forEach(a => a.classList.remove('active'));
        const link = document.querySelector('#nav a[href="#' + e.target.id + '"]');
        if (link) link.classList.add('active');
      }}
    }});
  }}, {{ rootMargin: '-20% 0px -75% 0px' }});
  sections.forEach(s => observer.observe(s));
</script>

</body>
</html>
"""
