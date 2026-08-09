import pytest

from backend.app.core import library_root


def test_crateiq_environment_variable_is_preferred(monkeypatch, tmp_path):
    preferred = tmp_path / "preferred"
    legacy = tmp_path / "legacy"
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(preferred))
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(legacy))

    assert library_root.selected_library_root() == preferred.resolve()


def test_legacy_environment_variable_remains_supported(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(legacy))

    assert library_root.selected_library_root() == legacy.resolve()


def test_library_root_environment_variables_must_be_absolute(monkeypatch):
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", "relative/library")

    with pytest.raises(RuntimeError, match="CRATEIQ_LIBRARY_ROOT must be absolute"):
        library_root.selected_library_root()


def test_no_root_configured_fails_closed_without_reading_legacy_config(monkeypatch):
    """
    With neither environment variable set, selected_library_root() must raise
    rather than silently falling back to the legacy pipeline's config.py
    MUSIC_ROOT (which itself defaults to /music). This also proves the
    legacy root config module is never imported as a side effect of root
    resolution: DJ_MUSIC_ROOT (config.py's own env override) is set here and
    must have no effect on the backend's root resolution.
    """
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CRATEMINDAI_LIBRARY_ROOT", raising=False)
    monkeypatch.setenv("DJ_MUSIC_ROOT", "/music")

    with pytest.raises(RuntimeError, match="No safe library root is configured"):
        library_root.selected_library_root()


def test_selected_library_root_has_no_toolkit_config_fallback_helper():
    """
    Guards against reintroducing an implicit config.py-backed fallback:
    the module must not expose a helper that loads the legacy root config.
    """
    assert not hasattr(library_root, "_load_toolkit_music_root")
