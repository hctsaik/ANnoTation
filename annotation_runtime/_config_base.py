"""Persistent settings, isolated from the former CIM installation."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from annotation_runtime import app_root, data_root


def project_root():
    return app_root()


def log_dir():
    return data_root()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def manifest_key(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]


def config_path(module_id):
    identifier = str(module_id)
    if not identifier.isdecimal():
        raise ValueError("Module configuration ID must be numeric")
    return data_root() / "config" / f"module_{identifier}.json"


def _read(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_config(module_id, defaults):
    return {**defaults, **_read(config_path(module_id))}


def save_config(module_id, value):
    atomic_write(config_path(module_id), json.dumps(value, ensure_ascii=False, indent=2))


def shared_path():
    return data_root() / "config" / "shared.json"


def load_shared():
    return _read(shared_path())


def shared_manifest_id():
    return load_shared().get("last_manifest_id", "")


def manifest_db_path():
    return data_root() / "db" / "annotation-standalone-v1.sqlite"
