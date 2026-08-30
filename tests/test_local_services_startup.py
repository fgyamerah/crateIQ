"""Regression coverage for configured-library startup root precedence."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = REPO_ROOT / "scripts" / "crateiq-local-services.sh"


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
        input_text="2\n2\n",
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
        ("crateiq_start", "2\n2\n"),
        ("crate_restart", "2\n2\n"),
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
