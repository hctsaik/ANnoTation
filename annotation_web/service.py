from __future__ import annotations

import importlib.util
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
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from annotation_runtime import data_root
from annotation_runtime import _manifest_db as manifest_db


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")
EXPORT_FORMATS = {"coco_json", "yolo_txt", "pascal_voc", "imagefolder", "csv"}
TOOLS = {"x-anylabeling", "labelme", "isat"}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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
                "source_type": row["source_type"],
                "created_at": row["created_at"],
                "item_count": row["item_count"],
            }
            for row in rows
        ]

    def create_dataset(self, params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode", "local")
        if mode != "local":
            raise ServiceError(501, "This web build currently supports local folder datasets")
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
        if result.get("mode") != "ready":
            raise ServiceError(400, "Could not create a dataset from the selected folder")
        seed = result.get("yolo_seed", {})
        return {
            "manifest_id": result["manifest_id"],
            "name": result.get("manifest_name") or path.name,
            "item_count": result.get("total_count", 0),
            "yolo_seed": {
                key: seed[key]
                for key in ("applied", "classes", "reason")
                if key in seed
            },
        }

    def workspace(self, manifest_id: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        self._manifest(manifest_id)
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
        return {"item_id": item_id, "saved": True, "shape_count": len(checked_shapes)}

    def save_classification(self, manifest_id: str, item_id: str, label: str) -> dict[str, Any]:
        manifest, item = self._raw_item(manifest_id, item_id)
        path = self._local_item_path(manifest, item)
        cfg = self._process("012")._cfg
        classifications = cfg.load_classifications(manifest_id)
        label = label.strip()
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
        return {"item_id": item_id, "classification": label}

    def review(self, manifest_id: str, item_id: str, status: str, comment: str = "") -> dict[str, Any]:
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
        self._manifest(manifest_id)
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

    def run_yolo(self, manifest_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self._manifest(manifest_id)
        model_path = Path(str(params.get("model_path") or "")).expanduser().resolve()
        if not model_path.is_file():
            raise ServiceError(400, "Select a local YOLO model file")
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
        try:
            from ultralytics import YOLO

            model = YOLO(str(model_path))
        except Exception as exc:
            raise ServiceError(503, "Could not load the selected local YOLO model") from exc
        counts = {"ok": 0, "skipped": 0, "errors": 0, "detected": 0}
        overwrite = bool(params.get("overwrite", False))
        for item in selected:
            path = Path(item["file_path"])
            annotation_path = path.with_suffix(".json")
            if annotation_path.exists() and not overwrite:
                counts["skipped"] += 1
                continue
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
