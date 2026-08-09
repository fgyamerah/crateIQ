#!/usr/bin/env bash
# =============================================================================
# CrateIQ — transfer prepared library to external drive
#
# Copies:
#   /music/library/sorted/   → <DRIVE>/music/library/sorted/
#   /music/playlists/        → <DRIVE>/music/playlists/
#
# Uses rsync with --checksum for integrity verification.
# Only transfers changed/new files (subsequent runs are fast).
#
# Usage:
#   ./transfer.sh /mnt/djdrive          # transfer to mounted drive
#   ./transfer.sh /mnt/djdrive --dry-run
#
# The drive must be mounted before running this script.
# Recommended filesystem: exFAT (cross-platform, no permission issues)
# Format: sudo mkfs.exfat /dev/sdX1 -n DJMUSIC
# =============================================================================
set -euo pipefail

MUSIC_ROOT="${DJ_MUSIC_ROOT:-/music}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <drive_mount_point> [--dry-run]"
    echo "Example: $0 /mnt/djdrive"
    echo "Example: $0 /mnt/djdrive --dry-run"
    exit 1
fi

DRIVE_ROOT="$1"
DRY_RUN=0
RSYNC_DRYRUN_FLAG=""

shift
for arg in "$@"; do
    if [[ "${arg}" == "--dry-run" ]]; then
        DRY_RUN=1
        RSYNC_DRYRUN_FLAG="--dry-run"
    fi
done

# ---------------------------------------------------------------------------
# Verify drive is mounted
# ---------------------------------------------------------------------------
if [[ ! -d "${DRIVE_ROOT}" ]]; then
    echo "ERROR: Drive not mounted or path does not exist: ${DRIVE_ROOT}"
    exit 1
fi

# Test write access
if [[ ${DRY_RUN} -eq 0 ]] && ! touch "${DRIVE_ROOT}/.djtoolkit_test" 2>/dev/null; then
    echo "ERROR: Cannot write to ${DRIVE_ROOT} — is it mounted read-only?"
    exit 1
fi
rm -f "${DRIVE_ROOT}/.djtoolkit_test" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DEST_SORTED="${DRIVE_ROOT}/music/library/sorted"
DEST_PLAYLISTS="${DRIVE_ROOT}/music/playlists"
LOG_FILE="${MUSIC_ROOT}/logs/transfer_$(date +%Y%m%d_%H%M%S).log"
MUSIC_ROOT="${DJ_MUSIC_ROOT:-/music}"

mkdir -p "$(dirname "${LOG_FILE}")"

log()  { echo "[$(date -u '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }
sep()  { echo "$(printf '%.0s-' {1..70})" | tee -a "${LOG_FILE}"; }

# ---------------------------------------------------------------------------
# Transfer
# ---------------------------------------------------------------------------
log "========================================================"
log "CrateIQ Transfer"
log "Source : ${MUSIC_ROOT}"
log "Dest   : ${DRIVE_ROOT}"
log "Dry-run: $([ ${DRY_RUN} -eq 1 ] && echo YES || echo no)"
log "========================================================"

# rsync flags:
#   -a  archive (preserve timestamps, symlinks, etc.)
#   -v  verbose
#   -h  human-readable sizes
#   --checksum    verify by checksum, not mtime (reliable for FAT/exFAT)
#   --progress    show per-file progress
#   --delete      remove files on dest that no longer exist in source
#   --exclude     skip hidden files and temp files

RSYNC_COMMON=(
    rsync
    -avh
    --checksum
    --progress
    --delete
    --exclude=".DS_Store"
    --exclude="._*"
    --exclude="*.tmp"
    --exclude="Thumbs.db"
    --log-file="${LOG_FILE}"
    ${RSYNC_DRYRUN_FLAG}
)

sep
log "Syncing library: sorted/ ..."
"${RSYNC_COMMON[@]}" \
    "${MUSIC_ROOT}/library/sorted/" \
    "${DEST_SORTED}/"

sep
log "Syncing playlists/ ..."
"${RSYNC_COMMON[@]}" \
    "${MUSIC_ROOT}/playlists/" \
    "${DEST_PLAYLISTS}/"

sep
log "Transfer complete."
log ""
log "NEXT STEPS ON WINDOWS:"
log "  1. Open Rekordbox"
log "  2. File > Import Library"
log "  3. Select: ${DEST_PLAYLISTS}/xml/rekordbox_library.xml"
log "  4. Select all new tracks > right-click > Analyze"
log "  5. Set cue points as needed"
log "  6. File > Export to USB"
log "========================================================"

if [[ ${DRY_RUN} -eq 1 ]]; then
    log "(Dry-run: no files were actually transferred)"
fi
