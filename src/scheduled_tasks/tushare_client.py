"""TuShare Pro client setup shared by sync jobs."""

from __future__ import annotations

import os

DEFAULT_TUSHARE_HTTPS_BASE = "https://api.tushare.pro"


def normalize_data_api(url: str) -> str:
    raw_url = url.strip().rstrip("/")
    if not raw_url:
        return DEFAULT_TUSHARE_HTTPS_BASE
    if "://" not in raw_url:
        forced = (os.environ.get("DATA_API_SCHEME") or "").strip().lower()
        if forced in ("http", "https"):
            return f"{forced}://{raw_url}"
        return f"http://{raw_url}"
    return raw_url


def _apply_proxy_env(gateway_normalized: str) -> None:
    if not gateway_normalized.strip():
        return
    flag = (os.environ.get("TUSHARE_SYNC_PROXY_ENV") or "0").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return
    proxy = gateway_normalized.rstrip("/")
    os.environ.setdefault("HTTP_PROXY", proxy)
    os.environ.setdefault("HTTPS_PROXY", proxy)


def create_pro():  # noqa: ANN201 - TuShare returns a dynamic DataApi instance.
    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not set")

    gateway = (os.environ.get("DATA_API") or "").strip()
    base = normalize_data_api(gateway)
    if gateway:
        _apply_proxy_env(base)

    import tushare as ts

    pro = ts.pro_api(token)
    pro._DataApi__http_url = base
    return pro
