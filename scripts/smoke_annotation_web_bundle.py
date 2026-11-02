"""Launch the frozen Windows service and verify React, API, and local ingestion."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image


def _read_startup_line(process: subprocess.Popen[str], timeout: float) -> dict:
    if process.stdout is None:
        raise RuntimeError("service stdout pipe was not created")
    lines: queue.Queue[str] = queue.Queue(maxsize=1)
    reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
    reader.start()
    try:
        line = lines.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"service did not announce readiness within {timeout:g} seconds") from exc
    if not line:
        raise RuntimeError(f"service exited before readiness (return code {process.poll()})")
    value = json.loads(line)
    if value.get("event") != "ready" or not isinstance(value.get("port"), int):
        raise RuntimeError(f"invalid service readiness message: {value!r}")
    return value


def _request(
    url: str, *, token: str = "", body: dict | None = None, method: str | None = None,
) -> tuple[int, bytes, dict]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Annotation-Token"] = token
    data = None
    request_method = method or "GET"
    if body is not None:
        request_method = method or "POST"
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=request_method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def _start_service(executable: Path, package: Path, static_dir: Path, app_data: Path, timeout: float):
    process = subprocess.Popen(
        [str(executable), "--host", "127.0.0.1", "--port", "0",
         "--app-data-dir", str(app_data), "--web-dir", str(static_dir)],
        cwd=package,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        ready = _read_startup_line(process, timeout)
        session_status, session_body, _ = _request(
            f"http://127.0.0.1:{ready['port']}/api/session",
        )
        if session_status != 200:
            raise RuntimeError(f"session endpoint failed: {session_body!r}")
        token = str(json.loads(session_body)["token"])
        return process, f"http://127.0.0.1:{ready['port']}", token
    except Exception:
        process.kill()
        process.wait(timeout=5)
        raise


def _stop_service(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path("dist/annotation-web"))
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    args = parser.parse_args()

    package = args.package.resolve()
    executable = package / "annotation-service" / "annotation-service.exe"
    static_dir = package / "annotation-service" / "_internal" / "web" / "dist"
    if os.name != "nt" or not executable.is_file() or not (static_dir / "index.html").is_file():
        raise SystemExit(f"Expected a built Windows package at {package}")
    internal = package / "annotation-service" / "_internal"
    for optional_module in ("ultralytics", "torch", "torchvision", "cv2", "numpy", "cryptography", "psutil"):
        if (internal / optional_module).exists():
            raise SystemExit(f"Optional build-host module leaked into base bundle: {optional_module}")
    package_manifest = json.loads((package / "annotation-package.json").read_text(encoding="utf-8"))
    if package_manifest.get("ai_runtime_included") is not False:
        raise SystemExit("Base bundle must explicitly exclude the optional AI runtime")

    with tempfile.TemporaryDirectory(prefix="annotation-bundle-smoke-") as temp_name:
        temp = Path(temp_name)
        source = temp / "images"
        source.mkdir()
        Image.new("RGB", (19, 13), (40, 100, 160)).save(source / "sample.png")
        app_data = temp / "app-data"
        process = None
        try:
            process, base, token = _start_service(
                executable, package, static_dir, app_data, args.startup_timeout,
            )

            status, health_body, _ = _request(f"{base}/api/health")
            assert status == 200 and json.loads(health_body).get("app") == "annotation", health_body
            status, html, headers = _request(base + "/")
            assert status == 200 and "text/html" in headers.get("Content-Type", ""), headers
            assert b"<html" in html.lower(), "bundled React entry page was not served"
            status, dataset_body, _ = _request(
                f"{base}/api/manifests", token=token,
                body={"mode": "local", "folder_path": str(source), "manifest_name": "Frozen smoke"},
            )
            dataset = json.loads(dataset_body)
            assert status == 201 and dataset.get("item_count") == 1, dataset
            status, item_body, _ = _request(
                f"{base}/api/manifests/{dataset['manifest_id']}/items?view=workspace", token=token,
            )
            items = json.loads(item_body).get("items", [])
            assert status == 200 and len(items) == 1 and items[0]["name"] == "sample.png", items
            item_id = items[0]["item_id"]
            model_path = temp / "optional-model.pt"
            model_path.write_bytes(b"optional AI runtime smoke fixture")
            status, unavailable_body, _ = _request(
                f"{base}/api/manifests/{dataset['manifest_id']}/prelabel",
                token=token,
                body={"model_path": str(model_path), "confidence": 0.25},
            )
            unavailable = json.loads(unavailable_body)
            assert status == 503 and "Could not load" in unavailable.get("error", ""), unavailable
            status, saved_body, _ = _request(
                f"{base}/api/manifests/{dataset['manifest_id']}/items/{item_id}/annotation",
                token=token,
                method="PUT",
                body={
                    "version": "2.4.0",
                    "shapes": [{
                        "label": "smoke", "shape_type": "rectangle",
                        "points": [[1, 2], [12, 10]],
                    }],
                },
            )
            assert status == 200 and json.loads(saved_body).get("shape_count") == 1, saved_body

            _stop_service(process)
            process, base, token = _start_service(
                executable, package, static_dir, app_data, args.startup_timeout,
            )
            status, manifests_body, _ = _request(f"{base}/api/manifests", token=token)
            manifests = json.loads(manifests_body).get("manifests", [])
            assert status == 200 and any(
                row.get("manifest_id") == dataset["manifest_id"] for row in manifests
            ), manifests
            status, reopened_body, _ = _request(
                f"{base}/api/manifests/{dataset['manifest_id']}/items/{item_id}", token=token,
            )
            reopened = json.loads(reopened_body)
            assert status == 200 and reopened["annotation"]["shapes"][0]["label"] == "smoke", reopened
            print(json.dumps({
                "result": "passed",
                "reopened_port": int(base.rsplit(":", 1)[1]),
                "dataset_items": len(items),
                "static_ui": True,
                "local_ingestion": True,
                "annotation_saved_and_read_after_new_process": True,
                "optional_ai_runtime_excluded": True,
                "optional_ai_request_reports_unavailable": True,
            }, indent=2))
        finally:
            if process is not None:
                _stop_service(process)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
