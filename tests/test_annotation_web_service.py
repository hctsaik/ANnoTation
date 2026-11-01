from __future__ import annotations

import json
import http.client
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

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
