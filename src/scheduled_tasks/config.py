"""Environment configuration parsing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str


def _project_root() -> Path:
    # src/scheduled_tasks/config.py → 仓库根目录
    return Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """把 .env 写入 os.environ；已存在的环境变量不被覆盖。"""
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def load_settings() -> Settings:
    _load_dotenv(_project_root() / ".env")
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. 请复制 .env.example 为 .env 并填入 "
            "Supabase Postgres 连接串，或先 export DATABASE_URL=..."
        )
    return Settings(database_url=database_url)
