"""Environment configuration parsing."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

INDEX_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|CSI)$")


@dataclass(frozen=True)
class IndexMeta:
    code: str
    name: str
    category: str
    display_order: int


@dataclass(frozen=True)
class Settings:
    database_url: str
    index_codes: tuple[str, ...]


def parse_index_codes(raw: str) -> tuple[str, ...]:
    codes: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        code = item.strip().upper()
        if not code:
            continue
        if not INDEX_CODE_RE.match(code):
            raise ValueError(f"invalid index code: {code}")
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        raise ValueError("INDEX_CODES is empty")
    return tuple(codes)


def load_settings() -> Settings:
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    raw_index_codes = (os.environ.get("INDEX_CODES") or "").strip()
    return Settings(
        database_url=database_url,
        index_codes=parse_index_codes(raw_index_codes),
    )


def infer_index_meta(code: str, display_order: int) -> IndexMeta:
    return IndexMeta(
        code=code,
        name=code,
        category="未分类",
        display_order=display_order,
    )
