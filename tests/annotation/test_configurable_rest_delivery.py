from __future__ import annotations

import json

import httpx
import pytest
import respx

from domain.core.models import SystemTenant
from domain.integrations.configuration import normalize_result_delivery_config
from domain.integrations.connectors.configurable_rest_connector import ConfigurableRestConnector


BASE = "https://tasks.example/api"


def tenant(config: dict | None = None) -> SystemTenant:
    return SystemTenant(
        tenant_id="tenant-1",
        system_name="Test service",
        server_host_name=BASE,
        target_format="coco",
        api_token="connector-secret",
        connector_config=config,
    )


def test_result_delivery_sends_configured_json_and_escapes_path_ids():
    config = normalize_result_delivery_config("/tasks/{ant_id}/results/{task_id}", "patch")
    assert config == {
        "result_path": "/tasks/{ant_id}/results/{task_id}",
        "result_method": "PATCH",
    }
    payload = {
        "react_sidecars": {"format": "annotation-react-sidecar-bundle/v1", "items": {}},
    }
    with respx.mock:
        route = respx.patch(f"{BASE}/tasks/ant%2F7/results/task%3A1").mock(
            return_value=httpx.Response(202)
        )
        result = ConfigurableRestConnector(tenant(config)).deliver_result(
            "ant/7", "task:1", payload, "NG", "worker-9"
        )

    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer connector-secret"
    assert json.loads(request.content) == {
        "ant_id": "ant/7",
        "task_id": "task:1",
        "annotation_json": payload,
        "new_classification": "NG",
        "annotated_by": "worker-9",
    }
    assert result == {"status": "ok", "ant_id": "ant/7", "http_status": 202}


def test_result_delivery_is_unsupported_when_no_endpoint_is_configured():
    connector = ConfigurableRestConnector(tenant({"list_path": "/tasks"}))

    assert connector.deliver_result("ant-1", "task-1", {}, None, None) == {
        "status": "unsupported",
        "ant_id": "ant-1",
    }


def test_result_delivery_reports_http_failure_without_response_body():
    connector = ConfigurableRestConnector(tenant({"result_path": "/results"}))
    with respx.mock:
        respx.post(f"{BASE}/results").mock(
            return_value=httpx.Response(503, text="private upstream diagnostic")
        )
        with pytest.raises(RuntimeError, match="HTTP 503") as error:
            connector.deliver_result("ant-1", "task-1", {}, None, None)

    assert "private upstream diagnostic" not in str(error.value)


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("https://other.example/submit", "POST"),
        ("//other.example/submit", "POST"),
        (r"\\other.example\submit", "POST"),
        ("/submit?token=x", "POST"),
        ("/tasks/../submit", "POST"),
        ("/tasks/{unknown}/submit", "POST"),
        ("/submit", "GET"),
    ],
)
def test_result_delivery_configuration_rejects_unsafe_or_unsupported_values(path, method):
    with pytest.raises(ValueError):
        normalize_result_delivery_config(path, method)
