"""Load and validate external-system connection profiles for this app."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from domain.core.models import SystemTenant


_REQUIRED_FIELDS = ("tenant_id", "system_name", "server_host_name", "target_format")


def load_profile(data: dict[str, Any]) -> SystemTenant:
    if not isinstance(data, dict):
        raise ValueError("profile must be an object")

    values: dict[str, str] = {}
    for field in _REQUIRED_FIELDS:
        raw = data.get(field)
        value = str(raw or "").strip()
        if not value:
            raise ValueError(f"{field} is required")
        values[field] = value

    token = data.get("api_token")
    connector_config = data.get("connector_config") or data.get("rest_mapping")
    return SystemTenant(
        tenant_id=values["tenant_id"],
        system_name=values["system_name"],
        server_host_name=values["server_host_name"].rstrip("/"),
        target_format=values["target_format"],
        api_token=str(token) if token is not None else None,
        connector_type=(str(data["connector_type"]).strip() or None)
        if data.get("connector_type") is not None else None,
        connector_config=connector_config if isinstance(connector_config, dict) else None,
    )


def load_profile_from_file(path: str | Path) -> SystemTenant:
    profile_path = Path(path)
    with profile_path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    return load_profile(payload)


__all__ = ["SystemTenant", "load_profile", "load_profile_from_file"]
