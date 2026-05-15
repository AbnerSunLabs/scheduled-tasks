"""Environment configuration parsing."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

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


@lru_cache(maxsize=1)
def load_supported_index_metas() -> dict[str, IndexMeta]:
    data_path = resources.files("scheduled_tasks").joinpath(
        "data/indices/supported-indices.json"
    )
    rows = json.loads(data_path.read_text(encoding="utf-8"))
    metas: dict[str, IndexMeta] = {}
    for row in rows:
        code = str(row.get("code", "")).strip().upper()
        name = str(row.get("name", "")).strip()
        category = str(row.get("category", "")).strip()
        display_order = row.get("displayOrder")
        if not INDEX_CODE_RE.match(code):
            continue
        if not name or not category:
            continue
        if not isinstance(display_order, int):
            continue
        metas[code] = IndexMeta(
            code=code,
            name=name,
            category=category,
            display_order=display_order,
        )
    return metas


def infer_index_meta(code: str, display_order: int) -> IndexMeta:
    meta = load_supported_index_metas().get(code)
    if meta:
        return meta
    return IndexMeta(
        code=code,
        name=code,
        category="未分类",
        display_order=display_order,
    )
