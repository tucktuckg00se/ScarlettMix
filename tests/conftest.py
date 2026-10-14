import pytest


@pytest.fixture(autouse=True)
def _private_config(tmp_path, monkeypatch):
    """No test may read or write the user's real settings folder."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
