from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .service import AnnotationService, ServiceError


_MAX_BODY = 10 * 1024 * 1024
_DEFAULT_DEV_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


def make_handler(
    service: AnnotationService,
    token: str,
    static_dir: Path,
    allowed_origins: set[str],
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ANnoTationWeb/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("[annotation-web] " + (fmt % args) + "\n")

        def _origin(self) -> str:
            return self.headers.get("Origin", "")

        def _check_host(self) -> bool:
            host = self.headers.get("Host", "").lower().split(":", 1)[0].strip("[]")
            return host in {"localhost", "127.0.0.1", "::1", "tauri.localhost"}

        def _check_origin(self) -> bool:
            origin = self._origin()
            if not origin:
                return True
            if origin in allowed_origins:
                return True
            if origin == f"http://{self.headers.get('Host', '')}" and self._check_host():
                return True
            return origin in {"tauri://localhost", "http://tauri.localhost"}

        def _cors_headers(self) -> None:
            origin = self._origin()
            if origin and self._check_origin():
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: dict[str, Any]) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ServiceError(400, "Invalid request length") from exc
            if length < 0 or length > _MAX_BODY:
                raise ServiceError(413, "Request body is too large")
            try:
                value = json.loads(self.rfile.read(length) or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ServiceError(400, "Request body must be valid JSON") from exc
            if not isinstance(value, dict):
                raise ServiceError(400, "Request body must be a JSON object")
            return value

        def _authorize(self) -> bool:
            if not self._check_host():
                self._json(403, {"error": "Requests must use the local ANnoTation host"})
                return False
            if not self._check_origin():
                self._json(403, {"error": "This browser origin is not allowed"})
                return False
            if not hmac.compare_digest(self.headers.get("X-Annotation-Token", ""), token):
                self._json(401, {"error": "Missing or invalid local session token"})
                return False
            return True

        def _segments(self) -> tuple[list[str], dict[str, list[str]]]:
            parsed = urlsplit(self.path)
            return [unquote(part) for part in parsed.path.strip("/").split("/") if part], parse_qs(parsed.query)

        def _static(self, url_path: str) -> None:
            if url_path in {"", "/"}:
                relative = "index.html"
            else:
                relative = unquote(url_path.lstrip("/"))
            root = static_dir.resolve()
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                self._json(404, {"error": "Not found"})
                return
            if not candidate.is_file():
                candidate = root / "index.html"
            if not candidate.is_file():
                self._json(503, {"error": "Build the React app with npm run build before launching the local server"})
                return
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self._send(200, candidate.read_bytes(), content_type)

        def do_OPTIONS(self) -> None:
            if not self._check_host() or not self._check_origin():
                self._json(403, {"error": "This browser origin is not allowed"})
                return
            self.send_response(204)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Annotation-Token")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_GET(self) -> None:
            parts, query = self._segments()
            if parts == ["api", "health"]:
                self._json(200, {"ok": True, "app": "annotation"})
                return
            if parts == ["api", "session"]:
                if not self._check_host() or not self._check_origin():
                    self._json(403, {"error": "This browser origin is not allowed"})
                    return
                self._json(200, {"token": token})
                return
            if not parts or parts[0] != "api":
                if not self._check_host() or not self._check_origin():
                    self._json(403, {"error": "This browser origin is not allowed"})
                    return
                self._static(urlsplit(self.path).path)
                return
            if not self._authorize():
                return
            try:
                if parts == ["api", "sources", "remote"]:
                    service_url = query.get("service_url", [""])[0]
                    self._json(200, {"datasets": service.remote_datasets(service_url)})
                    return
                if parts == ["api", "manifests"]:
                    self._json(200, {"manifests": service.list_manifests()})
                    return
                if len(parts) >= 4 and parts[1] == "manifests":
                    manifest_id = parts[2]
                    if parts[3] == "items" and len(parts) == 4:
                        view = query.get("view", ["workspace"])[0]
                        filters = {key: values[0] for key, values in query.items()}
                        self._json(200, {"items": service.items(manifest_id, view, filters)})
                        return
                    if parts[3] == "items" and len(parts) == 5:
                        self._json(200, service.item_detail(manifest_id, parts[4]))
                        return
                    if parts[3] == "items" and len(parts) == 6 and parts[5] == "image":
                        image, content_type = service.image(manifest_id, parts[4])
                        self._send(200, image, content_type)
                        return
                    if parts[3] == "workspace" and len(parts) == 4:
                        self._json(200, service.workspace(manifest_id))
                        return
                    if parts[3] == "labels" and len(parts) == 4:
                        self._json(200, service.labels(manifest_id))
                        return
                    if parts[3] == "exports" and len(parts) == 4:
                        self._json(200, {"exports": service.exports(manifest_id)})
                        return
                self._json(404, {"error": "Not found"})
            except ServiceError as exc:
                self._json(exc.status, {"error": exc.message})
            except Exception:
                self._json(500, {"error": "The local ANnoTation service could not complete the request"})

        def do_POST(self) -> None:
            if not self._authorize():
                return
            parts, _query = self._segments()
            try:
                body = self._read_json()
                if parts == ["api", "manifests"]:
                    self._json(201, service.create_dataset(body))
                    return
                if parts == ["api", "workspace"]:
                    self._json(200, service.workspace(str(body.get("manifest_id") or ""), body))
                    return
                if len(parts) == 5 and parts[1] == "manifests" and parts[3] == "labels":
                    self._json(200, service.update_labels(parts[2], parts[4], body))
                    return
                if len(parts) == 4 and parts[1] == "manifests" and parts[3] == "exports":
                    self._json(200, service.export(parts[2], body))
                    return
                if len(parts) == 4 and parts[1] == "manifests" and parts[3] == "prelabel":
                    self._json(200, service.run_yolo(parts[2], body))
                    return
                if len(parts) == 6 and parts[1] == "manifests" and parts[3] == "items" and parts[5] == "launch":
                    self._json(
                        200,
                        service.launch_tool(
                            parts[2],
                            parts[4],
                            str(body.get("tool") or ""),
                            str(body.get("executable_override") or ""),
                        ),
                    )
                    return
                self._json(404, {"error": "Not found"})
            except ServiceError as exc:
                self._json(exc.status, {"error": exc.message})
            except Exception:
                self._json(500, {"error": "The local ANnoTation service could not complete the request"})

        def do_PUT(self) -> None:
            if not self._authorize():
                return
            parts, _query = self._segments()
            try:
                body = self._read_json()
                if len(parts) == 6 and parts[1] == "manifests" and parts[3] == "items":
                    manifest_id, item_id, action = parts[2], parts[4], parts[5]
                    if action == "annotation":
                        self._json(200, service.save_annotation(manifest_id, item_id, body))
                        return
                    if action == "classification":
                        self._json(200, service.save_classification(manifest_id, item_id, str(body.get("label") or "")))
                        return
                    if action == "review":
                        self._json(
                            200,
                            service.review(
                                manifest_id,
                                item_id,
                                str(body.get("status") or ""),
                                str(body.get("comment") or ""),
                            ),
                        )
                        return
                self._json(404, {"error": "Not found"})
            except ServiceError as exc:
                self._json(exc.status, {"error": exc.message})
            except Exception:
                self._json(500, {"error": "The local ANnoTation service could not complete the request"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local ANnoTation React web app and API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--app-data-dir", type=Path)
    parser.add_argument("--web-dir", type=Path, default=Path(__file__).resolve().parents[1] / "web" / "dist")
    parser.add_argument("--token", default="")
    parser.add_argument("--allowed-origin", action="append", default=[])
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("The local web API must bind to a loopback address")
    service = AnnotationService(args.app_data_dir)
    token = args.token or secrets.token_urlsafe(32)
    origins = _DEFAULT_DEV_ORIGINS | set(args.allowed_origin)
    static_dir = args.web_dir.expanduser().resolve()
    handler = make_handler(service, token, static_dir, origins)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.daemon_threads = True
    port = server.server_address[1]
    print(json.dumps({"event": "ready", "host": args.host, "port": port, "token": token}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
