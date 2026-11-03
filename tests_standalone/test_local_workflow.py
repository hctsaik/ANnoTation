"""Real source-only workflow, fresh isolated data; no CIM import or UI doubles."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(number, suffix):
    path = ROOT / "modules" / f"module_{number}" / f"{number}_{suffix}.py"
    if suffix.startswith("_"):
        path = path.with_name(suffix + ".py")
    spec = importlib.util.spec_from_file_location(f"standalone_{number}_{suffix}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def phase(name, workspace):
    from annotation_runtime import _config_base as cfg, _manifest_db as db
    media = workspace / "media"
    if name == "create":
        from PIL import Image
        media.mkdir()
        Image.new("RGB", (60, 40), "red").save(media / "sample.png")
        result = load("026", "process").execute_logic({"mode": "local", "folder_path": str(media)})
        assert result["mode"] == "ready", result
        mid = result["manifest_id"]
        (workspace / "id.txt").write_text(mid)
        assert db.list_manifests(cfg.manifest_db_path())[0]["item_count"] == 1
        return
    mid = (workspace / "id.txt").read_text()
    img = media / "sample.png"
    before = hashlib.sha256(img.read_bytes()).hexdigest()
    if name == "annotate":
        result = load("012", "process").execute_logic({"manifest_id": mid, "labels": ["part"]})
        assert result["mode"] == "ready" and result["total"] == 1, result
        canvas = load("012", "_canvas_editor")
        canvas.save_canvas_annotation(str(img), [{"label": "part", "x": 5, "y": 5, "w": 20, "h": 15}], 60, 40)
        load("018", "output")._set_review(str(img), "approved", "checked")
    else:
        result = load("012", "process").execute_logic({"manifest_id": mid, "labels": ["part"]})
        assert result["annotated"] == 1, result
        reviewed = load("018", "process").execute_logic({"manifest_id": mid})
        assert reviewed["items"][0]["review_status"] == "approved", reviewed
        exported = load("014", "process").execute_logic({"manifest_id": mid, "export_formats": ["coco_json", "yolo_txt"], "export_dir": str(workspace / "export")})
        assert exported["mode"] == "done", exported
        assert len(db.get_exports(cfg.manifest_db_path(), mid)) == 2
        dashboard = load("017", "process").execute_logic({"manifest_id": mid})
        assert dashboard["total_items"] == 1, dashboard
    assert before == hashlib.sha256(img.read_bytes()).hexdigest()


class LocalWorkflow(unittest.TestCase):
    def test_reopen_annotation_review_export_across_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            env = {**os.environ, "APP_DATA_DIR": str(workspace / "data"), "CIM_LOG_DIR": str(workspace / "data"), "PYTHONIOENCODING": "utf-8"}
            for name in ("create", "annotate", "reopen"):
                run = subprocess.run([sys.executable, __file__, name, folder], env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_legacy_database_rejected_without_changes(self):
        from annotation_runtime import _manifest_db as db
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.sqlite"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE manifests (legacy TEXT)")
                conn.execute("INSERT INTO manifests VALUES('preserve')")
            conn.close()
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "No migration"):
                db.init_db(path)
            self.assertEqual(path.read_bytes(), before)

    def test_canvas_refuses_corrupt_existing_sidecar(self):
        canvas = load("012", "_canvas_editor")
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "a.png"
            sidecar = image.with_suffix(".json")
            sidecar.write_text("not valid json")
            with self.assertRaises(json.JSONDecodeError):
                canvas.save_canvas_annotation(str(image), [], 10, 10)
            self.assertEqual(sidecar.read_text(), "not valid json")

    def test_canvas_preserves_classification_polygons_and_metadata(self):
        canvas = load("012", "_canvas_editor")
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "a.png"
            sidecar = image.with_suffix(".json")
            polygon = {"label": "part", "shape_type": "polygon", "points": [[1, 1], [5, 1], [5, 5]]}
            sidecar.write_text(json.dumps({"flags": {"classification": "good"}, "custom": 42, "shapes": [polygon]}))
            result = canvas.save_canvas_annotation(str(image), [], 10, 10)
            self.assertEqual(result["flags"], {"classification": "good"})
            self.assertEqual(result["custom"], 42)
            self.assertEqual(result["shapes"], [polygon])


if __name__ == "__main__":
    if len(sys.argv) == 3:
        phase(sys.argv[1], Path(sys.argv[2]))
    else:
        unittest.main()
