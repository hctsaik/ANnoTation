from __future__ import annotations

import hashlib
import http.client
import io
import json
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
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


def test_lv_duplicate_handoff_pair_context_is_path_free_and_survives_restart(tmp_path: Path):
    app_data = tmp_path / "app-data"
    images = tmp_path / "lv_labeling_handoff" / "duplicate_batch" / "images"
    images.mkdir(parents=True)
    pair_paths = [images / "first.png", images / "second.png"]
    Image.new("RGB", (20, 16), "#aa2244").save(pair_paths[0])
    Image.new("RGB", (20, 16), "#22aa44").save(pair_paths[1])
    shas = [hashlib.sha256(path.read_bytes()).hexdigest() for path in pair_paths]
    renamed_paths = [images / f"{sha}.png" for sha in shas]
    for path, renamed in zip(pair_paths, renamed_paths):
        path.replace(renamed)
    pair_paths = renamed_paths
    spec = {
        "handoff_id": "duplicate_batch",
        "version": 1,
        "source": "duplicate",
        "task": "verify",
        "instructions": "Review the two images; never delete them automatically.",
        "payload": {
            "kind": "duplicate_pair_review",
            "method": "embedding",
            "cross_split_only": True,
            "pairs": [{
                "left_sha256": shas[0],
                "right_sha256": shas[1],
                "left_filename": "C:\\private\\panel-train.png",
                "right_filename": "panel-test.png",
                "left_split": "train",
                "right_split": "test",
                "distance": 0.0125,
            }],
        },
        "items": [
            {"sha256": shas[0], "filename": "panel-train.png"},
            {"sha256": shas[1], "filename": "panel-test.png"},
        ],
    }
    (images.parent / "_handoff.json").write_text(json.dumps(spec), encoding="utf-8")
    service = AnnotationService(app_data)
    created = service.create_dataset({
        "folder_path": str(images), "manifest_name": "Duplicate pair review", "recursive": True,
    })

    first_workspace = service.workspace(created["manifest_id"])
    context = first_workspace["handoff_context"]
    assert context["kind"] == "duplicate_pair_review"
    assert context["task"] == "verify"
    assert context["method"] == "embedding"
    assert context["cross_split_only"] is True
    assert context["pairs"] == [{
        "left_item_id": next(item["item_id"] for item in first_workspace["items"] if item["name"] == pair_paths[0].name),
        "right_item_id": next(item["item_id"] for item in first_workspace["items"] if item["name"] == pair_paths[1].name),
        "left_filename": "panel-train.png",
        "right_filename": "panel-test.png",
        "left_split": "train",
        "right_split": "test",
        "distance": 0.0125,
    }]
    assert context["instructions"] == "Review the two images; never delete them automatically."
    assert str(tmp_path) not in json.dumps(first_workspace)

    reopened = AnnotationService(app_data).workspace(created["manifest_id"])
    assert reopened["handoff_context"] == context


def test_local_dataset_scans_supported_formats_recursively_or_root_only(
    tmp_path: Path,
):
    source = tmp_path / "format-matrix"
    nested = source / "nested"
    nested.mkdir(parents=True)
    formats = {
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".bmp": "BMP",
        ".webp": "WEBP",
        ".tif": "TIFF",
        ".tiff": "TIFF",
    }
    for index, (extension, image_format) in enumerate(formats.items()):
        Image.new("RGB", (16, 12), (20 + index * 20, 90, 160)).save(
            source / f"root-{index}{extension}", format=image_format,
        )
    Image.new("RGB", (16, 12), "#aa5500").save(source / "upper.PNG", format="PNG")
    Image.new("RGB", (16, 12), "#0055aa").save(nested / "nested.jpeg", format="JPEG")
    (source / "ignore.gif").write_bytes(b"not part of the supported image matrix")
    (source / "notes.txt").write_text("ignore this", encoding="utf-8")
    source_files = [path for path in source.rglob("*") if path.is_file()]
    source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    service = AnnotationService(tmp_path / "format-matrix-app-data")

    recursive = service.create_dataset({
        "folder_path": str(source), "manifest_name": "All formats recursive", "recursive": True,
    })
    recursive_items = manifest_db.get_manifest_items(service.db_path, recursive["manifest_id"])
    recursive_names = {Path(item["file_path"]).relative_to(source).as_posix() for item in recursive_items}
    expected_recursive = {f"root-{index}{extension}" for index, extension in enumerate(formats)}
    expected_recursive |= {"upper.PNG", "nested/nested.jpeg"}
    assert recursive["item_count"] == len(expected_recursive) == 9
    assert recursive_names == expected_recursive
    assert {(item["width"], item["height"]) for item in recursive_items} == {(16, 12)}

    root_only = service.create_dataset({
        "folder_path": str(source), "manifest_name": "All formats root only", "recursive": False,
    })
    root_items = manifest_db.get_manifest_items(service.db_path, root_only["manifest_id"])
    root_names = {Path(item["file_path"]).relative_to(source).as_posix() for item in root_items}
    assert root_only["item_count"] == len(expected_recursive) - 1
    assert root_names == expected_recursive - {"nested/nested.jpeg"}
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files} == source_hashes


def test_tiff_browser_preview_is_png_and_preserves_source(tmp_path: Path):
    source = tmp_path / "tiff-preview"
    source.mkdir()
    tiff_path = source / "panel.tiff"
    Image.new("RGB", (16, 12), "#335577").save(tiff_path, format="TIFF")
    source_hash = hashlib.sha256(tiff_path.read_bytes()).hexdigest()
    service = AnnotationService(tmp_path / "tiff-preview-app-data")
    created = service.create_dataset({
        "folder_path": str(source), "manifest_name": "TIFF browser preview", "recursive": True,
    })
    item = manifest_db.get_manifest_items(service.db_path, created["manifest_id"])[0]

    image_bytes, content_type = service.image(created["manifest_id"], item["item_id"])

    assert content_type == "image/png"
    assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(image_bytes)) as preview:
        assert preview.format == "PNG"
        assert preview.size == (16, 12)
    assert hashlib.sha256(tiff_path.read_bytes()).hexdigest() == source_hash


def test_iwisc_result_delivery_registration_is_opt_in_and_redacted(service_and_dataset):
    service, _source, _created = service_and_dataset
    connection = service.register_iwisc_tenant({
        "system_name": "Configured result endpoint",
        "server_host_name": "https://tasks.example",
        "target_format": "coco",
        "api_token": "private-token",
        "result_path": "/tasks/{ant_id}/results/{task_id}",
        "result_method": "patch",
    })

    assert connection["result_delivery_configured"] is True
    assert "private-token" not in json.dumps(connection)
    stored = service._iwisc_domain_service().get_tenant(connection["tenant_id"])
    assert stored["connector_config"] == {
        "result_path": "/tasks/{ant_id}/results/{task_id}",
        "result_method": "PATCH",
    }

    with pytest.raises(ServiceError) as error:
        service.register_iwisc_tenant({
            "system_name": "Invalid result endpoint",
            "server_host_name": "https://tasks.example",
            "target_format": "coco",
            "result_path": "https://other.example/submit",
        })
    assert error.value.status == 400


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


def test_react_tool_launch_api_passes_granted_item_to_registered_adapter(
    service_and_dataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    service, source, created = service_and_dataset
    item_id = manifest_db.get_manifest_items(service.db_path, created["manifest_id"])[0]["item_id"]
    executable = tmp_path / "labelme.exe"
    executable.write_bytes(b"fixture executable")
    calls: list[tuple[str, dict]] = []

    class Adapter:
        def launch_file(self, file_path: str, options: dict) -> None:
            calls.append((file_path, options))
            return None

    class Registry:
        def get(self, tool_id: str):
            assert tool_id == "labelme"
            return None, Adapter()

    monkeypatch.setattr("domain.tools.registry.get_tool_registry", lambda: Registry())
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    origin = "http://127.0.0.1:5173"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(service, "tool-session", static_dir, {origin}),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request(
            "POST",
            f"/api/manifests/{created['manifest_id']}/items/{item_id}/launch",
            body=json.dumps({"tool": "labelme", "executable_override": str(executable)}),
            headers={
                "Origin": origin,
                "X-Annotation-Token": "tool-session",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        body = response.read()
        result = json.loads(body)

        assert response.status == 200
        assert result == {"launched": True, "tool": "labelme", "item_id": item_id}
        assert str(source) not in body.decode("utf-8")
        assert calls == [
            (str((source / "panel-a.png").resolve()), {"executable_override": str(executable.resolve())})
        ]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_yolo_prelabel_skips_existing_sidecar_without_loading_model(service_and_dataset, tmp_path, monkeypatch):
    service, source, created = service_and_dataset
    image_path = source / "panel-a.png"
    sidecar = image_path.with_suffix(".json")
    original = '{"shapes":[{"label":"human","shape_type":"rectangle"}]}\n'
    sidecar.write_text(original, encoding="utf-8")
    model_path = tmp_path / "local-model.pt"
    model_path.write_bytes(b"test model placeholder")

    def unexpected_model_load(_path):
        pytest.fail("all existing annotations are skipped, so the YOLO model must not load")

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=unexpected_model_load))

    result = service.run_yolo(created["manifest_id"], {"model_path": str(model_path)})

    assert result == {"ok": 0, "skipped": 1, "errors": 0, "detected": 0}
    assert sidecar.read_text(encoding="utf-8") == original


def test_uploaded_yolo_model_is_app_scoped_and_loads_by_content_reference(service_and_dataset, monkeypatch):
    service, _source, created = service_and_dataset
    model_bytes = b"fixture yolo model bytes"
    stored = service.store_yolo_model(
        "C:\\private\\weights\\custom.pt", io.BytesIO(model_bytes).read, len(model_bytes)
    )
    model_hash = hashlib.sha256(model_bytes).hexdigest()
    model_path = service.data_dir / "models" / "yolo" / f"{model_hash}.pt"
    loaded_paths: list[str] = []

    class FakeYolo:
        names = {0: "scratch"}

        def __init__(self, path: str):
            loaded_paths.append(path)

        def __call__(self, _image: str, *, conf: float, verbose: bool):
            assert conf == pytest.approx(0.25)
            assert verbose is False
            return [SimpleNamespace(orig_shape=(24, 32), boxes=[])]

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYolo))
    item_id = manifest_db.get_manifest_items(service.db_path, created["manifest_id"])[0]["item_id"]
    result = service.run_yolo(
        created["manifest_id"],
        {"model_path": stored["model_ref"], "item_ids": [item_id]},
    )

    assert stored == {
        "model_ref": f"local-model:{model_hash}.pt",
        "name": "custom.pt",
        "size_bytes": len(model_bytes),
    }
    assert model_path.read_bytes() == model_bytes
    assert str(service.data_dir) not in json.dumps(stored)
    assert loaded_paths == [str(model_path.resolve())]
    assert result == {"ok": 1, "skipped": 0, "errors": 0, "detected": 0}


def test_uploaded_yolo_model_rejects_unsupported_suffix_and_oversize(service_and_dataset):
    service, _source, _created = service_and_dataset
    with pytest.raises(ServiceError) as unsupported:
        service.store_yolo_model("weights.txt", io.BytesIO(b"x").read, 1)
    assert unsupported.value.status == 415

    with pytest.raises(ServiceError) as too_large:
        service.store_yolo_model(
            "weights.pt", lambda _size: pytest.fail("oversized body must not be read"), 256 * 1024 * 1024 + 1
        )
    assert too_large.value.status == 413


def test_yolo_prelabel_overwrite_uses_confidence_and_writes_boxes(service_and_dataset, tmp_path, monkeypatch):
    service, source, created = service_and_dataset
    image_path = source / "panel-a.png"
    sidecar = image_path.with_suffix(".json")
    sidecar.write_text('{"flags":{"keep":true},"shapes":[{"label":"old"}]}', encoding="utf-8")
    model_path = tmp_path / "local-model.pt"
    model_path.write_bytes(b"test model placeholder")
    observed = {}

    class FakeModel:
        names = {0: "scratch"}

        def __call__(self, path, *, conf, verbose):
            observed.update(path=path, confidence=conf, verbose=verbose)
            box = SimpleNamespace(xyxy=[[2, 3, 14, 17]], cls=[0], conf=[0.87654])
            return [SimpleNamespace(orig_shape=(24, 32), boxes=[box])]

    def load_model(path):
        observed["model_path"] = path
        return FakeModel()

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=load_model))
    before = image_path.read_bytes()

    result = service.run_yolo(created["manifest_id"], {
        "model_path": str(model_path), "confidence": 0.42, "overwrite": True,
    })

    assert result == {"ok": 1, "skipped": 0, "errors": 0, "detected": 1}
    assert observed == {
        "model_path": str(model_path.resolve()),
        "path": str(image_path),
        "confidence": 0.42,
        "verbose": False,
    }
    saved = json.loads(sidecar.read_text(encoding="utf-8"))
    assert saved["flags"] == {"keep": True}
    assert saved["imageWidth"] == 32 and saved["imageHeight"] == 24
    assert saved["shapes"] == [{
        "label": "scratch", "score": 0.8765, "points": [[2.0, 3.0], [14.0, 17.0]],
        "group_id": None, "description": "", "difficult": False,
        "shape_type": "rectangle", "flags": {},
    }]
    assert image_path.read_bytes() == before


def test_yolo_prelabel_reports_malformed_model(service_and_dataset, tmp_path, monkeypatch):
    service, _source, created = service_and_dataset
    model_path = tmp_path / "malformed-model.pt"
    model_path.write_bytes(b"not a model")

    def fail_to_load(_path):
        raise ValueError("invalid checkpoint")

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=fail_to_load))

    with pytest.raises(ServiceError, match="Could not load the selected local YOLO model") as error:
        service.run_yolo(created["manifest_id"], {"model_path": str(model_path)})

    assert error.value.status == 503


def test_yolo_prelabel_persists_empty_prediction(service_and_dataset, tmp_path, monkeypatch):
    service, source, created = service_and_dataset
    model_path = tmp_path / "local-model.pt"
    model_path.write_bytes(b"test model placeholder")

    class EmptyModel:
        names = {}

        def __call__(self, _path, *, conf, verbose):
            assert conf == 0.25 and verbose is False
            return [SimpleNamespace(orig_shape=(24, 32), boxes=[])]

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda _path: EmptyModel()))

    result = service.run_yolo(created["manifest_id"], {"model_path": str(model_path)})

    assert result == {"ok": 1, "skipped": 0, "errors": 0, "detected": 0}
    saved = json.loads((source / "panel-a.json").read_text(encoding="utf-8"))
    assert saved["shapes"] == []
    assert saved["imageWidth"] == 32 and saved["imageHeight"] == 24


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
