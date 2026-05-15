import pytest

from scheduled_tasks.config import infer_index_meta, parse_index_codes


def test_parse_index_codes_dedupes_and_normalizes() -> None:
    assert parse_index_codes("000300.sh, 000905.SH,000300.SH") == (
        "000300.SH",
        "000905.SH",
    )


def test_parse_index_codes_rejects_invalid_code() -> None:
    with pytest.raises(ValueError, match="invalid index code"):
        parse_index_codes("000300.SH,abc")


def test_infer_index_meta_uses_code_as_default_name() -> None:
    meta = infer_index_meta("000300.SH", 10)
    assert meta.code == "000300.SH"
    assert meta.name == "000300.SH"
    assert meta.category == "未分类"
    assert meta.display_order == 10
