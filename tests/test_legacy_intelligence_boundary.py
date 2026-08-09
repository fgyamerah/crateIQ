"""
Phase 6 regression guard: superseded intelligence/provider paths must stay
removed, the current backend provider architecture must never depend on
them, and the surviving legacy `intelligence/` trees that pipeline.py's
maintenance commands still import must keep importing cleanly.

Removed in Phase 6 (dead code, zero callers found repo-wide):
  - utils/llm_client.py                (unused Anthropic wrapper)
  - intelligence/label/cli.py          (unused standalone label-intel CLI,
                                         superseded by pipeline.py's direct
                                         scraper/exporters integration)
  - intelligence/label/providers/      (unused Beatport/Discogs stub,
                                         superseded by intelligence/label/
                                         sources/ and backend/app/services/
                                         providers/)
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_REMOVED_PATHS = (
    "utils/llm_client.py",
    "intelligence/label/cli.py",
    "intelligence/label/providers",
)

_FORBIDDEN_BACKEND_IMPORT_ROOTS = (
    "utils.llm_client",
    "intelligence.label.cli",
    "intelligence.label.providers",
    "anthropic",
)


def test_removed_legacy_paths_stay_removed():
    for rel in _REMOVED_PATHS:
        assert not (REPO_ROOT / rel).exists(), (
            f"{rel} was removed in Phase 6 as dead/superseded code and "
            "must not be reintroduced without a fresh dependency audit"
        )


def _iter_backend_files():
    base = REPO_ROOT / "backend" / "app"
    yield from sorted(base.rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    return roots


def test_backend_does_not_import_removed_or_forbidden_legacy_paths():
    findings: list[str] = []
    for path in _iter_backend_files():
        for root in _imported_roots(path):
            if any(
                root == forbidden or root.startswith(forbidden + ".")
                for forbidden in _FORBIDDEN_BACKEND_IMPORT_ROOTS
            ):
                findings.append(f"{path.relative_to(REPO_ROOT)}: imports {root}")
    assert not findings, (
        "current backend/app/services/providers architecture must not "
        "depend on removed/legacy intelligence paths or the anthropic "
        "SDK:\n" + "\n".join(findings)
    )


def test_current_provider_package_imports_independently():
    """Sanity check the current provider architecture imports cleanly
    on its own, with no reliance on the removed legacy modules."""
    module = importlib.import_module("backend.app.services.providers")
    assert module is not None


def test_surviving_legacy_intelligence_trees_still_import():
    """intelligence/enrichment and intelligence/label remain load-bearing
    for pipeline.py's metadata-enrich-online/enrichment-review/label-intel/
    label-clean commands (see docs/architecture/TOOLKIT_COMMAND_
    CLASSIFICATION.md); Phase 6 must not have broken their imports."""
    for name in (
        "intelligence.enrichment.runner",
        "intelligence.enrichment.metadata_matcher",
        "intelligence.enrichment.spotify_lookup",
        "intelligence.enrichment.deezer_lookup",
        "intelligence.enrichment.traxsource_lookup",
        "intelligence.label.scraper",
        "intelligence.label.cleaner",
        "intelligence.label.normalizer",
        "intelligence.label.sources.beatport",
        "intelligence.label.sources.traxsource",
    ):
        importlib.import_module(name)
