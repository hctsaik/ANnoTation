"""App-owned paths for the independent Streamlit annotation workspace."""
import os
from pathlib import Path


def app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def shared_root() -> Path:
    return Path(__file__).resolve().parent


def data_root() -> Path:
    value = os.environ.get("APP_DATA_DIR") or os.environ.get("CIM_LOG_DIR")
    return Path(value).expanduser().resolve() if value else Path.home() / ".annotation-standalone"
