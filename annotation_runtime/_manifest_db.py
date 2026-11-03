"""Versioned local dataset store. Legacy databases are never migrated implicitly."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

APPLICATION_ID = 0x414E4E31
VERSION = 1
_TABLES = {"manifests", "items", "exports"}


@contextmanager
def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        app_id = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if not tables and app_id == 0 and version == 0:
            db.executescript(f"""
                CREATE TABLE manifests (manifest_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    source_type TEXT NOT NULL, source_config TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE items (item_id TEXT PRIMARY KEY, manifest_id TEXT NOT NULL
                    REFERENCES manifests(manifest_id), payload TEXT NOT NULL);
                CREATE INDEX items_manifest ON items(manifest_id);
                CREATE TABLE exports (export_id TEXT PRIMARY KEY, manifest_id TEXT NOT NULL
                    REFERENCES manifests(manifest_id), format TEXT NOT NULL, output_path TEXT NOT NULL,
                    item_count INTEGER NOT NULL, created_at TEXT NOT NULL);
                PRAGMA application_id={APPLICATION_ID};
                PRAGMA user_version={VERSION};
            """)
        elif app_id != APPLICATION_ID or version != VERSION or tables != _TABLES:
            raise ValueError("Unsupported annotation database; choose a fresh App data directory. No migration performed.")
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            yield db
    finally:
        db.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db(path):
    with _connect(path):
        pass


def create_manifest(path, manifest_id, name, source_type, source_config):
    with _connect(path) as db:
        db.execute("INSERT INTO manifests VALUES(?,?,?,?,?)",
                   (manifest_id, name, source_type, json.dumps(source_config), _now()))


def add_manifest_items(path, manifest_id, items):
    rows = [(item["item_id"], manifest_id, json.dumps(item, ensure_ascii=False)) for item in items]
    with _connect(path) as db:
        db.executemany("INSERT INTO items VALUES(?,?,?)", rows)


def list_manifests(path):
    with _connect(path) as db:
        rows = db.execute("SELECT m.*, (SELECT COUNT(*) FROM items i WHERE i.manifest_id=m.manifest_id) item_count FROM manifests m ORDER BY created_at DESC").fetchall()
    result = []
    for row in rows:
        value = dict(row)
        value["source_config"] = json.loads(value["source_config"])
        result.append(value)
    return result


def get_manifest(path, manifest_id):
    return next((m for m in list_manifests(path) if m["manifest_id"] == manifest_id), None)


def get_manifest_items(path, manifest_id, limit=None):
    if limit is not None and (not isinstance(limit, int) or limit < 0):
        raise ValueError("limit must be a nonnegative integer")
    with _connect(path) as db:
        rows = db.execute("SELECT payload FROM items WHERE manifest_id=? ORDER BY rowid LIMIT ?",
                          (manifest_id, -1 if limit is None else limit)).fetchall()
    return [json.loads(row[0]) for row in rows]


def create_export_record(path, export_id, manifest_id, format, output_path, item_count):
    with _connect(path) as db:
        db.execute("INSERT INTO exports VALUES(?,?,?,?,?,?)",
                   (export_id, manifest_id, format, str(output_path), item_count, _now()))


def get_exports(path, manifest_id):
    with _connect(path) as db:
        return [{**dict(row), "export_format": row["format"], "export_path": row["output_path"]} for row in db.execute("SELECT * FROM exports WHERE manifest_id=? ORDER BY created_at DESC", (manifest_id,))]
