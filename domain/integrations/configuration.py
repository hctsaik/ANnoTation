"""Validation helpers for app-owned external connector configuration."""
from __future__ import annotations

import re
from urllib.parse import urlparse


_RESULT_PLACEHOLDERS = {"ant_id", "task_id"}


def normalize_result_delivery_config(
    path: object, method: object = "POST"
) -> dict[str, str] | None:
    """Return a constrained opt-in REST result endpoint configuration.

    The remote service owns the endpoint semantics. This app sends a documented
    JSON payload only when a relative path is explicitly configured.
    """
    endpoint = str(path or "").strip()
    if not endpoint:
        return None
    if len(endpoint) > 256 or any(ord(char) < 32 for char in endpoint):
        raise ValueError("Result endpoint path is invalid")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or endpoint.startswith("//")
        or "\\" in endpoint
    ):
        raise ValueError("Result endpoint must be a relative path without query or fragment")
    if any(segment in {".", ".."} for segment in endpoint.split("/")):
        raise ValueError("Result endpoint path cannot contain dot segments")
    if endpoint.count("{") != endpoint.count("}"):
        raise ValueError("Result endpoint has an unmatched placeholder")
    placeholders = set(re.findall(r"\{([^{}]+)\}", endpoint))
    remainder = re.sub(r"\{[^{}]+\}", "", endpoint)
    if (
        placeholders - _RESULT_PLACEHOLDERS
        or "{" in remainder
        or "}" in remainder
    ):
        raise ValueError("Result endpoint supports only {ant_id} and {task_id} placeholders")
    normalized_method = str(method or "POST").strip().upper()
    if normalized_method not in {"POST", "PUT", "PATCH"}:
        raise ValueError("Result delivery method must be POST, PUT, or PATCH")
    return {"result_path": endpoint, "result_method": normalized_method}


__all__ = ["normalize_result_delivery_config"]
