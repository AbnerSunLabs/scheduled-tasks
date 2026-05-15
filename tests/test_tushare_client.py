from scheduled_tasks.tushare_client import (
    DEFAULT_TUSHARE_HTTPS_BASE,
    normalize_data_api,
)


def test_normalize_data_api_defaults_to_official_api() -> None:
    assert normalize_data_api("") == DEFAULT_TUSHARE_HTTPS_BASE


def test_normalize_data_api_adds_http_for_bare_host(monkeypatch) -> None:
    monkeypatch.delenv("DATA_API_SCHEME", raising=False)
    assert normalize_data_api("example.com/proxy") == "http://example.com/proxy"


def test_normalize_data_api_keeps_explicit_scheme() -> None:
    assert normalize_data_api("https://example.com/proxy/") == "https://example.com/proxy"
