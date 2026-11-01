"""App-local connector factory registry for remote task systems."""
from __future__ import annotations

from typing import Callable
from urllib.parse import urlparse

from domain.core.models import SystemTenant
from domain.integrations.contracts import ExternalSystemConnector


ConnectorFactory = Callable[..., ExternalSystemConnector]
_FACTORIES: dict[str, ConnectorFactory] = {}


def register_connector(name: str, factory: ConnectorFactory) -> None:
    key = name.strip().lower()
    if not key:
        raise ValueError("connector name cannot be empty")
    _FACTORIES[key] = factory


def _rest_factory(tenant: SystemTenant, **_opts) -> ExternalSystemConnector:
    if tenant.connector_config:
        from domain.integrations.connectors.configurable_rest_connector import ConfigurableRestConnector
        return ConfigurableRestConnector(tenant)
    from domain.integrations.connectors.rest_connector import RestConnector
    return RestConnector(tenant)


def _file_factory(tenant: SystemTenant, **opts) -> ExternalSystemConnector:
    from domain.integrations.connectors.file_connector import FileConnector
    return FileConnector(tenant, **opts)


def _fake_factory(tenant: SystemTenant, **opts) -> ExternalSystemConnector:
    from domain.integrations.connectors.fake_connector import FakeConnector
    tasks = opts.pop("tasks", None)
    if tasks is None:
        tasks = [
            {
                "antID": f"FAKE_TASK_{index:03d}",
                "antActive": 0,
                "antPeriod": "2026-05-26T08:00:00Z",
                "external_context": {"lot_id": f"L{index:02d}", "eqp_id": "AOI-01"},
            }
            for index in range(1, 4)
        ]
    return FakeConnector(tasks=tasks, download_url=opts.pop("download_url", ""), **opts)


def _ensure_builtins() -> None:
    for name, factory in (("rest", _rest_factory), ("file", _file_factory), ("fake", _fake_factory)):
        _FACTORIES.setdefault(name, factory)


def available_types() -> list[str]:
    _ensure_builtins()
    return sorted(_FACTORIES)


def infer_type(server_host_name: str) -> str:
    scheme = (urlparse(server_host_name or "").scheme or "").lower()
    if scheme in {"fake", "file"}:
        return scheme
    return "rest"


def build_connector(tenant: SystemTenant, **opts) -> ExternalSystemConnector:
    _ensure_builtins()
    connector_type = (getattr(tenant, "connector_type", None) or "").strip().lower()
    connector_type = connector_type or infer_type(tenant.server_host_name)
    factory = _FACTORIES.get(connector_type)
    if factory is None:
        known = ", ".join(available_types())
        raise ValueError(f"Unknown connector type {connector_type!r}; available: {known}")
    return factory(tenant, **opts)


__all__ = ["available_types", "build_connector", "infer_type", "register_connector"]
