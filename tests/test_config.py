from pathlib import Path

import pytest

from scheduled_tasks.config import load_settings


def test_load_settings_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "scheduled_tasks.config._project_root",
        lambda: Path("/tmp/scheduled-tasks-no-env"),
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        load_settings()


def test_load_settings_reads_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setattr("scheduled_tasks.config._project_root", lambda: tmp_path)
    settings = load_settings()
    assert settings.database_url == "postgresql://user:pass@localhost:5432/db"


def test_load_settings_reads_dotenv_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://dotenv:pass@localhost:5432/db\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scheduled_tasks.config._project_root", lambda: tmp_path)
    settings = load_settings()
    assert settings.database_url == "postgresql://dotenv:pass@localhost:5432/db"


def test_load_settings_env_overrides_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://env:pass@localhost:5432/db")
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://dotenv:pass@localhost:5432/db\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scheduled_tasks.config._project_root", lambda: tmp_path)
    settings = load_settings()
    assert settings.database_url == "postgresql://env:pass@localhost:5432/db"
