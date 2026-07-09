import pytest

from scheduled_tasks.config import load_settings


def test_load_settings_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        load_settings()


def test_load_settings_reads_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    settings = load_settings()
    assert settings.database_url == "postgresql://user:pass@localhost:5432/db"
