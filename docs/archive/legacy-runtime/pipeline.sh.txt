#!/usr/bin/env bash
# =============================================================================
# CrateIQ — pipeline entry point
#
# Usage:
#   ./pipeline.sh [--dry-run] [--skip-beets] [--skip-analysis] [--verbose]
#
# Environment variables (override config.py defaults):
#   DJ_MUSIC_ROOT   — root music directory (default: /music)
#   DJ_WIN_DRIVE    — Windows drive letter for Rekordbox XML (default: E)
#   DJ_PYTHON       — Python interpreter to use (default: python3)
#   DJ_VENV         — path to virtualenv (activates it if set)
#
# Designed to be called by:
#   - systemd user timer (djtoolkit.timer)
#   - inotifywait watcher (watch_inbox.sh)
#   - manually from the terminal
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MUSIC_ROOT="${DJ_MUSIC_ROOT:-/music}"
LOG_DIR="${MUSIC_ROOT}/logs"
LOG_FILE="${LOG_DIR}/pipeline_shell.log"
LOCKFILE="${LOG_DIR}/.pipeline.lock"
PYTHON="${DJ_PYTHON:-python3}"
VENV="${DJ_VENV:-}"

# ---------------------------------------------------------------------------
# Colours (only when stdout is a terminal)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    RED='\033[0;31m'
    CYAN='\033[0;36m'
    RESET='\033[0m'
else
    GREEN='' YELLOW='' RED='' CYAN='' RESET=''
fi

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
log_info()  { echo -e "${GREEN}[INFO]${RESET}  $*" | tee -a "${LOG_FILE}"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*" | tee -a "${LOG_FILE}"; }
log_error() { echo -e "${RED}[ERROR]${RESET} $*" | tee -a "${LOG_FILE}"; }
log_step()  { echo -e "${CYAN}[STEP]${RESET}  $*" | tee -a "${LOG_FILE}"; }

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

# ---------------------------------------------------------------------------
# Parse arguments — pass unknown args through to pipeline.py
# ---------------------------------------------------------------------------
PYTHON_ARGS=()
for arg in "$@"; do
    PYTHON_ARGS+=("$arg")
done

# ---------------------------------------------------------------------------
# Lock file — prevent concurrent runs
# ---------------------------------------------------------------------------
acquire_lock() {
    if [[ -f "${LOCKFILE}" ]]; then
        local pid
        pid=$(cat "${LOCKFILE}" 2>/dev/null || echo "")
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log_warn "Pipeline already running (PID ${pid}) — exiting"
            exit 0
        else
            log_warn "Stale lockfile found — removing"
            rm -f "${LOCKFILE}"
        fi
    fi
    echo $$ > "${LOCKFILE}"
    trap 'rm -f "${LOCKFILE}"' EXIT INT TERM
}

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------
setup_env() {
    # Create log directory if missing
    mkdir -p "${LOG_DIR}"

    # Activate virtualenv if configured
    if [[ -n "${VENV}" && -f "${VENV}/bin/activate" ]]; then
        # shellcheck disable=SC1091
        source "${VENV}/bin/activate"
        log_info "Virtualenv: ${VENV}"
    fi

    # Verify Python is available
    if ! command -v "${PYTHON}" &>/dev/null; then
        log_error "Python not found at '${PYTHON}'. Set DJ_PYTHON env var."
        exit 2
    fi

    # Verify pipeline.py exists
    if [[ ! -f "${SCRIPT_DIR}/pipeline.py" ]]; then
        log_error "pipeline.py not found in ${SCRIPT_DIR}"
        exit 2
    fi
}

# ---------------------------------------------------------------------------
# Dependency check — warn on missing tools (don't abort, pipeline handles it)
# ---------------------------------------------------------------------------
check_deps() {
    local missing=()
    for tool in ffprobe rmlint aubiobpm keyfinder-cli beet; do
        if ! command -v "${tool}" &>/dev/null; then
            missing+=("${tool}")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_warn "Optional tools not found (pipeline will degrade gracefully): ${missing[*]}"
    fi

    # Python packages
    if ! "${PYTHON}" -c "import mutagen" 2>/dev/null; then
        log_warn "mutagen not installed. Run: pip install mutagen"
    fi
}

# ---------------------------------------------------------------------------
# Inbox check — skip run if inbox is empty (fast exit)
# ---------------------------------------------------------------------------
inbox_has_files() {
    local inbox="${MUSIC_ROOT}/inbox"
    if [[ ! -d "${inbox}" ]]; then
        return 1
    fi
    # Check for any audio file recursively
    find "${inbox}" \( \
        -name "*.mp3"  -o -name "*.flac" -o -name "*.wav" \
        -o -name "*.aiff" -o -name "*.aif" -o -name "*.m4a" \
        -o -name "*.ogg"  -o -name "*.opus" \
    \) -maxdepth 5 -print -quit 2>/dev/null | grep -q .
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log_info "========================================================"
    log_info "CrateIQ pipeline starting at $(ts)"
    log_info "MUSIC_ROOT: ${MUSIC_ROOT}"
    log_info "Script dir: ${SCRIPT_DIR}"
    [[ ${#PYTHON_ARGS[@]} -gt 0 ]] && log_info "Args: ${PYTHON_ARGS[*]}"
    log_info "========================================================"

    acquire_lock
    setup_env
    check_deps

    # Fast exit if inbox is empty (common for timed runs)
    if ! inbox_has_files; then
        log_info "Inbox is empty — nothing to do"
        exit 0
    fi

    log_step "Running pipeline.py ..."
    local start_time end_time elapsed
    start_time=$(date +%s)

    set +e  # don't abort on non-zero — let the Python exit code propagate
    DJ_MUSIC_ROOT="${MUSIC_ROOT}" \
    PYTHONPATH="${SCRIPT_DIR}" \
    "${PYTHON}" "${SCRIPT_DIR}/pipeline.py" "${PYTHON_ARGS[@]}"
    exit_code=$?
    set -e

    end_time=$(date +%s)
    elapsed=$(( end_time - start_time ))

    if [[ ${exit_code} -eq 0 ]]; then
        log_info "Pipeline completed successfully in ${elapsed}s"
    elif [[ ${exit_code} -eq 1 ]]; then
        log_warn "Pipeline completed with some errors in ${elapsed}s (exit 1)"
        log_warn "Check: ${LOG_DIR}/reports/"
    else
        log_error "Pipeline failed (exit ${exit_code}) after ${elapsed}s"
        log_error "Check: ${LOG_FILE}"
    fi

    log_info "Report directory: ${MUSIC_ROOT}/logs/reports/"
    log_info "========================================================"
    exit ${exit_code}
}

main
