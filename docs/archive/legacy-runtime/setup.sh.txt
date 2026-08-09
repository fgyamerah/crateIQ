#!/usr/bin/env bash
# =============================================================================
# CrateIQ — first-time setup script
#
# Run once after cloning the repo.
# Creates the music directory structure, installs Python deps,
# installs the beets config, and registers systemd user services.
#
# Usage:
#   ./setup.sh [--music-root /path/to/music] [--venv] [--no-systemd]
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MUSIC_ROOT="/music"
USE_VENV=0
SKIP_SYSTEMD=0
VENV_DIR="${SCRIPT_DIR}/.venv"

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --music-root)
            MUSIC_ROOT="$2"; shift 2 ;;
        --venv)
            USE_VENV=1; shift ;;
        --no-systemd)
            SKIP_SYSTEMD=1; shift ;;
        *)
            echo "Unknown argument: $1"; exit 1 ;;
    esac
done

log()  { echo "[setup] $*"; }
warn() { echo "[setup] WARN: $*"; }
ok()   { echo "[setup] ✓  $*"; }

log "=================================================="
    log "CrateIQ Setup"
log "Music root : ${MUSIC_ROOT}"
log "Script dir : ${SCRIPT_DIR}"
log "=================================================="

# ---------------------------------------------------------------------------
# 1. Create music directory tree
# ---------------------------------------------------------------------------
log "Creating directory structure under ${MUSIC_ROOT}/ ..."
for d in \
    "${MUSIC_ROOT}/inbox" \
    "${MUSIC_ROOT}/processing" \
    "${MUSIC_ROOT}/library/sorted/_unsorted" \
    "${MUSIC_ROOT}/library/sorted/_compilations" \
    "${MUSIC_ROOT}/duplicates" \
    "${MUSIC_ROOT}/rejected" \
    "${MUSIC_ROOT}/playlists/m3u" \
    "${MUSIC_ROOT}/playlists/xml" \
    "${MUSIC_ROOT}/logs/reports"
do
    mkdir -p "${d}"
    ok "${d}"
done

# ---------------------------------------------------------------------------
# 2. System dependencies (apt)
# ---------------------------------------------------------------------------
log "Checking system packages ..."

MISSING_APT=()
for pkg in ffmpeg rmlint aubio-tools inotify-tools kid3; do
    if ! dpkg -s "${pkg}" &>/dev/null; then
        MISSING_APT+=("${pkg}")
    fi
done

if [[ ${#MISSING_APT[@]} -gt 0 ]]; then
    log "Installing system packages: ${MISSING_APT[*]}"
    sudo apt-get install -y "${MISSING_APT[@]}"
else
    ok "All system packages already installed"
fi

# beets via apt (older but stable)
if ! command -v beet &>/dev/null; then
    log "Installing beets ..."
    sudo apt-get install -y beets
fi

# ---------------------------------------------------------------------------
# 3. keyfinder-cli (not in apt — check if installed or provide instructions)
# ---------------------------------------------------------------------------
if ! command -v keyfinder-cli &>/dev/null; then
    warn "keyfinder-cli not found."
    warn "Install from: https://github.com/EvanPurkhiser/keyfinder-cli"
    warn "Quick option: download the AppImage release and:"
    warn "  sudo cp keyfinder-cli /usr/local/bin/keyfinder-cli"
    warn "  sudo chmod +x /usr/local/bin/keyfinder-cli"
    warn "Key detection will be skipped until keyfinder-cli is installed."
else
    ok "keyfinder-cli found"
fi

# ---------------------------------------------------------------------------
# 4. Python environment
# ---------------------------------------------------------------------------
if [[ ${USE_VENV} -eq 1 ]]; then
    log "Creating Python virtualenv at ${VENV_DIR} ..."
    python3 -m venv "${VENV_DIR}"
    PIP="${VENV_DIR}/bin/pip"
    ok "Virtualenv created"
else
    PIP="pip3"
fi

log "Installing Python packages ..."
"${PIP}" install --upgrade \
    mutagen \
    beets \
    2>/dev/null && ok "Python packages installed"

# ---------------------------------------------------------------------------
# 5. Beets config
# ---------------------------------------------------------------------------
BEETS_CONFIG_DIR="${HOME}/.config/beets"
BEETS_CONFIG_FILE="${BEETS_CONFIG_DIR}/config.yaml"

mkdir -p "${BEETS_CONFIG_DIR}"

if [[ -f "${BEETS_CONFIG_FILE}" ]]; then
    warn "Beets config already exists at ${BEETS_CONFIG_FILE}"
    warn "Backing up to ${BEETS_CONFIG_FILE}.bak"
    cp "${BEETS_CONFIG_FILE}" "${BEETS_CONFIG_FILE}.bak"
fi

# Substitute the actual music root into the config
sed "s|/music|${MUSIC_ROOT}|g" "${SCRIPT_DIR}/beets_config.yaml" > "${BEETS_CONFIG_FILE}"
ok "Beets config installed at ${BEETS_CONFIG_FILE}"

# ---------------------------------------------------------------------------
# 6. config_local.py — user overrides
# ---------------------------------------------------------------------------
if [[ ! -f "${SCRIPT_DIR}/config_local.py" ]]; then
    cat > "${SCRIPT_DIR}/config_local.py" << EOF
# Local config overrides — this file is git-ignored.
# Uncomment and change values as needed.

from pathlib import Path

# Root music directory
MUSIC_ROOT = Path("${MUSIC_ROOT}")

# Windows drive letter for Rekordbox XML paths
# Change this to match the drive letter your external drive always gets on Windows
# WINDOWS_DRIVE_LETTER = "E"

# Python virtualenv (leave empty if not using one)
# import os; os.environ["DJ_VENV"] = "${VENV_DIR}"
EOF
    ok "config_local.py created (git-ignored)"
fi

# ---------------------------------------------------------------------------
# 7. systemd user services
# ---------------------------------------------------------------------------
if [[ ${SKIP_SYSTEMD} -eq 0 ]]; then
    SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
    mkdir -p "${SYSTEMD_USER_DIR}"

    log "Installing systemd user services ..."

    for svc in djtoolkit.service djtoolkit.timer djtoolkit-watch.service; do
        # Substitute paths in service files
        sed "s|%h/code/apps/djtoolkit|${SCRIPT_DIR}|g; s|/music|${MUSIC_ROOT}|g" \
            "${SCRIPT_DIR}/systemd/${svc}" \
            > "${SYSTEMD_USER_DIR}/${svc}"
        ok "Installed ${svc}"
    done

    # daemon-reload requires an active D-Bus user session.
    # It fails with "No medium found" when run from a bare TTY or without
    # a running user session (e.g. fresh login, no desktop started yet).
    # We attempt it but don't abort on failure — the user can run it manually.
    if DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}" \
       systemctl --user daemon-reload 2>/dev/null; then
        ok "systemd daemon reloaded"
    else
        warn "systemctl --user daemon-reload failed (no D-Bus session)."
        warn "The service files are installed. Run this once from your desktop session:"
        warn "  systemctl --user daemon-reload"
    fi

    log ""
    log "To enable the timed pipeline (runs every 30 min):"
    log "  systemctl --user enable --now djtoolkit.timer"
    log ""
    log "To enable the inbox watcher (triggers on file drop):"
    log "  systemctl --user enable --now djtoolkit-watch.service"
    log ""
    log "To run the pipeline right now:"
    log "  systemctl --user start djtoolkit.service"
    log "  (or: ${SCRIPT_DIR}/pipeline.sh)"
fi

# ---------------------------------------------------------------------------
# 8. Make scripts executable
# ---------------------------------------------------------------------------
chmod +x \
    "${SCRIPT_DIR}/pipeline.sh" \
    "${SCRIPT_DIR}/scripts/watch_inbox.sh" \
    "${SCRIPT_DIR}/scripts/transfer.sh"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
log ""
log "=================================================="
log "Setup complete!"
log ""
log "Quick start:"
log "  1. Drop audio files into: ${MUSIC_ROOT}/inbox/"
log "  2. Run: ${SCRIPT_DIR}/pipeline.sh"
log "  3. Review: ${MUSIC_ROOT}/logs/reports/"
log "  4. Transfer: ${SCRIPT_DIR}/scripts/transfer.sh /mnt/djdrive"
log ""
log "Dry run test:"
log "  ${SCRIPT_DIR}/pipeline.sh --dry-run"
log "=================================================="
