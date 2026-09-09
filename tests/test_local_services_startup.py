"""Regression coverage for local startup profiles and root precedence."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = REPO_ROOT / "scripts" / "crateiq-local-services.sh"


def test_supervisor_managed_backend_script_contract_is_non_reload_and_keeps_ports():
    text = SERVICE_SCRIPT.read_text(encoding="utf-8")
    assert "backend.app.supervisor" in text
    assert "--active-role rootless" in text
    assert "--active-role active" in text
    assert "--reload" not in text
    assert 'CRATEIQ_BACKEND_PORT="${CRATEIQ_BACKEND_PORT:-8020}"' in text
    assert 'CRATEIQ_FRONTEND_PORT="${CRATEIQ_FRONTEND_PORT:-5175}"' in text


def _service_root(tmp_path: Path, saved_root: Path | None) -> Path:
    root = tmp_path / "service-root"
    (root / ".run" / "local").mkdir(parents=True)
    if saved_root is not None:
        (root / ".run" / "local" / "crateiq.env").write_text(
            "# Managed by CrateIQ Settings. This file is local-only and contains no secrets.\n"
            f"CRATEIQ_LIBRARY_ROOT={saved_root}\n",
            encoding="utf-8",
        )
    (root / "frontend" / "node_modules").mkdir(parents=True)
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    python.chmod(0o755)
    return root


def _library_root(root: Path, name: str) -> Path:
    library = root / name
    (library / "logs").mkdir(parents=True)
    (library / "logs" / "processed.db").touch()
    return library


def _dry_run(
    root: Path,
    command: str,
    backend_port: int,
    frontend_port: int,
    inherited_root: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CRATEIQ_ROOT"] = str(root)
    env["CRATEIQ_DRY_RUN"] = "1"
    env["CRATEIQ_BACKEND_PORT"] = str(backend_port)
    env["CRATEIQ_FRONTEND_PORT"] = str(frontend_port)
    if inherited_root is None:
        env.pop("CRATEIQ_LIBRARY_ROOT", None)
    else:
        env["CRATEIQ_LIBRARY_ROOT"] = str(inherited_root)
    return subprocess.run(
        ["bash", str(SERVICE_SCRIPT), command],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        input=input_text,
    )


def _sourced_dry_run(
    root: Path,
    function: str,
    backend_port: int,
    frontend_port: int,
    inherited_root: Path | None = None,
    saved_env: str | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CRATEIQ_ROOT"] = str(root)
    env["CRATEIQ_DRY_RUN"] = "1"
    env["CRATEIQ_BACKEND_PORT"] = str(backend_port)
    env["CRATEIQ_FRONTEND_PORT"] = str(frontend_port)
    if inherited_root is None:
        env.pop("CRATEIQ_LIBRARY_ROOT", None)
    else:
        env["CRATEIQ_LIBRARY_ROOT"] = str(inherited_root)
    script = """
source "$1" --aliases
if [[ "$2" != "__UNCHANGED__" ]]; then
    printf '%s\\n' "$2" > "$CRATEIQ_ROOT/.run/local/crateiq.env"
fi
"$3"
"""
    return subprocess.run(
        ["bash", "-c", script, "bash", str(SERVICE_SCRIPT), saved_env or "__UNCHANGED__", function],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        input=input_text,
    )


def test_saved_root_wins_over_stale_inherited_root_for_configured_local_start(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    saved_root = _library_root(tmp_path, "saved-library")
    stale_root = _library_root(tmp_path, "stale-library")
    service_root = _service_root(tmp_path, saved_root)

    result = _dry_run(service_root, "start-library-local", free_tcp_port, free_tcp_port_factory(), stale_root)

    assert result.returncode == 0, result.stderr
    assert f"Configured library ({saved_root / 'logs' / 'processed.db'})" in result.stdout
    assert str(stale_root) not in result.stdout


def test_saved_root_is_used_when_no_root_is_inherited(tmp_path, free_tcp_port, free_tcp_port_factory):
    saved_root = _library_root(tmp_path, "saved-library")
    service_root = _service_root(tmp_path, saved_root)

    result = _dry_run(service_root, "start-library-local", free_tcp_port, free_tcp_port_factory())

    assert result.returncode == 0, result.stderr
    assert f"Configured library ({saved_root / 'logs' / 'processed.db'})" in result.stdout


def test_interactive_start_uses_saved_root_over_stale_inherited_root(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    saved_root = _library_root(tmp_path, "saved-library")
    stale_root = _library_root(tmp_path, "stale-library")
    service_root = _service_root(tmp_path, saved_root)

    result = _dry_run(
        service_root,
        "start",
        free_tcp_port,
        free_tcp_port_factory(),
        stale_root,
        input_text="3\n2\n",
    )

    assert result.returncode == 0, result.stderr
    assert f"Configured library ({saved_root / 'logs' / 'processed.db'})" in result.stdout


def test_demo_start_ignores_saved_and_inherited_configured_roots(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    saved_root = _library_root(tmp_path, "saved-library")
    stale_root = _library_root(tmp_path, "stale-library")
    service_root = _service_root(tmp_path, saved_root)
    _library_root(service_root / ".run", "demo-library")

    result = _dry_run(service_root, "start-demo-local", free_tcp_port, free_tcp_port_factory(), stale_root)

    assert result.returncode == 0, result.stderr
    demo_db = service_root / ".run" / "demo-library" / "logs" / "processed.db"
    assert f"Demo library ({demo_db})" in result.stdout
    assert str(saved_root) not in result.stdout
    assert str(stale_root) not in result.stdout


@pytest.mark.parametrize("command", ["start-library-local", "start-library-lan"])
def test_configured_local_and_lan_start_use_saved_root(
    tmp_path, command, free_tcp_port, free_tcp_port_factory,
):
    saved_root = _library_root(tmp_path, "saved-library")
    stale_root = _library_root(tmp_path, "stale-library")
    service_root = _service_root(tmp_path, saved_root)

    result = _dry_run(service_root, command, free_tcp_port, free_tcp_port_factory(), stale_root)

    assert result.returncode == 0, result.stderr
    assert f"Configured library ({saved_root / 'logs' / 'processed.db'})" in result.stdout


@pytest.mark.parametrize(
    ("function", "input_text"),
    [
        ("crateiq_start_library_local", None),
        ("crateiq_start_library_lan", None),
        ("crateiq_start", "3\n2\n"),
        ("crate_restart", "3\n2\n"),
    ],
)
def test_sourced_functions_refresh_root_after_settings_changes(
    tmp_path, function, input_text, free_tcp_port, free_tcp_port_factory,
):
    initial_root = _library_root(tmp_path, "initial-library")
    saved_root = _library_root(tmp_path, "newly-saved-library")
    stale_root = _library_root(tmp_path, "stale-inherited-library")
    service_root = _service_root(tmp_path, initial_root)

    result = _sourced_dry_run(
        service_root,
        function,
        free_tcp_port,
        free_tcp_port_factory(),
        stale_root,
        f"CRATEIQ_LIBRARY_ROOT={saved_root}",
        input_text,
    )

    assert result.returncode == 0, result.stderr
    assert f"Configured library ({saved_root / 'logs' / 'processed.db'})" in result.stdout
    assert str(initial_root) not in result.stdout
    assert str(stale_root) not in result.stdout


def test_sourced_function_uses_inherited_root_when_settings_file_is_missing(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    inherited_root = _library_root(tmp_path, "inherited-library")
    service_root = _service_root(tmp_path, None)

    result = _sourced_dry_run(
        service_root, "crateiq_start_library_local", free_tcp_port, free_tcp_port_factory(), inherited_root,
    )

    assert result.returncode == 0, result.stderr
    assert f"Configured library ({inherited_root / 'logs' / 'processed.db'})" in result.stdout


def test_rootless_launcher_start_does_not_require_configured_library(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    service_root = _service_root(tmp_path, None)

    result = _dry_run(
        service_root, "start-launcher-local", free_tcp_port, free_tcp_port_factory(),
    )

    assert result.returncode == 0, result.stderr
    assert "Rootless launcher bootstrap (no library selected)" in result.stdout
    assert "expected local index" not in result.stdout.lower()


@pytest.mark.parametrize(
    ("access_choice", "access_label"),
    [("1", "LAN"), ("2", "Local only")],
)
def test_interactive_start_defaults_to_rootless_launcher_in_each_access_mode(
    tmp_path, access_choice, access_label, free_tcp_port, free_tcp_port_factory,
):
    service_root = _service_root(tmp_path, None)

    result = _sourced_dry_run(
        service_root,
        "crateiq_start",
        free_tcp_port,
        free_tcp_port_factory(),
        input_text=f"\n{access_choice}\n",
    )

    assert result.returncode == 0, result.stderr
    assert "Library Launcher (recommended)" in result.stdout
    assert "Rootless launcher bootstrap (no library selected)" in result.stdout
    assert f"Access: {access_label}" in result.stdout
    assert "processed.db" not in result.stdout


def test_interactive_rootless_launcher_ignores_stale_inherited_library_root(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    inherited_root = _library_root(tmp_path, "stale-inherited-library")
    service_root = _service_root(tmp_path, None)

    result = _sourced_dry_run(
        service_root,
        "crateiq_start",
        free_tcp_port,
        free_tcp_port_factory(),
        inherited_root,
        input_text="1\n2\n",
    )

    assert result.returncode == 0, result.stderr
    assert "Rootless launcher bootstrap (no library selected)" in result.stdout
    assert str(inherited_root) not in result.stdout


def test_explicit_legacy_configured_start_still_requires_initialized_index(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    uninitialized_root = tmp_path / "uninitialized-library"
    uninitialized_root.mkdir()
    service_root = _service_root(tmp_path, uninitialized_root)

    result = _dry_run(
        service_root,
        "start-library-local",
        free_tcp_port,
        free_tcp_port_factory(),
    )

    assert result.returncode == 1
    assert "configured library is not initialized" in result.stderr
    assert str(uninitialized_root / "logs" / "processed.db") in result.stderr


@pytest.mark.parametrize("input_text", ["4\n", "1\n3\n"])
def test_interactive_start_cancel_does_not_start_a_profile(
    tmp_path, input_text, free_tcp_port, free_tcp_port_factory,
):
    service_root = _service_root(tmp_path, None)

    result = _dry_run(
        service_root,
        "start",
        free_tcp_port,
        free_tcp_port_factory(),
        input_text=input_text,
    )

    assert result.returncode == 0, result.stderr
    assert "Cancelled." in result.stdout
    assert "Dry run:" not in result.stdout


def test_status_reports_supervisor_backend_port_and_frontend_without_service_start(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    service_root = _service_root(tmp_path, None)
    backend_port = free_tcp_port
    frontend_port = free_tcp_port_factory()
    env = os.environ.copy()
    env.update(
        CRATEIQ_ROOT=str(service_root),
        CRATEIQ_BACKEND_PORT=str(backend_port),
        CRATEIQ_FRONTEND_PORT=str(frontend_port),
    )
    script = r'''
source "$1"
_crateiq_pid_from_file() {
    case "$1" in
        *backend.pid) echo 4101 ;;
        *frontend.pid) echo 4102 ;;
    esac
}
_crateiq_process_running() { return 0; }
_crateiq_port_listening() { echo LISTENING; }
_crateiq_http_code() { echo 200; }
crateiq_status
'''

    result = subprocess.run(
        ["bash", "-c", script, "bash", str(SERVICE_SCRIPT)],
        cwd=service_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Supervisor (owns backend child)" in result.stdout
    assert "running (PID 4101)" in result.stdout
    assert "running (PID 4102)" in result.stdout
    assert f"Port {backend_port}: LISTENING" in result.stdout
    assert f"Port {frontend_port}: LISTENING" in result.stdout


def test_stop_routes_only_owned_frontend_and_supervisor_targets_without_signals(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    service_root = _service_root(tmp_path, None)
    backend_port = free_tcp_port
    frontend_port = free_tcp_port_factory()
    env = os.environ.copy()
    env.update(
        CRATEIQ_ROOT=str(service_root),
        CRATEIQ_BACKEND_PORT=str(backend_port),
        CRATEIQ_FRONTEND_PORT=str(frontend_port),
    )
    script = r'''
source "$1"
_crateiq_stop_service() { printf '%s|%s|%s|%s\n' "$1" "$2" "$3" "$4"; }
crateiq_stop
'''

    result = subprocess.run(
        ["bash", "-c", script, "bash", str(SERVICE_SCRIPT)],
        cwd=service_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"frontend|{service_root / '.run' / 'frontend.pid'}|{frontend_port}|_crateiq_pid_is_frontend",
        f"backend|{service_root / '.run' / 'backend.pid'}|{backend_port}|_crateiq_pid_is_backend",
    ]


def test_rootless_launcher_start_ignores_inherited_library_root(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    inherited_root = _library_root(tmp_path, "inherited-library")
    service_root = _service_root(tmp_path, None)

    result = _dry_run(
        service_root, "start-launcher-local", free_tcp_port, free_tcp_port_factory(), inherited_root,
    )

    assert result.returncode == 0, result.stderr
    assert "Rootless launcher bootstrap (no library selected)" in result.stdout
    assert str(inherited_root) not in result.stdout


@pytest.mark.parametrize(
    "saved_env",
    ["CRATEIQ_LIBRARY_ROOT=", "UNRELATED=value\n# CRATEIQ_LIBRARY_ROOT=ignored"],
)
def test_sourced_function_safely_falls_back_for_empty_or_unrelated_settings(
    tmp_path, saved_env, free_tcp_port, free_tcp_port_factory,
):
    inherited_root = _library_root(tmp_path, "inherited-library")
    service_root = _service_root(tmp_path, None)

    result = _sourced_dry_run(
        service_root,
        "crateiq_start_library_local",
        free_tcp_port,
        free_tcp_port_factory(),
        inherited_root,
        saved_env,
    )

    assert result.returncode == 0, result.stderr
    assert f"Configured library ({inherited_root / 'logs' / 'processed.db'})" in result.stdout


def test_sourced_function_treats_shell_like_settings_content_as_literal_data(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    marker = "crateiq-settings-was-executed"
    saved_root = _library_root(tmp_path, f"literal$(touch {marker})")
    service_root = _service_root(tmp_path, None)

    result = _sourced_dry_run(
        service_root,
        "crateiq_start_library_local",
        free_tcp_port,
        free_tcp_port_factory(),
        saved_env=f"UNRELATED=ignored\nCRATEIQ_LIBRARY_ROOT={saved_root}",
    )

    assert result.returncode == 0, result.stderr
    assert f"Configured library ({saved_root / 'logs' / 'processed.db'})" in result.stdout
    assert not (service_root / marker).exists()


def test_repeated_sourcing_and_starting_refreshes_the_current_saved_root(
    tmp_path, free_tcp_port, free_tcp_port_factory,
):
    initial_root = _library_root(tmp_path, "initial-library")
    changed_root = _library_root(tmp_path, "changed-library")
    stale_root = _library_root(tmp_path, "stale-inherited-library")
    service_root = _service_root(tmp_path, initial_root)
    env = os.environ.copy()
    env.update(
        CRATEIQ_ROOT=str(service_root),
        CRATEIQ_DRY_RUN="1",
        CRATEIQ_BACKEND_PORT=str(free_tcp_port),
        CRATEIQ_FRONTEND_PORT=str(free_tcp_port_factory()),
        CRATEIQ_LIBRARY_ROOT=str(stale_root),
    )
    script = """
source "$1" --aliases
crateiq_start_library_local
printf 'CRATEIQ_LIBRARY_ROOT=%s\\n' "$2" > "$CRATEIQ_ROOT/.run/local/crateiq.env"
source "$1" --aliases
crateiq_start_library_local
"""

    result = subprocess.run(
        ["bash", "-c", script, "bash", str(SERVICE_SCRIPT), str(changed_root)],
        cwd=service_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"Configured library ({initial_root / 'logs' / 'processed.db'})" in result.stdout
    assert f"Configured library ({changed_root / 'logs' / 'processed.db'})" in result.stdout
    assert str(stale_root) not in result.stdout
