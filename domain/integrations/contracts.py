"""App-owned contracts for external annotation task systems."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExternalTask:
    ant_id: str
    ant_active: int = 0
    ant_period: str | None = None
    external_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExternalTaskDetail:
    download_url: str


@dataclass(frozen=True)
class ConnectorHealth:
    connected: bool
    latency_ms: int | None = None
    error: str | None = None


class ExternalSystemConnector(ABC):
    @abstractmethod
    def get_ant_list(self) -> list[ExternalTask]:
        """Return the tasks visible from a registered external system."""

    @abstractmethod
    def get_ant_task_detail(self, ant_id: str, format: str) -> ExternalTaskDetail:
        """Return the download location and format metadata for a task."""

    @abstractmethod
    def health_check(self) -> ConnectorHealth:
        """Check connectivity without changing external state."""

    def mark_task_claimed(self, ant_id: str) -> None:
        """Notify the external system that a task was claimed, when supported."""

    def deliver_result(
        self,
        ant_id: str,
        platform_task_id: str,
        annotation_json: dict[str, Any],
        new_classification: str | None,
        annotated_by: str | None,
    ) -> dict[str, Any]:
        """Submit a completed annotation; connectors may override this hook."""
        return {"status": "unsupported", "ant_id": ant_id}


# Compatibility names used by the existing app and connector tests.
AntTask = ExternalTask
TaskDetailResponse = ExternalTaskDetail

__all__ = [
    "AntTask",
    "ConnectorHealth",
    "ExternalSystemConnector",
    "ExternalTask",
    "ExternalTaskDetail",
    "TaskDetailResponse",
]
