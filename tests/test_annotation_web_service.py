from __future__ import annotations

import io
import http.client
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest
from PIL import Image

from annotation_runtime import _manifest_db as manifest_db
from annotation_web.server import make_handler
from annotation_web.service import AnnotationService, ServiceError


@pytest.fixture
def service_and_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIR", str(data_dir))
    source = tmp_path / "synthetic-images"
    source.mkdir()
    Image.new("RGB", (32, 24), "#88aacc").save(source / "panel-a.png")
    service = AnnotationService(data_dir)
    created = service.create_dataset(
        {"folder_path": str(source), "manifest_name": "Synthetic inspection", "recursive": True}
    )
    return service, source, created


def test_local_dataset_image_and_annotation_round_trip(service_and_dataset):
    service, source, created = service_and_dataset
    assert created["item_count"] == 1

    item = service.list_manifests()[0]
    assert item["name"] == "Synthetic inspection"
    item_id = manifest_db.get_manifest_items(service.db_path, created["manifest_id"])[0]["item_id"]
    detail = service.item_detail(created["manifest_id"], item_id)
    assert detail["item"]["name"] == "panel-a.png"
    assert detail["annotation"]["imagePath"] == "panel-a.png"
    assert detail["annotation"]["imageData"] is None
    assert str(source) not in json.dumps(detail)

    item_id = detail["item"]["item_id"]
    image, content_type = service.image(created["manifest_id"], item_id)
    assert image.startswith(b"\x89PNG")
    assert content_type == "image/png"
    for invalid_points in (
        [[0, 0], [float("nan"), 12]],
        [[0, 0], [40, 20]],
    ):
        with pytest.raises(ServiceError, match="Rectangle coordinates are invalid"):
            service.save_annotation(
                created["manifest_id"],
                item_id,
                {"shapes": [{"label": "scratch", "shape_type": "rectangle", "points": invalid_points}]},
            )

    service.save_annotation(
        created["manifest_id"],
        item_id,
        {
            "version": "2.4.0",
            "flags": {},
            "shapes": [
                {
                    "label": "scratch",
                    "shape_type": "rectangle",
                    "points": [[2, 3], [14, 17]],
                    "score": None,
                },
                {
                    "label": "dent",
                    "shape_type": "polygon",
                    "points": [[3, 4], [18, 4], [18, 16]],
                },
            ],
        },
    )
    reopened = service.item_detail(created["manifest_id"], item_id)
    assert reopened["annotation"]["shapes"][0]["points"] == [[2, 3], [14, 17]]
    assert reopened["annotation"]["shapes"][1]["shape_type"] == "polygon"
    assert reopened["item"]["shape_count"] == 2

    labels = service.labels(created["manifest_id"])
    assert labels["label_map"] == {"scratch": 1, "dent": 1}
    assert labels["label_counts"] == {"scratch": 1, "dent": 1}
    assert str(source) not in json.dumps(labels)
    renamed = service.update_labels(created["manifest_id"], "rename", {"old": "scratch", "new": "scuff"})
    assert renamed["labels"]["label_map"] == {"dent": 1, "scuff": 1}
    merged = service.update_labels(created["manifest_id"], "merge", {"sources": ["scuff"], "target": "dent"})
    assert merged["labels"]["label_map"] == {"dent": 1}
    deleted = service.update_labels(created["manifest_id"], "delete", {"label": "dent"})
    assert deleted["labels"]["label_map"] == {}


def test_classification_and_review_round_trip(service_and_dataset):
    service, _source, created = service_and_dataset
    item_id = manifest_db.get_manifest_items(service.db_path, created["manifest_id"])[0]["item_id"]

    service.save_classification(created["manifest_id"], item_id, "NG")
    service.review(created["manifest_id"], item_id, "approved", "checked")

    detail = service.item_detail(created["manifest_id"], item_id)
    assert detail["item"]["classification"] == "NG"
    assert detail["item"]["review_status"] == "approved"
    assert detail["item"]["review_comment"] == "checked"
    review_items = service.items(created["manifest_id"], view="review")
    assert review_items[0]["review_status"] == "approved"
    assert review_items[0]["review_comment"] == "checked"
    assert str(service.data_dir) not in json.dumps(review_items)


def test_dataset_grant_rejects_files_outside_source_folder(tmp_path: Path):
    service = AnnotationService(tmp_path / "app-data")
    source = tmp_path / "granted"
    source.mkdir()
    outside = tmp_path / "private.png"
    Image.new("RGB", (3, 3), "white").save(outside)
    with pytest.raises(ServiceError, match="outside the dataset grant"):
        service._local_item_path(
            {"source_config": {"folder_path": str(source)}},
            {"file_path": str(outside)},
        )


def test_remote_dataset_list_download_and_ingest_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "app-data"))
    image = io.BytesIO()
    Image.new("RGB", (20, 16), "#ddaa88").save(image, format="PNG")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("images/remote-panel.png", image.getvalue())
        bundle.writestr(
            "annotations/remote-panel.json",
            json.dumps({"version": "2.4.0", "flags": {}, "shapes": []}),
        )
    archive_bytes = archive.getvalue()

    class RemoteServiceHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/datasets":
                payload = json.dumps([{"dataset_id": "remote-1", "name": "Remote inspection", "item_count": 1}]).encode()
                content_type = "application/json"
            elif self.path == "/datasets/remote-1/download":
                payload = archive_bytes
                content_type = "application/zip"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RemoteServiceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service = AnnotationService(tmp_path / "app-data")
    service_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(ServiceError, match="valid remote Service URL"):
            service.remote_datasets("file:///not-a-service")
        datasets = service.remote_datasets(service_url)
        assert datasets == [{"dataset_id": "remote-1", "name": "Remote inspection", "item_count": 1}]

        created = service.create_dataset(
            {
                "mode": "remote",
                "service_url": service_url,
                "dataset_id": "remote-1",
                "dataset_name": "Remote inspection",
                "overwrite": True,
            }
        )
        assert created["name"] == "Remote inspection"
        assert created["item_count"] == 1
        items = manifest_db.get_manifest_items(service.db_path, created["manifest_id"])
        assert Path(items[0]["file_path"]).is_relative_to(tmp_path / "app-data" / "downloads")
        detail = service.item_detail(created["manifest_id"], items[0]["item_id"])
        assert detail["item"]["name"] == "remote-panel.png"
        assert detail["annotation"]["shapes"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_iwisc_task_listing_claim_and_local_manifest_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIR", str(app_data))
    image = io.BytesIO()
    Image.new("RGB", (24, 18), "#8ab4d8").save(image, format="PNG")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("images/panel-a.png", image.getvalue())
    archive_bytes = archive.getvalue()
    calls: list[tuple[str, str | None]] = []

    class IwiscHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str = "application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            if self.path == "/getAntList":
                payload = json.dumps([
                    {"antID": "ant-01", "antActive": 0, "antPeriod": "2026-11", "line": "L01", "camera": "top", "part": "A", "private_note": "must not be projected"},
                    {"antID": "ant-done", "antActive": 2, "antPeriod": "2026-10"},
                ]).encode()
                self._send(200, payload)
            elif self.path == "/artifact.zip":
                self._send(200, archive_bytes, "application/zip")
            else:
                self._send(404, b"{}")

        def do_POST(self):
            calls.append((self.path, self.headers.get("Authorization")))
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/getAntTaskDetail" and payload == {"antID": "ant-01", "format": "coco"}:
                url = f"http://127.0.0.1:{external.server_address[1]}/artifact.zip"
                self._send(200, json.dumps({"download_url": url}).encode())
            else:
                self._send(404, b"{}")

        def do_PATCH(self):
            calls.append((self.path, self.headers.get("Authorization")))
            self._send(200 if self.path == "/tasks/ant-01/claim" else 404, b"{}")

        def log_message(self, _format, *_args):
            return

    external = ThreadingHTTPServer(("127.0.0.1", 0), IwiscHandler)
    external_thread = threading.Thread(target=external.serve_forever, daemon=True)
    external_thread.start()
    service = AnnotationService(app_data)
    service_url = f"http://127.0.0.1:{external.server_address[1]}"
    api_server = api_thread = None
    try:
        tenant = service.register_iwisc_tenant({
            "system_name": "AOI Line 1",
            "server_host_name": service_url,
            "target_format": "coco",
            "api_token": "service-secret",
        })
        assert tenant["has_api_token"] is True
        assert "api_token" not in tenant
        tenants = service.iwisc_tenants()
        assert tenants == [tenant]
        assert "service-secret" not in json.dumps(tenants)

        tasks = service.iwisc_tasks(tenant["tenant_id"])
        assert [task["ant_id"] for task in tasks] == ["ant-01"]
        assert tasks[0]["ant_active"] == 0
        assert tasks[0]["external_context"] == {"line": "L01", "camera": "top", "part": "A"}
        assert "private_note" not in json.dumps(tasks)

        static_dir = tmp_path / "dist"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("<main>ANnoTation</main>", encoding="utf-8")
        api_server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(service, "iwisc-session", static_dir, {"http://127.0.0.1:5173"}),
        )
        api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
        api_thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", api_server.server_address[1], timeout=10)
        headers = {"Origin": "http://127.0.0.1:5173", "X-Annotation-Token": "iwisc-session"}
        connection.request(
            "POST",
            "/api/sources/iwisc/tenants",
            body=json.dumps({
                "system_name": "AOI Line 2",
                "server_host_name": service_url,
                "target_format": "coco",
                "api_token": "another-secret",
            }),
            headers={**headers, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        registered = json.loads(response.read())
        assert response.status == 201
        assert registered["system_name"] == "AOI Line 2"
        assert "api_token" not in registered
        assert "another-secret" not in json.dumps(registered)
        connection.request("GET", "/api/sources/iwisc/tenants", headers=headers)
        response = connection.getresponse()
        api_tenants = json.loads(response.read())
        assert response.status == 200
        assert "service-secret" not in json.dumps(api_tenants)
        connection.request(
            "GET",
            f"/api/sources/iwisc/tasks?{urlencode({'tenant_id': tenant['tenant_id']})}",
            headers=headers,
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["tasks"][0]["ant_id"] == "ant-01"
        connection.close()

        created = service.create_dataset({
            "mode": "iwisc",
            "tenant_id": tenant["tenant_id"],
            "ant_id": "ant-01",
            "user_id": "worker-7",
        })
        manifest = manifest_db.get_manifest(service.db_path, created["manifest_id"])
        items = manifest_db.get_manifest_items(service.db_path, created["manifest_id"])
        assert created["source_type"] == "iwisc"
        assert created["iwisc_ant_id"] == "ant-01"
        assert created["item_count"] == 1
        assert manifest["source_type"] == "iwsc"  # module 026 legacy storage contract
        listed = next(row for row in service.list_manifests() if row["manifest_id"] == created["manifest_id"])
        assert listed["source_type"] == "iwisc"
        assert Path(items[0]["file_path"]).is_relative_to(app_data / "annotation_workspace" / "tasks")
        detail = service.item_detail(created["manifest_id"], items[0]["item_id"])
        assert detail["item"]["name"] == "panel-a.png"
        assert len(calls) >= 4
        assert all(
            auth == "Bearer service-secret"
            for path, auth in calls
            if path != "/artifact.zip"
        )

        manifest_id = created["manifest_id"]
        item_id = items[0]["item_id"]
        connection = http.client.HTTPConnection("127.0.0.1", api_server.server_address[1], timeout=10)
        connection.request(
            "PUT",
            f"/api/manifests/{manifest_id}/items/{item_id}/annotation",
            body=json.dumps({
                "version": "2.4.0",
                "shapes": [{"label": "scratch", "shape_type": "rectangle", "points": [[1, 2], [12, 13]]}],
            }),
            headers={**headers, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["shape_count"] == 1
        connection.request(
            "PUT",
            f"/api/manifests/{manifest_id}/items/{item_id}/classification",
            body=json.dumps({"label": "NG"}),
            headers={**headers, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["classification"] == "NG"

        connection.request(
            "POST",
            f"/api/manifests/{manifest_id}/iwisc/complete",
            body=json.dumps({"annotated_by": "worker-e2e"}),
            headers={**headers, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        completed = json.loads(response.read())
        assert response.status == 200
        assert completed["task"]["ant_active"] == 2
        assert completed["task"]["annotated_by"] == "worker-e2e"
        assert completed["delivery"] == {"status": "unsupported", "error": False}
        task = service._iwisc_domain_service().get_task(created["iwisc_task_id"])
        bundle = task["annotation_json"]["react_sidecars"]
        assert bundle["format"] == "annotation-react-sidecar-bundle/v1"
        assert bundle["items"][item_id]["annotation"]["shapes"][0]["label"] == "scratch"
        assert bundle["items"][item_id]["classification"] == "NG"

        connection.request(
            "PUT",
            f"/api/manifests/{manifest_id}/items/{item_id}/annotation",
            body=json.dumps({"version": "2.4.0", "shapes": []}),
            headers={**headers, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 409
        assert "read-only" in json.loads(response.read())["error"]
        connection.request(
            "PUT",
            f"/api/manifests/{manifest_id}/items/{item_id}/review",
            body=json.dumps({"status": "approved"}),
            headers={**headers, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 409
        response.read()
        connection.close()
        assert service.iwisc_tasks(tenant["tenant_id"]) == []
    finally:
        if api_server is not None:
            api_server.shutdown()
            api_server.server_close()
        if api_thread is not None:
            api_thread.join(timeout=5)
        external.shutdown()
        external.server_close()
        external_thread.join(timeout=5)


def test_loopback_server_requires_session_token_and_rejects_other_origins(service_and_dataset, tmp_path: Path):
    service, _source, _created = service_and_dataset
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>ANnoTation</main>", encoding="utf-8")
    origin = "http://127.0.0.1:5173"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(service, "test-session-token", static_dir, {origin}),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        assert b"ANnoTation" in response.read()

        connection.request("GET", "/api/session", headers={"Origin": origin})
        response = connection.getresponse()
        session = json.loads(response.read())
        assert response.status == 200
        assert session["token"] == "test-session-token"

        connection.request("GET", "/api/manifests", headers={"Origin": origin})
        response = connection.getresponse()
        response.read()
        assert response.status == 401

        connection.request(
            "GET",
            "/api/manifests",
            headers={"Origin": origin, "X-Annotation-Token": session["token"]},
        )
        response = connection.getresponse()
        manifests = json.loads(response.read())
        assert response.status == 200
        assert len(manifests["manifests"]) == 1

        remote_query = urlencode({"service_url": "file:///not-a-service"})
        connection.request(
            "GET",
            f"/api/sources/remote?{remote_query}",
            headers={"Origin": origin, "X-Annotation-Token": session["token"]},
        )
        response = connection.getresponse()
        remote_error = json.loads(response.read())
        assert response.status == 400
        assert "valid remote Service URL" in remote_error["error"]

        connection.request(
            "GET",
            "/api/session",
            headers={"Origin": "https://untrusted.example"},
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 403
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
