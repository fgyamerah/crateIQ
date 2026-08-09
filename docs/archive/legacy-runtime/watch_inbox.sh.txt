#!/usr/bin/env bash
# =============================================================================
# CrateIQ — inbox folder watcher
#
# Watches /music/inbox/ using inotifywait.
# When new audio files are dropped, waits briefly (for multi-file drops)
# then triggers the pipeline.
#
# Designed to run as a systemd user service (djtoolkit-watch.service).
# Can also be run manually in a terminal for testing.
#
# Requires: inotify-tools (sudo apt install inotify-tools)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MUSIC_ROOT="${DJ_MUSIC_ROOT:-/music}"
INBOX="${MUSIC_ROOT}/inbox"
LOG_FILE="${MUSIC_ROOT}/logs/watcher.log"
SETTLE_SECONDS=15   # wait this long after last file event before triggering
PIPELINE="${SCRIPT_DIR}/pipeline.sh"

log() { echo "[$(date -u '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }

# Ensure inotifywait is available
if ! command -v inotifywait &>/dev/null; then
    echo "ERROR: inotifywait not found. Install: sudo apt install inotify-tools"
    exit 2
fi

mkdir -p "${MUSIC_ROOT}/logs"
mkdir -p "${INBOX}"
log "Watcher started. Monitoring: ${INBOX}"

# Audio file extensions to watch
AUDIO_EXTS="mp3,flac,wav,aiff,aif,m4a,ogg,opus,MP3,FLAC,WAV,AIFF,AIF,M4A,OGG,OPUS"

# inotifywait event loop
inotifywait \
    --monitor \
    --recursive \
    --event close_write \
    --event moved_to \
    --format '%w%f' \
    --include "\\.(${AUDIO_EXTS//,/|})$" \
    "${INBOX}" 2>>"${LOG_FILE}" | \
while read -r filepath; do
    log "File detected: $(basename "${filepath}")"

    # Settle: keep resetting a timer on each new file event.
    # This prevents triggering mid-copy for large batches.
    while true; do
        # Wait for SETTLE_SECONDS with a 1-second polling loop
        remaining=${SETTLE_SECONDS}
        new_file=0
        while [[ ${remaining} -gt 0 ]]; do
            sleep 1
            remaining=$(( remaining - 1 ))
            # Check if another file arrived while we were waiting
            if read -r -t 0 _; then
                read -r new_filepath
                log "Additional file: $(basename "${new_filepath}")"
                remaining=${SETTLE_SECONDS}   # reset timer
            fi
        done
        break
    done

    log "Settle complete — triggering pipeline"
    if [[ -x "${PIPELINE}" ]]; then
        "${PIPELINE}" >> "${LOG_FILE}" 2>&1 &
    else
        log "ERROR: pipeline.sh not found or not executable at ${PIPELINE}"
    fi
done
