#!/usr/bin/env bash
# CrateIQ local service helper.
#
# Run as a script:
#   scripts/crateiq-local-services.sh {start|stop|restart|status|logs|back-logs|front-logs}
#
# Or install shell functions (crate_start, crate_stop, ...):
#   source "$HOME/code/gewcc/crateIQ/scripts/crateiq-local-services.sh" --aliases
#
# Ports (LedgerIQ owns 5173/8000 — never touched here):
#   backend  127.0.0.1:8020
#   frontend 127.0.0.1:5175
#
# PID files and logs live under <repo>/.run/ (gitignored). No sudo needed.

if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    CRATEIQ_ROOT="${CRATEIQ_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    _CRATEIQ_SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
else
    CRATEIQ_ROOT="${CRATEIQ_ROOT:-$PWD}"
    _CRATEIQ_SCRIPT_PATH="$CRATEIQ_ROOT/scripts/crateiq-local-services.sh"
fi

CRATEIQ_BACKEND_PORT="${CRATEIQ_BACKEND_PORT:-8020}"
CRATEIQ_FRONTEND_PORT="${CRATEIQ_FRONTEND_PORT:-5175}"
CRATEIQ_BIND="127.0.0.1"
CRATEIQ_RUN_DIR="$CRATEIQ_ROOT/.run"
CRATEIQ_BACKEND_PID_FILE="$CRATEIQ_RUN_DIR/backend.pid"
CRATEIQ_SUPERVISOR_SOCKET="$CRATEIQ_RUN_DIR/local/crateiq-supervisor.sock"
CRATEIQ_SUPERVISOR_LOCK="$CRATEIQ_RUN_DIR/local/crateiq-supervisor.lock"
CRATEIQ_ACTIVATION_STATE="$CRATEIQ_RUN_DIR/local/library_activation_state.json"
CRATEIQ_ACTIVATION_LOCK="$CRATEIQ_RUN_DIR/local/library_activation.lock"
CRATEIQ_FRONTEND_PID_FILE="$CRATEIQ_RUN_DIR/frontend.pid"
CRATEIQ_BACKEND_LOG="$CRATEIQ_RUN_DIR/backend.log"
CRATEIQ_FRONTEND_LOG="$CRATEIQ_RUN_DIR/frontend.log"
CRATEIQ_BACKEND_URL="http://${CRATEIQ_BIND}:${CRATEIQ_BACKEND_PORT}"
CRATEIQ_FRONTEND_URL="http://${CRATEIQ_BIND}:${CRATEIQ_FRONTEND_PORT}"
CRATEIQ_HEALTH_URL="${CRATEIQ_BACKEND_URL}/api/health"
CRATEIQ_READINESS_URL="${CRATEIQ_BACKEND_URL}/api/runtime/readiness"
CRATEIQ_LOCAL_ENV_FILE="$CRATEIQ_RUN_DIR/local/crateiq.env"

# Read only the managed, non-secret root setting. Do not source this file:
# Settings must never turn a local configuration file into executable shell.
# A Settings-managed root is authoritative for normal configured-library
# startup, so a stale inherited CRATEIQ_LIBRARY_ROOT cannot preserve a
# restart-required state. Environment roots remain a fallback when Settings
# has not saved a root yet. Keep the inherited value separate: configured
# starts replace CRATEIQ_LIBRARY_ROOT with their active root.
if [[ -z "${_CRATEIQ_INHERITED_LIBRARY_ROOT+x}" ]]; then
    _CRATEIQ_INHERITED_LIBRARY_ROOT="${CRATEIQ_LIBRARY_ROOT:-}"
fi

_crateiq_load_local_library_root() {
    local line configured_root=""
    [[ -f "$CRATEIQ_LOCAL_ENV_FILE" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" == CRATEIQ_LIBRARY_ROOT=* ]] || continue
        configured_root="${line#CRATEIQ_LIBRARY_ROOT=}"
    done < "$CRATEIQ_LOCAL_ENV_FILE"
    [[ -n "$configured_root" ]] && printf '%s' "$configured_root"
}

_crateiq_refresh_library_root() {
    local configured_root
    configured_root="$(_crateiq_load_local_library_root)"
    CRATEIQ_LIBRARY_ROOT="${configured_root:-$_CRATEIQ_INHERITED_LIBRARY_ROOT}"
}

_crateiq_pid_from_file() {
    local pid_file="$1" pid
    [[ -f "$pid_file" ]] || return 1
    pid="$(tr -d '[:space:]' < "$pid_file" 2>/dev/null || true)"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    echo "$pid"
}

_crateiq_pid_command() {
    ps -p "$1" -o args= 2>/dev/null || true
}

_crateiq_process_running() {
    [[ "$1" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$1" >/dev/null 2>&1
}

# A PID counts as CrateIQ-owned only if its command line matches the expected
# service AND it is anchored to this repo (command path or /proc cwd).
_crateiq_pid_in_repo() {
    local pid="$1" cwd
    if [[ "$(_crateiq_pid_command "$pid")" == *"$CRATEIQ_ROOT"* ]]; then
        return 0
    fi
    cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
    [[ -n "$cwd" && "$cwd" == "$CRATEIQ_ROOT"* ]]
}

_crateiq_pid_is_backend() {
    local cmd
    cmd="$(_crateiq_pid_command "$1")"
    [[ "$cmd" == *"backend.app.supervisor"* ]] \
        && _crateiq_pid_in_repo "$1"
}

_crateiq_pid_is_frontend() {
    local cmd
    cmd="$(_crateiq_pid_command "$1")"
    [[ "$cmd" == *"vite"* || "$cmd" == *"npm run dev"* ]] \
        && _crateiq_pid_in_repo "$1"
}

_crateiq_port_pids() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u
        return 0
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp "sport = :$port" 2>/dev/null \
            | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u
        return 0
    fi
    return 2
}

_crateiq_port_is_free() {
    local pids
    pids="$(_crateiq_port_pids "$1")" || return 2
    [[ -z "$pids" ]]
}

_crateiq_port_listening() {
    local pids
    pids="$(_crateiq_port_pids "$1")" || { echo "UNKNOWN"; return 0; }
    [[ -n "$pids" ]] && echo "LISTENING" || echo "NOT LISTENING"
}

_crateiq_check_start_requirements() {
    if [[ ! -x "$CRATEIQ_ROOT/.venv/bin/python" ]]; then
        echo "CrateIQ: missing Python venv at $CRATEIQ_ROOT/.venv" >&2
        echo "Set it up first:" >&2
        echo "  cd $CRATEIQ_ROOT && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
        return 1
    fi
    if [[ ! -d "$CRATEIQ_ROOT/frontend/node_modules" ]]; then
        echo "CrateIQ: frontend/node_modules missing." >&2
        echo "Install first:  npm --prefix $CRATEIQ_ROOT/frontend install" >&2
        return 1
    fi
    if ! _crateiq_port_is_free "$CRATEIQ_BACKEND_PORT"; then
        echo "CrateIQ: backend port $CRATEIQ_BACKEND_PORT is already in use. Run crate_status." >&2
        return 1
    fi
    if ! _crateiq_port_is_free "$CRATEIQ_FRONTEND_PORT"; then
        echo "CrateIQ: frontend port $CRATEIQ_FRONTEND_PORT is already in use. Run crate_status." >&2
        return 1
    fi
}

_crateiq_stop_service() {
    local label="$1" pid_file="$2" port="$3" verify_fn="$4"
    local pid stopped=0 attempt

    pid="$(_crateiq_pid_from_file "$pid_file" || true)"
    if [[ -n "$pid" ]]; then
        if ! _crateiq_process_running "$pid"; then
            rm -f "$pid_file"
            echo "CrateIQ ${label}: stale PID file removed."
        elif ! "$verify_fn" "$pid"; then
            echo "CrateIQ ${label}: refusing to stop PID $pid — not a CrateIQ ${label} process." >&2
        else
            kill "$pid" >/dev/null 2>&1 || true
            for attempt in 1 2 3 4 5 6; do
                _crateiq_process_running "$pid" || { stopped=1; break; }
                sleep 1
            done
            if [[ "$stopped" -eq 0 ]] && "$verify_fn" "$pid"; then
                kill -KILL "$pid" >/dev/null 2>&1 || true
            fi
            rm -f "$pid_file"
        fi
    fi

    # Fallback: only our assigned port, only verified CrateIQ processes.
    local leftover
    while read -r leftover; do
        [[ -n "$leftover" ]] || continue
        if "$verify_fn" "$leftover"; then
            kill "$leftover" >/dev/null 2>&1 || true
        else
            echo "CrateIQ ${label}: port $port held by non-CrateIQ PID $leftover — left untouched." >&2
        fi
    done < <(_crateiq_port_pids "$port" || true)

    if _crateiq_port_is_free "$port"; then
        echo "CrateIQ ${label} stopped (port $port free)."
    else
        echo "CrateIQ ${label}: port $port still occupied. Inspect with crate_status." >&2
        return 1
    fi
}

_crateiq_stop() {
    _crateiq_stop_service frontend "$CRATEIQ_FRONTEND_PID_FILE" "$CRATEIQ_FRONTEND_PORT" _crateiq_pid_is_frontend
    _crateiq_stop_service backend  "$CRATEIQ_BACKEND_PID_FILE"  "$CRATEIQ_BACKEND_PORT"  _crateiq_pid_is_backend
}

_crateiq_http_code() {
    command -v curl >/dev/null 2>&1 || { echo "curl-missing"; return 0; }
    local code
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 "$1" 2>/dev/null)" || true
    echo "${code:-000}"
}

_crateiq_status() {
    local status_mode="${1:-}"
    local backend_pid frontend_pid backend_short="stopped" frontend_short="stopped"
    backend_pid="$(_crateiq_pid_from_file "$CRATEIQ_BACKEND_PID_FILE" || true)"
    frontend_pid="$(_crateiq_pid_from_file "$CRATEIQ_FRONTEND_PID_FILE" || true)"

    local backend_proc="not running" frontend_proc="not running"
    if [[ -n "$backend_pid" ]] && _crateiq_process_running "$backend_pid"; then
        backend_proc="running (PID $backend_pid)"
        backend_short="$backend_pid"
    fi
    if [[ -n "$frontend_pid" ]] && _crateiq_process_running "$frontend_pid"; then
        frontend_proc="running (PID $frontend_pid)"
        frontend_short="$frontend_pid"
    fi

    if [[ "$status_mode" == "--short" ]]; then
    echo "CrateIQ supervisor=$backend_short frontend=$frontend_short ports=8020:$(_crateiq_port_listening "$CRATEIQ_BACKEND_PORT") 5175:$(_crateiq_port_listening "$CRATEIQ_FRONTEND_PORT")"
        return 0
    fi
    echo "CrateIQ status"
    echo "--------------"
    echo "Repository: $CRATEIQ_ROOT"
    echo
    echo "Supervisor (owns backend child)"
    echo "  Process:  $backend_proc"
    echo "  Port ${CRATEIQ_BACKEND_PORT}: $(_crateiq_port_listening "$CRATEIQ_BACKEND_PORT")"
    echo "  URL:      $CRATEIQ_BACKEND_URL"
    echo "  Health:   $CRATEIQ_HEALTH_URL (HTTP $(_crateiq_http_code "$CRATEIQ_HEALTH_URL"))"
    echo "  Readiness: $CRATEIQ_READINESS_URL (HTTP $(_crateiq_http_code "$CRATEIQ_READINESS_URL"))"
    echo "  Log:      $CRATEIQ_BACKEND_LOG"
    echo
    echo "Frontend"
    echo "  Process:  $frontend_proc"
    echo "  Port ${CRATEIQ_FRONTEND_PORT}: $(_crateiq_port_listening "$CRATEIQ_FRONTEND_PORT")"
    echo "  URL:      $CRATEIQ_FRONTEND_URL"
    echo "  Log:      $CRATEIQ_FRONTEND_LOG"
}

_crateiq_logs() {
    local files=()
    [[ -f "$CRATEIQ_BACKEND_LOG" ]] && files+=("$CRATEIQ_BACKEND_LOG")
    [[ -f "$CRATEIQ_FRONTEND_LOG" ]] && files+=("$CRATEIQ_FRONTEND_LOG")
    if [[ "${#files[@]}" -eq 0 ]]; then
        echo "CrateIQ: no log files yet under $CRATEIQ_RUN_DIR. Start the app first." >&2
        return 1
    fi
    tail -n 40 -f "${files[@]}"
}

_crateiq_tail_one() {
    local file="$1"
    if [[ ! -f "$file" ]]; then
        echo "CrateIQ: log file not found: $file" >&2
        return 1
    fi
    tail -n 60 -f "$file"
}

_crateiq_usage() {
    cat <<EOF
Usage: crateiq-local-services.sh {start|start-demo-local|start-demo-lan|start-library-local|start-library-lan|start-launcher-local|start-launcher-lan|recover-launcher|stop|restart|status|logs|back-logs|front-logs}

  start       interactively select the Library Launcher (default), demo, or
              legacy configured-library mode, then LAN/local access
  start-demo-local / start-demo-lan
              start the safe demo library with local-only or LAN access
  start-library-local / start-library-lan
              start CRATEIQ_LIBRARY_ROOT with local-only or LAN access
  start-launcher-local / start-launcher-lan
              start the rootless launcher bootstrap (no library is selected)
  recover-launcher
              explicitly recover a provably stale fail-closed activation;
              never starts, stops, or activates a library
  stop        stop CrateIQ services only (never LedgerIQ on 5173/8000)
  restart     stop then interactively select/start a profile
  status [--short]
              process/port/URL/log overview, or a compact status line
  logs        tail backend + frontend logs
  back-logs   tail backend log only
  front-logs  tail frontend log only

Install shell functions (crate_start, crate_stop, ...):
  source "$_CRATEIQ_SCRIPT_PATH" --aliases
EOF
}

# Database profiles are intentionally library-root profiles, not arbitrary SQLite
# files: the backend owns jobs.db and reads the selected processed.db read-only.
_crateiq_profile() {
    case "$1" in
        demo) CRATEIQ_DB_LABEL="Demo library"; CRATEIQ_LIBRARY_ROOT="$CRATEIQ_ROOT/.run/demo-library" ;;
        library)
            CRATEIQ_DB_LABEL="Configured library"
            _crateiq_refresh_library_root
            CRATEIQ_LIBRARY_ROOT="${CRATEIQ_LIBRARY_ROOT:-${DJ_MUSIC_ROOT:-}}"
            [[ -n "$CRATEIQ_LIBRARY_ROOT" ]] || { echo "CrateIQ: set CRATEIQ_LIBRARY_ROOT for the library profile." >&2; return 1; }
            ;;
        launcher)
            CRATEIQ_DB_LABEL="Rootless launcher bootstrap"
            CRATEIQ_LIBRARY_ROOT=""
            CRATEIQ_DB_PATH=""
            return 0
            ;;
        *) echo "CrateIQ: unknown database profile: $1" >&2; return 1 ;;
    esac
    CRATEIQ_LIBRARY_ROOT="$(realpath -m "$CRATEIQ_LIBRARY_ROOT")"
    CRATEIQ_DB_PATH="$CRATEIQ_LIBRARY_ROOT/logs/processed.db"
}

_crateiq_detect_lan_ip() {
    local route candidate dev
    route="$(ip -4 route get 1.1.1.1 2>/dev/null || true)"
    candidate="$(awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1);exit}}' <<< "$route")"
    dev="$(awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1);exit}}' <<< "$route")"
    if [[ "$candidate" =~ ^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.) ]] && [[ ! "$dev" =~ ^(docker|br-|veth|virbr|tun|tap|wg) ]]; then echo "$candidate"; return; fi
    ip -o -4 addr show scope global 2>/dev/null | awk '{split($4,a,"/"); if ($2 !~ /^(docker|br-|veth|virbr|tun|tap|wg)/ && a[1] ~ /^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)/) {print a[1]; exit}}'
}

_crateiq_start_profile() {
    local profile="$1" mode="$2" host
    _crateiq_profile "$profile" || return 1
    [[ "$profile" == "launcher" || -f "$CRATEIQ_DB_PATH" ]] || {
        echo "CrateIQ: configured library is not initialized." >&2
        echo "Open Settings and click Initialize Library, then restart CrateIQ." >&2
        echo "Expected local index: $CRATEIQ_DB_PATH" >&2
        return 1
    }
    case "$mode" in
        local) CRATEIQ_BIND=127.0.0.1; host=127.0.0.1; CRATEIQ_ACCESS_LABEL="Local only" ;;
        lan) CRATEIQ_BIND=0.0.0.0; host="$(_crateiq_detect_lan_ip)"; CRATEIQ_ACCESS_LABEL="LAN" ;;
        *) return 1 ;;
    esac
    CRATEIQ_BACKEND_URL="http://${host:-0.0.0.0}:$CRATEIQ_BACKEND_PORT"
    CRATEIQ_FRONTEND_URL="http://${host:-0.0.0.0}:$CRATEIQ_FRONTEND_PORT"
    _crateiq_check_start_requirements || return 1
    if [[ "$profile" == "launcher" ]]; then
        echo "CrateIQ: $CRATEIQ_DB_LABEL (no library selected)"
    else
        echo "CrateIQ: database $CRATEIQ_DB_LABEL ($CRATEIQ_DB_PATH)"
    fi
    echo "Access: $CRATEIQ_ACCESS_LABEL"
    [[ -n "$host" || "$mode" != lan ]] || echo "LAN address not detected; listening on all interfaces. Run: ip -4 addr show scope global"
    [[ "${CRATEIQ_DRY_RUN:-0}" == 1 ]] && { echo "Dry run: backend $CRATEIQ_BIND:8020; frontend $CRATEIQ_BIND:5175"; return 0; }
    mkdir -p "$CRATEIQ_RUN_DIR"
    (
        cd "$CRATEIQ_ROOT" || exit 1
        local supervisor_args=(
            -m backend.app.supervisor
            --socket "$CRATEIQ_SUPERVISOR_SOCKET"
            --state "$CRATEIQ_ACTIVATION_STATE"
            --lock "$CRATEIQ_ACTIVATION_LOCK"
            --port "$CRATEIQ_BACKEND_PORT"
            --bind-host "$CRATEIQ_BIND"
            --access-mode "$mode"
            --cors-origins "http://127.0.0.1:5175,http://localhost:5175${host:+,http://$host:5175}"
        )
        if [[ "$profile" == "launcher" ]]; then
            nohup env -u CRATEIQ_LIBRARY_ROOT -u CRATEMINDAI_LIBRARY_ROOT -u DJ_MUSIC_ROOT .venv/bin/python "${supervisor_args[@]}" --active-role rootless > "$CRATEIQ_BACKEND_LOG" 2>&1 &
        else
            nohup env -u CRATEMINDAI_LIBRARY_ROOT .venv/bin/python "${supervisor_args[@]}" --active-role active --library-root "$CRATEIQ_LIBRARY_ROOT" > "$CRATEIQ_BACKEND_LOG" 2>&1 &
        fi
        echo $! > "$CRATEIQ_BACKEND_PID_FILE"
    )
    (
        cd "$CRATEIQ_ROOT/frontend" || exit 1
        nohup env CRATEIQ_API_PROXY_TARGET="http://127.0.0.1:$CRATEIQ_BACKEND_PORT" npm run dev -- --host "$CRATEIQ_BIND" --port "$CRATEIQ_FRONTEND_PORT" --strictPort > "$CRATEIQ_FRONTEND_LOG" 2>&1 &
        echo $! > "$CRATEIQ_FRONTEND_PID_FILE"
    ) || { _crateiq_stop; return 1; }
    local attempt
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        curl -fsS --max-time 2 "http://127.0.0.1:$CRATEIQ_BACKEND_PORT/api/health" >/dev/null && break
        sleep 1
    done
    curl -fsS --max-time 2 "http://127.0.0.1:$CRATEIQ_BACKEND_PORT/api/health" >/dev/null || { echo "CrateIQ backend health check failed; see $CRATEIQ_BACKEND_LOG" >&2; _crateiq_stop; return 1; }
    printf 'CRATEIQ_DB_LABEL=%q\nCRATEIQ_DB_PATH=%q\nCRATEIQ_ACCESS_LABEL=%q\nCRATEIQ_BACKEND_URL=%q\nCRATEIQ_FRONTEND_URL=%q\n' "$CRATEIQ_DB_LABEL" "$CRATEIQ_DB_PATH" "$CRATEIQ_ACCESS_LABEL" "$CRATEIQ_BACKEND_URL" "$CRATEIQ_FRONTEND_URL" > "$CRATEIQ_RUN_DIR/runtime.env"
    _crateiq_status --short
}

_crateiq_interactive_start() {
    local profile access
    while :; do
        printf 'Select startup mode:\n  1) Library Launcher (recommended)\n  2) Demo library\n  3) Legacy configured library (CRATEIQ_LIBRARY_ROOT)\n  4) Cancel\n'
        read -r -p 'Choice [1]: ' profile
        case "${profile:-1}" in
            1) profile=launcher; break ;;
            2) profile=demo; break ;;
            3) profile=library; break ;;
            4) echo Cancelled.; return 0 ;;
            *) echo 'Invalid selection.' ;;
        esac
    done
    while :; do
        printf 'Select access mode:\n  1) LAN - accessible from other devices on this network\n  2) Local only - accessible only on this computer\n  3) Cancel\n'
        read -r -p 'Choice [1]: ' access
        case "${access:-1}" in
            1) access=lan; break ;;
            2) access=local; break ;;
            3) echo Cancelled.; return 0 ;;
            *) echo 'Invalid selection.' ;;
        esac
    done
    _crateiq_start_profile "$profile" "$access"
}

_crateiq_recover_launcher() {
    if [[ ! -x "$CRATEIQ_ROOT/.venv/bin/python" ]]; then
        echo "CrateIQ: missing Python venv at $CRATEIQ_ROOT/.venv" >&2
        return 1
    fi
    (
        cd "$CRATEIQ_ROOT" || exit 1
        .venv/bin/python -m backend.app.launcher_recovery \
            --repo-root "$CRATEIQ_ROOT" \
            --socket "$CRATEIQ_SUPERVISOR_SOCKET" \
            --state "$CRATEIQ_ACTIVATION_STATE" \
            --activation-lock "$CRATEIQ_ACTIVATION_LOCK" \
            --supervisor-lock "$CRATEIQ_SUPERVISOR_LOCK" \
            --registry "$CRATEIQ_RUN_DIR/local/library_registry.json" \
            --local-env "$CRATEIQ_LOCAL_ENV_FILE" \
            --backend-port "$CRATEIQ_BACKEND_PORT"
    )
}

_crateiq_dispatch() {
    case "${1:-}" in
        start)      _crateiq_interactive_start ;;
        start-demo-local) _crateiq_start_profile demo local ;;
        start-demo-lan) _crateiq_start_profile demo lan ;;
        start-library-local) _crateiq_start_profile library local ;;
        start-library-lan) _crateiq_start_profile library lan ;;
        start-launcher-local) _crateiq_start_profile launcher local ;;
        start-launcher-lan) _crateiq_start_profile launcher lan ;;
        recover-launcher) _crateiq_recover_launcher ;;
        stop)       _crateiq_stop ;;
        restart)    _crateiq_stop; _crateiq_interactive_start && _crateiq_status ;;
        status)     _crateiq_status "${2:-}" ;;
        logs)       _crateiq_logs ;;
        back-logs)  _crateiq_tail_one "$CRATEIQ_BACKEND_LOG" ;;
        front-logs) _crateiq_tail_one "$CRATEIQ_FRONTEND_LOG" ;;
        ""|help|-h|--help) _crateiq_usage ;;
        *) echo "CrateIQ: unknown subcommand: $1" >&2; _crateiq_usage >&2; return 1 ;;
    esac
}

if [[ "${BASH_SOURCE[0]:-}" != "$0" ]]; then
    # Sourced: define the crate_* shell functions.
    crateiq_start()    { _crateiq_dispatch start "$@"; }
    crateiq_start_demo_local() { _crateiq_dispatch start-demo-local; }
    crateiq_start_demo_lan() { _crateiq_dispatch start-demo-lan; }
    crateiq_start_library_local() { _crateiq_dispatch start-library-local; }
    crateiq_start_library_lan() { _crateiq_dispatch start-library-lan; }
    crateiq_start_launcher_local() { _crateiq_dispatch start-launcher-local; }
    crateiq_start_launcher_lan() { _crateiq_dispatch start-launcher-lan; }
    crateiq_recover_launcher() { _crateiq_dispatch recover-launcher; }
    crateiq_stop() { _crateiq_dispatch stop; }
    crateiq_stop_backend() { _crateiq_stop_service backend "$CRATEIQ_BACKEND_PID_FILE" "$CRATEIQ_BACKEND_PORT" _crateiq_pid_is_backend; }
    crateiq_stop_frontend() { _crateiq_stop_service frontend "$CRATEIQ_FRONTEND_PID_FILE" "$CRATEIQ_FRONTEND_PORT" _crateiq_pid_is_frontend; }
    crateiq_status() { _crateiq_dispatch status "$@"; }
    crateiq_logs() { _crateiq_dispatch logs; }
    crateiq_logs_backend() { _crateiq_dispatch back-logs; }
    crateiq_logs_frontend() { _crateiq_dispatch front-logs; }
    crate_start()      { _crateiq_dispatch start "$@"; }
    crate_stop()       { _crateiq_dispatch stop "$@"; }
    crate_restart()    { _crateiq_dispatch restart "$@"; }
    crate_status()     { _crateiq_dispatch status "$@"; }
    crate_logs()       { _crateiq_dispatch logs "$@"; }
    crate_back_logs()  { _crateiq_dispatch back-logs "$@"; }
    crate_front_logs() { _crateiq_dispatch front-logs "$@"; }
    if [[ "${1:-}" == "--aliases" ]]; then
        echo "CrateIQ shell functions installed: crateiq_start crateiq_start_demo_local crateiq_start_demo_lan crateiq_start_library_local crateiq_start_library_lan crateiq_start_launcher_local crateiq_start_launcher_lan crateiq_recover_launcher crateiq_stop crateiq_status crateiq_logs crateiq_logs_backend crateiq_logs_frontend crate_start crate_stop crate_restart crate_status crate_logs crate_back_logs crate_front_logs"
    fi
else
    _crateiq_dispatch "$@"
fi
