from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import math
import mimetypes
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4

from PIL import Image

from annotation_runtime import data_root
from annotation_runtime import _manifest_db as manifest_db
from domain.integrations.configuration import normalize_result_delivery_config


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")
EXPORT_FORMATS = {"coco_json", "yolo_txt", "pascal_voc", "imagefolder", "csv"}
TOOLS = {"x-anylabeling", "labelme", "isat"}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_EXTERNAL_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LOCAL_YOLO_MODEL_REF = re.compile(r"^local-model:([0-9a-f]{64})(\.(?:pt|onnx|engine))$")
_YOLO_MODEL_SUFFIXES = {".pt", ".onnx", ".engine"}
MAX_LOCAL_MODEL_BYTES = 256 * 1024 * 1024


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load application module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class AnnotationService:
    """Local application services used by the React UI and the desktop host."""

    def __init__(self, app_data_dir: Path | str | None = None):
        if app_data_dir is not None:
            os.environ["APP_DATA_DIR"] = str(Path(app_data_dir).expanduser().resolve())
        self.data_dir = data_root()
        self.db_path = self.data_dir / "db" / "annotation-standalone-v1.sqlite"
        self.repo_root = Path(__file__).resolve().parents[1]
        if str(self.repo_root) not in sys.path:
            sys.path.insert(0, str(self.repo_root))
        self._instance = uuid4().hex[:12]
        self._modules: dict[str, ModuleType] = {}

    def _process(self, module_id: str) -> ModuleType:
        if module_id not in self._modules:
            path = self.repo_root / "modules" / f"module_{module_id}" / f"{module_id}_process.py"
            self._modules[module_id] = _load_module(
                f"annotation_web_{module_id}_{self._instance}", path
            )
        return self._modules[module_id]

    def _manifest(self, manifest_id: str) -> dict[str, Any]:
        if not _ID_PATTERN.fullmatch(manifest_id):
            raise ServiceError(400, "Invalid dataset identifier")
        manifest = manifest_db.get_manifest(self.db_path, manifest_id)
        if manifest is None:
            raise ServiceError(404, "Dataset not found")
        return manifest

    def _raw_item(self, manifest_id: str, item_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest = self._manifest(manifest_id)
        if not _ID_PATTERN.fullmatch(item_id):
            raise ServiceError(400, "Invalid image identifier")
        for item in manifest_db.get_manifest_items(self.db_path, manifest_id):
            if item.get("item_id") == item_id:
                return manifest, item
        raise ServiceError(404, "Image not found in this dataset")

    def _local_item_path(self, manifest: dict[str, Any], item: dict[str, Any]) -> Path:
        raw = str(item.get("file_path") or "")
        if not raw or urlparse(raw).scheme in {"http", "https"}:
            raise ServiceError(415, "This image source is not available as a local file")
        path = Path(raw).expanduser().resolve()
        source = manifest.get("source_config") or {}
        root_value = source.get("folder_path") or source.get("root")
        if root_value:
            root = Path(root_value).expanduser().resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ServiceError(403, "Image is outside the dataset grant") from exc
        if not path.is_file():
            raise ServiceError(404, "Image file is no longer available")
        return path

    @staticmethod
    def _remote_service_url(value: Any) -> str:
        service_url = str(value or "").strip().rstrip("/")
        parsed = urlparse(service_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ServiceError(400, "Enter a valid remote Service URL using HTTP or HTTPS")
        return service_url

    @staticmethod
    def _public_item(item: dict[str, Any], details: dict[str, Any] | None = None) -> dict[str, Any]:
        path = Path(str(item.get("file_path") or ""))
        result = {
            "item_id": str(item.get("item_id") or ""),
            "name": path.name or str(item.get("item_id") or "Image"),
            "width": item.get("width"),
            "height": item.get("height"),
            "image_url": "image",
        }
        if details:
            result.update(details)
        return result

    def list_manifests(self) -> list[dict[str, Any]]:
        rows = manifest_db.list_manifests(self.db_path)
        try:
            preferred = str(self._process("012")._cfg.load_config().get("last_manifest_id") or "")
        except Exception:
            preferred = ""
        if preferred:
            rows.sort(key=lambda row: row.get("manifest_id") != preferred)
        return [
            {
                "manifest_id": row["manifest_id"],
                "name": row["name"],
                "source_type": "iwisc" if row["source_type"] == "iwsc" else row["source_type"],
                "created_at": row["created_at"],
                "item_count": row["item_count"],
            }
            for row in rows
        ]

    def create_dataset(self, params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode", "local")
        if mode == "remote":
            service_url = self._remote_service_url(params.get("service_url"))
            dataset_id = str(params.get("dataset_id") or "").strip()
            dataset_name = str(params.get("dataset_name") or "").strip()
            if not dataset_id:
                raise ServiceError(400, "Choose a remote dataset first")
            result = self._process("026").execute_logic(
                {
                    "mode": "remote",
                    "service_url": service_url,
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "overwrite": bool(params.get("overwrite", False)),
                }
            )
        elif mode == "iwisc":
            tenant_id = str(params.get("tenant_id") or "").strip()
            ant_id = str(params.get("ant_id") or "").strip()
            user_id = str(params.get("user_id") or "").strip()
            if not _ID_PATTERN.fullmatch(tenant_id):
                raise ServiceError(400, "Choose a valid external system")
            if not _EXTERNAL_TASK_ID_PATTERN.fullmatch(ant_id):
                raise ServiceError(400, "Choose a valid external task")
            if not user_id or len(user_id) > 128 or any(ord(char) < 32 for char in user_id):
                raise ServiceError(400, "Enter a valid user ID before claiming this task")
            result = self._process("026").execute_logic(
                {
                    "mode": "iwsc",
                    "tenant_id": tenant_id,
                    "ant_id": ant_id,
                    "user_id": user_id,
                }
            )
        elif mode == "local":
            folder = str(params.get("folder_path") or "").strip()
            if not folder:
                raise ServiceError(400, "Choose a source folder first")
            path = Path(folder).expanduser().resolve()
            if not path.is_dir():
                raise ServiceError(400, "The selected source folder is not available")
            extensions = params.get("extensions") or list(IMAGE_EXTENSIONS)
            extensions = sorted({str(ext).lower() for ext in extensions if str(ext).startswith(".")})
            if not extensions:
                raise ServiceError(400, "Choose at least one image format")
            result = self._process("026").execute_logic(
                {
                    "mode": "local",
                    "folder_path": str(path),
                    "manifest_name": str(params.get("manifest_name") or path.name),
                    "recursive": bool(params.get("recursive", True)),
                    "extensions": extensions,
                }
            )
        else:
            raise ServiceError(400, "Choose a supported dataset source")
        if result.get("mode") != "ready":
            raise ServiceError(502, str(result.get("error") or "Could not create a dataset from this source"))
        seed = result.get("yolo_seed", {})
        return {
            "manifest_id": result["manifest_id"],
            "name": result.get("manifest_name") or str(params.get("manifest_name") or params.get("dataset_name") or "Dataset"),
            "item_count": result.get("total_count", 0),
            "source_type": "iwisc" if mode == "iwisc" else mode,
            "iwisc_ant_id": result.get("iwsc_ant_id"),
            "iwisc_task_id": result.get("iwsc_task_id"),
            "yolo_seed": {
                key: seed[key]
                for key in ("applied", "classes", "reason")
                if key in seed
            },
        }

    def remote_datasets(self, service_url: str) -> list[dict[str, Any]]:
        service_url = self._remote_service_url(service_url)
        try:
            rows = self._process("019").list_datasets(service_url)
        except Exception as exc:
            raise ServiceError(502, f"Could not load remote datasets: {exc}") from exc
        if not isinstance(rows, list):
            raise ServiceError(502, "The remote Service returned an invalid dataset list")
        datasets = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            dataset_id = str(row.get("dataset_id") or "").strip()
            if not dataset_id:
                continue
            try:
                item_count = max(0, int(row.get("item_count") or 0))
            except (TypeError, ValueError):
                item_count = 0
            datasets.append({
                "dataset_id": dataset_id,
                "name": str(row.get("name") or dataset_id),
                "item_count": item_count,
            })
        return datasets

    def _iwisc_domain_service(self):
        try:
            return self._process("026")._get_service()
        except Exception as exc:
            raise ServiceError(500, "The local external-task workspace is not available") from exc

    @staticmethod
    def _public_iwisc_tenant(row: dict[str, Any]) -> dict[str, Any]:
        connector_config = row.get("connector_config")
        return {
            "tenant_id": str(row.get("tenant_id") or ""),
            "system_name": str(row.get("system_name") or ""),
            "server_host_name": str(row.get("server_host_name") or ""),
            "target_format": str(row.get("target_format") or ""),
            "has_api_token": bool(row.get("api_token")),
            "result_delivery_configured": bool(
                isinstance(connector_config, dict) and connector_config.get("result_path")
            ),
        }

    def iwisc_tenants(self) -> list[dict[str, Any]]:
        try:
            rows = self._iwisc_domain_service().list_tenants()
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(502, "Could not load external system connections") from exc
        return [self._public_iwisc_tenant(row) for row in rows]

    def register_iwisc_tenant(self, params: dict[str, Any]) -> dict[str, Any]:
        system_name = str(params.get("system_name") or "").strip()
        server_host_name = self._remote_service_url(params.get("server_host_name"))
        target_format = str(params.get("target_format") or "coco").strip().lower()
        api_token = str(params.get("api_token") or "").strip()
        try:
            delivery_config = normalize_result_delivery_config(
                params.get("result_path"), params.get("result_method", "POST")
            )
        except ValueError as exc:
            raise ServiceError(400, str(exc)) from exc
        if not system_name or len(system_name) > 120:
            raise ServiceError(400, "Enter a connection name (up to 120 characters)")
        if target_format not in {"coco", "yolo-detection", "labelme", "isat"}:
            raise ServiceError(400, "Choose a supported annotation format")
        if len(api_token) > 4096 or any(ord(char) < 32 for char in api_token):
            raise ServiceError(400, "The API key is invalid")
        try:
            row = self._iwisc_domain_service().register_tenant(
                system_name=system_name,
                server_host_name=server_host_name,
                target_format=target_format,
                api_token=api_token or None,
                connector_type="rest",
                connector_config=delivery_config,
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(500, "Could not save the external system connection") from exc
        return self._public_iwisc_tenant(row)

    @staticmethod
    def _safe_external_context(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, str] = {}
        for key, item in list(value.items())[:3]:
            if isinstance(item, (str, int, float, bool)) or item is None:
                rendered = str(item) if item is not None else ""
            else:
                rendered = json.dumps(item, ensure_ascii=False, default=str)
            result[str(key)[:80]] = rendered[:200]
        return result

    def iwisc_tasks(self, tenant_id: str) -> list[dict[str, Any]]:
        if not _ID_PATTERN.fullmatch(str(tenant_id or "")):
            raise ServiceError(400, "Choose a valid external system")
        service = self._iwisc_domain_service()
        try:
            rows = service.get_ant_list(tenant_id)
        except Exception as exc:
            raise ServiceError(502, f"Could not load external tasks: {exc}") from exc
        try:
            local_state = {
                str(row.get("ant_id") or ""): int(row.get("ant_active", 0))
                for row in service.list_tasks(tenant_id)
            }
        except Exception:
            local_state = {}
        tasks = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ant_id = str(row.get("ant_id") or "").strip()
            if not _EXTERNAL_TASK_ID_PATTERN.fullmatch(ant_id):
                continue
            try:
                ant_active = local_state.get(ant_id, int(row.get("ant_active", 0)))
            except (TypeError, ValueError):
                ant_active = 0
            if ant_active == 2:
                continue
            tasks.append({
                "ant_id": ant_id,
                "ant_active": ant_active,
                "ant_period": str(row.get("ant_period") or ""),
                "external_context": self._safe_external_context(row.get("external_context")),
            })
        return tasks

    @staticmethod
    def _iwisc_manifest_ref(manifest: dict[str, Any]) -> dict[str, str] | None:
        source = manifest.get("source_config") or {}
        if manifest.get("source_type") not in {"iwsc", "iwisc"} or not isinstance(source, dict):
            return None
        task_id = str(source.get("task_id") or "").strip()
        tenant_id = str(source.get("tenant_id") or "").strip()
        ant_id = str(source.get("ant_id") or "").strip()
        if not task_id or not _ID_PATTERN.fullmatch(task_id):
            return None
        if not _ID_PATTERN.fullmatch(tenant_id) or not _EXTERNAL_TASK_ID_PATTERN.fullmatch(ant_id):
            raise ServiceError(500, "The linked external task reference is invalid")
        return {"task_id": task_id, "tenant_id": tenant_id, "ant_id": ant_id}

    @staticmethod
    def _public_iwisc_task(task: dict[str, Any]) -> dict[str, Any]:
        delivery_status = task.get("delivery_status")
        return {
            "task_id": str(task.get("task_id") or ""),
            "ant_id": str(task.get("ant_id") or ""),
            "ant_active": int(task.get("ant_active") or 0),
            "annotated_by": str(task.get("annotated_by") or ""),
            "delivery_status": str(delivery_status.get("status") or "")
            if isinstance(delivery_status, dict) else "",
        }

    def _linked_iwisc_task(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        reference = self._iwisc_manifest_ref(manifest)
        if reference is None:
            return None
        try:
            task = self._iwisc_domain_service().get_task(reference["task_id"])
        except Exception as exc:
            raise ServiceError(409, "The linked iWISC task is no longer available") from exc
        if task.get("tenant_id") != reference["tenant_id"] or task.get("ant_id") != reference["ant_id"]:
            raise ServiceError(409, "The linked iWISC task does not match this dataset")
        return self._public_iwisc_task(task)

    def _require_open_iwisc_task(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        reference = self._iwisc_manifest_ref(manifest)
        if reference is None:
            return None
        try:
            task = self._iwisc_domain_service().get_task(reference["task_id"])
        except Exception as exc:
            raise ServiceError(409, "The linked iWISC task is no longer available") from exc
        if task.get("tenant_id") != reference["tenant_id"] or task.get("ant_id") != reference["ant_id"]:
            raise ServiceError(409, "The linked iWISC task does not match this dataset")
        if int(task.get("ant_active") or 0) == 2:
            raise ServiceError(409, "This iWISC task is complete and read-only")
        return task

    def _sync_iwisc_task_bundle(self, manifest_id: str, item_id: str | None = None) -> dict[str, Any] | None:
        manifest = self._manifest(manifest_id)
        reference = self._iwisc_manifest_ref(manifest)
        if reference is None:
            return None
        task = self._require_open_iwisc_task(manifest)
        if task is None:
            return None
        domain_service = self._iwisc_domain_service()

        rows = manifest_db.get_manifest_items(self.db_path, manifest_id)
        if item_id is not None:
            if not _ID_PATTERN.fullmatch(item_id):
                raise ServiceError(400, "Invalid image identifier")
            rows = [row for row in rows if row.get("item_id") == item_id]
            if not rows:
                raise ServiceError(404, "Image not found in this dataset")

        annotation_json = task.get("annotation_json")
        annotation_json = dict(annotation_json) if isinstance(annotation_json, dict) else {}
        prior_bundle = annotation_json.get("react_sidecars")
        sidecar_items = dict(prior_bundle.get("items") or {}) if isinstance(prior_bundle, dict) else {}
        classifications = self._process("012")._cfg.load_classifications(manifest_id)
        for row in rows:
            current_id = str(row.get("item_id") or "")
            path = self._local_item_path(manifest, row)
            annotation = self._read_annotation(str(path.with_suffix(".json")))
            sidecar_items[current_id] = {
                "image_name": path.name,
                "annotation": annotation,
                "classification": str(classifications.get(current_id) or ""),
            }
        annotation_json["react_sidecars"] = {
            "format": "annotation-react-sidecar-bundle/v1",
            "items": sidecar_items,
        }
        try:
            return domain_service.save_annotation(reference["task_id"], annotation_json)
        except Exception as exc:
            raise ServiceError(502, "The image was saved locally, but its iWISC task record could not be updated") from exc

    def complete_iwisc_task(self, manifest_id: str, annotated_by: str) -> dict[str, Any]:
        worker_id = str(annotated_by or "").strip()
        if not worker_id or len(worker_id) > 128 or any(ord(char) < 32 for char in worker_id):
            raise ServiceError(400, "Enter a valid operator ID before completing this task")
        manifest = self._manifest(manifest_id)
        reference = self._iwisc_manifest_ref(manifest)
        if reference is None:
            raise ServiceError(400, "This dataset is not linked to an iWISC task")
        self._require_open_iwisc_task(manifest)
        self._sync_iwisc_task_bundle(manifest_id)
        try:
            result = self._iwisc_domain_service().complete_task(reference["task_id"], worker_id)
        except Exception as exc:
            raise ServiceError(502, "The iWISC task could not be marked complete locally") from exc
        delivery = result.get("delivery") if isinstance(result.get("delivery"), dict) else {}
        status = str(delivery.get("status") or "unknown")
        return {
            "task": self._public_iwisc_task(result),
            "delivery": {
                "status": status,
                "error": status == "error",
            },
        }

    def _duplicate_handoff_context(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        """Read only the typed, path-free pair context beside an LV handoff folder."""
        source = manifest.get("source_config") or {}
        folder_value = source.get("folder_path") if isinstance(source, dict) else None
        if manifest.get("source_type") != "folder" or not folder_value:
            return None
        folder = Path(str(folder_value)).expanduser().resolve()
        if folder.name.lower() != "images":
            return None
        spec_path = folder.parent / "_handoff.json"
        try:
            if spec_path.is_symlink() or not spec_path.is_file() or spec_path.stat().st_size > 2_000_000:
                return None
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(spec, dict) or spec.get("source") != "duplicate" or spec.get("task") != "verify":
            return None
        payload = spec.get("payload")
        if not isinstance(payload, dict) or payload.get("kind") != "duplicate_pair_review":
            return None

        item_ids_by_sha: dict[str, str] = {}
        for item in manifest_db.get_manifest_items(self.db_path, str(manifest.get("manifest_id") or "")):
            sha = Path(str(item.get("file_path") or "")).stem.lower()
            if re.fullmatch(r"[0-9a-f]{64}", sha):
                item_ids_by_sha[sha] = str(item.get("item_id") or "")

        pairs = []
        raw_pairs = payload.get("pairs")
        if not isinstance(raw_pairs, list):
            return None
        for pair in raw_pairs[:200]:
            if not isinstance(pair, dict):
                continue
            left_sha = str(pair.get("left_sha256") or "").lower()
            right_sha = str(pair.get("right_sha256") or "").lower()
            left_id = item_ids_by_sha.get(left_sha)
            right_id = item_ids_by_sha.get(right_sha)
            distance = pair.get("distance")
            if (
                not left_id or not right_id
                or not re.fullmatch(r"[0-9a-f]{64}", left_sha)
                or not re.fullmatch(r"[0-9a-f]{64}", right_sha)
                or isinstance(distance, bool)
                or not isinstance(distance, (int, float))
            ):
                continue
            try:
                normalized_distance = float(distance)
            except OverflowError:
                continue
            if not math.isfinite(normalized_distance):
                continue
            pairs.append({
                "left_item_id": left_id,
                "right_item_id": right_id,
                "left_filename": (
                    str(pair.get("left_filename") or "Image")
                    .replace("\\", "/").rsplit("/", 1)[-1][:255]
                ),
                "right_filename": (
                    str(pair.get("right_filename") or "Image")
                    .replace("\\", "/").rsplit("/", 1)[-1][:255]
                ),
                "left_split": str(pair.get("left_split") or "")[:128],
                "right_split": str(pair.get("right_split") or "")[:128],
                "distance": normalized_distance,
            })
        if not pairs:
            return None
        method = payload.get("method")
        if method not in {"phash", "embedding"}:
            return None
        return {
            "handoff_id": str(spec.get("handoff_id") or "")[:128],
            "task": "verify",
            "kind": "duplicate_pair_review",
            "method": method,
            "cross_split_only": bool(payload.get("cross_split_only")),
            "instructions": str(spec.get("instructions") or "")[:500],
            "pairs": pairs,
        }

    def _sync_duplicate_handoff_answer(
        self, manifest: dict[str, Any], item: dict[str, Any], label: str,
    ) -> None:
        """Publish image-level keep/drop decisions in LV's existing answers.csv contract."""
        source = manifest.get("source_config") or {}
        folder_value = source.get("folder_path") if isinstance(source, dict) else None
        if manifest.get("source_type") != "folder" or not folder_value:
            return
        raw_images_dir = Path(str(folder_value)).expanduser()
        if raw_images_dir.is_symlink():
            return
        images_dir = raw_images_dir.resolve()
        if images_dir.name.lower() != "images":
            return
        handoff_dir = images_dir.parent
        spec_path = handoff_dir / "_handoff.json"
        try:
            if spec_path.is_symlink() or not spec_path.is_file() or spec_path.stat().st_size > 2_000_000:
                return
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        payload = spec.get("payload") if isinstance(spec, dict) else None
        if (
            not isinstance(spec, dict)
            or spec.get("source") != "duplicate"
            or spec.get("task") != "verify"
            or not isinstance(payload, dict)
            or payload.get("kind") != "duplicate_pair_review"
        ):
            return

        sha = Path(str(item.get("file_path") or "")).stem.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            return
        spec_items = spec.get("items")
        if not isinstance(spec_items, list):
            return
        matching_item = next((
            row for row in spec_items
            if isinstance(row, dict) and str(row.get("sha256") or "").lower() == sha
        ), None)
        if matching_item is None:
            return
        expected_image = str(matching_item.get("image") or "").replace("\\", "/")
        expected_parts = Path(expected_image).parts
        if (
            len(expected_parts) != 2
            or expected_parts[0].lower() != "images"
            or Path(expected_parts[1]).name != expected_parts[1]
            or (images_dir / expected_parts[1]).resolve() != Path(str(item.get("file_path") or "")).resolve()
        ):
            return

        answers_path = handoff_dir / "answers.csv"
        if answers_path.is_symlink():
            raise ServiceError(409, "The duplicate-review result file cannot be a symbolic link")
        answers: dict[str, str] = {}
        try:
            if answers_path.is_file():
                if answers_path.stat().st_size > 2_000_000:
                    raise ServiceError(409, "The duplicate-review result file is too large")
                with answers_path.open("r", encoding="utf-8", newline="") as handle:
                    for row in csv.DictReader(handle):
                        row_sha = str(row.get("sha256") or "").lower()
                        row_label = str(row.get("label") or "").strip()
                        if re.fullmatch(r"[0-9a-f]{64}", row_sha) and row_label:
                            answers[row_sha] = row_label
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise ServiceError(409, "The duplicate-review result file could not be read") from exc

        if label:
            answers[sha] = label
        else:
            answers.pop(sha, None)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["sha256", "label"])
        for answer_sha, answer_label in sorted(answers.items()):
            writer.writerow([answer_sha, answer_label])
        _atomic_text(answers_path, output.getvalue())

    def workspace(self, manifest_id: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        manifest = self._manifest(manifest_id)
        cfg = self._process("012")._cfg
        saved = cfg.load_config()
        values = settings or {}
        result = self._process("012").execute_logic(
            {
                "manifest_id": manifest_id,
                "annotation_tool": values.get("annotation_tool", saved.get("annotation_tool", "x-anylabeling")),
                "labels": values.get("labels", saved.get("annotation_labels", [])),
                "classification_labels": values.get(
                    "classification_labels", saved.get("classification_labels", [])
                ),
                "autorefresh_enabled": bool(
                    values.get("autorefresh_enabled", saved.get("autorefresh_enabled", False))
                ),
                "autorefresh_seconds": int(
                    values.get("autorefresh_seconds", saved.get("autorefresh_seconds", 10))
                ),
            }
        )
        if result.get("mode") != "ready":
            raise ServiceError(400, "Could not open the selected dataset")
        classifications = self._process("012")._cfg.load_classifications(manifest_id)
        items = []
        for item in result.get("items", []):
            ann = self._read_annotation(item.get("ann_path") or "")
            review = self._review_data(str(item.get("file_path") or ""))
            items.append(
                self._public_item(
                    item,
                    {
                        "has_annotation": bool(item.get("has_ann")),
                        "has_classification": bool(classifications.get(item.get("item_id", ""))),
                        "has_bbox": bool(item.get("shape_count") or 0),
                        "shape_count": int(item.get("shape_count") or 0),
                        "classification": classifications.get(item.get("item_id", ""), ""),
                        **review,
                        "shapes": ann.get("shapes", []),
                        "max_conf": self._max_confidence(ann.get("shapes", [])),
                    },
                )
            )
        return {
            "manifest_id": manifest_id,
            "manifest_name": result.get("manifest_name", manifest_id),
            "total": int(result.get("total", 0)),
            "annotated": int(result.get("annotated", 0)),
            "pending": max(0, int(result.get("total", 0)) - int(result.get("annotated", 0))),
            "labels": result.get("labels", []),
            "classification_labels": result.get("classification_labels", []),
            "annotation_tool": result.get("annotation_tool", "x-anylabeling"),
            "iwisc_task": self._linked_iwisc_task(manifest),
            "handoff_context": self._duplicate_handoff_context(manifest),
            "items": items,
        }

    def items(self, manifest_id: str, view: str = "workspace", filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self._manifest(manifest_id)
        filters = filters or {}
        if view == "review":
            result = self._process("018").execute_logic(
                {
                    "manifest_id": manifest_id,
                    "filter": filters.get("filter", "全部"),
                    "label_filter": filters.get("label_filter", ""),
                    "review_filter": filters.get("review_filter", "全部"),
                    "cols_count": 3,
                    "show_overlay": True,
                }
            )
            rows = result.get("items", [])
        else:
            cfg = self._process("012")._cfg.load_config()
            result = self._process("012").execute_logic(
                {
                    "manifest_id": manifest_id,
                    "annotation_tool": cfg.get("annotation_tool", "x-anylabeling"),
                    "labels": cfg.get("annotation_labels", []),
                    "classification_labels": cfg.get("classification_labels", []),
                    "autorefresh_enabled": bool(cfg.get("autorefresh_enabled", False)),
                    "autorefresh_seconds": int(cfg.get("autorefresh_seconds", 10)),
                }
            )
            rows = result.get("items", [])
        classifications = self._process("012")._cfg.load_classifications(manifest_id)
        public = []
        for row in rows:
            ann = self._read_annotation(row.get("ann_path") or "")
            shapes = ann.get("shapes", [])
            review = self._review_data(str(row.get("file_path") or ""))
            public.append(
                self._public_item(
                    row,
                    {
                        "has_annotation": bool(row.get("has_ann", row.get("has_json", False))),
                        "has_bbox": bool(row.get("has_bbox", row.get("shape_count", 0))),
                        "has_classification": bool(
                            classifications.get(row.get("item_id", ""))
                            or row.get("has_classification", False)
                        ),
                        "classification": classifications.get(
                            row.get("item_id", ""), row.get("classification", "")
                        ),
                        "shape_count": int(row.get("shape_count") or 0),
                        "labels": row.get("labels") or list(dict.fromkeys(
                            str(shape.get("label") or "") for shape in shapes if shape.get("label")
                        )),
                        "review_status": row.get("review_status") or review["review_status"],
                        "review_comment": review["review_comment"],
                        "max_conf": self._max_confidence(shapes),
                    },
                )
            )
        return public

    def item_detail(self, manifest_id: str, item_id: str) -> dict[str, Any]:
        manifest, item = self._raw_item(manifest_id, item_id)
        path = self._local_item_path(manifest, item)
        annotation_path = path.with_suffix(".json")
        annotation = self._read_annotation(str(annotation_path))
        annotation["imagePath"] = path.name
        annotation["imageData"] = None
        classifications = self._process("012")._cfg.load_classifications(manifest_id)
        return {
            "item": self._public_item(
                item,
                {
                    "has_annotation": annotation_path.is_file(),
                    "has_bbox": any(shape.get("shape_type") == "rectangle" for shape in annotation.get("shapes", [])),
                    "has_classification": bool(classifications.get(item_id, "")),
                    "shape_count": len(annotation.get("shapes", [])),
                    "classification": classifications.get(item_id, ""),
                    **self._review_data(str(path)),
                    "labels": list(dict.fromkeys(
                        str(shape.get("label") or "") for shape in annotation.get("shapes", []) if shape.get("label")
                    )),
                    "max_conf": self._max_confidence(annotation.get("shapes", [])),
                },
            ),
            "annotation": annotation,
        }

    def image(self, manifest_id: str, item_id: str) -> tuple[bytes, str]:
        manifest, item = self._raw_item(manifest_id, item_id)
        path = self._local_item_path(manifest, item)
        if path.suffix.lower() in {".tif", ".tiff"}:
            preview = io.BytesIO()
            try:
                with Image.open(path) as source:
                    source.seek(0)
                    source.convert("RGB").save(preview, format="PNG")
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise ServiceError(422, "The selected image could not be previewed") from exc
            return preview.getvalue(), "image/png"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return path.read_bytes(), content_type

    @staticmethod
    def _read_annotation(path: str) -> dict[str, Any]:
        if not path:
            return {"version": "2.4.0", "flags": {}, "shapes": []}
        annotation_path = Path(path)
        try:
            value = json.loads(annotation_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return {"flags": {}, "shapes": []}
            if not isinstance(value.get("flags"), dict):
                value["flags"] = {}
            if not isinstance(value.get("shapes"), list):
                value["shapes"] = []
            return value
        except (OSError, json.JSONDecodeError):
            return {"version": "2.4.0", "flags": {}, "shapes": []}

    @staticmethod
    def _max_confidence(shapes: list[dict[str, Any]]) -> float:
        scores: list[float] = []
        for shape in shapes:
            try:
                score = float(shape.get("score"))
            except (TypeError, ValueError):
                continue
            if 0 < score <= 1:
                scores.append(score)
        return max(scores, default=0.0)

    @staticmethod
    def _review_data(file_path: str) -> dict[str, str]:
        if not file_path:
            return {"review_status": "pending", "review_comment": ""}
        review_path = Path(file_path).parent / (Path(file_path).name + ".review.json")
        try:
            value = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, AttributeError):
            return {"review_status": "pending", "review_comment": ""}
        if not isinstance(value, dict):
            return {"review_status": "pending", "review_comment": ""}
        status = value.get("status", "pending")
        return {
            "review_status": status if status in {"pending", "approved", "rejected"} else "pending",
            "review_comment": str(value.get("comment") or "")[:4000],
        }

    @classmethod
    def _review_status(cls, file_path: str) -> str:
        return cls._review_data(file_path)["review_status"]

    def save_annotation(self, manifest_id: str, item_id: str, value: dict[str, Any]) -> dict[str, Any]:
        self._require_open_iwisc_task(self._manifest(manifest_id))
        manifest, item = self._raw_item(manifest_id, item_id)
        path = self._local_item_path(manifest, item)
        shapes = value.get("shapes")
        if not isinstance(shapes, list) or len(shapes) > 10000:
            raise ServiceError(400, "Annotation must contain a valid shapes list")
        checked_shapes = []
        for shape in shapes:
            if not isinstance(shape, dict):
                raise ServiceError(400, "Each annotation shape must be an object")
            copied = dict(shape)
            if not isinstance(copied.get("label", ""), str):
                raise ServiceError(400, "Shape label must be text")
            if copied.get("shape_type") == "rectangle":
                points = copied.get("points")
                if not isinstance(points, list) or len(points) < 2:
                    raise ServiceError(400, "Rectangle shapes need two corner points")
                try:
                    (x1, y1), (x2, y2) = points[:2]
                    x1, y1, x2, y2 = map(float, (x1, y1, x2, y2))
                    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
                        raise ValueError
                    width, height = item.get("width"), item.get("height")
                    if (
                        x1 < 0
                        or y1 < 0
                        or x2 <= x1
                        or y2 <= y1
                        or (width is not None and x2 > float(width))
                        or (height is not None and y2 > float(height))
                    ):
                        raise ValueError
                except (TypeError, ValueError, IndexError) as exc:
                    raise ServiceError(400, "Rectangle coordinates are invalid") from exc
            checked_shapes.append(copied)
        annotation_path = path.with_suffix(".json")
        existing = self._read_annotation(str(annotation_path))
        updated = {
            **existing,
            "version": str(value.get("version") or existing.get("version") or "2.4.0"),
            "flags": value.get("flags") if isinstance(value.get("flags"), dict) else existing.get("flags", {}),
            "shapes": checked_shapes,
            "imagePath": path.name,
            "imageData": None,
            "imageHeight": int(item.get("height") or value.get("imageHeight") or 0),
            "imageWidth": int(item.get("width") or value.get("imageWidth") or 0),
        }
        _atomic_json(annotation_path, updated)
        self._sync_iwisc_task_bundle(manifest_id, item_id)
        return {"item_id": item_id, "saved": True, "shape_count": len(checked_shapes)}

    def save_classification(self, manifest_id: str, item_id: str, label: str) -> dict[str, Any]:
        self._require_open_iwisc_task(self._manifest(manifest_id))
        manifest, item = self._raw_item(manifest_id, item_id)
        path = self._local_item_path(manifest, item)
        label = label.strip()
        cfg = self._process("012")._cfg
        classifications = cfg.load_classifications(manifest_id)
        if label:
            classifications[item_id] = label
        else:
            classifications.pop(item_id, None)
        cfg.save_classifications(manifest_id, classifications)
        by_path = cfg.load_classifications_by_path()
        if label:
            by_path[str(path)] = label
        else:
            by_path.pop(str(path), None)
        cfg.save_classifications_by_path(by_path)
        self._sync_duplicate_handoff_answer(manifest, item, label)
        self._sync_iwisc_task_bundle(manifest_id, item_id)
        return {"item_id": item_id, "classification": label}

    def review(self, manifest_id: str, item_id: str, status: str, comment: str = "") -> dict[str, Any]:
        self._require_open_iwisc_task(self._manifest(manifest_id))
        manifest, item = self._raw_item(manifest_id, item_id)
        path = self._local_item_path(manifest, item)
        if status not in {"pending", "approved", "rejected"}:
            raise ServiceError(400, "Review status must be pending, approved, or rejected")
        review_path = path.parent / (path.name + ".review.json")
        value = {
            "status": status,
            "comment": str(comment)[:4000],
            "reviewer": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        _atomic_json(review_path, value)
        return {"item_id": item_id, **value}

    def labels(self, manifest_id: str) -> dict[str, Any]:
        self._manifest(manifest_id)
        result = self._process("017").execute_logic({"manifest_id": manifest_id})
        if result.get("error"):
            raise ServiceError(400, "Could not scan labels in the selected dataset")
        return {
            "manifest_id": manifest_id,
            "label_map": {
                str(label): len(files) if isinstance(files, list) else int(files or 0)
                for label, files in result.get("label_map", {}).items()
            },
            "near_dupes": result.get("near_dupes", []),
            "total_items": result.get("total_items", 0),
            "annotated_xany": result.get("annotated_xany", 0),
            "label_counts": result.get("label_counts", {}),
            "classification_counts": result.get("classification_counts", {}),
            "export_count": result.get("export_count", 0),
        }

    def exports(self, manifest_id: str) -> list[dict[str, Any]]:
        self._manifest(manifest_id)
        return [
            {
                "export_id": row.get("export_id", ""),
                "format": row.get("format") or row.get("export_format", ""),
                "item_count": row.get("item_count", 0),
                "created_at": row.get("created_at", ""),
            }
            for row in manifest_db.get_exports(self.db_path, manifest_id)
        ]

    def update_labels(self, manifest_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        self._require_open_iwisc_task(self._manifest(manifest_id))
        module = self._process("017")
        context = {"manifest_id": manifest_id}
        if action == "rename":
            count = module.do_rename(context, str(params.get("old") or ""), str(params.get("new") or ""))
        elif action == "merge":
            count = module.do_merge(context, list(params.get("sources") or []), str(params.get("target") or ""))
        elif action == "delete":
            count = module.do_delete(context, str(params.get("label") or ""))
        else:
            raise ServiceError(404, "Unknown label operation")
        return {"updated": int(count), "labels": self.labels(manifest_id)}

    def export(self, manifest_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self._manifest(manifest_id)
        formats = params.get("export_formats") or ["coco_json"]
        if not isinstance(formats, list) or not formats or any(fmt not in EXPORT_FORMATS for fmt in formats):
            raise ServiceError(400, "Choose one or more supported export formats")
        result = self._process("014").execute_logic({"manifest_id": manifest_id, **params, "export_formats": formats})
        result.pop("export_dir", None)
        result.pop("export_paths", None)
        result.pop("lv_handoff_closed", None)
        if result.get("mode") == "error":
            result["error"] = "Export failed. Check the local log for details."
        return result

    def launch_tool(
        self,
        manifest_id: str,
        item_id: str,
        tool_id: str,
        executable_override: str = "",
    ) -> dict[str, Any]:
        self._require_open_iwisc_task(self._manifest(manifest_id))
        manifest, item = self._raw_item(manifest_id, item_id)
        path = self._local_item_path(manifest, item)
        if tool_id not in TOOLS:
            raise ServiceError(400, "Unsupported annotation tool")
        options: dict[str, Any] = {}
        if executable_override.strip():
            override = Path(executable_override).expanduser().resolve()
            if not override.is_file():
                raise ServiceError(400, "The selected annotation tool executable was not found")
            options["executable_override"] = str(override)
        try:
            from domain.tools.registry import get_tool_registry

            _tool, adapter = get_tool_registry().get(tool_id)
            error = adapter.launch_file(str(path), options)
        except Exception as exc:  # tool installation is optional
            raise ServiceError(503, "The selected annotation tool could not be started") from exc
        if error:
            raise ServiceError(503, "The selected annotation tool could not be started")
        return {"launched": True, "tool": tool_id, "item_id": item_id}

    def store_yolo_model(
        self, filename: str, reader: Callable[[int], bytes], content_length: int
    ) -> dict[str, Any]:
        """Copy a user-selected YOLO model into app data without exposing its path."""
        if not isinstance(filename, str):
            raise ServiceError(400, "Select a local YOLO model file")
        display_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        display_name = "".join(char for char in display_name if char.isprintable())[:255]
        suffix = Path(display_name).suffix.lower()
        if not display_name or suffix not in _YOLO_MODEL_SUFFIXES:
            raise ServiceError(415, "Choose a .pt, .onnx, or .engine model file")
        if content_length <= 0:
            raise ServiceError(400, "The selected model file is empty")
        if content_length > MAX_LOCAL_MODEL_BYTES:
            raise ServiceError(413, "The selected model file exceeds the 256 MiB limit")

        app_data = self.data_dir.resolve()
        model_dir = app_data / "models" / "yolo"
        if any(path.is_symlink() for path in (app_data / "models", model_dir)):
            raise ServiceError(409, "The local model storage directory is not safe to use")
        try:
            model_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ServiceError(507, "The selected model could not be stored in app data") from exc
        try:
            model_dir.resolve().relative_to(app_data)
        except ValueError as exc:
            raise ServiceError(409, "The local model storage directory is not safe to use") from exc

        fd, temporary_name = tempfile.mkstemp(prefix=".model-upload-", suffix=".tmp", dir=model_dir)
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        remaining = content_length
        try:
            with os.fdopen(fd, "wb") as target:
                while remaining:
                    chunk = reader(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ServiceError(400, "The model upload ended before its declared size")
                    target.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                target.flush()
                os.fsync(target.fileno())

            model_hash = digest.hexdigest()
            destination = model_dir / f"{model_hash}{suffix}"
            if destination.is_symlink():
                raise ServiceError(409, "The existing local model entry is not safe to use")
            if destination.exists():
                if not destination.is_file() or destination.stat().st_size != content_length:
                    raise ServiceError(409, "The existing local model entry failed its integrity check")
                existing_digest = hashlib.sha256()
                with destination.open("rb") as existing_model:
                    for chunk in iter(lambda: existing_model.read(1024 * 1024), b""):
                        existing_digest.update(chunk)
                if existing_digest.hexdigest() != model_hash:
                    raise ServiceError(409, "The existing local model entry failed its integrity check")
                temporary_path.unlink()
            else:
                os.replace(temporary_path, destination)
            return {
                "model_ref": f"local-model:{model_hash}{suffix}",
                "name": display_name,
                "size_bytes": content_length,
            }
        except ServiceError:
            raise
        except OSError as exc:
            raise ServiceError(507, "The selected model could not be stored in app data") from exc
        finally:
            temporary_path.unlink(missing_ok=True)

    def _resolve_yolo_model_path(self, model_value: str) -> Path:
        reference = _LOCAL_YOLO_MODEL_REF.fullmatch(model_value)
        if reference:
            app_data = self.data_dir.resolve()
            stored_dir = app_data / "models" / "yolo"
            if any(path.is_symlink() for path in (app_data / "models", stored_dir)):
                raise ServiceError(400, "Select a local YOLO model file")
            model_dir = stored_dir.resolve()
            try:
                model_dir.relative_to(app_data)
            except ValueError as exc:
                raise ServiceError(400, "Select a local YOLO model file") from exc
            stored_path = model_dir / f"{reference.group(1)}{reference.group(2)}"
            path = stored_path.resolve()
            if stored_path.is_symlink() or path.parent != model_dir or not path.is_file():
                raise ServiceError(400, "Select a local YOLO model file")
            return path
        path = Path(model_value).expanduser().resolve()
        if not path.is_file():
            raise ServiceError(400, "Select a local YOLO model file")
        return path

    def run_yolo(self, manifest_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self._require_open_iwisc_task(self._manifest(manifest_id))
        model_path = self._resolve_yolo_model_path(str(params.get("model_path") or ""))
        try:
            confidence = float(params.get("confidence", 0.25))
        except (TypeError, ValueError) as exc:
            raise ServiceError(400, "Confidence must be a number between 0 and 1") from exc
        if not 0 <= confidence <= 1:
            raise ServiceError(400, "Confidence must be between 0 and 1")
        result = self._process("012").execute_logic({"manifest_id": manifest_id})
        items = result.get("items", [])
        selected_ids = set(params.get("item_ids") or [item.get("item_id") for item in items])
        selected = [item for item in items if item.get("item_id") in selected_ids]
        if len(selected) > 500:
            raise ServiceError(413, "Run local pre-labeling on at most 500 images at a time")
        counts = {"ok": 0, "skipped": 0, "errors": 0, "detected": 0}
        overwrite = bool(params.get("overwrite", False))
        model = None
        for item in selected:
            path = Path(item["file_path"])
            annotation_path = path.with_suffix(".json")
            if annotation_path.exists() and not overwrite:
                counts["skipped"] += 1
                continue
            if model is None:
                try:
                    # Keep the optional model runtime out of the base service
                    # bundle. It is imported only after an explicit prelabel
                    # request, and remains independently installable.
                    yolo_module = importlib.import_module("ultralytics")
                    model = yolo_module.YOLO(str(model_path))
                except Exception as exc:
                    raise ServiceError(503, "Could not load the selected local YOLO model") from exc
            try:
                prediction = model(str(path), conf=confidence, verbose=False)[0]
                height, width = (int(prediction.orig_shape[0]), int(prediction.orig_shape[1]))
                shapes = []
                for box in prediction.boxes:
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                    class_id = int(box.cls[0])
                    names = model.names
                    label = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(names[class_id])
                    shapes.append(
                        {
                            "label": label,
                            "score": round(float(box.conf[0]), 4),
                            "points": [[x1, y1], [x2, y2]],
                            "group_id": None,
                            "description": "",
                            "difficult": False,
                            "shape_type": "rectangle",
                            "flags": {},
                        }
                    )
                existing = self._read_annotation(str(annotation_path))
                existing.update(
                    {
                        "version": "1.0.0",
                        "flags": existing.get("flags", {}),
                        "shapes": shapes,
                        "imagePath": path.name,
                        "imageData": None,
                        "imageHeight": height,
                        "imageWidth": width,
                    }
                )
                _atomic_json(annotation_path, existing)
                counts["ok"] += 1
                counts["detected"] += len(shapes)
            except Exception:
                counts["errors"] += 1
        return counts
