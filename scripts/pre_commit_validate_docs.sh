#!/usr/bin/env bash
# ======================================================================
# CrateIQ — pre-commit hook: validate-docs
# ======================================================================
#
# INSTALLATION (one-time setup):
#
#   cp scripts/pre_commit_validate_docs.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
#
# WHAT IT DOES:
#   Before every commit, runs:
#     python3 pipeline.py validate-docs --strict
#
#   If any commands in the registry are missing from COMMANDS.txt,
#   or any stale entries are detected, the commit is BLOCKED and a
#   helpful message is printed.
#
# TO BYPASS (emergency only):
#   git commit --no-verify
#
# TO FIX a docs-out-of-sync failure:
#   python3 pipeline.py generate-docs
#   git add COMMANDS.txt README.md COMMANDS.html
#   git commit
#
# ======================================================================

set -euo pipefail

# Locate the repo root (the script may be run from anywhere)
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "[pre-commit] Running validate-docs..."

if python3 pipeline.py validate-docs --strict; then
    echo "[pre-commit] validate-docs: OK"
    exit 0
else
    echo ""
    echo "======================================================="
    echo "  COMMIT BLOCKED: documentation is out of sync."
    echo "======================================================="
    echo ""
    echo "  Fix:  python3 pipeline.py generate-docs"
    echo "        git add COMMANDS.txt README.md COMMANDS.html"
    echo "        git commit"
    echo ""
    echo "  Skip: git commit --no-verify   (not recommended)"
    echo ""
    exit 1
fi
