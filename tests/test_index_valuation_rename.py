"""指数估值表命名回归测试。"""

from __future__ import annotations

import inspect
from pathlib import Path

from scheduled_tasks import db

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "src/scheduled_tasks/models/migrations/20260719_rename_etf_valuation_to_index_valuation.sql"
)


def test_index_valuation_db_helpers_use_index_named_table() -> None:
    helper_names = (
        "fetch_index_valuation",
        "upsert_index_valuation",
        "update_index_valuation_pe_official",
    )

    for helper_name in helper_names:
        helper = getattr(db, helper_name, None)
        assert helper is not None, f"missing database helper: {helper_name}"
        assert "public.index_valuation" in inspect.getsource(helper)


def test_current_schema_defines_index_valuation() -> None:
    schema = (ROOT / "src/scheduled_tasks/models/schema.sql").read_text(encoding="utf-8")

    assert "create table if not exists index_valuation" in schema
    assert "create table if not exists etf_valuation" not in schema
    assert "left join index_valuation" in schema


def test_rename_migration_covers_table_constraint_and_public_read_policies() -> None:
    assert MIGRATION.is_file()
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "alter table public.etf_valuation rename to index_valuation" in sql
    assert "rename constraint etf_valuation_pkey to index_valuation_pkey" in sql
    assert "etf_valuation_select_anon" in sql
    assert "index_valuation_select_anon" in sql
    assert "etf_valuation_select_authenticated" in sql
    assert "index_valuation_select_authenticated" in sql
