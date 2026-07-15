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
